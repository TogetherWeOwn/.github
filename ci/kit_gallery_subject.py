"""Reconcile PR identity; the caller must independently authenticate API data.

The fetch helper is not wired to a caller and does not admit evidence.
"""

import http.client
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

_WORKER_PATH = str(Path(__file__).resolve())
_MAX_RESPONSE_BYTES = 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl


class SubjectError(ValueError):
    """The event and current API subject cannot be reconciled."""


def _id(value):
    if type(value) is not int or value <= 0:
        raise SubjectError("invalid numeric identity")
    return value


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise SubjectError("invalid full commit SHA")
    return value


def _ref(value):
    if (
        not isinstance(value, str)
        or not value
        or value == "HEAD"
        or value.startswith("-")
        or re.search(r"[\x00-\x20\x7f~^:?*\[\\]", value)
        or ".." in value
        or "@{" in value
        or value.endswith(".")
        or any(
            not part or part.startswith(".") or part.endswith(".lock")
            for part in value.split("/")
        )
    ):
        raise SubjectError("invalid branch ref")
    return value


def _identity(pr):
    try:
        return {
            "number": _id(pr["number"]),
            "base_repository_id": _id(pr["base"]["repo"]["id"]),
            "base_sha": _sha(pr["base"]["sha"]),
            "base_ref": _ref(pr["base"]["ref"]),
            "head_repository_id": _id(pr["head"]["repo"]["id"]),
            "head_sha": _sha(pr["head"]["sha"]),
            "head_ref": _ref(pr["head"]["ref"]),
        }
    except (KeyError, TypeError) as exc:
        raise SubjectError("incomplete pull request identity") from exc


def _fetch_metadata(repository_id, number, token):
    request = urllib.request.Request(
        f"https://api.github.com/repositories/{repository_id}/pulls/{number}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "kit-gallery-subject",
        },
    )
    try:
        with urllib.request.build_opener(_NoRedirect).open(
            request, timeout=10
        ) as response:
            if response.status != 200:
                raise SubjectError("pull request API refused")
            data = response.read(_MAX_RESPONSE_BYTES + 1)
            if response.length not in (None, 0):
                raise SubjectError("incomplete pull request API response")
        if len(data) > _MAX_RESPONSE_BYTES:
            raise SubjectError("pull request API response too large")
        return data
    except (
        urllib.error.URLError,
        TimeoutError,
        OSError,
        ValueError,
        http.client.HTTPException,
        RecursionError,
    ) as exc:
        raise SubjectError("pull request API unavailable or invalid") from exc


def fetch_pull_request_subject(event, token):
    """Fetch current PR metadata with a bounded worker, then reconcile it."""
    try:
        repository_id = _id(event["repository"]["id"])
        number = _id(event["number"])
    except (KeyError, TypeError) as exc:
        raise SubjectError("incomplete event identity") from exc
    if not isinstance(token, str) or not re.fullmatch(r"[!-~]{1,4096}", token):
        raise SubjectError("missing or invalid API token")
    try:
        result = subprocess.run(
            [sys.executable, "-I", _WORKER_PATH, "--fetch-worker"],
            input=json.dumps([repository_id, number, token]).encode(),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=True,
        )
        api_pull = json.loads(result.stdout)
    except (subprocess.SubprocessError, OSError, ValueError, RecursionError) as exc:
        raise SubjectError("pull request API unavailable or invalid") from exc
    return resolve_pull_request(event, api_pull)


def merge_group_event_identity(event):
    """Parse identity only, not membership, freshness, checkout or admission."""
    try:
        if event["action"] != "checks_requested":
            raise SubjectError("unsupported merge group action")
        group = event["merge_group"]
        refs = []
        for ref in (group["head_ref"], group["base_ref"]):
            if not isinstance(ref, str) or not ref.startswith("refs/heads/"):
                raise SubjectError("invalid merge group ref")
            _ref(ref.removeprefix("refs/heads/"))
            refs.append(ref)
        return {
            "repository_id": _id(event["repository"]["id"]),
            "head_sha": _sha(group["head_sha"]),
            "base_sha": _sha(group["base_sha"]),
            "head_ref": refs[0],
            "base_ref": refs[1],
        }
    except (KeyError, TypeError) as exc:
        raise SubjectError("incomplete merge group event") from exc


def resolve_pull_request(event, api_pull):
    """Return the exact identity only if event and current API data agree."""
    try:
        event_repository_id = _id(event["repository"]["id"])
        event_number = _id(event["number"])
        event_subject = _identity(event["pull_request"])
        api_subject = _identity(api_pull)
        if api_pull["state"] != "open":
            raise SubjectError("pull request is not open")
    except (KeyError, TypeError) as exc:
        raise SubjectError("incomplete event or API response") from exc
    if (
        event_number != event_subject["number"]
        or event_repository_id != event_subject["base_repository_id"]
        or event_subject != api_subject
    ):
        raise SubjectError("event and current pull request do not match")
    return api_subject


if __name__ == "__main__" and sys.argv[1:] == ["--fetch-worker"]:
    try:
        repository_id, number, token = json.loads(sys.stdin.buffer.read(8192))
        sys.stdout.buffer.write(_fetch_metadata(_id(repository_id), _id(number), token))
    except (SubjectError, ValueError, TypeError):
        sys.exit(1)
