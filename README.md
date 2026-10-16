# Lodgings

A pool of hosts where work can start on, or move to, any host that can honestly run it. It has two parts:

- **Know what each host can run.** Lodgings probes each host once a day and at start, records what it found as tags
  with their evidence plus a toolchain fingerprint, and tells you when a host's toolchain changed or no longer matches
  what you expect, before a result from it is trusted or compared.
- **Know which hosts should take work now** ([docs/drain.md](docs/drain.md)). From beats and canary results each
  host is `UP`, `LATE`, `STALE`, `LOST`, `DRAINING`, `DRAINED`, `PAUSED` or `ASLEEP`; drain inputs (failed canaries,
  fallbacks, battery, a pending reboot, a paused marker, your request) say why; a goodbye beat on sleep keeps a closed
  laptop from reading as lost; and an evacuation *offer* lists what would requeue and the exact lane-passport commands
  that would move each parked lane. Advice only: nothing is moved.

**Status: early.** 0.3.0 is the first public version (the earlier numbers in the changelog were internal and never
released); it has run on Linux, WSL, a Mac (Apple Silicon) and native Windows, as a collector and as a probe target, in
one test pass on 2026-10-15 (see the [support matrix](#support-matrix-what-has-actually-been-run) for what each run did
and did not cover).

It is **not** a scheduler, a job runner, a deployment tool or a monitor. It does not start, move or stop anything.
Live health (memory pressure, load, disk) belongs to a beat from whatever monitors the host; Lodgings adds what a beat cannot say:
*which* OS and toolchain, *whether* a GPU-backed Chrome exists, *whether* signing tools are there.

- **Honest about gaps.** A capability is probed, declared, verified, contradicted or unverified; *unknown* is never *no*
  and never green. A host that cannot be reached keeps its last good record, marked with its age.
- **Nothing installed on the hosts for probing.** Linux, WSL and macOS get one Python file on standard input; Windows gets
  PowerShell on standard input. No agent, no service, no file written there. (Beats, which the `LATE`, `STALE`, `LOST` and
  `DRAINING` states need, are sent by a Lodgings checkout on each host; see step 4 of the quickstart.)
- **Read-only, local, tiny.** Python 3.8+ standard library only, no build step, no telemetry, no root. State is a handful of
  JSON files in one directory. Deterministic: no model call anywhere.
- **Drift you can see.** The fingerprint changes when a tool version, the OS, the hardware or a browser build changes;
  the change is recorded and shown for a day.
- **Fixtures, not mocks.** Any host can be replayed from recorded facts, so every state can be shown and tested without a
  second machine.

## Five-minute quickstart

Clone this repository (or download a source archive of it) and work from its top folder. You need Python 3.8 or newer.
Nothing to install: the command is `python3 bin/lodgings` on macOS and Linux and `bin\lodgings.cmd` on Windows, and the
blocks below are written in the Linux and macOS form. (`pip install .` in the checkout or the unzipped release also puts a
`lodgings` command on your PATH. On Debian and Ubuntu, `python3 -m venv` needs the `python3-venv` package and `pip`
needs `python3-pip`, which a minimal install may lack (or use `uv venv`); and `pip install .` leaves a `build/` folder
and `src/lodgings.egg-info/` in the checkout, which git ignores and you can delete.)

To remove it, run `pip uninstall lodgings` if you installed it with pip, or delete the checkout or the unzipped release. What it
recorded is only the state directory (the `state_dir` in `hosts.toml`; by default `~/.local/state/lodgings` on Linux,
`~/Library/Application Support/lodgings` on macOS, `%LOCALAPPDATA%\lodgings` on Windows) and your `hosts.toml`; delete those if you
want them gone. Nothing was installed on the hosts you probed.

1. **See it with synthetic data** (three invented hosts replayed from recorded facts):

   ```
   python3 bin/lodgings probe --hosts examples/demo-hosts.toml --state demo-state
   python3 bin/lodgings status -v --hosts examples/demo-hosts.toml --state demo-state
   ```

   ```
   HOST           VIEW     STATE   PROBED  FINGERPRINT   TAGS  NOTES
   mac            darwin   ok      0s      51515edd0bec  22    Mac (fixture) toolchain differs from what is expected
       expected           spacetime 2.8.2 (want 2.10)   [warn]
       os                 macOS 15.1 · arm64
       gpu                M3
       chrome             150 · webgpu yes
       ...
   ```

2. **Ask who can run something:**

   ```
   python3 bin/lodgings match --requires gpu,chrome-webgpu --hosts examples/demo-hosts.toml --state demo-state
   python3 bin/lodgings match --requires macos-signing --same-toolchain-as mac --hosts examples/demo-hosts.toml --state demo-state
   ```

3. **See a toolchain change.** Replay the same Mac after an update and read the history:

   ```
   python3 bin/lodgings probe mac --replay examples/fixtures/macos-arm64-after-update.json --hosts examples/demo-hosts.toml --state demo-state
   python3 bin/lodgings history mac --hosts examples/demo-hosts.toml --state demo-state
   ```

4. **Probe your own hosts.** Copy `examples/hosts.example.toml` to `hosts.toml` (it passes `hosts check` as it is), then
   replace its sample hosts with your machines: each is one of `local = true`, `ssh = "alias"` (an alias from your
   `~/.ssh/config`) or `fixture = "file"` (a path relative to `hosts.toml`). The `ssh` aliases in the sample do not exist on
   your machine until you change them, so `probe` reports those hosts as failed. Then:

   ```
   python3 bin/lodgings hosts check
   python3 bin/lodgings probe            # all hosts; add --deep to measure WebGPU on hosts with Chrome
   python3 bin/lodgings status
   python3 bin/lodgings watch            # probes now and every 24 h, in the foreground (Ctrl-C stops it)
   ```

   To keep `watch` running across logins, start it from your own supervisor (systemd user unit, launchd, Task
   Scheduler). Lodgings never installs one.

   `probe` and `watch` give facts, not live states. `UP`, `LATE`, `STALE`, `LOST` and `DRAINING` come from beats (step 5),
   and a beat is sent by a Lodgings checkout and a `hosts.toml` on the host that sends it. With the defaults, a host is
   `STALE` after 60 s without a beat, so a scheduled job that beats once a minute makes every host read `STALE`. For that
   case set, in the `[drain]` table of the collector's `hosts.toml`, `beat_interval_s = 60`, `late_after_beats = 2`,
   `stale_after_s = 180` and `lost_after_s = 900` (`hosts check` validates them), or beat every 15 s with `--every`.
   `match` answers capability only: combine it with `states` to see whether a matching host is up.

5. **See host states, a drain and an evacuation offer** (a synthetic hour: a tower with a reboot due in 20 minutes and
   a lane parked on its WSL view, then a Mac whose lid closes):

   ```
   python3 bin/lodgings record --replay examples/drain/demo-records.jsonl --hosts examples/demo-hosts.toml --state demo-state
   python3 bin/lodgings states -v --now 1800003600 --hosts examples/demo-hosts.toml --state demo-state
   python3 bin/lodgings evacuate host-a-wsl --now 1800003600 --hosts examples/demo-hosts.toml --state demo-state
   python3 bin/lodgings states --now 1800005400 --hosts examples/demo-hosts.toml --state demo-state     # asleep, not lost
   ```

   ```
   HOST           STATE     WORK  BEAT    SINCE   WHY
   mac            UP        yes   9s      -       -
   host-a-win     DRAINED   no    9s      -       a reboot is due in 20 min (maintenance window)
   host-a-wsl     DRAINING  no    7s      -       a reboot is due in 20 min (maintenance window, seen by host-a-win); 1 job running; 1 lane parked
   ```

   Two words from that output: a **lane** is one unit of work (a branch, its worktree and its review history) that is
   parked on a host, and a **passport** is the sealed directory `core/passport/bin/lodgings-passport` writes so a person
   can move a lane to another host ([core/passport](core/passport/README.md)). A **canary** is a small recurring test job
   whose pass or fail a host reports; repeated failures are one reason a host is drained ([docs/drain.md](docs/drain.md)).

   On your own hosts, each one beats (`python3 bin/lodgings beat --every 15s -- ssh collector python3 lodgings/bin/lodgings record -`,
   or `--record` on the collector itself) and says goodbye before sleeping ([examples/goodbye](examples/goodbye/README.md)).
   `lodgings-passport` is not installed with Lodgings: an evacuation offer names `core/passport/bin/lodgings-passport` of a
   checkout, and for a Windows host the command is `py -3 <checkout>\core\passport\bin\lodgings-passport`.

6. **Show it on a dashboard.** `lodgings chips` prints JSON (`lodgings.chips.list` 1.0): display-ready chips and alerts for each host
   (see [docs/capabilities.md](docs/capabilities.md)). A dashboard or script reads that, and the core knows no dashboard.

## Commands

In this table `lodgings` stands for `python3 bin/lodgings` (`bin\lodgings.cmd` on Windows).

| Command | Does |
| --- | --- |
| `lodgings hosts check [--strict]` | validate `hosts.toml`, list every problem |
| `lodgings probe [HOST...] [--deep] [--save-raw] [--replay FACTS]` | probe now and store the result; `--replay` uses recorded facts for one host |
| `lodgings watch [--every 24h] [--once]` | probe now, then on an interval, in the foreground |
| `lodgings status [-v]` | one line per host: state, age, fingerprint, drift, alerts |
| `lodgings show HOST` | everything known, with the evidence behind each tag |
| `lodgings history HOST` | recorded fingerprint changes |
| `lodgings match --requires TAG,... [--same-toolchain-as HOST]` | which hosts honestly qualify, and why not |
| `lodgings compare A B` | fingerprint differences between two hosts |
| `lodgings chips [HOST...]` | display-ready chips and alerts as JSON (what a dashboard or script reads) |
| `lodgings beat [--goodbye REASON] [--every 15s] [--record \| --out FILE \| -- CMD...]` | on a host: its liveness and drain inputs (power, pending reboot, paused) as one `lodgings.beat/1` record |
| `lodgings record [FILE...] [--replay]` | on the collector: take beats and events (`lodgings.beat/1`, `lodgings.event/1`) |
| `lodgings states [HOST...] [-v] [--now EPOCH]` | each host's state, whether it takes work, and why |
| `lodgings drain HOST [--reason TEXT]` / `lodgings undrain HOST` | record or withdraw an operator's drain request (advice only) |
| `lodgings evacuate HOST` | what would requeue and the exact passport commands for each parked lane; runs nothing |
| `lodgings ledger [--days N] [--dry-run]` | read the last closed UTC day(s) into the pool's value ledger, counted from its own records; a held day is only superseded by a better reading. Run it at least every hour, e.g. `17 * * * * lodgings ledger --hosts <hosts.toml> --state <state-dir>` ([docs/ledger.md](docs/ledger.md)) |
| `lodgings thresholds [--from FILE]` | the drain thresholds in force and what a recorded history would have done under them |

`--json` gives machine-readable output on every command that prints a table. Plain text first, no colour. Exit codes:
`0` ok, `1` findings (check errors, no eligible host, hosts differ, no probe yet), `2` usage or configuration error,
`3` a probe failed, `70` internal error.

## What it will never do

Start, stop or install anything on a host; run a command outside the closed list in [docs/safety.md](docs/safety.md);
read a credential or a user's files; write outside its state directory; send data anywhere you did not configure;
act on a finding; move a job or a lane (a drain and an evacuation are advice and offers).

## Honesty

Every word and number Lodgings prints comes from a probe, a beat or a record it holds, and a value it does not have reads as
unknown, never as zero or as a guess. This section says what that means here.

- **Measured.** Tool versions, OS, hardware and Chrome facts come from the probe on that host ([docs/safety.md](docs/safety.md),
  [docs/capabilities.md](docs/capabilities.md)); power, pending reboot and paused marker come from a beat the host sent
  ([docs/drain.md](docs/drain.md)). Each is as old as its probe or beat, and the age is shown.
- **Derived.** The fingerprint and drift ([docs/capabilities.md](docs/capabilities.md)), and each host's state (`UP`, `LATE`,
  `STALE`, `LOST`, `DRAINING`, ...) and its reason, are computed from those records and the thresholds in
  [docs/drain.md](docs/drain.md). An evacuation offer is derived from recorded jobs and lanes.
