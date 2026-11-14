"""Inert profile check; acceptance is not runtime isolation or admission.

This deliberately restrictive schema permits no host mounts or credentials.
It neither constructs a container nor verifies that a runtime enforces the profile.
"""


class IsolationError(ValueError):
    """The proposed workload profile does not match this static policy."""


def check_profile(profile):
    """Refuse anything except explicit, well-formed isolation controls."""
    controls = {
        "privileged": False,
        "host_pid": False,
        "host_ipc": False,
        "host_uts": False,
        "host_network": False,
        "trusted_source_writable": False,
        "trusted_output_writable": False,
        "host_mounts": [],
        "credentials": [],
    }
    if type(profile) is not dict or profile.keys() != controls.keys():
        raise IsolationError("invalid isolation profile")
    for key, expected in controls.items():
        value = profile[key]
        if type(value) is not type(expected) or value != expected:
            raise IsolationError("isolation control refused: " + key)
