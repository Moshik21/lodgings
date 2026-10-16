# Host states, drain and evacuate

Lodgings tells you, for each host, whether it should take new work right now, and if not, why and what would have to
move. It computes that from what hosts and runners tell it: **beats** (a host is alive, on battery, about to reboot,
paused, or saying goodbye) and **events** (canary results, placements and fallbacks, jobs and parked lanes).

**Advice only.** In this slice nothing changes any runner's placement. A state is what a scheduler *should* do with a
host; `lodgings drain` records a request; `lodgings evacuate` prints the commands a move would take. Lodgings runs none of
them and tells no runner anything. A scheduler that wants to follow the advice reads `lodgings states --json`.

```
lodgings beat [--goodbye REASON] [--every 15s [--no-pace]] [--record | --out FILE | -- DELIVER...]   on each host
lodgings record [FILE...] [--replay]                                                     on the collector
lodgings states [HOST...] [-v] [--now EPOCH]
lodgings drain HOST [--reason TEXT]        lodgings undrain HOST
lodgings evacuate HOST [--now EPOCH]
lodgings thresholds [--from FILE]
```

## The states

| State | Entered when | Effect (advice) | Takes work |
| --- | --- | --- | --- |
| `UNKNOWN` | no beat received yet | Lodgings cannot tell; never shown green | no |
| `UP` | a beat within `late_after_beats` beat intervals and no drain input | takes work | yes |
| `LATE` | the last beat is older than `late_after_beats` × the host's beat interval (30 s by default), or this machine has just woken (below) | takes work, shown amber | yes |
| `STALE` | the last beat is older than `stale_after_s` (60 s) | no new placements; running work keeps its leases | no |
| `LOST` | the last beat is older than `lost_after_s` (15 min), **or** the host is `STALE` and a running job's lease ran out with no word | no placements; its running jobs requeue once elsewhere (attempt n+1); a "lost" card | no |
| `DRAINING` | a drain input (next table) and a job still running | no new placements; running work finishes; parked lanes are offered a move | no |
| `DRAINED` | a drain input and nothing running | safe to sleep, reboot or update | no |
| `PAUSED` | the paused marker is set (the machine is in use, for example a game) | no new placements; running work finishes | no |
| `ASLEEP` | the host said goodbye and has not beaten since | an expected absence: no lost alarm, no requeue storm; jobs it still held requeue at once | no |

Precedence: `UNKNOWN`, `ASLEEP` and `LOST` come from liveness and win. Otherwise any drain input gives `DRAINING` or
`DRAINED`; otherwise the paused marker gives `PAUSED`; otherwise the liveness state (`UP`, `LATE`, `STALE`) stands.
`lodgings states` records each change in `<state>/hosts/<name>/transitions.jsonl` and shows how long the current state
has held (`SINCE`).

## Drain inputs

| Input | Drains when | Threshold (default) |
| --- | --- | --- |
| `operator` | `lodgings drain HOST` was run and not withdrawn with `lodgings undrain HOST` | none |
| `canary` | the last N canaries of the host all failed (one pass clears it) | `canary_failures` (2) |
| `fallback` | over a share of the placements made in the last window fell back, once enough were made to judge | `fallback_share` (0.2), `fallback_window_s` (3600), `fallback_min_placements` (5) |
| `battery` | any view of the machine reports running on battery | `on_battery` (true; false makes it a note) |
| `reboot` | a reboot is pending and due within the window, or already past due. A pending reboot with no known time is a note unless `reboot_unknown_due = "drain"` | `reboot_window_s` (3600), `reboot_unknown_due` ("note") |
| `paused` | the paused marker file exists on the host: gives `PAUSED`, not `DRAINING` | the marker path (`paused_marker`, default `~/.config/lodgings/paused`, `%APPDATA%\lodgings\paused` on Windows) |

What stays below a threshold is shown as a note (`lodgings states -v`): one failed canary, a few fallbacks, a reboot due
in three hours.

**Machine and view.** One physical machine can be several hosts (`machine = "..."` in `hosts.toml`): a PC's Windows and
WSL views. Battery, a pending machine reboot and the paused marker belong to the machine, so the freshest beat of any
view (no older than `stale_after_s`) speaks for all of them and the reason says which view saw it
(`seen by host-a-win`). A reboot pending *inside* a WSL distribution restarts only that distribution and drains only that
view. Canaries, placements, jobs and lanes stay per host.

## Goodbye beats: ASLEEP, not LOST

A goodbye (`lodgings beat --goodbye REASON`) is the last thing a host says before an expected absence. Until the host
beats again it reads `ASLEEP`, however long that takes: a laptop closed overnight is not a lost host.

