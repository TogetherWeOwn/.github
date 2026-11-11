#!/usr/bin/env python3
"""Pull-request size: advisory labels and a required size gate.

Two contracts share this one file:

* label / weekly are advisory only. Their workflows are never required
  checks; any API failure is a warning and an exit code of 0. They exist as
  a visible nudge plus a countable record (the labels) for a weekly tally.
* gate is a REQUIRED check. It fails (exit 1) when the reviewable diff
  exceeds the reviewability budget - more than 400 counted lines or more
  than 20 counted files - so an over-limit pull request fails here instead
  of wasting a review cycle on a refusal (or a superficial review) later.
  It fails closed: if the size cannot be measured, it errors rather than
  passing silently. Pull requests at or above the 1,000-line hard stop
  have no exception: they must be split. Between the gate and the hard
  stop, the only way past it for a genuinely atomic change is the
  `size-exception` label plus an APPROVED review from someone other than
  the author - and even then Devin must still be satisfied via review
  slices (see the STANDARD.md "PR size gate" section). The gate warns
  (posts a warning comment, still passes) from 200 counted lines.

  Why these numbers, not the automated reviewer's ceiling: Devin's limit
  is not documented, and measurements only bracket it (35,226 raw lines
  reviewed vs 47,982 refused), so the gate sits at the evidence-based
  reviewability budget, far below any reviewer ceiling (see the gate
  reusable's threshold comment for the sources).

Size is additions + deletions over the files the pull-request API reports,
excluding lockfiles, generated files and fixtures (see EXCLUDED below; a repo
adds its own generated paths with PR_SIZE_EXCLUDE_EXTRA). A renamed file is
judged by its new path. Binary files report 0 additions/deletions in the API,
so images and other binaries never inflate the count. The gate and its
comments report raw and reviewable (counted) totals separately: the raw
total is every reported line, the reviewable total is after exclusions,
and only the reviewable total is gated. Hiding files in GitHub's display
is not exclusion - the exclusions above are the script's, applied per
repository config, never the viewer's.

Modes (first argument):
  label   Label one pull request and keep one advisory comment when the
          counted size is over the threshold (default: this mode).
  weekly  Count pull requests merged in the last 7 days by size label and
          print the tally (also appended to $GITHUB_STEP_SUMMARY when set).
  gate    Required check: exit 1 when the counted size exceeds
          PR_SIZE_GATE_THRESHOLD lines or PR_SIZE_GATE_FILES files
          without a valid `size-exception` (never at or above
          PR_SIZE_HARD_STOP), and keep a failure comment with the split
          procedure. Passing-but-large diffs (at or above
          PR_SIZE_WARN_THRESHOLD lines) keep a warning comment.

Environment contract (every value is a plain string):
  GH_TOKEN               token for the GitHub API (the workflow's GITHUB_TOKEN)
  REPO                   "owner/name"
  PR_NUMBER              label and gate modes: the pull request number
  PR_SIZE_THRESHOLD      label mode: counted lines above which the advisory
                         comment posts (default 400)
  PR_SIZE_GATE_THRESHOLD gate mode: counted lines the gate allows (default 400)
  PR_SIZE_GATE_FILES     gate mode: counted files the gate allows (default 20)
  PR_SIZE_HARD_STOP      gate mode: counted lines where the exception no
                         longer applies (default 1000)
  PR_SIZE_WARN_THRESHOLD gate mode: counted lines from which the passing
                         gate warns (default 200)
  PR_SIZE_EXCLUDE_EXTRA  comma-separated extra exclusion globs for this repo
  GITHUB_API_URL         API base (default https://api.github.com)
  GITHUB_STEP_SUMMARY    optional path the weekly tally is appended to

Stdlib only. This file lives once, here in the org `.github` repository, and
runs unchanged for every adopting repository through the reusable workflows
`.github/workflows/pr-size.yml`, `.github/workflows/pr-size-weekly.yml` and
`.github/workflows/pr-size-gate.yml`: adopters add only the thin caller from
`workflow-templates/pr-size.yml` and never copy this script. Thresholds are
fixed here (SIZES below): size/S <= 100, size/M <= 400, size/L <= 1000
counted lines, size/XL above that.
"""
import fnmatch
import http.client
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

