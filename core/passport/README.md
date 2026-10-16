# Lodgings lane passport

`lodgings-passport` moves one unit of work (a **lane**: a branch, its worktree, its review-gate custody, its pipeline history, its brief and decisions) from one machine to another **at a checkpoint**, as a sealed, content-addressed directory: the **passport**.

It is part of Lodgings, a toolkit for running one pool of work across several personal machines. The rule behind it: **move state at checkpoints, never processes.** Nothing that is running gets migrated. What travels is git objects and text receipts, and the target starts fresh from them.

**What it is.** Four commands: `export` (origin, read-only), `verify` (anywhere), `import` (target), `confirm` (origin, after the target reports). They replace the hand ritual of bundles, scratch repositories, hand-made descendant commits and archive refs that a lane move takes today.

**What it is not.** It does not copy files over the network (ship the passport directory any way you like: `scp`, `rsync`, a USB stick). It does not start agents, run pipelines, merge, push to any remote, or tear the origin lane down. Those stay with your existing tools.

## Five-minute quickstart

You need git 2.38 or newer and Python 3.8 or newer. Nothing to install: the tool is standard-library Python.

```sh
python3 examples/demo /tmp/passport-demo      # builds a tiny two-machine setup and prints the commands below
cd /tmp/passport-demo
HP="python3 /path/to/core/passport/bin/lodgings-passport"

$HP export --task demo --repo origin/repo --worktree origin/wt --branch lane/demo --base main \
    --gate-git-dir origin/gate.git --record brief.md=brief.md --out passports --state state
$HP verify  passports/demo-1 --repo target/repo --state state
$HP import  passports/demo-1 --repo target/repo --worktree target/wt --gate-git-dir target/gate.git --state state
$HP confirm passports/demo-1 --repo origin/repo --gate-git-dir origin/gate.git --state state
git -C target/wt push ../gate.git lane/demo     # the review gate fast-forwards
```

The demo reproduces the commonest failure of a move: the review gate still holds the lane's first head, the lane was rebased since, and the gate rejects the rebased head as a non-fast-forward. The passport sees that every change in the gate head is already in the lane (`custody contained`) and makes one bridge merge on the target. The bridge has the lane's exact tree and both heads as parents, so the gate fast-forwards and nobody makes a descendant commit by hand.

Sample output:

```
exported · demo-1 · lane 8987ac10f61b · custody contained · 2 ref(s) · 1 record(s) · pipeline none · 3780 bytes · passports/demo-1
  note: custody 'contained': the target will add a bridge merge so the gate fast-forwards
verified · demo-1 · lane 8987ac10f61b · custody contained · 4 check(s) ok · target ready · sha256:eb9115cd29e0
imported · demo-1 · branch lane/demo at ec05a1dafcae · custody contained (bridge ec05a1dafcae) · worktree target/wt · receipt passports/demo-1/after/import-receipt.json
confirmed · demo-1 · 2 archive ref(s) · target <host> at ec05a1dafcae · origin lane, worktree and gate untouched
```

Add `--json` to any command for the full record.

### When the review gate squashed or rebased the lane

A review gate that squashes or rebases a PR leaves its own head, which the lane's history does not contain, and the lane's commits are not in the gate's history either. The passport refuses that by default (`custody diverged`), says which side is ahead and by how many commits, and changes nothing. The demo builds it:

```sh
python3 examples/demo /tmp/passport-squash squashed-gate     # or: rebased-gate
```

```
refused · custody diverged: neither side is ahead: lane head bc75f9c2b4c3 has 2 commit(s) the gate lacks, gate head 839b3b985fa1 has 2 commit(s) the lane lacks (merge base 91de459886b4); every change of the lane is already in the gate head, so the gate holds a squash or a rebase of this lane (it may also hold newer base commits)
  how: nothing was changed. Options:
       1. ... export again with --adopt-gate-head ...
$HP export ... --adopt-gate-head
refused · adopt-gate-head: the gate head's tree differs from the lane head's in 1 path(s) and the lane holds 2 commit(s) the gate lacks
  how: nothing was changed.
       commits only the lane holds (2): ...   paths whose content differs, lane -> gate (1): differs  app.txt
       to adopt the gate head with exactly these differences, run the same command again with: --adopt-gate-head --confirm-differences fb772a64431eec0a
$HP export ... --adopt-gate-head --confirm-differences fb772a64431eec0a
exported · demo-1 · lane bc75f9c2b4c3 · custody diverged (adopted gate head, differences confirmed) · ...
```

