"""Inert identity comparison, not receipt validation, provenance or admission.

The caller must obtain expected values independently from trusted sources. This
module neither authenticates those values nor establishes any API mapping.
"""

import re


class ReceiptIdentityError(ValueError):
    """The identity projection is malformed or does not match expectations."""


def check_identity(actual, expected):
    """Compare exact identity projections; return None or raise ReceiptIdentityError.

    Both dictionaries contain only subject_type, subject_sha, checkout_sha,
    supervisor_sha, run_id, attempt and job_id. A checkout may differ from the
    subject only when the independently supplied expected projection says so.
    """
    keys = {
        "subject_type", "subject_sha", "checkout_sha", "supervisor_sha",
        "run_id", "attempt", "job_id",
    }
    for identity in (actual, expected):
        if type(identity) is not dict or identity.keys() != keys:
            raise ReceiptIdentityError("invalid identity projection")
        if type(identity["subject_type"]) is not str or identity["subject_type"] not in (
            "pull_request", "merge_group"
        ):
            raise ReceiptIdentityError("invalid subject type")
        for key in ("subject_sha", "checkout_sha", "supervisor_sha"):
            value = identity[key]
            if type(value) is not str or re.fullmatch(r"[0-9a-f]{40}", value) is None:
                raise ReceiptIdentityError("invalid SHA: " + key)
        for key in ("run_id", "attempt", "job_id"):
            value = identity[key]
            if type(value) is not int or value <= 0:
                raise ReceiptIdentityError("invalid numeric identity: " + key)
    if actual != expected:
        raise ReceiptIdentityError("identity mismatch")