# Caller-selected runners may still provide Python 3.10, before datetime.UTC.
UTC = timezone(timedelta(0))
MARKER = "<!-- pr-size-advisory -->"
GATE_MARKER = "<!-- pr-size-gate -->"
DEFAULT_THRESHOLD = 400
DEFAULT_GATE_THRESHOLD = 400
DEFAULT_GATE_FILE_THRESHOLD = 20
DEFAULT_HARD_STOP_THRESHOLD = 1000
DEFAULT_WARN_THRESHOLD = 200
EXCEPTION_LABEL = "size-exception"
EXCEPTION_LABEL_COLOR = "5319E7"
# The split procedure for over-gate pull requests, published once in the
# org standard so every repository's gate posts the same procedure.
SPLIT_PROCEDURE_URL = (
    "https://github.com/TogetherWeOwn/.github/blob/main/STANDARD.md#pr-size-gate"
)

# (label suffix, largest counted size with that label, label colour)
SIZES = [
    ("S", 100, "0E8A16"),
    ("M", 400, "FBCA04"),
    ("L", 1000, "D93F0B"),
    ("XL", None, "B60205"),
]
SIZE_LABELS = [f"size/{suffix}" for suffix, _cap, _colour in SIZES]

LOCKFILE_BASENAMES = {
    "package-lock.json",
    "npm-shrinkwrap.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "Cargo.lock",
    "uv.lock",
    "poetry.lock",
    "Pipfile.lock",
    "Gemfile.lock",
    "composer.lock",
    "flake.lock",
    "go.sum",
}

# Globs are matched against the whole path and, for patterns without a slash,
# the basename. `**/` also matches zero directories (see is_excluded).
EXCLUDED_GLOBS = [
    # any remaining lockfile shape not named above
    "*.lock",
    # generated: minified bundles and sourcemaps
    "*.min.js",
    "*.min.css",
    "*.map",
    # generated: test snapshots
    "**/__snapshots__/**",
    "*.snap",
    # generated / vendored trees committed in some repositories
    "**/dist/**",
    "**/vendor/**",
    "**/node_modules/**",
    # fixtures and recorded test data
    "**/fixtures/**",
    "**/__fixtures__/**",
    "**/testdata/**",
    "**/test-data/**",
]


def _glob_variants(pattern):
    """fnmatch has no `**`; expand it to the two shapes it must cover."""
    variants = [pattern]
    if pattern.startswith("**/"):
        variants.append(pattern[3:])
    return variants


def is_excluded(path, extra_globs=()):
    basename = path.rsplit("/", 1)[-1]
    if basename in LOCKFILE_BASENAMES:
        return True
    for pattern in [*EXCLUDED_GLOBS, *extra_globs]:
        for variant in _glob_variants(pattern):
            if fnmatch.fnmatchcase(path, variant):
                return True
            if "/" not in variant and fnmatch.fnmatchcase(basename, variant):
                return True
    return False


def size_label(counted):
    for suffix, cap, _colour in SIZES:
        if cap is None or counted <= cap:
            return f"size/{suffix}"
    raise AssertionError("unreachable: XL has no cap")


def count_files(files, extra_globs=()):
    """files: API file objects. Returns (counted, excluded, counted_files)."""
    counted = 0
    excluded = 0
    counted_files = 0
    for entry in files:
        lines = int(entry.get("additions", 0)) + int(entry.get("deletions", 0))
        if is_excluded(entry.get("filename", ""), extra_globs):
            excluded += lines
        else:
            counted += lines
            counted_files += 1
    return counted, excluded, counted_files


def comment_body(counted, excluded, counted_files, threshold):
    files_word = "file" if counted_files == 1 else "files"
    lines = [
        MARKER,
        "## Pull request size (advisory)",
        "",
        (f"This pull request changes **{counted} counted lines** across "
         f"{counted_files} {files_word}, over the {threshold}-line advisory threshold "
         f"({excluded} further lines in lockfiles, generated files and "
         "fixtures are not counted)."),
        "",
        ("Smaller pull requests get faster, more careful review. If this change "
         "can be split into independently mergeable pieces, please consider "
         "splitting it; if it cannot, no action is needed."),
        "",
        "_Advisory only: this comment and the `size/*` label never block a merge._",
    ]
    return "\n".join(lines) + "\n"


def under_body(counted, threshold):
    lines = [
        MARKER,
        "## Pull request size (advisory)",
        "",
        (f"This pull request is back under the {threshold}-line advisory "
         f"threshold ({counted} counted lines). Thank you."),
        "",
        "_Advisory only: this comment and the `size/*` label never block a merge._",
    ]
    return "\n".join(lines) + "\n"