| Reason | Scope by default |
| --- | --- |
| `sleep`, `lid`, `hibernate`, `shutdown`, `reboot`, `other` | `machine`: every view of the machine reads ASLEEP |
| `wsl-stop`, `stop` | `host`: only this view |

When one view of a machine is back (it beats after the goodbye), the views that have not beaten yet are counted from
that moment: a WSL distribution that does not come back after its PC wakes reads `LOST` after `lost_after_s`, not at
once and not never. Jobs a host held when it said goodbye are listed as requeue-now (`requeue_now`), since they cannot
finish there.

Hooks that send a goodbye on sleep, lid close, restart and WSL stop are in [examples/goodbye](../examples/goodbye/README.md),
as examples with install and removal instructions. Lodgings installs none of them. A goodbye that does not arrive costs one
false `LOST` that clears with the next beat.

## Time: never a wall-clock string, and sleep-safe

- Every record gets `received_epoch`, this machine's clock (epoch seconds) when it arrived. Ages are `now -
  received_epoch` on this machine; a time another machine wrote is never compared with this machine's clock. A host's
  `reboot.due_epoch` is turned into a span on the host's own clock (`due_epoch - sent_epoch`) and placed on ours, so a
  host whose clock is off by an hour still drains at the right moment.
- **This machine's own sleep never counts against a host.** On every look Lodgings reads an awake clock that stops while the
  machine sleeps (`CLOCK_MONOTONIC` on Linux and WSL, `CLOCK_UPTIME_RAW` on macOS, `QueryUnbiasedInterruptTime` on
  Windows) next to the epoch and keeps both in `<state>/observer.json`. When the epoch moved more than 30 s further than
  the awake clock (sleep, a suspended WSL distribution, or a forward clock step), or the awake clock went backwards (a
  restart), silence is counted only from that moment: hosts read `LATE` (unconfirmed) until they beat again, never
  `LOST` for time nobody was listening. `lodgings states` says so in a note.
- `--now EPOCH` judges a recorded history as it stood at that time (records received after it are left out) and writes
  nothing; it is how replays and the tests work.

## Records

Both are JSON objects; `lodgings record` takes JSON lines, one JSON object, or a list. Unknown fields are dropped, a bad
record is rejected with the reason and the rest are kept (exit 1 when any was rejected). Hosts must be in `hosts.toml`.

`lodgings.beat/1` (what `lodgings beat` sends):

| Field | Meaning |
| --- | --- |
| `host` | required: the host's name in `hosts.toml` |
| `kind` | `beat` (default) or `goodbye`, with `reason` and `scope` as above |
| `sent_epoch`, `interval_s`, `seq`, `boot_id`, `uptime_s` | the host's clock and identity; `interval_s` sets how soon it is `LATE` |
| `power` | `{on_battery, percent}`: true, false or null (unknown) |
| `reboot` | `{pending, due_epoch, source}`: `due_epoch` on the host's clock, null when unknown |
| `paused` | true when the paused marker exists |
| `paced` | `{reason, factor}` on a beat slowed on purpose, with the `interval_s` it now keeps ([Back-off and paced beats](#back-off-and-paced-beats)) |

`lodgings.event/1` (sent by runners, schedulers or adapters; `at_epoch` optional):

| `kind` | Fields |
| --- | --- |
| `canary` | `ok` (required), `duration_s`, `reason` |
| `placement` | `outcome`: `placed` or `fallback` (work meant for this host that ran elsewhere), `reason`, `class`, `gate` (optional: what the toolchain gate did, `passed`, `refused` or `unchecked`) |
| `job` | `phase`: `start`, `renew` or `end`; `job` (id); `class`, `attempt`, `lease_s`, `queued_s` (optional: seconds the job waited for a slot), `requires` (tags), `rc`, `resubmit` (an argument list that requeues the job, shown in the offer; never a shell string, never run) |
| `lane` | `phase`: `park` or `leave`; `lane`; `project`, `task`, `seq`, `spec` (the lane spec on that host), `repo`, `requires` |

What `lodgings beat` measures, read-only, on each OS:

| OS | Power | Pending reboot | Due time |
| --- | --- | --- | --- |
| Linux, WSL | `/sys/class/power_supply` | `/run/reboot-required`, `/run/systemd/shutdown/scheduled` | from a scheduled `shutdown -r`; else unknown |
| macOS | `pmset -g batt` | not measured (null) | unknown |
| Windows | `GetSystemPowerStatus` | the Windows Update `RebootRequired` and servicing `RebootPending` registry keys | unknown: Windows does not publish it; pass `--reboot-in 45m` or `--reboot-at EPOCH` if your update policy knows it |

Delivery is yours to choose: `--record` on the collector itself, `--out FILE` for a file another tool ships, or
`-- CMD ARG...` to run a command with the record on standard input (`-- ssh collector python3 lodgings/bin/lodgings record -`).
With `--every 15s` it keeps beating in the foreground and reports a delivery failure once, and once again when it clears.
A tool that already writes its own beat file can turn it into a `lodgings.beat/1` record and deliver it the same way.

## Evacuate: an offer, never a move

`lodgings evacuate HOST` prints, for that host:

- **jobs** running there, each with its next attempt number, the hosts that take work now and meet its `requires`
  and its job class, and its `resubmit` command if the runner reported one. For a `DRAINING` host they requeue only if
  you evacuate (a drain lets them finish); for a `LOST` or `ASLEEP` host they requeue now. Jobs are requeued by whoever
  submitted them.
- **lanes** parked there, each with a lane-passport move ([core/passport](../core/passport/README.md)) to every eligible
  host: the exact `lodgings-passport export`, `verify`, `import` and `confirm` commands with the paths from `hosts.toml`
  (`confine` is where a passport and an imported worktree go on a host, `repos.<project>` is the project's clone there),
  and the two copy steps between them. A path Lodgings cannot know is shown as `<placeholder>` and listed under
  `missing`, never guessed. `lodgings-passport` is `core/passport/bin/lodgings-passport` of a checkout, not installed
  with Lodgings; for a Windows host the offer names it by its path: `py -3 <checkout>\core\passport\bin\lodgings-passport`.

Moving a lane is a placement decision for a person or their supervisor. Lodgings ran nothing; the JSON says
`"executed": false`.

## Choosing thresholds: `lodgings thresholds`

`lodgings thresholds` prints the thresholds in force and, from the recorded history (or `--from FILE` of beats and
events), what each would have done: how many beat gaps would have read `LATE`, `STALE` or `LOST` (gaps after a goodbye,
including a machine-wide goodbye from another view, are skipped); how many drains a canary threshold of 1, 2 or 3 would
have caused; the hourly fallback share and how often it crossed the line; how long jobs run against the reboot window.
It never changes `hosts.toml`.

### Where the defaults come from

The defaults keep the design values; one history checked them. It is 38 hours of a collector sending build, browser and
test jobs to one remote Windows build host (994 records: canaries, admissions and fallbacks from the runner's log, job
starts and ends), converted to `lodgings.event/1` and read with `lodgings thresholds --from`.

| Threshold | Default | What the history says |
| --- | --- | --- |
| `canary_failures` | 2 | 117 canaries, 4 failed, in two runs (1 and 3). A threshold of 1 drains twice, once falsely (a single failure, cleared by the next canary 83 s later). 2 drains once, for the real outage (3 failures in a row), at its second failure, and clears with the next pass 284 s later. 3 catches the same outage about 3 minutes later. Passing canaries took 36 s (p50), 56 s (p99), 62 s at most; failures took 182 to 271 s |
| `fallback_share`, `fallback_window_s`, `fallback_min_placements` | 0.2, 3600, 5 | 26 of 315 placements fell back (8%; 23 because the run did not finish, 3 because the job could not be shipped). The hourly share once 5 placements were made: p50 0, p90 0.2, p99 0.7. Episodes over the line: 5 at 0.1, 4 at 0.2, 3 at 0.3, 2 at 0.5; with a minimum of 10 placements only 1 at 0.2. The history cannot tell host trouble from job trouble, so the design value stays; review it with a week of receipts |
| `reboot_window_s` | 3600 | 273 jobs ran p50 149 s, p95 709 s, p99 6792 s; 7 (2.6%) ran longer than an hour. A drain one hour before a known reboot lets about 97% of jobs finish in place |
| lease expiry → `LOST` | only when also `STALE` | 8 leases missed renewals, silent for 5 to 393 s (p50 120 s); the host itself dropped only 2 of them (silent 33 and 39 s). An expired lease alone is not proof of loss, so it only turns a `STALE` host `LOST` |
| `beat_interval_s`, `late_after_beats`, `stale_after_s`, `lost_after_s` | 15, 2, 60, 900 | not measurable yet: the runners do not send beats, and contacts on demand (gaps p50 44 s, p99 1118 s) are not beats. The collector's own log fell silent for over 15 min 10 times and for over an hour twice in the same 38 hours (idle, asleep or restarting; a log cannot tell which). Whenever such a silence is the collector's own sleep, a plain age would mark every host `LOST` on waking; the observer clock prevents that |
| `on_battery`, `reboot_unknown_due` | true, "note" | no battery or reboot inputs in the history; design values |

Set them pool-wide in `[drain]` and per host with `drain = { ... }` ([hosts-toml.md](hosts-toml.md)). The ages must
grow: `late_after_beats` × `beat_interval_s` < `stale_after_s` < `lost_after_s`, which `lodgings hosts check` enforces.

Beats come from a Lodgings checkout and a `hosts.toml` on each host that sends them: probing needs nothing installed
there, beating does. A scheduled job that beats once a minute reads `STALE` under the defaults (`stale_after_s = 60`, sized
for beats every 15 s). For one beat a minute set `beat_interval_s = 60`, `late_after_beats = 2`, `stale_after_s = 180` and
`lost_after_s = 900`; `lodgings hosts check` accepts that set, and the hosts then read `UP`.

## Back-off and paced beats

The tools should cost the machine as little as possible, and a host that is busy or running on a low battery should not
spend its beats proving it is alive. `lodgings beat --every` therefore measures itself before each beat and, when the
machine is strained, lengthens its own interval. It never stays silent about it: the beat it does send says so.

| Strain | Limit (default) | Interval grows |
| --- | --- | --- |
| load | the 5-minute load average per logical core is above 1.0 (above 2.0, above 4.0) | 2 times (4, 8) |
| battery | on battery below 40%, or on battery with the charge unknown | 4 times |
| cpu | the loop (its measuring and its delivery command) used more than 10 CPU-seconds in the last hour | 2 times |

The largest factor wins. A paced interval is never below 5 s or above 120 s (a host that would need more stays at 120 s,
well inside `lost_after_s`). It returns to normal once the load is under 0.75 per core, so it does
not flap at the line. Windows has no load average, so a Windows loop paces on battery and CPU only; an unreadable load
is unknown, never zero. Set the limits with `--pace-load PER_CORE`, `--pace-battery PERCENT` and `--cpu-budget SECONDS`;
`--no-pace` turns the back-off off. A beat that is not part of a loop (one-shot, or a goodbye) is never paced:
whatever schedules it owns its interval, and a new process every 15 s costs about 13 CPU-seconds an hour where `--every`
costs 0.2 (README, "Resource budget").

A paced beat carries what it did, and the interval it will now keep:

```json
{"schema": "lodgings.beat/1", "host": "...", "interval_s": 60, "paced": {"reason": "load 2.3 per core (limit 1)", "factor": 4}, ...}
```

`paced.factor` is greater than 1, `paced.reason` is text for people, and `interval_s` is required on a paced beat. A beat at
its normal pace has no `paced` field, which is how the pool sees recovery.

**How the pool reads it.** A paced beat is healthy but slow, never late: the host is judged against the interval the
beat announced. `LATE` after `late_after_beats` of those, `STALE` after twice that (and never later than `lost_after_s`),
`LOST` at `lost_after_s` as for any host. A paced host reads `UP` with `slowed on purpose` first in its reasons (`lodgings states`
text and `--json`, where `paced` holds `{reason, factor, interval_s}`), until its next beat
arrives at the normal pace. `lodgings thresholds` judges the gap after a paced beat by the same rule and counts such gaps
(`paced_gaps`).

**The probe.** `lodgings watch` waits, in five-minute steps and at most an hour, while the machine is strained by the same
load and battery limits, says so once, and then probes anyway. `lodgings watch --once` and `lodgings probe` are run by a
person or a scheduler and are never delayed.

## Files

```
<state>/hosts/<name>/beats.jsonl        received beats and goodbyes (the last 500 are kept)
<state>/hosts/<name>/events.jsonl       canaries, placements, jobs, lanes (the last 5000)
<state>/hosts/<name>/drain.json         an operator's drain request
<state>/hosts/<name>/transitions.jsonl  state changes seen by `lodgings states` (the last 200)
<state>/observer.json                   this machine's clocks at the last look
<state>/ledger/lodgings.jsonl             the value ledger, append-only (docs/ledger.md)
<state>/ledger/coverage/<name>.jsonl    what each ledger run saw of a host's beats (the last 15 days)
```

Appends take a lock directory next to the file, so several `lodgings record` processes can write at once.

## Not in this slice

- Acting on a state: no runner reads it yet; wiring a scheduler to `lodgings states --json` is a later slice, as is
  requeueing jobs automatically.
- Measuring a Mac's pending updates and Windows' restart time; both are reported as unknown.
- A cross-host transport: records reach the collector through whatever you give `lodgings beat` (ssh, a shared file).
