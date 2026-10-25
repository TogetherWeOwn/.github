import json


def read_snapshot(stream, warn):
    try:
        data = json.load(stream)
        return data
    except json.JSONDecodeError:
        warn()
