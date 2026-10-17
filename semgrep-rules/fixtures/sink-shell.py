# Fixture: every sink below must fire tier0-no-shell-exec-py.
import os
import subprocess


def risky(cmd, name):
    os.system(cmd)
    subprocess.call("ls " + name, shell=True)
    return subprocess.Popen(cmd, shell=True)
