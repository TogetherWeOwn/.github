def exempt_prose(name, parent, root, exempt):
    if name in {"README.md", "LICENSE", "NOTICE"} and parent == root:
        exempt()
