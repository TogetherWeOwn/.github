def exempt_prose(name, exempt):
    if name.startswith(("README", "LICENSE", "NOTICE")):
        exempt()
