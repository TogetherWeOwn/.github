"""Validate PR event identity against a separately fetched GitHub API response.

This is groundwork only: it does not launch a workload or qualify an attempt.
The fetch helper is not yet wired into the fail-closed workflow.
"""

import json
import re
import urllib.error
import urllib.request

_MAX_RESPONSE_BYTES = 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class SubjectError(ValueError):
    """The event and the current API subject cannot be reconciled."""


def _id(value):
    if type(value) is not int or value <= 0:
        raise SubjectError("invalid numeric identity")
    return value


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise SubjectError("invalid full commit SHA")
    return value


def _identity(pr):
    try:
        return {
            "number": _id(pr["number"]),
            "base_repository_id": _id(pr["base"]["repo"]["id"]),
            "base_sha": _sha(pr["base"]["sha"]),
            "head_repository_id": _id(pr["head"]["repo"]["id"]),
            "head_sha": _sha(pr["head"]["sha"]),
        }
    except (KeyError, TypeError) as exc:
        raise SubjectError("incomplete pull request identity") from exc


def fetch_pull_request_subject(event, token):
    """Fetch current PR metadata using numeric event identities, then reconcile it."""
    try:
        repository_id = _id(event["repository"]["id"])
        number = _id(event["number"])
    except (KeyError, TypeError) as exc:
        raise SubjectError("incomplete event identity") from exc
    if not isinstance(token, str) or not token or any(c in token for c in "\r\n"):
        raise SubjectError("missing or invalid API token")
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
        if len(data) > _MAX_RESPONSE_BYTES:
            raise SubjectError("pull request API response too large")
        api_pull = json.loads(data)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise SubjectError("pull request API unavailable or invalid") from exc
    return resolve_pull_request(event, api_pull)


def merge_group_event_identity(event):
    """Parse event identity only; group membership and freshness are not established."""
    try:
        if event["action"] != "checks_requested":
            raise SubjectError("unsupported merge group action")
        group = event["merge_group"]
        head_ref = group["head_ref"]
        base_ref = group["base_ref"]
        if any(
            not isinstance(ref, str)
            or not ref.startswith("refs/heads/")
            or any(ord(char) < 32 or ord(char) == 127 for char in ref)
            for ref in (head_ref, base_ref)
        ):
            raise SubjectError("invalid merge group ref")
        return {
            "repository_id": _id(event["repository"]["id"]),
            "head_sha": _sha(group["head_sha"]),
            "base_sha": _sha(group["base_sha"]),
            "head_ref": head_ref,
            "base_ref": base_ref,
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
