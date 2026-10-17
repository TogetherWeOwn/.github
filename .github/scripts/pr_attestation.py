#!/usr/bin/env python3
"""Shadow-only PR attestation validator. Stdlib; run with python3 -I.

PR text is data, never code. No checkout of the target repository, subprocess,
third-party imports, or execution of claimed tests. API/schema failures produce
an explicit error observation, NOT a clean verdict. The CLI always exits zero:
this instrument is not a merge gate and has no enforce-mode switch.
"""

import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SCHEMA_VERSION = 1
# Central path floor: match real module names as well as directory layouts.
# Word boundaries (snake/kebab/dot/CamelCase) avoid substrings such as authors,
# voices, billings, and roleplay. No target-repository policy is executed.
T0_DIRECTORIES = frozenset({"migrations", "sql", "db", "database", "drizzle"})
T0_WORDS = frozenset({
    "auth", "authn", "authz", "authentication", "authorization", "oauth",
    "session", "sessions", "billing", "gateway", "voice", "crypto", "vault",
    "secret", "secrets", "backup", "backups", "dump", "dumps",
    "grant", "grants", "role", "roles", "erasure", "ingest",
})
T0_PATTERNS = (
    r"^\.github/workflows/deploy[^/]*(?:/.*)?$",
    r"^wrangler[^/]*(?:/.*)?$", r"^crates/cutover/",
)
DOC_SUFFIXES = (".md", ".rst", ".txt")
TEXT_DOC_NAMES = frozenset({
    "readme.txt", "license.txt", "notice.txt", "changelog.txt", "contributing.txt",
    "code_of_conduct.txt", "security.txt", "support.txt", "authors.txt",
})
CLAIM = re.compile(r"- (C[1-9][0-9]*): (.+)")
TEST = re.compile(
    r"- (C[1-9][0-9]*) \| test=([^|]+) \| failed_before=(yes|no|not-run) \| proof=(.+)"
)
CHECK = re.compile(r"- \[([ xX])\] (D[1-8]): (.+)")
PLACEHOLDER = re.compile(r"(?:<[^>]+>|\b(?:TODO|TBD|REPLACE_ME)\b)", re.I)
SHA = re.compile(r"[a-f0-9]{40}")
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


class ObservationError(Exception):
    """Safe, fixed diagnostic code; never include API response/token/body."""


def meaningful(text):
    return bool(text.strip()) and not PLACEHOLDER.search(text)


def valid_path(path):
    return (isinstance(path, str) and bool(path) and not path.startswith("/")
            and "\\" not in path and not any(ord(c) < 32 for c in path)
            and all(p not in ("", ".", "..") for p in path.split("/")))


def path_words(component):
    # Split both SessionController and OAuthToken before normalizing case.
    component = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", component)
    component = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", component)
    return set(re.split(r"[^a-z0-9]+", component.lower()))


def is_t0(path):
    if any(re.search(pattern, path) for pattern in T0_PATTERNS):
        return True
    parts = path.split("/")
    directories = parts[:-1]
    if any(part.lower() in T0_DIRECTORIES for part in directories):
        return True
    # Prose named auth-guide.md is not an auth module. Documentation INSIDE a
    # critical directory still inherits that directory's floor.
    components = directories if path.lower().endswith(DOC_SUFFIXES) else parts
    return any(path_words(part) & T0_WORDS for part in components)


def docs_only(path):
    # Reject build/dependency inputs before matching documentation. In
    # particular, docs/requirements.txt and CMakeLists.txt are executable policy.
    name = path.rsplit("/", 1)[-1].lower()
    if name == "cmakelists.txt" or re.fullmatch(r"(?:requirements|constraints)(?:[._-].*)?", name):
        return False
    if path.startswith(".github/") or ("/" in path and not path.startswith("docs/")):
        return False
    # Arbitrary .txt is not documentation: only these conventional names qualify.
    return name.endswith((".md", ".rst")) or name in TEXT_DOC_NAMES


