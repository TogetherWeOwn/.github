try:
    data = json.load(stream)
except json.JSONDecodeError:
    warn()
