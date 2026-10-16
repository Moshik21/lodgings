# Safety model

## What each command writes

| Command | Origin repository | Origin gate | Origin worktree | Target repository | Target gate | Files |
| --- | --- | --- | --- | --- | --- | --- |
| `export` | nothing | nothing | nothing (git runs with `GIT_OPTIONAL_LOCKS=0`) | - | - | the new `<out>/<task>-<seq>/`; a scratch repository in the system temp dir, deleted on exit |
| `verify` | - | - | - | nothing | - | a scratch repository when no `--repo` is given, deleted on exit |
| `import` | - | - | - | `refs/lodgings/import/<task>/<seq>/*` (non-forcing fetch), `refs/heads/<branch>` (create-only), one new worktree | nothing (read) | `<passport>/after/import-receipt.json`, `after/resume.md` |
| `confirm` | `refs/lodgings/archive/<task>/<seq>/*` (create-only / non-forcing fetch) | nothing (read) | nothing | - | - | `<passport>/after/confirm.json` |
| any | | | | | | one line in `<state>/actions.jsonl` |

Every command does all its checks first, in a scratch repository, and writes only after they pass. A refusal (exit 3) or an invalid passport (exit 4) changes nothing. The one exception is a failed post-import verification, which is reported after the additive writes and lists them in the receipt.

## Never, by construction

- No `--force`, no `+` refspec, no `update-ref -d`, no `reset`, no `checkout` of an existing worktree, no `stash`, no `gc` or `prune`.
- No shell: every subprocess is an argv list. Config cannot add commands; only the read-only custody check is configurable, and only as an argv list.
- No network: local paths only. Shipping the passport between machines is the caller's choice of transport.
- No secrets: records, the pipeline receipt, decisions and the custody output are scanned with `secret_patterns`. A hit refuses, and the message names the file, the line and the pattern number, never the text.
- No silent adoption: the gate head replaces the lane head as the resume head only when `--adopt-gate-head` is named and the trees are identical, or the exact difference was listed and confirmed with its token. The lane head stays in the bundle, the import refs and the archive refs either way.
- No teardown: `confirm` writes a tombstone and archive refs. Removing the origin worktree and branch is left to your normal teardown and its own landed-work checks.

## Refusals and what to do

| Refusal | Why | Do |
| --- | --- | --- |
| worktree has uncommitted paths | a move happens at a clean checkpoint | commit work in progress as a normal commit (never stash) |
| worktree HEAD is not the lane branch head | the spec names the wrong branch or worktree | fix the spec |
| pipeline step is running/fixing | its custody belongs to the step | wait for the gate or a terminal state |
| custody `behind` / `diverged` | the gate holds work the lane lacks (a squash or rebase of the lane by the gate, or real gate-only work) | take the gate's commits into the lane on the origin (the gate tool's custody return), `--adopt-gate-head`, or decide by hand which head is the lane |
| `--adopt-gate-head`, trees differ | the lane's own commits would not be resumed | read the listed commits and paths; repeat with `--confirm-differences TOKEN` |
| the pipeline holds commits the lane lacks | an unpushed fix round | let the run push its fix, or return custody to the lane |
| record looks like it holds a secret | secrets never travel | remove it from the record |
| target branch exists with other content | never overwrite | `--branch <new name>` |
| target gate holds a head the resume head does not descend from | the target gate has its own history | decide by hand |
| confirm: origin lane moved after export | its new commits would be stranded | export a new passport |
| confirm: origin gate moved after export | a pipeline ran on the origin | export a new passport |

## Kill switch and log

- `<state>/off` (a file) or `LODGINGS_PASSPORT=off`: every command prints `off · kill switch (...)`, exits 0 and does nothing else.
- `<state>/actions.jsonl`: one JSON object per call, `{at, verb, argv, host, verdict, task, seq, passport_id, custody, reason}`, refusals included.
- `<state>` is `--state`, else the `state_dir` config value, else `$LODGINGS_PASSPORT_STATE`, else the platform default: `~/.local/state/lodgings/passport` on Linux (or `$XDG_STATE_HOME`), `~/Library/Application Support/lodgings/passport` on macOS, `%LOCALAPPDATA%\lodgings\passport` on Windows.