def classify(paths):
    if any(is_t0(path) for path in paths):
        return "T0"
    if paths and all(docs_only(path) for path in paths):
        return "T2"
    return "T1"


def visible_lines(body):
    """Ignore comment/example blocks; only visible, unfenced fields count."""
    text = re.sub(r"<!--[\s\S]*?(?:-->|$)", "", body)
    fence = None
    result = []
    for raw in text.splitlines():
        line = raw.strip()
        match = re.match(r"^(`{3,}|~{3,})(.*)$", line)
        if fence:
            if match and match[1][0] == fence[0] and len(match[1]) >= len(fence) and not match[2].strip():
                fence = None
            continue
        if match:
            fence = match[1]
            continue
        # Four-space indented examples and blockquotes are not attestations.
        if not raw.startswith(("    ", "\t", ">")):
            result.append(line)
    return result


def parse_attestation(body):
    if not isinstance(body, str) or len(body) > 65536:
        return None, ["body_missing_or_oversize"]
    lines = visible_lines(body)
    starts = [i for i, line in enumerate(lines) if line == "## Attestation"]
    if len(starts) != 1:
        return None, ["attestation_missing_or_duplicate"]
    start = starts[0] + 1
    end = next((i for i in range(start, len(lines)) if re.match(r"^#{1,2} ", lines[i])), len(lines))
    sections = {"header": []}
    current = "header"
    issues = []
    for line in lines[start:end]:
        if not line:
            continue
        if line.startswith("### "):
            current = line[4:]
            if current not in ("Claims", "Tests", "High-risk checklist") or current in sections:
                issues.append("section_unknown_or_duplicate")
            sections.setdefault(current, [])
        else:
            sections[current].append(line)
    for name in ("Claims", "Tests", "High-risk checklist"):
        if name not in sections:
            issues.append("section_missing")
    header = sections["header"]
    tier = header[0][11:] if len(header) == 1 and header[0].startswith("risk_tier: ") else None
    if tier not in ("T0", "T1", "T2"):
        issues.append("risk_tier_invalid")
        tier = None
    claims = {}
    for line in sections.get("Claims", []):
        match = CLAIM.fullmatch(line)
        if not match or not meaningful(match[2]):
            issues.append("claim_shape_invalid")
        elif match[1] in claims:
            issues.append("claim_duplicate")
        else:
            claims[match[1]] = match[2]
    if not claims:
        issues.append("claims_empty")
    tests = []
    no_tests = False
    for line in sections.get("Tests", []):
        if line.startswith("- none | reason="):
            if no_tests or not meaningful(line[len("- none | reason="):]):
                issues.append("no_tests_reason_invalid")
            no_tests = True
            continue
        match = TEST.fullmatch(line)
        if not match or not meaningful(match[4]):
            issues.append("test_shape_invalid")
            continue
        target = match[2].strip()
        path, separator, selector = target.partition("::")
        if not valid_path(path) or (separator and not meaningful(selector)):
            issues.append("test_path_invalid")
            continue
        tests.append({"claim": match[1], "file": path, "failed_before": match[3]})
    if no_tests and (tests or tier != "T2"):
        issues.append("no_tests_only_allowed_for_docs")
    if not tests and not no_tests:
        issues.append("tests_empty")
    checklist = {}
    for line in sections.get("High-risk checklist", []):
        match = CHECK.fullmatch(line)
        if not match or not meaningful(match[3]):
            issues.append("checklist_shape_invalid")
        elif match[2] in checklist:
            issues.append("checklist_duplicate")
        else:
            checklist[match[2]] = match[1].lower() == "x"
    if set(checklist) != {f"D{i}" for i in range(1, 9)}:
        issues.append("checklist_incomplete")
    return {"tier": tier, "claims": claims, "tests": tests,
            "no_tests": no_tests, "checklist": checklist}, issues


