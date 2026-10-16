# The pool's value ledger

No tool earns its place unmeasured. `lodgings ledger` writes the pool's own append-only ledger, so what the pool did can be
read as numbers a reviewer can recount, not as stories. It follows the tool value ledger's v1 line (the contract lives with
whoever owns the ledger; a reader of it sees these lines as the `lodgings` tool's).

It reads only records the pool already holds (`hosts/<name>/events.jsonl`, `beats.jsonl`, `transitions.jsonl`,
`drain.json`), adds no process, probes nothing and sends nothing. Besides the ledger it keeps one small file of its own
per host, the coverage record (below).

**The one requirement: run it at least every hour.** `beats.jsonl` keeps only the last few hours of beats, so each run
writes down what it sees of them, and a day is read from those rows. A day the writer did not look at often enough is
unknown, never a guess. A scheduled line, with placeholders:

```
17 * * * * lodgings ledger --hosts <hosts.toml> --state <state-dir>
```

```
lodgings ledger [--days N] [--dry-run] [--now EPOCH]
```

It reads the last N closed UTC days (default 1, at most 14) and appends their lines to `<state>/ledger/lodgings.jsonl`. A
day is closed for a host once the time is past the day's end by max(300 s, twice the largest `interval_s` its beats
announced then); a day still open, or a host whose day is not closed yet, is never written, and a later run reads it.
A line whose id the file already holds is left alone, so running it again adds nothing; a line is never rewritten. A day
already held is only superseded by a better reading (see [Corrections](#corrections)).

## What is counted, per host, per day

One line is one counter on one host over one closed day. The host is the pool host's name in `hosts.toml`: where the work
was placed, and whose records were read.

| Counter | Counts | From |
| --- | --- | --- |
| `jobs_placed` | placements with `outcome: placed` | `events.jsonl` |
| `placements_fell_back` | placements with `outcome: fallback` (work meant for this host that ran elsewhere) | `events.jsonl` |
| `jobs_finished` | job events with `phase: end` | `events.jsonl` |
| `queued_s_sum`, `jobs_waited_60s` | seconds a job waited for a slot, and how many waited at least 60 s | job `start` events that carry `queued_s` |
| `gate_refused`, `gate_unchecked`, `gate_checked` | placements the toolchain gate refused, could not compare, or compared (`passed` or `refused`) | placement events that carry `gate` |
| `drain_transitions` | a host entering `DRAINING` or `DRAINED` (`lower-bound`: a change is written only when `lodgings states` runs) | `transitions.jsonl`, with `beats.jsonl` for coverage |
| `drain_requests` | an operator's standing request made in the day (`lower-bound`) | `drain.json` |

`placed` says a job was placed on the host. It is placement activity, not proof that placement beat running locally.
`gate_checked` against `gate_unchecked` is the gate's coverage: how often it could compare at all.

Two optional fields are new in `lodgings.event/1`, so a runner that has them can say them: `queued_s` on a job `start`
(seconds the job waited for a slot) and `gate` on a placement (`passed`, `refused` or `unchecked`). A runner that does not
send them leaves the counters that need them unknown, never zero.

## What is not counted, and what would make it measurable

Each of these is written as a line with `value: null`, its reason in `unknown`, and in `note` the one field that would
make it measurable, so its absence is visible and is never read as a quiet day.

| Counter | `unknown` | The one field |
| --- | --- | --- |
| `jobs_placed_elsewhere` | `field-absent` | a `submitter` on placement events |
| `offers`, `offers_moved` | `field-absent` | an `offer_id` on every evacuation offer, carried into the lane passport's actions row. An offer is printed and not recorded, so an offer and a move cannot be linked |
| `drains_completed` | `field-absent` | a `kind: "drain"` event with `phase: complete` |
| `placement_minutes_vs_local` | `not-measurable` | none: it would be a counterfactual, and no record holds one. Observed waits (`queued_s_sum`) and run times are reported as what they are, beside each other |

`drain_requests` is a floor and says so: `drain.json` holds only the standing request, a repeated request overwrites the one
before and a withdrawn one leaves nothing. A day with no standing request made in it is unknown, not zero; the field that
would count them all is a `kind: "drain"` event with `phase: request` (and `cancel`).

**A host that sends no beats cannot show a drain, or a day.** The drain counters of a host with no `beats.jsonl` are
written as unknown (`field-absent`) with the note "no beats received from this host, so no state change is seen and a drain
cannot be measured", and its events counters with the note "no beats received from this host, so how much of the day its
records cover is not known". A host with no records at all is `no-state-dir`.

## How a number is trusted

- **Coverage from beats.** How much of a day a host's records cover is read from the continuity of its beats, never from
  when its events happened: a quiet host sends beats and no events. `beats.jsonl` keeps the last 500 to 1000 beats (two
  to four hours at the default 15 s), so the writer keeps its own record, `<state>/ledger/coverage/<host>.jsonl`: each
  run (not a dry run) first appends one row per host with the time it looked (`looked_at`), the first and last
  `received_epoch` of the beats it can see then (`first_at`, `last_at`), the gaps between them longer than max(3 x the
  `interval_s` the earlier beat announced, 120 s) (`gaps`; a beat that announces none is judged by the pool's
  `beat_interval_s`), whether the last beat was a goodbye (`asleep`) and the largest interval announced
  (`interval_max`). The span after a goodbye is an expected absence, as `lodgings states` reads it as ASLEEP, not a gap; when
  the last beat is a goodbye the row covers up to `looked_at`. A day's coverage is the union of the rows' spans; a hole of
  120 s or less between two rows is not a gap. The rows are kept 15 days, longer than `--days` can reach, written
  atomically under the ledger's lock, and a coverage file that cannot be read stops the run like the ledger does.
- **Record range.** `coverage` holds `first_at` and `last_at` (the start and end of the union of the coverage rows), `rows`
  (records of the kind in the day) and `covers_window`, the fraction of the day inside the union, never rounded up.
- **Digest.** `src` names the file (a bare name), the filter in words, the first and last counted record
  (`received:<epoch>`) and `digest`: `sha256:` of the exact lines counted, each without its newline, joined with `\n`, in
  file order. Re-running the filter on the same file gives the same digest, or shows that the file changed.
- **Zero.** When the union covers the whole day and the closing margin after it, every events counter is an exact count,
  and 0 is an exact zero. With a hole in it, every events counter and `drain_transitions` is `value: null`,
  `unknown: source-truncated`, and the note says `no coverage between T1 and T2` (the epochs of the first hole); when no
  row reaches into the day at all, the note says `writer did not observe this day`. Never a lower bound because of
  coverage.
- **Replays.** With `--now EPOCH`, only the beats, events and transitions received by then are read, and the coverage row
  is the one a run at that time would have written.
- **Lower bound.** `quality: lower-bound` is left only for rows that lack an optional field (`queued_s`, `gate`), named in
  `excluded` and `note`, and for `drain_transitions` and `drain_requests`, whose records are floors. A count that would be 0
  only because some rows lack the field is `value: null` (`field-absent`).
- **Words.** Counter names and notes use the record's own verbs: placed, refused, finished, fell back. Nothing says a person
  acted because of the pool or what would have happened without it.

## Corrections

A run reads its days again. When the newest line the ledger holds for a counter, host and day is `value: null` because of
a hole in its coverage (`source-truncated`), or a lower bound because the coverage held part of the day, and this run reads a
number that differs from it, the run appends a new line with the id `counter@host@from#2` (then `#3`, ...) whose `supersedes`
names the id of the newest earlier line. The newest superseding line wins; the earlier one stays in the file. A line held
for any other reason is never superseded, and the same reading is never appended twice. Beats can reach the pool after a
day is written (a host that buffered them and sent them later with their own times), and a later run records them, so
`--days N` can fill a day in.

## The file

`<state>/ledger/lodgings.jsonl` is append-only: each run reads the whole file, adds only lines whose id it does not hold (and
the corrections above), and
replaces the file in one atomic step (a temporary file in the same directory, flushed, then renamed over it), under a lock
directory like the record files. **A ledger it cannot read is never overwritten.** If the file is not valid UTF-8, has a
line that is not a JSON object with an `id`, or ends in a torn line, `lodgings ledger` writes nothing, changes nothing, says
which line, and exits 1; move the file aside or repair it by hand. Lines carry no ids, titles, paths or host details beyond
the host's alias.

## What it costs

One run reads the three small record files and the coverage record of each host, appends one coverage row per host (about
110 bytes; 24 rows a day, kept 15 days, so about 40 KB per host) and, once a day, about 15 ledger lines per host (about 500
bytes each, so about 7 KB per host per day, or about 2.7 MB per host per year). Measured on one machine (WSL2 on Windows
11, 24 logical cores, Python 3.14, a load of about 0.05 per core), 15 runs of the command timed from outside with
`time.perf_counter` and CPU read from `getrusage` of the finished children (user plus system), three hosts with 1700
recorded events, 800 beats and 15 days of hourly coverage rows each:

| | Wall | CPU |
| --- | --- | --- |
| one run, no ledger yet | 0.095 s median, 0.102 s at most | 0.094 s |
| a run that adds no ledger line, over a year of lines (16,470 lines, 8.7 MB) | 0.182 s median, 0.190 s at most | 0.181 s |
| the day appended to that year of lines | 0.191 s | 0.190 s |
| `lodgings states` over the same records, for comparison | 0.075 s median | |

Run every hour, that is about 4.4 s of CPU a day with a year of lines. It holds nothing in memory between runs and has no
idle cost. Because the ledger is replaced whole, the cost of a run grows with the ledger (about 90 ms a year here).
Other platforms are not measured.