`--adopt-gate-head` is never automatic. It adopts without a token only when the lane head's tree equals the gate head's tree. Otherwise it lists what only the lane holds and what differs, and the token binds the confirmation to exactly that. The lane's own commits stay in the passport and are archived by `confirm`; the target's branch starts at the gate head, so the gate fast-forwards. Details: [docs/custody.md](docs/custody.md#adopting-the-gate-head). The fixture run `python3 tests/e2e.py --work /tmp/passport-e2e` replays every real case together with its unsafe twin (below).

## What it will never do

- **Write the origin before `confirm`.** `export` reads the lane repository and the gate through a scratch repository that borrows their objects. Git is run with `GIT_OPTIONAL_LOCKS=0`, so even `git status` leaves the lane's index alone.
- **Force, delete or rewrite a ref, anywhere.** Every ref write is create-only (`update-ref <ref> <sha> 0000…`) or a non-forcing fetch. A ref that exists with another value is a refusal.
- **Discard work.** A dirty worktree, a gate or pipeline head holding commits the lane lacks, or an origin lane that moved after export all refuse, and the refusal says how to resolve it.
- **Overwrite a branch or a worktree on the target.** An existing branch with other content refuses; the worktree path must not exist yet.
- **Abort or drive a pipeline.** A pipeline step that is executing right now refuses the export; the run must park at a gate first.
- **Carry secrets.** Records are scanned against secret patterns before they are sealed. A hit refuses the export without echoing the match.
- **Call the network or phone home.** No telemetry and no outbound traffic.

Full write table per command: [docs/safety.md](docs/safety.md).

## Output safety

Text taken from a repository or a passport (commit subjects, file names, branch names) is printed escaped, never raw:
control characters, terminal escape sequences and invisible or direction-changing characters appear as `\xNN`
(`\uNNNN` above U+00FF), so a hostile name cannot drive or spoof your terminal; what is decided and recorded is not
changed by it. Git's output is decoded as UTF-8 on every platform, whatever the locale or Windows code page (a byte that
is not UTF-8 shows as U+FFFD). Details: [docs/cli.md](docs/cli.md).

## The passport

```
<task>-<seq>/
  manifest.json   schema lodgings.passport/1: task, seq, origin, base, every ref with its sha, lane head and tree, custody
  refs.bundle     one git bundle: lane head, gate head, pipeline head, PR head, archive refs; prerequisite = the base
  custody.json    lane head vs gate head vs pipeline head, the rule that relates them, the gate's own check as evidence
  pipeline.json   receipt of the last pipeline run: steps, rounds, findings, parked or not
  decisions.json  questions asked and their answers
  records/        brief, status log, backlog row: whatever text the lane spec names
  SHA256SUMS      every sealed file; the passport id is sha256(SHA256SUMS)
  after/          import-receipt.json, resume.md, confirm.json (written later, never sealed)
```

The format is specified in [docs/format.md](docs/format.md), and the custody rules in [docs/custody.md](docs/custody.md).

## Commands

| Command | Where | Writes | Exit codes |
| --- | --- | --- | --- |
| `export --lane spec.json --out DIR [--adopt-gate-head [--confirm-differences TOKEN]]` (or flags) | origin | the new passport directory only | 0, 2, 3, 5 |
| `verify PASSPORT [--repo TARGET]` | anywhere | nothing | 0, 2, 4 |
| `import PASSPORT --repo TARGET --worktree PATH` | target | `refs/lodgings/import/<task>/<seq>/*`, the lane branch if absent, one new worktree, `after/` | 0, 2, 3, 4, 5 |
| `confirm PASSPORT --repo ORIGIN` | origin | `refs/lodgings/archive/<task>/<seq>/*` (create-only), `after/confirm.json` | 0, 2, 3, 4 |

Exit codes: `0` done, `2` usage, `3` refused (unsafe, nothing changed), `4` invalid (damaged or tampered passport, or a target that cannot take it), `5` environment (git missing or failing). Details are in [docs/cli.md](docs/cli.md).

Every command appends one JSON line to `<state>/actions.jsonl`, refusals included. Two kill switches make every command exit 0 and do nothing: a file `<state>/off`, or `LODGINGS_PASSPORT=off` in the environment.