def gate_failure_body(counted, excluded, counted_files,
                      threshold=DEFAULT_GATE_THRESHOLD,
                      file_threshold=DEFAULT_GATE_FILE_THRESHOLD,
                      hard_stop=DEFAULT_HARD_STOP_THRESHOLD):
    files_word = "file" if counted_files == 1 else "files"
    raw = counted + excluded
    lines = [
        GATE_MARKER,
        "## Pull request too large to review (required gate)",
        "",
        (f"This pull request changes **{counted} counted lines** across "
         f"{counted_files} {files_word} (raw diff {raw} lines including "
         f"{excluded} in lockfiles, generated files and fixtures, which "
         f"are not counted), over the {threshold}-line / {file_threshold}-file "
         "gate. Reviewers - automated and human alike - cannot review "
         "diffs this large carefully, so this check fails now instead of "
         "wasting a review cycle on a refusal or a superficial review later."),
        "",
        (f"**Split it.** Follow the split procedure: {SPLIT_PROCEDURE_URL} - "
         "stacked pull requests, refactor-then-change, feature flags for "
         "incomplete features, generated code in its own pull request, or "
         "review-only slices alongside one mergeable pull request (each "
         "slice its own small pull request)."),
        "",
        (f"**Genuinely atomic?** Add the `{EXCEPTION_LABEL}` label and get an "
         "APPROVED review **from someone other than the author**; this gate "
         f"then passes - but only below the {hard_stop}-line hard stop. At "
         "or above the hard stop there is no exception: split it (review "
         "slices count as separate small pull requests). Devin must still "
         "be satisfied via review slices for the over-limit diff even with "
         "the exception."),
    ]
    return "\n".join(lines) + "\n"


def gate_exception_body(counted, threshold):
    lines = [
        GATE_MARKER,
        "## Pull request size gate: exception approved",
        "",
        (f"This pull request changes {counted} counted lines, over the "
         f"{threshold}-line gate, but carries the `{EXCEPTION_LABEL}` label "
         "and an APPROVED review from someone other than the author, so the "
         "gate passes."),
        "",
        ("Devin must still be satisfied via review slices for the "
         "over-limit diff - open the slices before merging."),
    ]
    return "\n".join(lines) + "\n"


def gate_under_body(counted, excluded, threshold, file_threshold):
    lines = [
        GATE_MARKER,
        "## Pull request size gate",
        "",
        (f"This pull request is back under the {threshold}-line / "
         f"{file_threshold}-file gate ({counted} counted lines, raw diff "
         f"{counted + excluded} lines). Thank you."),
    ]
    return "\n".join(lines) + "\n"


def gate_warn_body(counted, excluded, counted_files,
                   threshold=DEFAULT_GATE_THRESHOLD,
                   warn=DEFAULT_WARN_THRESHOLD,
                   file_threshold=DEFAULT_GATE_FILE_THRESHOLD):
    files_word = "file" if counted_files == 1 else "files"
    lines = [
        GATE_MARKER,
        "## Pull request size gate: large but passing (warning)",
        "",
        (f"This pull request changes **{counted} counted lines** across "
         f"{counted_files} {files_word} (raw diff {counted + excluded} "
         f"lines including {excluded} in lockfiles, generated files and "
         f"fixtures, which are not counted) - over the {warn}-line review "
         f"target but under the {threshold}-line / "
         f"{file_threshold}-file gate, so this check passes. "
         "The target is 100-200 lines and 10 files or fewer; smaller pull "
         "requests get faster, more careful review."),
    ]
    return "\n".join(lines) + "\n"


def tally(items):
    """Count search-result items (merged PRs) by their size label."""
    counts = {label: 0 for label in SIZE_LABELS}
    counts["unlabelled"] = 0
    for item in items:
        names = [label.get("name", "") for label in item.get("labels", [])]
        found = [name for name in names if name in counts and name != "unlabelled"]
        if found:
            counts[found[0]] += 1
        else:
            counts["unlabelled"] += 1
    return counts


