# Security Policy

## Reporting a vulnerability

Please report vulnerabilities **privately** through GitHub's private vulnerability
reporting (the repo's Security tab, "Report a vulnerability"). Do not open a public
issue for a vulnerability, and do not paste secrets, tokens, credentials or customer
data into any issue, comment or PR — not even redacted-looking fragments.
If you pasted one by accident, say so in the private report so the credential owner
can decide what to do.

## Scope notes

Treat code that touches auth, sessions, secrets, permissions, payments or public
exposure as security-sensitive: it needs a careful review pass, honest test disclosure
in the PR, and no credential material in the diff, the title, the body or the
branch name.
