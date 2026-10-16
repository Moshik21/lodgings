# Changelog

All notable changes to `lodgings-passport` are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the tool uses [semantic versioning](https://semver.org/). Schema versions (`lodgings.passport/1`, `lodgings.lane/1`, `lodgings.pipeline/1`, `lodgings.custody/1`, `lodgings.decisions/1`, `lodgings.passport-import/1`, `lodgings.passport-confirm/1`) are versioned separately from the tool.

## [Unreleased]

### Added
- `export --adopt-gate-head [--confirm-differences TOKEN]`: resume the lane at the review gate's head when the gate holds
  a squash or a rebase of the lane. A field test refused 6 of 6 exports as `custody diverged` for exactly that reason
  (the gate's PR head was squashed or rebased on a newer base, while the lane kept its older history). The mode is named
  explicitly and never implied. It compares the lane head's tree with the gate head's: identical trees are adopted;
  otherwise it refuses, lists the commits only the lane holds, the commits only the gate holds and every path that
  differs, and prints a token that is bound to those exact heads and that exact difference. The same command with the
  token adopts. The lane's own commits are still bundled, imported under the import refs and archived by `confirm`; the
  target's branch starts at the gate head, with no bridge merge. The passport records both heads, the counts, the digest
  and the confirmation (`adopt` in `manifest.json`, the lists in `custody.json`); import recomputes the difference
  from the objects and refuses a passport that does not match it.
- The squash and rebase case in `examples/demo` (`squashed-gate` and `rebased-gate`, next to the default
  `rebased-lane`), in `tests/e2e.py` (scenarios E, F and G) and in `docs/custody.md`, `docs/cli.md` and
  `docs/troubleshooting.md`.

### Changed
- `import` says what comes next: the printed output ends with a `next:` line (`next` in `--json`), and `after/resume.md`
  lists it as step 1, naming the step a supervisor needs before a merge: give the lane a task record on the home that
  supervises it (restart it through that home's normal task-creation and brief path, with `resume.md` as the brief),
  record the PR on it, then merge. A supervisor that guards merges on a task record refuses a merge before that. The
  text is generic; the passport knows no supervisor. `tests/e2e.py` checks it in the output and in `resume.md`.
- A refusal for `behind` or `diverged` custody now says which side is ahead and gives both commit counts and the merge
  base, says when every change of the lane is already in the gate head (a squash or rebase), and numbers the ways out
  (`--adopt-gate-head`, return the gate's commits to the lane, decide by hand). The `how:` line can run to several
  lines. The relation record (`custody.json`) gains `lane_only_commits`, `gate_only_commits`, `ahead` and
  `gate_holds_lane_changes`. The refusal semantics are unchanged: it still refuses by default and changes nothing.

### Fixed
- `export` works for a lane branch that has no worktree. A `--worktree` path that no longer exists was a usage error
  (exit 2); it is now recorded as `worktree.checked: false` with the reason, and the branch is read from the repository.
  When the named path is gone but the branch is checked out somewhere else, that checkout gets the clean-tree check, so
  a mistyped path cannot skip it. The custody check runs in the repository when there is no worktree.
- Repository- and record-derived text (a commit subject, a file name) is printed, and written to `after/resume.md`,
  with control characters, terminal escape sequences and zero-width or bidirectional characters shown as `\xNN`
  (`\uNNNN` above U+00FF) instead of raw, so a hostile name can neither drive nor spoof the terminal; what is decided
  and recorded is unchanged.
- git's output was decoded with the Windows code page (cp1252) instead of UTF-8, so a commit subject or file name
  holding U+009B or U+202E reached the print boundary as ordinary-looking characters and was written back out as the
  original bytes. It is now decoded as UTF-8, a byte that is not UTF-8 is shown as U+FFFD, and the print boundary
  writes only what the output's own encoding can hold (the rest as `\uNNNN`), including a byte in 0x80-0x9f.

### Changed
- Renamed from `harbor-passport` to `lodgings-passport` (module `harbor_passport` -> `lodgings_passport`), with no
  forwarding shim. `HARBOR_PASSPORT*` environment variables become `LODGINGS_PASSPORT*`, schema ids `harbor.*` become
  `lodgings.*`, the `refs/harbor/` ref namespace becomes `refs/lodgings/`, and the default state directory moves from
  `.../harbor/passport` to `.../lodgings/passport`. A passport exported under the old schema ids is no longer accepted.
  See [MIGRATION.md](../../MIGRATION.md). Entries below keep the old names as history.

## [0.1.0] - 2026-10-09

### Added

- `harbor-passport export | verify | import | confirm` for one lane, as a manual command.
- Passport format `harbor.passport/1`: one git bundle of lane, gate, pipeline, PR and archive refs (prerequisite = the base), custody, pipeline and decisions receipts, text records, `SHA256SUMS`, and the passport id = sha256 of `SHA256SUMS`.
- Custody relations `no-gate`, `equal`, `ahead`, `contained`, `behind`, `diverged`, decided from git objects with `git merge-tree --write-tree`. A bridge merge on the target for `contained`, so the review gate fast-forwards without a hand-made descendant commit.
- Refusals that change nothing: dirty worktree, busy pipeline step, gate or pipeline holding work the lane lacks, secrets in records, tampered passport, existing target branch or worktree, target missing the base, origin moved since export.
- Kill switch (`<state>/off`, `HARBOR_PASSPORT=off`) and an actions log (`<state>/actions.jsonl`).
- `scripts/self-audit` with an external denylist; `tests/smoke.py` (three-OS smoke) and `tests/e2e.py` (behavioural fixture run through the CLI).
- Licence: MIT (the repository `LICENSE`).

### Verified

- `tests/e2e.py`: 48/48 checks (Linux, WSL2, Python 3.14.4, git 2.53.0).
- `tests/smoke.py`: 12/12 on Python 3.11.15, 3.12.13 and 3.14.4 (Linux, WSL2).
- Self-audit: clean.
- Not run yet: macOS, Windows native, Python 3.8 to 3.10 (grammar checked only).
