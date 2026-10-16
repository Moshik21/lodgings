## What and why

## Checklist
- [ ] `python3 -m unittest discover -s tests` passes
- [ ] `scripts/self-audit` is clean (with your `--denylist` files, if any), with no exemptions
- [ ] `scripts/merged-check` reports ok against current `origin/main` (run it right before hand-off, and again if main moved before the merge)
- [ ] standard library only; no personal names, hosts, addresses or home paths
- [ ] unknown or stale is still shown as such, never as zero; nothing is shown without a real field behind it
- [ ] a new probe command is added to `docs/safety.md` in the same change
- [ ] docs and CHANGELOG updated
