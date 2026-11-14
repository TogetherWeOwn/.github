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


def check_network_phases(phases):
    """Check proposed controls only; this proves neither ownership nor enforcement."""
    expected = {
        "preparation": {
            "network": "registry-only",
            "database_active": False,
            "credentials": [],
            "privileged_shared_cache": False,
            "timeout_seconds": int,
        },
        "execution": {
            "network": "attempt-owned-internal",
            "general_egress": False,
            "published_database_ports": [],
        },
    }
    if type(phases) is not dict or phases.keys() != expected.keys():
        raise IsolationError("invalid network phases")
    for phase, controls in expected.items():
        proposed = phases[phase]
        if type(proposed) is not dict or proposed.keys() != controls.keys():
            raise IsolationError("invalid network phase: " + phase)
        for key, value in controls.items():
            actual = proposed[key]
            if key == "timeout_seconds":
                valid = type(actual) is int and 0 < actual <= 900
            else:
                valid = type(actual) is type(value) and actual == value
            if not valid:
                raise IsolationError("network control refused: " + phase + "." + key)