- **Unknown.** A host with no beat is `UNKNOWN`, never green; a tag the probe could not check is *unverified*, never *no*; a
  reboot or battery value the host cannot report is null or unknown ([docs/drain.md](docs/drain.md),
  [docs/capabilities.md](docs/capabilities.md)).
- **Stale and silent are shown as such.** A host that cannot be reached keeps its last good record, marked with its age
  (`stale`, `failed`, `never`), and silence reads `LATE`, `STALE` or `LOST`, not healthy. A host that said goodbye reads
  `ASLEEP`, and this machine's own sleep never counts against a host ([docs/drain.md](docs/drain.md)).
- **What it cannot know.** Whether a probe or a beat is right, why a host went quiet (a sleeping laptop and a dead network can
  look alike until a goodbye or a later beat), or anything between two probes or beats. A pending reboot's time is
  unknown on macOS and Windows unless you pass it.
- **What it never claims.** That a host is fine because nothing was heard; that an unchecked capability is absent or
  present; that a drain, a requeue or an evacuation happened. It makes no model call (see "Read-only, local, tiny" above).
- **Advice only.** Drain requests, host states and evacuation offers move and stop nothing; the commands an offer prints are
  for a person to run ([docs/safety.md](docs/safety.md), "What `lodgings evacuate` prints" and "What it will never do").

