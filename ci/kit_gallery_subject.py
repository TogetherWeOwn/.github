"""Validate PR event identity against a separately fetched GitHub API response.

This is groundwork only: it does not fetch metadata, launch a workload, or
qualify an attempt. The caller must authenticate the API response and bind it
to the event repository and pull number before invoking this function.
"""

import re


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
