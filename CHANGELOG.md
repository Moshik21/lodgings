# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project follows [Semantic Versioning](https://semver.org/).
Schema versions (`lodgings.hosts/1`, `lodgings.facts 1.0`, `lodgings.capabilities 1.0`, `lodgings.chips 1.0`, `lodgings.beat/1`,
`lodgings.event/1`) are versioned apart from the tool.

## [Unreleased]

## [0.3.1] - 2026-10-16

Windows parity fixes, a guide to host tags, and what `lodgings-passport import` tells you to do next.

### Added
- `docs/host-tags.md`: what host tags are, how a probe produces them, the toolchain fingerprint and how drift is
  reported, and how to use tags in a hosts file to place work, with commands and output taken from the demo hosts.
- `docs/windows.md`, "Running Lodgings itself on Windows": what differs on a Windows collector and why (CRLF in redirected
  output, `python` against `py -3`, resolver wording), each verified on native Windows, WSL and macOS side by side.
- `lodgings-passport import` ends with a `next:` line (and `next` in `--json`), and `after/resume.md` lists it as step 1:
  the lane has no task record on the home that supervises it yet, so restart it through that home's normal
  task-creation and brief path, record the PR on it, then merge; a supervisor that guards merges on a task record refuses
  a merge before that. The passport README and `docs/cli.md` say the same in "After an import".

### Changed
- `scripts/build-package` is reproducible: every zip entry carries one time, `SOURCE_DATE_EPOCH` when set, else the commit
  time of `HEAD`, else 1980-01-01 outside a git checkout, with entries in case-sensitive sorted order and the same
  "made on Unix" header from any OS, so two clones of one commit give byte-identical zips and the digest in `SHA256SUMS`
  can be re-derived. (It stamped the newest file mtime before.)
- The `test` workflow's jobs have `timeout-minutes: 6` (3 times the slowest job of the last green hosted run, rounded up),
  where the default is 360.
- The ssh transport tests (`SshTransport` in `tests/test_cli.py`) now run on Windows through a batch-file stand-in for
  ssh; three of the four ran nowhere on Windows before. The fourth needs `python3` on the remote path and stays skipped there.
- The beat smoke test (`BeatSmoke`) no longer depends on whether the machine running it is on battery: a laptop on battery
  read `DRAINED` where the test expects `PAUSED`, on unchanged code too.