## Documents

- [docs/hosts-toml.md](docs/hosts-toml.md): every key of the registry.
- [docs/capabilities.md](docs/capabilities.md): raw facts, the tag catalogue, the fingerprint, drift, expectations.
- [docs/drain.md](docs/drain.md): host states, drain inputs and their thresholds, goodbye beats, evacuation offers,
  sleep-safe time, the beat and event records.
- [docs/ledger.md](docs/ledger.md): the pool's value ledger: what is counted, what is left unmeasured and why, the digests.
- [docs/safety.md](docs/safety.md): the exact commands a probe and a beat may run and what is written.
- [docs/windows.md](docs/windows.md): PowerShell over ssh, nothing installed; what it cannot see.
- [docs/troubleshooting.md](docs/troubleshooting.md): real failure messages.
- Integrations read the JSON seams: `lodgings chips` (`lodgings.chips.list` 1.0), `lodgings states --json` and the
  passport format ([core/passport/docs/format.md](core/passport/docs/format.md)). A connector for your own tooling is
  written against those seams (`examples/drain/demo-records.jsonl` shows the records one sends); the core knows none.
- `examples/`: a small pool, three recorded fixtures, a demo registry, a synthetic hour of beats and events, and
  goodbye-beat hooks for each OS (examples, never installed). `CHANGELOG.md`, `CONTRIBUTING.md`,
  `SECURITY.md`, `CODE_OF_CONDUCT.md`, `SUPPORT.md`, and issue and pull request templates under `.github/`.
  Help and reporting: [SUPPORT.md](SUPPORT.md), [SECURITY.md](SECURITY.md), [CONTRIBUTING.md](CONTRIBUTING.md).

