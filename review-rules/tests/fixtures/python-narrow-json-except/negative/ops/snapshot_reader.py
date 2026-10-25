try:
    data = json.load(stream)
except (OSError, ValueError, RecursionError):
    warn()
