# Security

Lodgings is read-only toward the hosts it probes and writes only its own state directory.
See [docs/safety.md](docs/safety.md) for the exact list of commands a probe may run.

## Reporting a vulnerability

Report it privately through GitHub's private vulnerability reporting: open the repository's **Security** tab and choose
**Report a vulnerability**. Please do not open a public issue, pull request or discussion for a vulnerability.

Include what you saw, the version or commit, your operating system and Python version, and the smallest steps that
reproduce it. Use invented host names and no real secrets.

## In scope

Anything that makes a probe run a command it should not, read or print a secret, write outside the state directory, or
send data to a host the user did not configure. The same holds for the passport core (`core/passport`): a write to an
origin before `confirm`, a forced or rewritten ref, a passport that verifies after tampering, a path that escapes its
directory, or input that reaches a shell.

Out of scope: weaknesses in the user's ssh setup, in the hosts probed, or in the channel a passport is copied over.

## Supported versions

Only the latest release is supported, and while the project is pre-1.0 a fix may change behaviour. Nothing older gets
a patch.

## What to expect

This is maintained in spare time by one person. No response time is guaranteed, and a report is handled on a best-effort
basis.
