# Command line

```
lodgings-passport export  [--lane SPEC] --out DIR [--seq N] [lane flags] [common]
lodgings-passport verify  PASSPORT [--repo TARGET] [common]
lodgings-passport import  PASSPORT --repo TARGET --worktree PATH [--branch NAME]
                        [--gate-git-dir DIR [--gate-ref REF]] [--custody-check JSON-ARGV] [common]
lodgings-passport confirm PASSPORT --repo ORIGIN [--receipt FILE] [--gate-git-dir DIR [--gate-ref REF]]
                        [--no-archive] [common]

common: --config FILE  --set NAME=VALUE (repeatable)  --state DIR  --json
lane flags: --task --project --repo --worktree --branch --base --pipeline --decisions
            --gate-git-dir --gate-ref --pipeline-head --archive-ref PATTERN (repeatable)
            --record NAME=PATH (repeatable) --pr-url --pr-head --custody-check JSON-ARGV
            --adopt-gate-head [--confirm-differences TOKEN]
```

On Windows, run it as `py -3 core\passport\bin\lodgings-passport ...`.

## Output

Plain text is one line per result, fields separated by ` · `, plus indented `note:` lines (warnings) or one `how:` line (what to do after a refusal):

```
exported · jam-1 · lane 197c42deb05a · custody contained · 2 ref(s) · 2 record(s) · pipeline fixture · 5024 bytes · /path/passports/jam-1
refused · custody diverged: gate head 2f1c... holds changes that lane head 9a0b... does not (merge changes the tree)
  how: the gate holds work the lane head does not contain; moving would strand it. ...
```

A refusal's `how:` line can run to several lines: the numbered ways out, or the commits and paths an `--adopt-gate-head` would replace, then the exact flags to run again with:

```
refused · custody diverged: neither side is ahead: lane head 2281e49e0d42 has 2 commit(s) the gate lacks, gate head 9409b8e4b156 has 2 commit(s) the lane lacks (merge base cccdb877e327); every change of the lane is already in the gate head, so the gate holds a squash or a rebase of this lane (it may also hold newer base commits)
  how: nothing was changed. Options:
       1. the gate head holds every change of the lane ...: export again with --adopt-gate-head. ...
       2. or keep the lane's own history: return the gate's head to the lane on the origin ...
       3. or decide by hand which head is the lane, then export again
```

`--json` prints one object instead: the full result, or `{verdict, reason, hint, evidence, exit}` on failure. No colour is ever printed, and text taken from a repository or a passport (a commit subject, a file name) shows control characters, escape sequences, and invisible or direction-changing characters (zero-width, bidirectional, BOM) as `\xNN` (`\uNNNN` above U+00FF), never raw, in the terminal output and in `after/resume.md`; the `--json` strings escape them as JSON does. git's output is read as UTF-8 whatever the locale (a byte that is not UTF-8 shows as U+FFFD), and a character the output's own encoding cannot hold, or would write as a byte in 0x80-0x9f, is escaped the same way.

## `export --adopt-gate-head`

Resume the lane at the review gate's head instead of the lane head, for a gate that holds a squash or a rebase of the lane ([custody.md](custody.md#adopting-the-gate-head)). Without `--confirm-differences`, it adopts only when the gate head's tree equals the lane head's tree. When they differ it exits 3, changes nothing, lists what only the lane holds and what differs, and prints the token. Run the same command again with `--adopt-gate-head --confirm-differences TOKEN` to adopt exactly that. `--confirm-differences` without `--adopt-gate-head` is a usage error (exit 2). The same two settings in a lane spec are `adopt_gate_head` and `confirm_differences`.

`export` does not need a worktree: omit `--worktree`, or name one that no longer exists, and the lane branch is read from the repository. If a named path is gone but the branch is checked out somewhere else, that checkout is the one checked for uncommitted work.

## Exit codes (stable)

| Code | Verdict | Meaning |
| --- | --- | --- |
| 0 | `exported`, `verified`, `imported`, `confirmed`, `already-confirmed`, `off` | done (or the kill switch is on) |
| 2 | `usage` | bad flags, missing spec fields, unknown config key |
| 3 | `refused` | unsafe; nothing was changed |
| 4 | `invalid` | the passport is damaged or tampered with, disagrees with its receipts, or the target cannot take it (missing base) |
| 5 | `error` | environment: git missing, older than 2.38, or a git call failed |

## A move, step by step

1. **Park** the lane: commit work in progress, and let any running pipeline step reach its gate.
2. **Export** on the origin: `lodgings-passport export --lane lane.json --out passports`.
3. **Ship** `passports/<task>-<seq>/` to the target, any way you like.
4. **Verify** on the target: `lodgings-passport verify passports/<task>-<seq> --repo <clone>`. If it reports a missing base, fetch the base branch into the clone first.
5. **Import** on the target: `lodgings-passport import ... --repo <clone> --worktree <new path>`. Read `after/resume.md`.
6. **Resume** on the target: start the agent from the brief plus `resume.md`, and start a fresh pipeline run.
7. **Confirm** on the origin: ship `after/import-receipt.json` back next to the passport and run `lodgings-passport confirm ... --repo <origin clone>`. Then tear the origin lane down through your normal path.
