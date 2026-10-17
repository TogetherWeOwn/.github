# Fixture: both sinks below must fire tier0-no-eval-py.


def risky(user_input, code):
    direct = eval(user_input)
    exec(code)
    return direct
