# Passport format (`lodgings.passport/1`)

Schema versions are separate from the tool version. A reader refuses a schema it does not know.

## Directory

```
<task>-<seq>/
  manifest.json      sealed
  custody.json       sealed
  pipeline.json      sealed
  decisions.json     sealed
  refs.bundle        sealed (an empty file when every ref is already in the base)
  records/<name>     sealed, UTF-8 text only, each at most max_record_bytes
  SHA256SUMS         "<sha256>  <relative path>" per sealed file, sorted, LF line ends
  after/             written after sealing, never sealed:
    import-receipt.json   lodgings.passport-import/1, written by import on the target
    resume.md             the brief for whoever resumes the lane on the target
    confirm.json          lodgings.passport-confirm/1, written by confirm on the origin
```

**Passport id** = `sha256:` + the SHA-256 of the `SHA256SUMS` bytes. `verify` refuses a passport when a sealed file is missing, a checksum differs, or any file outside `after/` is not listed.

## `manifest.json`

| Field | Meaning |
| --- | --- |
| `schema` | `lodgings.passport/1` |
| `tool`, `tool_version` | `lodgings-passport`, semver |
| `task`, `seq` | lane id (`[A-Za-z0-9][A-Za-z0-9._-]{0,99}`) and passport number for that lane (1, 2, ...) |
| `project`, `branch` | project name, lane branch (`refs/heads/<branch>` on the origin) |
| `created_at` | UTC, `YYYY-MM-DDTHH:MM:SSZ` |
| `origin` | `{host, ...}`: whatever the lane spec's `origin` held, plus the host label |
| `base` | `{ref, prerequisite}`: the base ref named in the spec and the bundle's prerequisite commit (`merge-base(base, lane head)`), or null for a full-history bundle |
| `refs[]` | `{kind, source, sha, bundle_ref, in_base}` for every carried head; `kind` is `lane`, `gate`, `pipeline`, `pr` or `archive` |
| `lane_head`, `lane_tree` | the commit and tree the target must reproduce |
| `gate_head` | the review gate's head for this lane, or null |
| `custody_relation`, `needs_bridge` | see [custody.md](custody.md) |
| `adopt` | only when exported with `--adopt-gate-head`: `{mode: "gate-head", lane_head, gate_head, lane_tree, gate_tree, tree_equal, lane_only_commits, gate_only_commits, tree_differences, digest, confirmed}` (counts are numbers; `digest` covers both heads, the full tree difference and the lane-only commit list). The target resumes at `gate_head`. Import recomputes the digest from the objects |
| `records[]` | `{name, bytes, sha256}` |
| `pipeline_source` | where `pipeline.json` came from (`none`, `fixture`, an adapter's reader) |

Bundle ref names live under `bundle_namespace` (default `refs/passport`): `refs/passport/lane/head`, `refs/passport/gate/head`, `refs/passport/pipeline/head`, `refs/passport/pr/head`, and `refs/passport/archive/<original ref without refs/>`. A ref whose commit is already in the base (`in_base: true`) is not bundled. The target already has it, and import checks that.

## `custody.json` (`lodgings.custody/1`)

`relation` holds the full relation record (lane head, lane tree, gate head, gate tree, merge base, relation, rule text, `lane_only_commits`, `gate_only_commits`, `ahead`, `gate_holds_lane_changes`). `adopt` (only for `--adopt-gate-head`) holds the same fields as the manifest's, plus the lists: `lane_only_commits` and `gate_only_commits` as `{count, entries: [{sha, subject}]}` and `tree_differences` as `{count, entries: [{status, side, path}]}`, each list cut at 200 entries (the digest covers all of them). `worktree.checked` is false, with `why`, when no worktree was named or it no longer exists and the branch is checked out nowhere. `pipeline_head` holds the pipeline's own head and its relation, `pr` the PR URL and head (bundled only if the object was present), `worktree` the clean-tree check, and `external_check` the read-only custody command's argv, exit code, output (bounded) and parsed `key: value` fields. The external check is evidence and never decides anything.

## `pipeline.json` (`lodgings.pipeline/1`)

Written by an adapter or by hand. The core reads only two things: `steps[].status`, against `busy_step_states`, and `run.parked`. The rest travels as is and feeds `resume.md`.

```json
{"schema": "lodgings.pipeline/1", "source": "fixture",
 "run": {"id": "RUN-1", "status": "running", "head": "<sha>", "parked": true},
 "steps": [{"name": "review", "status": "completed", "rounds": [{"round": 1, "trigger": "initial"}], "findings": []},
           {"name": "test", "status": "failed", "findings": [{"id": "test-1", "severity": "error", "title": "..."}]}],
 "open_findings": [{"id": "test-1", "severity": "error", "title": "..."}]}
```

## `decisions.json` (`lodgings.decisions/1`)

`{"schema": "lodgings.decisions/1", "items": [{"key", "asked", "answer", "open", ...}]}`. A bare JSON list is wrapped into `items`.

<a id="lane-spec"></a>
## Lane spec (`lodgings.lane/1`): the input to `export`

```json
{
  "schema": "lodgings.lane/1",
  "task": "demo",
  "project": "demo",
  "repo": "/path/to/project/clone",
  "worktree": "/path/to/lane/worktree",
  "branch": "lane/demo",
  "base": "main",
  "archive_refs": ["refs/archive/demo/"],
  "gate": {"git_dir": "/path/to/gate.git", "ref": "refs/heads/lane/demo", "pipeline_head": "<sha or omitted>"},
  "pr": {"url": "https://example.invalid/pr/1", "head": "<sha>"},
  "pipeline": "/path/to/pipeline.json",
  "decisions": "/path/to/decisions.json",
  "records": {"brief.md": "/path/to/brief.md", "status.log": "/path/to/status.log"},
  "custody_check": {"argv": ["review-tool", "sync", "--check"], "cwd": "/path/to/lane/worktree"},
  "adopt_gate_head": false,
  "confirm_differences": "<token printed by a refused --adopt-gate-head>",
  "origin": {"home": "label"}
}
```

Only `task`, `repo` and `branch` are required (`worktree` may be omitted, or name a path that no longer exists). Every flag of `export` sets the field of the same name and overrides the file. `archive_refs` are `git for-each-ref` patterns; `{task}` and `{branch}` are substituted. `custody_check.argv` is an argv list, never a shell string. See [examples/lane.example.json](../examples/lane.example.json).

## `after/import-receipt.json` (`lodgings.passport-import/1`)

`passport_id`, `task`, `seq`, `imported_at`, `target_host`, `target_repo`, `branch`, `branch_created`, `worktree`, `lane_head`, `lane_tree`, `gate_head`, `custody_relation`, `bridge` (sha or null), `adopted` (the manifest's `adopt` record as recomputed on the target, or null), `resume_head`, `target_gate_head`, `import_refs[]`, `checks[]` (`{check, ok, detail}`), `verified` and `external_check`.

`confirm` accepts the receipt only when `passport_id` matches, `verified` is true and `lane_tree` equals the manifest's; for an adopting passport the receipt must also show the same adoption (digest, and `resume_head` equal to the gate head). In v0 the receipt is evidence carried by hand, not a signed statement (see [SECURITY.md](../SECURITY.md)).

## `after/confirm.json` (`lodgings.passport-confirm/1`)

`passport_id`, `confirmed_at`, `origin_host`, `target_host`, `target_resume_head`, `import_receipt_sha256`, `archive_refs[]` (`{ref, sha, existed}`), `origin_untouched[]` and `next`.
