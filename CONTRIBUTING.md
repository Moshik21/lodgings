# Contributing

Lodgings is pre-1.0 hobby tooling, written by one person to solve problems they had and open-sourced in case it helps
someone else. It is MIT licensed (see [LICENSE](LICENSE)); contributions are accepted under the same licence.

## What this project is, and is not

Lodgings probes the hosts in a pool, records what each can honestly run (tags with their evidence, a toolchain
fingerprint, drift), and turns beats and canary results into host states and drain advice. It is **not** a scheduler, a
job runner, a deployment tool or a monitor, and it never starts, moves or stops anything. Changes that fit the scope are
welcome: a new probe fact, a clearer message, a fix, a recorded fixture, a doc correction, support for another OS. Changes
that turn it into something that acts on hosts will most likely be declined; ask first.

## Proposing a change

- Open an issue first for anything non-trivial (a new command, a new probe, a schema change, a behaviour change) and wait
  for a reply before writing the code. A typo, a broken link or a small obvious fix can go straight to a pull request.
- Keep pull requests small and about one thing. A small change is reviewed sooner and is more likely to land.
- Issues use the templates in `.github/ISSUE_TEMPLATE` (bug report, feature request, the pool advised something wrong);
  pull requests use `.github/PULL_REQUEST_TEMPLATE.md`.
- Response time is best effort, with no promise: this is maintained in spare time, and a request may wait a while or be
  declined.

## House rules

- Python 3.8 or newer, standard library only, no build step, no telemetry, no model call at runtime.
- Core code, schema, docs, fixtures and default config stay generic: no personal machine, host, account or project
  names, no e-mail addresses, no private network names, no home paths. Tool-specific code lives outside this repository
  and integrates through the JSON commands (`lodgings chips`, `lodgings states --json`, the passport format).
- No shell strings: a command is an argument list. A probe may only print versions and read facts; adding a command to
  the probe means adding it to the list in `docs/safety.md` in the same change.
- **The honesty rule.** The project shows what it measured, what it derived and what it does not know, and never mixes
  them up: an unknown or stale fact is shown as such, never as zero, "no" or green; nothing is shown without a real field
  behind it; and a report says what was actually run, with "not run" as an acceptable answer and a silent gap as not.
  See "Honesty" in the [README](README.md).

## Definition of done

Run these before you open a pull request, and say in the pull request what you ran and with what result:

1. The self-audit: `python3 -I scripts/self-audit`. Add `--denylist FILE` for your own list of names that must not
   appear (it lives outside the repository; repeat `--denylist` to apply several files). It must be clean.
2. The full suite: `python3 -m unittest discover -s tests`. Tests are behavioural and go through the public `lodgings`
   command: recorded facts replayed from `examples/fixtures`, fake `ssh` shims, the self-audit, and a smoke run. Do not
   add unit tests of internals. The passport core has its own: `python3 core/passport/tests/smoke.py`.
3. `scripts/merged-check` against current `origin/main`, right before you hand off, and again if main moved before the
   merge. It merges your head onto the base in a throwaway clone outside the repository and runs the self-audit and the
   full test suite on the merged result, so two changes that each passed alone cannot break main together. It prints
   `merged-check: ok` or `merged-check: FAILED <step>: <first finding>` and exits non-zero on any failure.
   Options: `--head REF_OR_SHA`, `--base REF` (default `origin/main`), `--pr N` (head resolved with `gh pr view`),
   `--denylist FILE`, repeatable and passed on to the self-audit (or `LODGINGS_AUDIT_DENYLIST`, several files separated
   by `:`).
4. Docs and `CHANGELOG.md` updated for anything a user can see.

## Where things are

- Questions and help: [SUPPORT.md](SUPPORT.md). Vulnerabilities: [SECURITY.md](SECURITY.md). Behaviour:
  [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