## Cases proven end to end

`tests/e2e.py` builds throwaway repositories and drives only the public CLI. Last recorded run: 109 of 109 checks (macOS and Windows native, 2026-10-15).

| Case | Expected | Unsafe twin (refused, nothing changed) |
| --- | --- | --- |
| The gate holds an older head; the lane was rebased away from it | `contained`; the target bridges; the gate fast-forwards | the gate holds work the lane lacks (`diverged`) |
| The gate holds a squash, then a rebase, of the lane on a newer main | `diverged`, refused by default with both counts and the ways out; `--adopt-gate-head` lists the lane-only commits and the differing paths, adopts with the confirmation token, and the gate fast-forwards from the adopted head | a token for another head, a lane that moved after the token, a re-sealed passport whose digest does not match the objects, adopting when the gate holds no head |
| A lane branch with no worktree | export reads the branch from the repository and records that no worktree was checked | a missing path does not hide a dirty checkout of the branch elsewhere |
| A lane mid-pipeline, parked at a gate after a fix round | the run, rounds, open findings and decisions travel; `resume.md` lists them | a step is executing; the pipeline holds an unpushed fix the lane lacks (`behind`) |
| A lane with archive refs, one already inside the base | every ref lands under the import prefix with its exact value | - |
| Every write | the origin stays byte-for-byte unchanged until `confirm`, which only adds archive refs | dirty tree, secret in a record, tampered passport, a re-sealed hostile passport, an existing target branch, a target missing the base, `confirm` without a receipt or after the origin moved, kill switch |

## Integrations

The core knows nothing about any particular agent supervisor or review tool. An adapter turns a tool's own records into a lane spec ([docs/format.md](docs/format.md#lane-spec-lodgingslane1-the-input-to-export)) and calls this command.

## Support matrix

| Platform | Python | Run here |
| --- | --- | --- |
| Linux (Ubuntu on WSL2, kernel 6.18) | 3.11.15, 3.12.13, 3.14.4 | smoke, fixture run, adapter run, live read-only export |
| Python 3.8 to 3.10 | - | grammar checked only (`ast.parse(feature_version=(3, 8))`); not run |
| macOS 27 (Apple Silicon) | 3.14.6 | smoke, the demo loop (export, verify, import, confirm) and the end-to-end checks (109 of 109) of 2026-10-15; the host was under heavy load, so its timings are not a budget measurement |
| Windows 11 native | 3.12.4 and 3.12.10 | smoke and the end-to-end checks (109 of 109) of 2026-10-15, and `export` exits 0. The first run there failed at once (exit 5), which found two defects, both fixed: the CR line ends below, and git's output read with the Windows code page instead of UTF-8. The demo loop was not run on Windows |

The code uses `os.path`, argv lists, `os.replace` and git's own path handling. The Windows failure was a text-mode write
of a file git reads (the alternates file of its scratch repository, which got a CR at each line end); it is written with LF
line ends now, and it was the only file the code writes that git reads. Python 3.8 to 3.10 is not run. On Windows native,
`py -3 core\passport\bin\lodgings-passport export ...` exited 5 before that fix and exits 0 after it.

## Budgets

| What | Budget | Measured |
| --- | --- | --- |
| Passport for a typical lane | under 5 MB plus objects the target lacks (warns above `warn_passport_bytes`) | 3.8 to 5 kB on fixtures; 106 kB for a live lane with a full status log |
| export / verify / import / confirm | under 2 s each, excluding the worktree checkout | 50 to 140 ms on fixtures. A Mac under heavy load (2026-10-15): export 1.3 s, verify 0.4 s, import 2.6 s including the checkout, confirm 0.7 s; an unloaded Mac is not measured |
| Memory | under 50 MB RSS for the tool itself | git's checkout of a large tree dominates import |

## Project

- [CHANGELOG.md](CHANGELOG.md) (Keep a Changelog, semver from 0.1.0; schema versions are separate from the tool version)
- [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), [LICENSE](../../LICENSE) (MIT, the repository licence)
- `scripts/self-audit`: refuses personal names, hosts, paths, addresses and secrets in the product tree, using a denylist kept outside the repository
- `tests/smoke.py` (the three-OS smoke) and `tests/e2e.py` (behavioural fixture run, through the CLI only)
- `ci/passport.yml.disabled`: CI for a public repository, kept disabled until publication
