# Troubleshooting

Every message below is real output from the fixture and smoke runs, except the git-version error, which is quoted from the code.

**`refused · worktree /path/wt-dirty has 1 uncommitted path(s): park the lane first`**
Commit work in progress as a normal commit, then export again. Never stash: a stash does not travel.

**`refused · pipeline step 'test' is fixing right now: a lane moves only at a checkpoint`**
A fix agent or a test run owns the lane at this moment. Wait until the run parks at its gate (the receipt's `run.parked` becomes true) or reaches a terminal state.

**`refused · custody behind: the gate is ahead: gate head 1a2b... is 2 commit(s) ahead of lane head 3c4d..., which is its ancestor`**
The gate holds pipeline commits (a fix round) the lane does not. Bring them into the lane on the origin with your review tool's custody return (for example a guarded fast-forward of the lane to the gate head), then export again. Or export with `--adopt-gate-head`: the lane head is an ancestor of the gate head, so no commit is lost.

**`refused · custody diverged: neither side is ahead: lane head 9a0b... has 2 commit(s) the gate lacks, gate head 2f1c... has 2 commit(s) the lane lacks (merge base 7e8d...); every change of the lane is already in the gate head, so the gate holds a squash or a rebase of this lane`**
The review gate squashed or rebased the PR (the commonest cause, on a base that moved), so the gate head and the lane head hold the same work under different histories. Export again with `--adopt-gate-head`: it lists the lane's own commits and the paths that differ and prints a `--confirm-differences` token; run it again with that token. See [custody.md](custody.md#adopting-the-gate-head).

**`refused · custody diverged: neither side is ahead: ... the gate head holds changes the lane head does not (merge conflicts)`**
The gate holds work the lane lacks, and the lane holds work the gate lacks. A person decides which head is the lane. Bring the gate's work into the lane first, or adopt the gate head knowing the lane-only work is listed, carried and archived, but not resumed.

**`refused · adopt-gate-head: the gate head's tree differs from the lane head's in 1 path(s) and the lane holds 2 commit(s) the gate lacks`**
Nothing was changed. The lines under `how:` are the lane-only commits and the differing paths. If that is what you want, run the same command with `--adopt-gate-head --confirm-differences <token>`. A token from an earlier run stops matching when either head moves (`the --confirm-differences token is for another head or difference`): read the new list and use the new token.

**`refused · --adopt-gate-head: the review gate holds no head for this lane, so there is nothing to adopt`**
No gate was named, or it has no ref for the lane. Export without the flag.

**`invalid · the adopt record does not match the objects: ...`** (import)
The passport says it adopts a gate head, and the objects it carries do not give the difference that was confirmed. Export again from the origin.

**`refused · the pipeline holds commits the lane lacks (pipeline head ... is behind)`**
A fix round was committed in the pipeline's worktree but not pushed. Let the run push, or return custody to the lane first.

**`invalid · passport seal is broken: checksum mismatch: records/brief.md`**
The passport changed after export: tampering, a bad copy, or a hand edit. Export again from the origin; never edit a sealed passport.

**`invalid · target /path/repo cannot take this bundle: ... lacks these prerequisite commits ...`** with **`how: the target lacks the base commit 634deb2bca3d; fetch the base branch there first`**
The target clone is older than the lane's base. Run `git fetch` there (for example `git fetch origin main`) and verify again.

**`refused · target branch lane/x already exists at 634deb2bca3d, not the resume head ec4971374017`**
Import under another name with `--branch lane/x-moved`, or inspect the existing branch by hand.

**`refused · the origin lane gained commits after export (ec4971374017 -> 191075269e5e); they would be stranded`**
Someone kept working on the origin after export. Export a new passport (the next `seq`) and import that one instead.

**`refused · no import receipt: the target has not confirmed an import of this passport`**
Copy the target's `after/import-receipt.json` into the origin's copy of the passport (`after/`), or pass `--receipt`.

**`error · git 2.38 or newer is required (found 2.34)`**
`git merge-tree --write-tree` (the content test behind `contained`) needs git 2.38.

**`off · kill switch (<state>/off exists): did nothing`**
Remove the file, or unset `LODGINGS_PASSPORT=off`.

**`unknown · no denylist found ...`** (from `scripts/self-audit`)
The audit refuses to call itself a pass without the private denylist. Pass `--denylist FILE` or set `LODGINGS_AUDIT_DENYLIST`.