def weekly_report(counts, since):
    total = sum(counts.values())
    over = counts["size/L"] + counts["size/XL"]
    rows = [
        "## Weekly pull-request size tally",
        "",
        (f"Pull requests merged since {since}: **{total}** "
         f"({over} over the advisory threshold)."),
        "",
        "| Label | Merged |",
        "|---|---|",
    ]
    rows += [f"| `{label}` | {counts[label]} |" for label in SIZE_LABELS]
    rows.append(f"| (no size label) | {counts['unlabelled']} |")
    return "\n".join(rows) + "\n"


class ApiError(Exception):
    pass


class GitHubApi:
    def __init__(self, token, repo, base="https://api.github.com"):
        self.token = token
        self.repo = repo
        self.base = base.rstrip("/")

    def request(self, method, path, body=None):
        url = f"{self.base}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raise ApiError(f"{method} {path} -> HTTP {exc.code}") from exc
        # Transport failures are NOT all wrapped in URLError: a dropped
        # connection raises http.client.RemoteDisconnected (an OSError) out
        # of getresponse(), and a truncated body raises
        # http.client.IncompleteRead (an HTTPException) out of resp.read().
        # Catch the full transport family so main() can keep the exit-0
        # advisory contract. OSError also covers URLError and TimeoutError.
        except (http.client.HTTPException, OSError, json.JSONDecodeError) as exc:
            raise ApiError(f"{method} {path} -> {exc}") from exc

    def paged(self, path):
        items = []
        page = 1
        while True:
            sep = "&" if "?" in path else "?"
            batch = self.request("GET", f"{path}{sep}per_page=100&page={page}")
            if isinstance(batch, dict):  # search API shape
                if "items" not in batch:
                    # A dict without `items` is not a list response at all
                    # (an empty 200 body collapses to `{}` in `request`).
                    # Treat it as an unmeasurable diff, never an empty page:
                    # otherwise the gate would pass on nothing.
                    raise ApiError(f"{path} -> malformed list response")
                batch = batch["items"]
            items.extend(batch)
            if len(batch) < 100:
                return items
            page += 1

    def pr_files(self, number):
        return self.paged(f"/repos/{self.repo}/pulls/{number}/files")

    def pr_labels(self, number):
        pr = self.request("GET", f"/repos/{self.repo}/pulls/{number}")
        return [label.get("name", "") for label in pr.get("labels", [])]

    def pr_author(self, number):
        pr = self.request("GET", f"/repos/{self.repo}/pulls/{number}")
        return (pr.get("user") or {}).get("login", "")

    def pr_head_sha(self, number):
        pr = self.request("GET", f"/repos/{self.repo}/pulls/{number}")
        return (pr.get("head") or {}).get("sha", "")

    def pr_reviews(self, number):
        return self.paged(f"/repos/{self.repo}/pulls/{number}/reviews")

    def permission_level(self, username):
        """The user's permission on this repository ('admin', 'write', ...).

        Association alone is not authorization - an organization MEMBER can
        hold only read access to this repository. Only a reviewer who can
        write (or administer) the repository may grant the size exception.
        """
        data = self.request(
            "GET", f"/repos/{self.repo}/collaborators/{username}/permission"
        )
        return (data.get("permission") or "none") if isinstance(data, dict) else "none"

    def ensure_named_label(self, name, colour, description):
        try:
            self.request(
                "POST",
                f"/repos/{self.repo}/labels",
                {"name": name, "color": colour, "description": description},
            )
        except ApiError as exc:
            if "HTTP 422" not in str(exc):  # 422: label already exists
                raise

    def ensure_label(self, name):
        suffix, _cap, colour = next(
            row for row in SIZES if f"size/{row[0]}" == name
        )
        description = f"Pull request size: {suffix} (advisory, counted lines)"
        self.ensure_named_label(name, colour, description)

    def add_label(self, number, name):
        self.request(
            "POST", f"/repos/{self.repo}/issues/{number}/labels", {"labels": [name]}
        )

    def remove_label(self, number, name):
        quoted = urllib.parse.quote(name, safe="")
        self.request("DELETE", f"/repos/{self.repo}/issues/{number}/labels/{quoted}")

    def find_marker_comment(self, number, marker=MARKER):
        for comment in self.paged(f"/repos/{self.repo}/issues/{number}/comments"):
            if marker in (comment.get("body") or ""):
                return comment
        return None

    def upsert_comment(self, number, body, marker=MARKER):
        existing = self.find_marker_comment(number, marker)
        if existing is not None:
            if (existing.get("body") or "") != body:
                self.request(
                    "PATCH",
                    f"/repos/{self.repo}/issues/comments/{existing['id']}",
                    {"body": body},
                )
            return "updated"
        self.request(
            "POST", f"/repos/{self.repo}/issues/{number}/comments", {"body": body}
        )
        return "created"

    def merged_since(self, since):
        query = urllib.parse.quote(f"repo:{self.repo} is:pr is:merged merged:>={since}")
        return self.paged(f"/search/issues?q={query}")


