# Custody

A review gate keeps its own copy of the lane's head (in a separate gate repository). After a rebase, a fix round or a move, the lane's head and the gate's head disagree. The gate then rejects the next push as a non-fast-forward, or worse, work held only by the gate gets stranded. The passport decides custody **from git objects**, before anything is written, and the rules below decide whether a move is safe.

## Relations

`L` = lane head, `G` = gate head.

| Relation | Condition | Safe | On the target |
| --- | --- | --- | --- |
| `no-gate` | the gate has no ref for the lane | yes | the first pipeline run creates it |
| `equal` | `L == G` | yes | nothing extra |
| `ahead` | `G` is an ancestor of `L` | yes | the gate fast-forwards |
| `contained` | `G` is not an ancestor of `L`, but `git merge-tree --write-tree L G` is clean **and** yields `L`'s tree | yes | a **bridge** merge |
| `behind` | `L` is an ancestor of `G`, and `G` adds content | **no** | refused, unless `--adopt-gate-head` |
| `diverged` | neither is an ancestor, and `G` holds content `L` lacks | **no** | refused, unless `--adopt-gate-head` |

`contained` covers both cases the hand repair existed for. In one, the gate has the same tree under a different commit. In the other, the lane was rebased and every change of the old gate head is already in the new lane head. The test is on content, not on patch ids, so it survives conflict-free rebases, squashes and merges of the base. It refuses whenever merging the gate into the lane would change a single byte.

Every relation with a gate head also records how far apart the two are, so a refusal can say which side is ahead:

| Field | Meaning |
| --- | --- |
| `lane_only_commits` | commits reachable from `L` and not from `G` (`git rev-list G..L`) |
| `gate_only_commits` | commits reachable from `G` and not from `L` (`git rev-list L..G`) |
| `ahead` | `lane` (only the lane has commits), `gate` (only the gate has), `neither` (both have: diverged), `equal` |
| `gate_holds_lane_changes` | merging `L` and `G` is clean and yields `G`'s own tree: every change of the lane is already in the gate head |

The refusal quotes these, for example `custody diverged: neither side is ahead: lane head 2281e49e0d42 has 2 commit(s) the gate lacks, gate head 9409b8e4b156 has 2 commit(s) the lane lacks (merge base cccdb877e327); every change of the lane is already in the gate head, so the gate holds a squash or a rebase of this lane`, and then numbers the ways out. Nothing is changed by a refusal.

### Why a squash or a rebase by the gate is `diverged`

A review gate that squashes or rebases a PR puts a **new** head on its side: the lane's commits are not in its history, and its commits are not in the lane's. Two cases:

- **On the same base** (the base branch did not move): the gate head has the lane's exact tree. That is `contained`, and the bridge handles it with no flag.
- **On a newer base** (the base branch moved while the PR waited): the gate head is the lane's changes *plus* the new base commits. The merge changes the lane's tree, so the relation is `diverged` (or `behind` when the lane head is an ancestor of the gate head), and the passport refuses. `gate_holds_lane_changes` is true: nothing the lane did is missing from the gate head, and the refusal says so.

Both leave the lane head and the gate head with different histories and the same work. A person has to say which history the lane continues from, and `--adopt-gate-head` is how.

## Adopting the gate head

`export --adopt-gate-head` makes the gate head the head the lane resumes from. It is never automatic and never implied by another flag. The lane's own history is not thrown away: the lane head is bundled, imported under the import refs and archived by `confirm`, exactly as without the flag. What changes is which commit the target's branch starts at.

It compares the lane head's tree with the gate head's tree, from git objects:

| Result | What happens |
| --- | --- |
| trees identical | adopted, no confirmation; the passport records both heads |
| trees differ | refused. The refusal lists the commits only the lane holds, the commits only the gate holds, and every path whose content differs (`only in the gate`, `only in the lane`, `differs`), then prints a **token** |

The token is `--confirm-differences TOKEN`: the same command with that flag adopts, with exactly those differences. The token is a digest of the two head ids, the full tree difference and the lane-only commit list, so it stops matching the moment either head moves or the difference changes. A mismatch is refused, and the refusal lists the new difference.

An adopting passport records `adopt` in the manifest (both heads and trees, `tree_equal`, the counts, the digest, `confirmed`) and the full lists in `custody.json`. On import, the target recomputes the difference from the bundled objects and refuses a passport whose digest does not match, or one that adopts differing trees without a confirmation (exit 4). The resume head is the gate head itself: no bridge merge, the worktree has the gate head's tree, and the gate fast-forwards from it.

It also works when the gate would have refused for another reason: `behind` (the lane head is an ancestor of the gate head, so no commit is lost) and a genuinely `diverged` gate. In the second case the lane-only commits are real work the gate lacks; they are listed, and adopting resumes from the gate head without them. They stay in the passport and the archive refs, and a person decides whether to cherry-pick them.

A pipeline head named by `gate.pipeline_head` is compared with the adopted gate head instead of the lane head.

## The bridge

For `contained`, the target writes one commit (never on the origin):

- **tree**: exactly `L`'s tree
- **parents**: `L` first (so first-parent history is the lane), then `G`
- **author and committer**: `bridge_name` / `bridge_email` from config, dated at the passport's `created_at`, so re-importing the same passport makes the same bridge
- **message**: names both heads and the passport

It is a real merge whose result equals the lane, so it holds no content of its own. Its only effect is that `G` becomes an ancestor of the resumed head, and the gate fast-forwards. Import checks it before use: its tree equals `L`'s, and both `L` and `G` are its ancestors. The bridge replaces the hand recipe (archive refs, `reset --hard <gate head>`, `read-tree -u --reset <lane head>`, commit, verify the diff is empty).

## The pipeline's own head

A pipeline can hold a newer head than the gate: a fix round committed in its run worktree and not yet pushed. When the lane spec names `gate.pipeline_head`, the same relation is computed between `L` and that head. Anything but `equal`, `ahead` or `contained` refuses: the pipeline holds commits the lane lacks. The head is bundled when it differs from `L` and `G`. If its object cannot be read, custody is unknown and the export refuses.

## Busy steps

The custody of a running step belongs to that step. If any `pipeline.json` step has a status in `busy_step_states` (default `running`, `fixing`) and `run.parked` is not true, export refuses. A parked run (waiting for the agent at a gate) is a checkpoint and may move.

## The gate's own check

A review tool usually has its own custody check (a read-only "sync check"). The lane spec's `custody_check` runs it in the worktree. On import, `--custody-check` runs it in the new worktree. Its argv, exit code, bounded output and parsed `key: value` fields are recorded in `custody.json` and the import receipt. It is evidence for a person. It does not decide, because it may not know a moved lane yet. For example, the target gate may report "repo not initialized" until its first run.

## The target's gate

`import --gate-git-dir` names the target's gate repository, which is read only. If it already holds a head for the lane branch, the resume head must descend from it, or the import refuses. The passport never writes to a gate: the first pipeline run on the target pushes the resume head, and that push is a fast-forward by construction.
