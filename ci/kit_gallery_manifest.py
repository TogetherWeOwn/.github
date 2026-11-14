"""Validate expected source policy only, not checkout, protection or provenance."""

import re


class ManifestError(ValueError):
    """The proposed manifest is not an explicit immutable source policy."""


def check_manifest(manifest):
    """Refuse malformed identities and moving pins without selecting any pins.

    The caller must independently establish the complete transitive inventory and
    actual enforcement; a syntactically valid manifest is not evidence of either.
    """
    def matches(value, pattern):
        return type(value) is str and re.fullmatch(pattern, value, re.ASCII) is not None

    def repository(value):
        return matches(value, r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*/[A-Za-z0-9_][A-Za-z0-9_.-]*") and len(value.split("/")[0]) <= 39

    def path(value):
        return (type(value) is str and bool(value) and
                all(part not in (".", "..") and matches(part, r"[A-Za-z0-9_.-]+")
                    for part in value.split("/")))

    sha = r"[0-9a-f]{40}"
    if (type(manifest) is not dict or
            manifest.keys() != {"repository", "path", "sha", "pins"} or
            not repository(manifest["repository"]) or not path(manifest["path"]) or
            not matches(manifest["sha"], sha) or manifest["sha"] == "0" * 40):
        raise ManifestError("invalid source identity")
    pins = manifest["pins"]
    if type(pins) is not list or not pins:
        raise ManifestError("missing transitive pins")
    seen = set()
    for pin in pins:
        if (type(pin) is not dict or pin.keys() != {"name", "kind", "digest"} or
                not path(pin["name"]) or pin["kind"] not in ("git", "sha256")):
            raise ManifestError("invalid transitive pin")
        pattern = sha if pin["kind"] == "git" else r"sha256:[0-9a-f]{64}"
        if (not matches(pin["digest"], pattern) or
                set(pin["digest"].removeprefix("sha256:")) == {"0"} or
                pin["name"].lower() in seen):
            raise ManifestError("invalid or duplicate transitive pin")
        seen.add(pin["name"].lower())