def checked_files(files):
    if not isinstance(files, list) or not files:
        raise ObservationError("files_empty_or_invalid")
    paths, existing = set(), set()
    seen = set()
    for item in files:
        if not isinstance(item, dict) or not valid_path(item.get("filename")):
            raise ObservationError("file_shape_invalid")
        name, status = item["filename"], item.get("status")
        if name in seen or status not in {"added", "modified", "removed", "renamed", "copied", "changed", "unchanged"}:
            raise ObservationError("file_duplicate_or_status_invalid")
        seen.add(name)
        paths.add(name)
        if status != "removed":
            existing.add(name)
        if status == "renamed":
            previous = item.get("previous_filename")
            if not valid_path(previous):
                raise ObservationError("rename_source_missing")
            paths.add(previous)
    return paths, existing


def validate(body, files):
    paths, existing = checked_files(files)
    inferred = classify(paths)
    parsed, issues = parse_attestation(body)
    report = {"declared_tier": None, "path_tier": inferred,
              "files": len(files), "t0_paths": sum(is_t0(p) for p in paths),
              "claims": 0, "tests": 0, "failed_before": {}, "issues": issues}
    if parsed:
        tier = parsed["tier"]
        report.update(declared_tier=tier, claims=len(parsed["claims"]), tests=len(parsed["tests"]))
        # Higher-risk declarations are allowed; understatements are not.
        if tier and int(tier[1]) > int(inferred[1]):
            issues.append("risk_tier_understated")
        if (tier == "T0" or inferred == "T0") and not all(parsed["checklist"].values()):
            issues.append("high_risk_checklist_unchecked")
        covered = set()
        for test in parsed["tests"]:
            if test["claim"] not in parsed["claims"]:
                issues.append("test_claim_unknown")
            else:
                covered.add(test["claim"])
            if test["file"] not in existing:
                issues.append("test_file_not_in_head_diff")
            state = test["failed_before"]
            report["failed_before"][state] = report["failed_before"].get(state, 0) + 1
        if not parsed["no_tests"] and covered != set(parsed["claims"]):
            issues.append("claim_without_test")
    report["issues"] = sorted(set(issues))
    report["verdict"] = "invalid" if issues else "valid"
    return report


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward the repository token to a redirect destination.
        raise ObservationError("api_redirect_refused")


