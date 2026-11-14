"""Static expected fixture policy only; no runtime enforcement or ownership proof."""

import re


class DatabasePolicyError(ValueError):
    """The trusted expected policy is incomplete or outside the fixture contract."""


def check_database_policy(policy):
    """Validate a trusted expected policy, never container inspection evidence.

    A syntactically pinned digest still requires independent review and selection.
    Acceptance grants no admission and establishes no runtime or race guarantees.
    """
    controls = {
        "image": None,
        "network": "attempt-owned-internal",
        "storage": "disposable",
        "host_network": False,
        "published_ports": [],
    }
    if type(policy) is not dict or policy.keys() != controls.keys():
        raise DatabasePolicyError("invalid database policy")
    image = policy["image"]
    if type(image) is not str or not re.fullmatch(
        r"postgres:17@sha256:[0-9a-f]{64}", image
    ):
        raise DatabasePolicyError("invalid pinned Postgres 17 image")
    for key in controls.keys() - {"image"}:
        value, expected = policy[key], controls[key]
        if type(value) is not type(expected) or value != expected:
            raise DatabasePolicyError("database control refused: " + key)
