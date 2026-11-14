"""Inert lifecycle outcome contract; no observation, enforcement or admission.

Input is an exact dict: conclusion; overall_deadline_seconds; phases (an exact
mapping of readiness, build, capture and lhci to outcome/deadline_seconds);
child_cleanup; child_grace_seconds; child_force_seconds; browser_cleanup;
whole_workload_disposal. Outcomes are strings and deadlines are integer seconds.
The caller must obtain these facts independently; this function does not prove
that a process exited, a deadline was enforced, or GitHub disposed of a VM.
"""


class LifecycleError(ValueError):
    """The proposed lifecycle outcome cannot represent a complete success."""


def check_lifecycle(outcome):
    """Refuse incomplete, failed, unbounded or overstated lifecycle reports."""
    keys = {
        "conclusion",
        "overall_deadline_seconds",
        "phases",
        "child_cleanup",
        "child_grace_seconds",
        "child_force_seconds",
        "browser_cleanup",
        "whole_workload_disposal",
    }
    if type(outcome) is not dict or outcome.keys() != keys:
        raise LifecycleError("invalid lifecycle outcome")
    if type(outcome["conclusion"]) is not str or outcome["conclusion"] != "success":
        raise LifecycleError("attempt did not complete successfully")
    deadline = outcome["overall_deadline_seconds"]
    if type(deadline) is not int or not 0 < deadline < 900:
        raise LifecycleError("invalid overall deadline")
    limits = {"readiness": 30, "build": 240, "capture": 240, "lhci": 240}
    phases = outcome["phases"]
    if type(phases) is not dict or phases.keys() != limits.keys():
        raise LifecycleError("invalid phase inventory")
    for name, limit in limits.items():
        phase = phases[name]
        if type(phase) is not dict or phase.keys() != {"outcome", "deadline_seconds"}:
            raise LifecycleError("invalid phase: " + name)
        bound = phase["deadline_seconds"]
        if (
            type(phase["outcome"]) is not str
            or phase["outcome"] != "success"
            or type(bound) is not int
            or not 0 < bound <= limit
            or bound > deadline
        ):
            raise LifecycleError("phase refused: " + name)
    for key in ("child_grace_seconds", "child_force_seconds"):
        bound = outcome[key]
        if type(bound) is not int or not 0 < bound <= 5 or bound > deadline:
            raise LifecycleError("invalid cleanup deadline: " + key)
    for key, expected in {
        "child_cleanup": "direct-child-reaped",
        "browser_cleanup": "closed",
        "whole_workload_disposal": "delegated-to-github",
    }.items():
        if type(outcome[key]) is not str or outcome[key] != expected:
            raise LifecycleError("cleanup refused: " + key)