## Support matrix (what has actually been run)

| | Probe as a target | Lodgings itself (collector) |
| --- | --- | --- |
| Linux / WSL2 (Ubuntu) | run, live | install, quickstart, demo and the whole repo suite (47 of 47) on Python 3.12.3 and 3.14.4; Python 3.8 to 3.11 not run here |
| Windows 11 (PowerShell 5.1 over ssh) | run, live: probe 2.9 to 3.1 s | run on Python 3.12.4 and 3.12.10 through `bin\lodgings.cmd` and `pip install .`: install, quickstart, demo and probe pass; the passport smoke and end-to-end checks pass (109 of 109) and `export` exits 0. The repo suite ran 47 tests, 6 skipped by design and 3 failed, all three in maintainer scripts (a path separator and a read-only scratch clone), fixed since; it has not been re-run on Windows after the fix |
| macOS 27 (Apple Silicon) | run, live: probe, one-shot beat (`pmset`) and `--record` | run on Python 3.14.6: install, quickstart, demo, probe of itself, beat, the whole repo suite (47 of 47), the passport demo loop, smoke and end-to-end checks (109 of 109) |

Not run on real hardware: the goodbye beat on sleep (replayed from fixtures only), and `--deep` (it starts a headless
Chrome). The sleep and wake behaviour, and the claim that a closed laptop reads `ASLEEP` and not `LOST`, are therefore
unproven outside the replay.

