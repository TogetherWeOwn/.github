"""Inert inventory agreement only; this does not establish evidence or admission."""


class InventoryError(ValueError):
    """The collector outcome does not match the trusted inventory."""


def check_inventory(required, outcome):
    """Compare an explicit trusted inventory with one complete collector outcome.

    Required entries have state and viewport; results additionally have status.
    Callers must obtain required independently of candidate-controlled data.
    """

    def identity(entry, keys):
        if type(entry) is not dict or entry.keys() != keys:
            raise InventoryError("malformed inventory entry")
        values = (entry["state"], entry["viewport"])
        if any(
            type(value) is not str or not value or value != value.strip()
            for value in values
        ):
            raise InventoryError("invalid state or viewport")
        return values

    if type(required) is not list or not required:
        raise InventoryError("missing trusted inventory")
    expected = set()
    for entry in required:
        key = identity(entry, {"state", "viewport"})
        if key in expected:
            raise InventoryError("duplicate required entry")
        expected.add(key)

    if (
        type(outcome) is not dict
        or outcome.keys() != {"status", "results"}
        or outcome["status"] != "success"
        or type(outcome["results"]) is not list
    ):
        raise InventoryError("incomplete collector outcome")
    seen = set()
    for result in outcome["results"]:
        key = identity(result, {"state", "viewport", "status"})
        if key in seen or key not in expected or result["status"] != "success":
            raise InventoryError("unexpected, duplicate or failed result")
        seen.add(key)
    if seen != expected:
        raise InventoryError("missing required result")