class GitHub:
    def __init__(self, token, repo):
        if not token or not isinstance(repo, str) or not REPO.fullmatch(repo):
            raise ObservationError("api_credentials_or_repository_missing")
        self.token = token
        self.repo = repo
        self.opener = urllib.request.build_opener(NoRedirect)
        self.deadline = time.monotonic() + 150
        self.calls = 0

    def get(self, suffix):
        self.calls += 1
        if self.calls > 600 or time.monotonic() >= self.deadline:
            raise ObservationError("api_budget_exceeded")
        request = urllib.request.Request(
            f"https://api.github.com/repos/{self.repo}/{suffix}",
            headers={"Authorization": f"Bearer {self.token}",
                     "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
        )
        try:
            with self.opener.open(request, timeout=min(20, max(1, self.deadline - time.monotonic()))) as response:
                raw = response.read(8_000_001)
                if len(raw) > 8_000_000:
                    raise ObservationError("api_response_oversize")
                return json.loads(raw)
        except (urllib.error.URLError, OSError, ValueError, RecursionError) as exc:
            raise ObservationError("api_request_failed") from exc

    def pages(self, suffix, key=None, max_pages=31):
        rows = []
        for page in range(1, max_pages + 1):
            data = self.get(f"{suffix}?per_page=100&page={page}")
            chunk = data.get(key) if key and isinstance(data, dict) else data
            if not isinstance(chunk, list) or any(not isinstance(row, dict) for row in chunk):
                raise ObservationError("api_list_shape_invalid")
            rows.extend(chunk)
            if len(chunk) < 100:
                return rows
        raise ObservationError("api_pagination_limit")


def sha(value):
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise ObservationError("sha_invalid")
    return value


def number(value):
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ObservationError("pr_number_invalid")
    return value


def pull(api, n):
    data = api.get(f"pulls/{number(n)}")
    if not isinstance(data, dict) or data.get("number") != n:
        raise ObservationError("pr_shape_invalid")
    try:
        sha(data["head"]["sha"])
        sha(data["base"]["sha"])
        if data["base"]["repo"]["full_name"].lower() != api.repo.lower():
            raise ObservationError("pr_repository_mismatch")
    except (KeyError, TypeError, AttributeError) as exc:
        raise ObservationError("pr_shape_invalid") from exc
    count = data.get("changed_files")
    if not isinstance(count, int) or isinstance(count, bool) or count < 1 or count > 3000:
        raise ObservationError("pr_file_count_unsupported")
    return data


def group_pulls(api, event):
    group = event.get("merge_group")
    if not isinstance(group, dict):
        raise ObservationError("merge_group_missing")
    base, head = sha(group.get("base_sha")), sha(group.get("head_sha"))
    base_ref = group.get("base_ref")
    if not isinstance(base_ref, str) or not base_ref.startswith("refs/heads/"):
        raise ObservationError("merge_group_base_invalid")
    suffix = f"compare/{base}...{head}"
    metadata = api.get(f"{suffix}?per_page=1&page=1")
    total = metadata.get("total_commits") if isinstance(metadata, dict) else None
    if not isinstance(total, int) or isinstance(total, bool) or not 1 <= total <= 500:
        raise ObservationError("merge_group_size_unsupported")
    commits = api.pages(suffix, key="commits", max_pages=6)
    commit_shas = {sha(row.get("sha")) for row in commits}
    if len(commits) != total or len(commit_shas) != total or head not in commit_shas:
        raise ObservationError("merge_group_commits_incomplete")
    candidates = set()
    # A synthetic head's association may cover just the last PR. Walk EVERY
    # compared commit and EVERY association page, never pulls[0] or branch-name
    # inference alone. Each candidate is refreshed through the pulls API below.
    for commit in sorted(commit_shas):
        for row in api.pages(f"commits/{commit}/pulls"):
            candidates.add(number(row.get("number")))
    result = []
    for n in sorted(candidates):
        pr = pull(api, n)
        head_in_group = pr["head"]["sha"] in commit_shas
        compatible = pr.get("state") == "open" and pr["base"].get("ref") == base_ref[len("refs/heads/"):]
        if head_in_group and not compatible:
            # A non-leading member may close or retarget after queue creation.
            # Its code remains in the compared group: never drop it and report
            # a clean observation for only the remaining compatible members.
            raise ObservationError("merge_group_candidate_state_or_base_stale")
        if compatible:
            if not head_in_group:
                # Could be a stale queued PR or a stacked PR sharing commits.
                # Ambiguity is an error observation, never silent exclusion.
                raise ObservationError("merge_group_candidate_head_unresolved")
            result.append(pr)
    if not result:
        raise ObservationError("merge_group_prs_unresolved")
    # GitHub's queue ref identifies at least its leading PR. It is only a
    # completeness cross-check, never the authority for enumerating all PRs.
    ref = group.get("head_ref", "")
    leading = re.search(r"/pr-([1-9][0-9]*)-", ref) if isinstance(ref, str) else None
    if leading and int(leading[1]) not in {pr["number"] for pr in result}:
        raise ObservationError("merge_group_leading_pr_unresolved")
    return result


def evaluate(api, event_name, event):
    if not isinstance(event, dict):
        raise ObservationError("event_shape_invalid")
    if event_name == "pull_request":
        detail = event.get("pull_request")
        if not isinstance(detail, dict) or not isinstance(detail.get("head"), dict):
            raise ObservationError("event_pr_missing")
        pr = pull(api, number(detail.get("number")))
        if pr["head"]["sha"] != sha(detail["head"].get("sha")):
            raise ObservationError("event_head_stale")
        prs = [pr]
    elif event_name == "merge_group":
        prs = group_pulls(api, event)
    else:
        raise ObservationError("event_unsupported")
    reports = []
    for pr in prs:
        n = pr["number"]
        try:
            files = api.pages(f"pulls/{n}/files")
            if len(files) != pr["changed_files"]:
                raise ObservationError("pr_files_incomplete")
            fresh = pull(api, n)
            if (fresh["head"]["sha"] != pr["head"]["sha"] or fresh["base"]["sha"] != pr["base"]["sha"]
                    or fresh.get("body") != pr.get("body") or fresh["changed_files"] != len(files)
                    or fresh.get("state") != pr.get("state")
                    or fresh["base"].get("ref") != pr["base"].get("ref")):
                raise ObservationError("pr_snapshot_changed")
            report = validate(pr.get("body"), files)
        except ObservationError as exc:
            report = {"verdict": "error", "issues": [str(exc)]}
        report.update(pr=n, head_sha=pr["head"]["sha"])
        reports.append(report)
    verdict = "error" if any(r["verdict"] == "error" for r in reports) else (
        "invalid" if any(r["verdict"] == "invalid" for r in reports) else "valid")
    return {"schema_version": SCHEMA_VERSION, "mode": "warn", "verdict": verdict,
            "observations": reports, "issues": []}


def summary(report):
    lines = ["## PR attestation (shadow / WARN)", "",
             f"Observation: **{report['verdict']}**. No enforcement; this check never blocks.", "",
             "Only declaration shape, path risk and changed-file references are checked. "
             "Test execution, test quality and failed-before claims are NOT verified.", ""]
    for issue in report.get("issues", []):
        lines.append(f"- Observation error: `{html.escape(issue)}`")
    for row in report.get("observations", []):
        lines.append(f"### PR #{row['pr']} — {row['verdict']}")
        if "path_tier" in row:
            lines.append(f"Declared: {row['declared_tier'] or 'missing'}; path floor: {row['path_tier']}; "
                         f"changed files: {row['files']}; T0 paths: {row['t0_paths']}; "
                         f"claims: {row['claims']}; test references: {row['tests']}.")
            lines.append(f"Failed-before declarations (unverified): `{json.dumps(row['failed_before'], sort_keys=True)}`")
        lines.extend(f"- `{html.escape(issue)}`" for issue in row["issues"])
        lines.append("")
    return "\n".join(lines) + "\n"


def main():
    try:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        api = GitHub(os.environ.get("GH_TOKEN"), os.environ.get("GITHUB_REPOSITORY"))
        report = evaluate(api, os.environ.get("GITHUB_EVENT_NAME"), event)
    except Exception as exc:
        # The exception text from an unknown error could contain untrusted body
        # data or credentials. Only our fixed ObservationError codes are safe.
        code = str(exc) if isinstance(exc, ObservationError) else "observation_runtime_error"
        report = {"schema_version": SCHEMA_VERSION, "mode": "warn", "verdict": "error",
                  "observations": [], "issues": [code]}
    print(json.dumps(report, sort_keys=True))
    if report["verdict"] != "valid":
        print(f"::warning::PR attestation shadow observation: {report['verdict']}; see job summary.")
    for env, text in (
        ("GITHUB_STEP_SUMMARY", summary(report)),
        ("GITHUB_OUTPUT", f"verdict={report['verdict']}\nwould_fail={str(report['verdict'] != 'valid').lower()}\n"),
    ):
        if os.environ.get(env):
            try:
                with open(os.environ[env], "a", encoding="utf-8") as handle:
                    handle.write(text)
            except OSError:
                print("::warning::PR attestation could not write reporting output.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