## Resource budget

A probe: Linux about 0.5 s, Windows about 3 s over ssh (2.9 to 3.1 s in 5 runs on 2026-10-15), deep adds up to 1 s for one
headless Chrome (not measured in the latest pass); once a day. Lodgings
holds nothing in memory between probes and has no idle cost.

A beat: about 0.06 s and 23 MB once per interval (`--every 15s` keeps one Python process of that size sleeping between
beats). `lodgings states` over a thousand recorded events: about 0.05 s. Stored records are capped per host (500 beats,
5000 events, 200 transitions).

**Budgets, and backing off.** Each run has a wall-time budget and each loop a CPU budget, and the loops lengthen their
own interval, and say so, when the machine cannot spare them:

| | Budget | Measured here |
| --- | --- | --- |
| one beat, measuring | 2 s wall (the only commands it runs, `pmset` and `sysctl` on macOS, time out at 2 s; a reading that takes longer is an unknown, not a zero) | 0.056 s median, 0.059 s at most (30 one-shot runs) |
| one probe of this machine | the per-command `timeout_s` (default 20 s); over ssh a host gets `4 x timeout_s + 30 s` in all | 0.24 s median over 15 runs; the first run after a quiet spell took 2.3 s |
| a beat loop (`--every`) | 10 CPU-seconds per rolling hour, its delivery command included | 0.0008 s of CPU per beat once the interpreter is running: 0.2 s per hour at the default 15 s interval |
| a beat on a schedule instead (a new process every time) | none: it cannot pace itself | 0.056 s of CPU per run, so 13 s per hour at 15 s: use `--every` |

Back off when the 5-minute load average per core is above 1.0, or the machine is on battery below 40% (or on battery with
the charge unknown), or the loop went over its CPU budget: the interval grows 2, 4 or 8 times (4 times on battery, twice
over budget), never below 5 s or above 120 s, and returns to normal once the load is under 0.75 per core. A paced beat
carries `paced: {reason, factor}` and the interval it now keeps, so `lodgings states` reads the host as up but slowed on
purpose, never late and never silent ([docs/drain.md](docs/drain.md), "Back-off and paced beats"). `lodgings watch` waits
for a quiet machine in five-minute steps, an hour at most, before it probes. `--no-pace` turns all of it off.

Measured on one machine: WSL2 on Windows 11, 24 logical cores, Python 3.14, at a load of about 0.1 per core. Method: 30
one-shot `lodgings beat` runs and 15 `lodgings probe` runs of the local host, each timed from outside with
`time.perf_counter` and CPU read from `getrusage` of the finished children (user plus system); the loop's CPU is the
change in `utime + stime` of the one running process over 60 s at `--every 5s`, divided by the beats in that time and
scaled to 240 beats an hour. Scripts and raw numbers are not shipped. **Other platforms are not measured cleanly here**:
the table was not measured on macOS or Windows. A Mac was timed once, at a load average of 180 to 250 on a shared machine,
so its figures are inflated and only show the order of magnitude (a probe 1.4 to 2.6 s, a one-shot beat 1.25 s wall);
an unloaded Mac is not measured. Windows has no load average, so a Windows loop can back off only on battery and CPU
budget.

## Layout

```
bin/lodgings(.cmd)      the command
src/lodgings/           registry, probes (probe_unix.py, probe_windows.ps1), facts, chips, store, CLI;
                      beats and events (records.py, beat.py), host states (avail.py), evacuation offers (evacuate.py),
                      threshold evidence (evidence.py), sleep-safe time (clock.py), value ledger (ledger.py)
docs/  examples/      contracts, fixtures
scripts/              self-audit, merged-check, build-package
tests/                behavioural tests through the public command
```

MIT licensed, see [LICENSE](LICENSE).
