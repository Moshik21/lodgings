# Contributing

Thanks for helping. The bar is "premier": small, safe, honest about what was actually run.

## House rules

1. **Python 3.8+ standard library only.** No dependencies and no build step. Use git through argv lists (`lodgings_passport/gitio.py`), never through a shell.
2. **Safety rules are not negotiable.** No force, no ref deletion or rewrite, no discarding work, no network, no secrets in passports. A new write must be create-only or non-forcing, checked in a scratch repository before it happens, and listed in [docs/safety.md](docs/safety.md).
3. **Generic core.** No tool-specific names in `core/`. Integrations live outside this repository and talk to the core only through the lane spec and the public command.
4. **No personal data.** No names, hosts, addresses, emails or home paths in the product tree. `scripts/self-audit --denylist <your private list>` must be clean before you open a change. Example paths use `/home/you`.
5. **Config, not code, for thresholds.** Every threshold is a named key in `lodgings_passport/config.py` and documented in [docs/config.md](docs/config.md).
6. **Schemas are versioned.** A change to a JSON shape bumps its schema version and adds a CHANGELOG entry.

## Tests

Tests go through the public CLI only, never into internals:

- `python3 tests/smoke.py`: start, one check, `--json`, every exit code, and the self-audit as a test. Run it on every OS you can, and record where in the README matrix.
- `python3 tests/e2e.py --work <empty dir>`: the behavioural fixture run (throwaway repositories, every case plus its unsafe twin).
- Every new refusal needs a case in `tests/e2e.py` that proves the unsafe twin changes nothing.

Do not add unit tests of internals.

## Changes

- One change per pull request, with a CHANGELOG entry under `[Unreleased]`.
- Say what you ran, where, and with what result. "Not run" is an acceptable answer; a silent gap is not.
- Issue and pull-request templates: [docs/templates/](docs/templates/).

## Code of conduct

Be kind and specific. Critique the code, not the person. Assume good faith, and ask before assuming. Maintainers may remove comments or contributions that harass or demean anyone. Report conduct problems as the root [CODE_OF_CONDUCT.md](../../CODE_OF_CONDUCT.md) describes.