def label_pr(api, number, threshold, extra_globs, log=print):
    files = api.pr_files(number)
    counted, excluded, counted_files = count_files(files, extra_globs)
    label = size_label(counted)
    log(
        f"PR #{number}: {counted} counted lines ({counted_files} files), "
        f"{excluded} excluded lines -> {label}"
    )
    api.ensure_label(label)
    current = api.pr_labels(number)
    for stale in SIZE_LABELS:
        if stale != label and stale in current:
            api.remove_label(number, stale)
    if label not in current:
        api.add_label(number, label)
    if counted > threshold:
        api.upsert_comment(
            number, comment_body(counted, excluded, counted_files, threshold)
        )
    elif api.find_marker_comment(number) is not None:
        api.upsert_comment(number, under_body(counted, threshold))
    return label, counted


def weekly_since(now):
    """Start of the weekly window: a true 7 days before `now` (UTC).

    A date-only value would be searched as `merged:>=YYYY-MM-DD` (midnight),
    so a Monday 07:17 run would cover 7 days + 7 hours and the next run
    would recount that morning's merges. Keep the full timestamp.
    """
    return (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")


def weekly(api, log=print):
    since = weekly_since(datetime.now(UTC))
    report = weekly_report(tally(api.merged_since(since)), since)
    log(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(report)
    return report


def approved_by_non_author(reviews, author, head_sha, permission_of):
    """Whether some non-author approves the pull request at `head_sha`.

    Reviews arrive chronologically. COMMENTED and PENDING reviews do not
    change a reviewer's standing (GitHub still counts an approver who
    later comments as approving); APPROVED, CHANGES_REQUESTED and
    DISMISSED do, so only the latest such state per user counts. The
    standing approval must also be on the current head - an approval of
    an older head never unlocks a newly pushed diff - and from a
    reviewer with approve weight (OWNER, MEMBER or COLLABORATOR) who can
    write or administer this repository: association is not
    authorization, since a read-only organization member reports MEMBER
    but cannot grant the exception. `permission_of` maps a login to its
    repository permission; a lookup failure is not authorization. An
    approval by the pull request's own author never qualifies.
    """
    latest = {}
    for review in reviews:
        login = (review.get("user") or {}).get("login", "")
        state = review.get("state", "")
        if login and state in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            latest[login] = review

    def authorized(login):
        try:
            return permission_of(login) in ("admin", "write")
        except ApiError:
            return False

    return any(
        review.get("state") == "APPROVED"
        and review.get("commit_id") == head_sha
        and review.get("author_association") in ("OWNER", "MEMBER", "COLLABORATOR")
        and authorized(login)
        for login, review in latest.items()
        if login != author
    )


def _comment_best_effort(api, number, body, log):
    """Post a passing-path comment; a write failure must not fail the gate.

    The gate has already measured the diff, so the run passes either way:
    a fork PR's read-only token cannot write the warning comment, and that
    must not turn a within-limits fork PR red. Measurement failures stay
    fatal; only the advisory comment is best-effort.
    """
    try:
        api.upsert_comment(number, body, GATE_MARKER)
    except ApiError as exc:
        log(f"::warning::pr_size gate: could not post comment ({exc}); gate still passes")


def gate_pr(api, number, threshold, extra_globs,
            file_threshold=DEFAULT_GATE_FILE_THRESHOLD,
            hard_stop=DEFAULT_HARD_STOP_THRESHOLD,
            warn=DEFAULT_WARN_THRESHOLD, log=print):
    """Required size gate for one pull request. True when it may merge.

    Passes when the counted size is within the line and file thresholds
    (with a warning comment at or above the warn line count), or over them
    with a valid `size-exception` below the hard stop. At or above the
    hard stop no exception applies. Otherwise posts the split-procedure
    comment and returns False; the caller exits 1. ApiError propagates:
    the gate fails closed.
    """
    files = api.pr_files(number)
    counted, excluded, counted_files = count_files(files, extra_globs)
    log(
        f"PR #{number}: {counted} counted lines ({counted_files} files), "
        f"raw diff {counted + excluded} lines ({excluded} excluded), gate "
        f"{threshold} lines / {file_threshold} files, hard stop {hard_stop}, "
        f"warn from {warn}"
    )
    if counted <= threshold and counted_files <= file_threshold:
        if counted >= warn:
            log(
                f"::warning::pr_size gate: {counted} counted lines is over "
                f"the {warn}-line review target (gate {threshold} lines / "
                f"{file_threshold} files); passing with a warning."
            )
            _comment_best_effort(
                api,
                number,
                gate_warn_body(
                    counted, excluded, counted_files, threshold, warn, file_threshold
                ),
                log,
            )
        elif api.find_marker_comment(number, GATE_MARKER) is not None:
            _comment_best_effort(
                api,
                number,
                gate_under_body(counted, excluded, threshold, file_threshold),
                log,
            )
        return True
    fail_body = gate_failure_body(
        counted, excluded, counted_files, threshold, file_threshold, hard_stop
    )
    if counted >= hard_stop:
        log(
            f"PR #{number}: {counted} counted lines at or above the "
            f"{hard_stop}-line hard stop; {EXCEPTION_LABEL} does not apply"
        )
        api.upsert_comment(number, fail_body, GATE_MARKER)
        return False
    api.ensure_named_label(
        EXCEPTION_LABEL,
        EXCEPTION_LABEL_COLOR,
        "Exception to the required PR size gate: needs an APPROVED review "
        "from someone other than the author",
    )
    labels = api.pr_labels(number)
    exception = EXCEPTION_LABEL in labels and approved_by_non_author(
        api.pr_reviews(number),
        api.pr_author(number),
        api.pr_head_sha(number),
        api.permission_level,
    )
    if exception:
        log(f"PR #{number}: {EXCEPTION_LABEL} approved by a non-author; gate passes")
        api.upsert_comment(number, gate_exception_body(counted, threshold), GATE_MARKER)
        return True
    api.upsert_comment(number, fail_body, GATE_MARKER)
    return False


def _extra_globs():
    return [
        glob.strip()
        for glob in os.environ.get("PR_SIZE_EXCLUDE_EXTRA", "").split(",")
        if glob.strip()
    ]


def main(argv):
    mode = argv[1] if len(argv) > 1 else "label"
    repo = os.environ.get("REPO", "")
    api = GitHubApi(
        os.environ.get("GH_TOKEN", ""),
        repo,
        os.environ.get("GITHUB_API_URL", "https://api.github.com"),
    )
    if mode == "gate":
        # The gate is a required check and fails CLOSED: an unmeasurable
        # size or a failed API call is an error, never a silent pass.
        try:
            threshold = int(os.environ.get("PR_SIZE_GATE_THRESHOLD", DEFAULT_GATE_THRESHOLD))
            file_threshold = int(os.environ.get("PR_SIZE_GATE_FILES", DEFAULT_GATE_FILE_THRESHOLD))
            hard_stop = int(os.environ.get("PR_SIZE_HARD_STOP", DEFAULT_HARD_STOP_THRESHOLD))
            warn = int(os.environ.get("PR_SIZE_WARN_THRESHOLD", DEFAULT_WARN_THRESHOLD))
            ok = gate_pr(
                api, int(os.environ["PR_NUMBER"]), threshold, _extra_globs(),
                file_threshold, hard_stop, warn,
            )
        except (ApiError, KeyError, ValueError) as exc:
            print(f"::error::pr_size gate could not verify size: {exc}")
            return 1
        if not ok:
            print(f"::error::pr_size gate: diff over {threshold} counted lines / "
                  f"{file_threshold} files without a {EXCEPTION_LABEL} exception, "
                  "or at/above the hard stop; see above.")
            return 1
        return 0
    try:
        if mode == "weekly":
            weekly(api)
        elif mode == "label":
            threshold = int(os.environ.get("PR_SIZE_THRESHOLD", DEFAULT_THRESHOLD))
            label_pr(api, int(os.environ["PR_NUMBER"]), threshold, _extra_globs())
        else:
            print(f"::warning::pr_size: unknown mode {mode!r}; nothing done.")
    except (ApiError, KeyError, ValueError) as exc:
        # Advisory only: a labelling failure must never turn a run red.
        print(f"::warning::pr_size advisory skipped: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