### Fixed
- `lodgings ledger` digests differed between Windows and the other platforms for the same records: state files written on
  Windows had CRLF line ends and the digest hashed the CR, against the documented "each line without its newline".
  State files (`current.json`, `beats.jsonl`, `events.jsonl`, ...) are now written with LF on every platform, and a CR
  before a newline is dropped when a digest is computed, so a state directory written by 0.3.0 on Windows gives the
  documented digest too (`drain.json`, digested as the file's bytes, is the one exception until it is next written).
- `lodgings probe` of an ssh host hung until its deadline, and then did not stop, when the collector ran on Windows:
  OpenSSH for Windows' `ssh.exe` stalls at start when its standard error is a pipe, which is how the probe read it.
  The probe now collects standard error through a temporary file in the state directory on every platform; an ssh alias
  that does not resolve fails in about a second with ssh's own message, as on Linux and macOS.

## [0.3.0] - 2026-10-15

The first public version. The numbers 0.1.0 and 0.2.0 below were internal labels and were never released.

### Added
- Real text in the community files, replacing their placeholders: `CONTRIBUTING.md`, `SECURITY.md` (GitHub private
  vulnerability reporting), `SUPPORT.md` and `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1); no placeholder is left.
- `scripts/merged-check [--head REF] [--base origin/main] [--pr N] [--denylist FILE]`: merges the head onto the base in a
  throwaway scratch clone outside the repository (the caller's tree and branch are never touched), then runs the
  self-audit and the full test suite on the merged result and prints one verdict line, `merged-check: ok` or
  `merged-check: FAILED <step>: <first finding>`. Two branches can each pass their own gate on their own base and still
  break the base once both land; run it against current `origin/main` right before a hand-off or merge.
- The pool's own value ledger: `lodgings ledger` appends, for the last closed UTC day(s), one line per counter per host to
  `<state>/ledger/lodgings.jsonl`, counted only from records the pool already holds: jobs placed and where, placements that
  fell back, jobs finished, queue waits and toolchain-gate coverage (from events that carry them), drain transitions and
  the standing drain request. Every number carries its record range and a digest of the exact rows it was counted from;
  what the records cannot show (drain requests in full, evacuation offers and moves, drains completed, where a job came
  from, any placement-versus-local time) is written as an unknown with its reason and the one field that would make it
  measurable, and a host that sends no beats shows no drain. How much of a day a host's records cover is read from the
  continuity of its beats (a goodbye is an expected absence): since `beats.jsonl` keeps only a few hours, each run first
  appends what it sees of them to its own coverage record (`<state>/ledger/coverage/<host>.jsonl`, kept 15 days), and a
  day is read from the union of those rows. **Run it at least every hour** (for example
  `17 * * * * lodgings ledger --hosts <hosts.toml> --state <state-dir>`). A quiet day it covered reads an exact zero; a day
  with a hole in the coverage, or one the writer did not observe, reads unknown, never a lower bound. A day is written
  only once it is closed, a later run that reads a held day better appends a line that supersedes it, and `--now`
  replays read only the records received by then. The file is append-only and written atomically, and a ledger that cannot be
  read is never overwritten. A record with a non-finite number (`NaN`, `Infinity`) is rejected. Optional `queued_s` on a job `start` and `gate` on a placement are
  new in `lodgings.event/1`. No new process; one run costs about 0.1 s, about 0.18 s over a year of lines ([docs/ledger.md](docs/ledger.md)).
- Back-off for the loops, so the tools cost the machine as little as possible: `lodgings beat --every` lengthens its own
  interval (2, 4 or 8 times, between 5 s and 120 s) when the load per core is above a limit, the machine is on a low
  battery, or the loop went over its CPU budget, and returns to normal when the load clears. The beat it does send says so
  (`paced: {reason, factor}` and the new `interval_s`, optional in `lodgings.beat/1`), so the pool reads a paced host as up
  but slowed on purpose, never late: judged by the interval it announced, `STALE` after twice the late age. `lodgings states
  --json` carries `paced`, `lodgings thresholds` counts `paced_gaps`, and `lodgings watch` waits up to an hour for a quiet
  machine before probing. Flags `--no-pace`, `--pace-load`, `--pace-battery`, `--cpu-budget`. No new process.
- Documented run and CPU budgets with measured numbers for a beat, a probe and a beat loop (README, "Resource budget";
  [docs/drain.md](docs/drain.md), "Back-off and paced beats").
- Community files: `SUPPORT.md`, and issue templates (bug report, feature request, the pool advised something wrong) and a
  pull request template under `.github/`.

### Fixed
- `pip install .` now gives a working package: it installs a `lodgings` command (`[project.scripts]`) and ships
  `probe_windows.ps1` as package data, so a probe of a Windows-view host from an installed copy no longer stops with a
  `FileNotFoundError`.
- The release zip (`scripts/build-package`) now holds `SUPPORT.md`, which the README and `CONTRIBUTING.md` link, and a
  relative link in a shipped `.md` file to something the zip does not hold (the adapters, `core/passport`) is written as
  plain text, so no link in the zip is dangling.
- Two broken links: the bug report template's pointer to `SECURITY.md` and the `docs/format.md` lane-spec anchor in
  `core/passport/README.md`.
- `lodgings` writes its output as UTF-8 whatever the console or pipe encoding. On Windows a redirected or piped `probe`
  stopped with a `UnicodeEncodeError` as soon as it printed `→` (a drift line); that no longer happens.
- `lodgings-passport export` failed on Windows native (exit 5, `object directory ...objects<CR> does not exist`): the
  alternates file it gives its scratch repository was written in text mode, so git read each path with a trailing CR. It
  is now written with LF line ends. It was the only file this code writes that git reads.
- `lodgings-passport` read git's output with the Windows code page (cp1252) instead of UTF-8, so a commit subject or
  file name holding U+009B or U+202E reached the print boundary as ordinary-looking characters and was written back
  out as the original bytes. It now decodes git and every other child process as UTF-8, shows a byte that is not
  UTF-8 as U+FFFD, and writes only what the output's own encoding can hold (the rest as `\uNNNN`).
- The `lodgings evacuate` offer for a Windows host said to run `py -3 lodgings-passport`, which only works from the
  directory that holds the script. It now names the file by its path, `py -3
  <checkout>\core\passport\bin\lodgings-passport`, as `core/passport/docs/cli.md` already did, and lists the checkout
  under `missing` for each Windows host in the move.

### Documentation
- The README support matrix, status line and resource budget say what the 2026-10-15 test pass found: a Mac and native
  Windows now ran as collectors and probe targets, the Windows probe time is measured (about 3 s), the Mac timings were
  taken under heavy load, and sleep and wake and `--deep` are still unrun on real hardware. The README, `docs/drain.md`
  and `core/passport/README.md` also say that beats need a checkout on each host, which beat intervals a once-a-minute
  schedule needs, and what a Debian or Ubuntu host needs for `pip install .`.
- Documentation and maintainer tooling: the support matrices (README and passport README) match the 2026-10-15 runs on a
  Mac, native Windows and WSL, the passport README states its output safety, the evacuate offer for a Windows host is
  documented, and on Windows `scripts/self-audit` prints `/` in finding paths and `scripts/merged-check` removes its
  scratch clone of read-only git objects (three maintainer-script test failures there); `.gitignore` covers `build/`
  and `src/lodgings.egg-info/`.

### Removed
- `adapters/` (the tool-specific code for one fleet: a passport adapter, a dashboard bridge and its tests) and
  `examples/sinks.example.txt`. A connector for your own tooling is written against the JSON seams. Integrations read the
  JSON seams instead: `lodgings chips` (`lodgings.chips.list` 1.0), `lodgings states --json` and the passport format
  (`core/passport/docs/format.md`).
- The `adapters/` default exemption of `scripts/self-audit` (the default is now `--exempt` none) and the adapter roots
  and the `adapters/` exemption of `core/passport/scripts/self-audit`.

### Changed
- `scripts/self-audit` and `core/passport/scripts/self-audit` take `--denylist` more than once and apply every file. The
  built-in name check keeps only generic private-network patterns; the names of the tools a maintainer builds around the
  product belong in a private denylist file, and the script no longer skips itself.
- `scripts/merged-check` takes `--denylist` more than once (or several `:`-separated files in `LODGINGS_AUDIT_DENYLIST`)
  and passes every file on to the self-audit.
- `lodgings evacuate` now says where `lodgings-passport` comes from (`core/passport/bin/lodgings-passport` in the source
  checkout, not installed with Lodgings) and that its commands assume it is reachable on each host under that name. The
  README's Honesty section states its rules in place instead of pointing at a document outside this repository.
- Renamed the project from Harbor to Lodgings, with no forwarding shim: the old command, module and names stop
  working. Old to new, in short: command `harbor` -> `lodgings` (`harbor-passport` -> `lodgings-passport`,
  `harbor-lookout` -> `lodgings-lookout`); Python package `harbor` -> `lodgings` (`harbor_passport` ->
  `lodgings_passport`); environment variables `HARBOR_*` -> `LODGINGS_*`; paths `.../harbor/...` -> `.../lodgings/...`
  (config, state and denylist); schema ids `harbor.*` -> `lodgings.*`; git refs `refs/harbor/...` ->
  `refs/lodgings/...`. The full list is in [MIGRATION.md](MIGRATION.md). Entries below this one keep the old names as
  history.
- The macOS commands `lodgings beat` runs (`pmset`, `sysctl`) now time out after 2 s instead of 5 s: a slow reading is an
  unknown.
- First-run fixes for a stranger: the README says how to get the code, writes the quickstart commands as
  `python3 bin/lodgings`, shows the real tag count (22), states that the project is early and lists the Python versions
  the suite has run on, and says in two sentences what a lane, a passport and a canary are.
  `examples/hosts.example.toml` no longer holds a fixture host whose relative path broke as soon as the file was copied to
  `hosts.toml`; it now passes `hosts check` from any folder. `docs/troubleshooting.md` lists the two errors a first run
  meets (`cannot read .../hosts.toml`, `fixture file ... does not exist`).

### Also in 0.3.0 (omitted from its notes at release)
These were listed under `[Unreleased]` when 0.3.0 was tagged, although the tagged tree already holds them.
- `lodgings-passport export --adopt-gate-head [--confirm-differences TOKEN]` (`core/passport`): resume a lane at the
  review gate's head when the gate holds a squash or a rebase of it, which a field test hit in 6 of 6 exports (refused as
  `custody diverged`). It is explicit, verifies the trees, lists what only the lane holds before it allows anything, and
  keeps the lane's own commits in the passport and the archive refs. The demo (`core/passport/examples/demo DIR
  squashed-gate`) and the passport docs show the case.
- A `lodgings-passport` custody refusal says which side is ahead, both commit counts, and the ways out.
- `lodgings-passport export` no longer needs a worktree: a named worktree path that no longer exists is recorded as
  not checked, and the branch is read from the repository.
- `lodgings-passport` and `lodgings` print repository- and record-derived text (a commit subject, a file name, a lane
  name) with control characters, terminal escape sequences and zero-width or bidirectional characters shown as `\xNN`
  (`\uNNNN` above U+00FF) instead of raw, so a hostile name can neither drive nor spoof the terminal; what is decided
  and recorded is unchanged.

## [0.2.0] - internal, not released

### Added
- Host states `UP`, `LATE`, `STALE`, `LOST`, `DRAINING`, `DRAINED`, `PAUSED`, `ASLEEP` (and `UNKNOWN` before the first
  beat), computed from recorded beats and events: `harbor states [-v] [--now EPOCH]`, with each change recorded and its
  age shown. Advice only: no runner is told and nothing is moved ([docs/drain.md](docs/drain.md)).
- Drain inputs: consecutive canary failures, the fallback share over a window, a machine on battery, a pending reboot
  due within the window, the paused marker, and an operator's request (`harbor drain HOST` / `harbor undrain HOST`).
  Thresholds in a new `[drain]` table and per host (`drain = { ... }`), validated by `harbor hosts check`.
- Records `harbor.beat/1` and `harbor.event/1`: `harbor beat` measures power, pending reboot and the paused marker on
  Linux, WSL, macOS and Windows (read-only) and delivers the record by `--record`, `--out FILE` or `-- CMD...`;
  `harbor record [--replay]` takes them on the collector.
- Goodbye beats (`harbor beat --goodbye REASON`): an expected absence reads `ASLEEP`, not `LOST`, machine-wide or for one
  view. Example hooks for Linux sleep, a systemd user unit (WSL stop), macOS (sleepwatcher) and Windows (scheduled
  tasks on sleep and restart events) in `examples/goodbye/`, with install and removal instructions; never installed.
- Sleep-safe time: ages are this machine's clock against `received_epoch`, a host's reboot time is a span on its own
  clock, and this machine's own sleep or restart (noticed through an awake clock) never counts against a host.
- `harbor evacuate HOST`: an offer listing the jobs that would requeue (next attempt, eligible hosts, the runner's
  resubmit command) and, for each parked lane, the exact `harbor-passport` export, verify, import and confirm commands to
  each eligible host. Runs nothing. New host keys `repos` and `paused_marker`; `confine` is now used for passports.
- `harbor thresholds [--from FILE]`: the thresholds in force and what a recorded history would have done under each.
  docs/drain.md shows the numbers from one 38-hour history behind the defaults.
- Lookout adapter: `--states` adds each host's state as its first chip and lost or draining hosts as attention cards;
  `card --card hosts|drain|all` writes a host-state card and a drain card (`lookout.card` 1.0, `mode` advice-only);
  `harbor-lookout beat` turns a `lookout.beat` file into a `harbor.beat/1` record. All off unless asked.
- `harbor-lookout card [--out PATH]` (adapter only; the core knows no dashboard): one `lookout.card` 1.0 status card for
  the pool, from the stored probes. Off unless asked; sends nothing.

## [0.1.0] - internal, not released

### Added
- `hosts.toml` (`harbor.hosts/1`): hosts, views (`linux`, `wsl`, `darwin`, `windows`), declared tags, slot classes,
  expectations, policy; `harbor hosts check` with unknown-key warnings.
- Capability probe for Linux, WSL and macOS (one self-contained Python file, streamed over ssh, nothing installed
  on the host) and for Windows (PowerShell streamed over ssh, nothing installed or written on the host).
- Tags with evidence: probed, declared, verified, contradicted, unverified. Matching is a set test (`harbor match`).
- Toolchain fingerprint (`sha256` over the stable fields), drift history, `harbor compare`, `--same-toolchain-as`.
- Optional deep probe: starts the installed Chrome once, headless, to measure WebGPU.
- `harbor chips`: display-ready chips and alerts (`harbor.chips 1.0`).
- Lookout adapter `adapters/lookout/harbor-lookout` and an optional patch that lets any source report machine
  capabilities.
- Fixture replay (`fixture = "file"`), `--save-raw`, three recorded fixtures, behavioural tests through the CLI.
- `scripts/self-audit` and `scripts/build-package`.
