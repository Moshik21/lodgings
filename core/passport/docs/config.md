# Configuration

Defaults are built in. `--config FILE` loads a JSON object; keys starting with `_` are comments. Then `--set NAME=VALUE` overrides single values. Lists take JSON (`--set 'busy_step_states=["running"]'`). Unknown keys are an error. Example: [examples/config.example.json](../examples/config.example.json).

| Key | Default | Meaning |
| --- | --- | --- |
| `state_dir` | platform default ([safety.md](safety.md#kill-switch-and-log)) | actions log and kill switch |
| `host_label` | the system host name | how this host names itself in receipts |
| `bundle_namespace` | `refs/passport` | ref names inside the bundle |
| `import_prefix` | `refs/lodgings/import` | where the target keeps imported refs: `<prefix>/<task>/<seq>/...` |
| `archive_prefix` | `refs/lodgings/archive` | where `confirm` archives the moved heads on the origin |
| `archive_ref_patterns` | `["refs/lodgings/archive/{task}/"]` | refs always carried as archive refs, so earlier moves of the lane travel with it |
| `busy_step_states` | `["running", "fixing"]` | step states that refuse an export unless the run is parked |
| `max_record_bytes` | `1048576` | largest single text record |
| `warn_passport_bytes` | `5242880` | passport size that earns a warning (the pool budget: 5 MB plus objects the target lacks) |
| `git_timeout_s` | `300` | per git call |
| `check_timeout_s` | `30` | for the read-only custody command |
| `bridge_name`, `bridge_email` | `lodgings passport`, `passport@lodgings.invalid` | identity of the bridge commit (target only) |
| `import_custody_check` | `[]` | default argv for import's read-only custody check |
| `secret_patterns` | private keys, common token shapes, `password=`/`token=` assignments | regular expressions; a match refuses |

Environment: `LODGINGS_PASSPORT=off` (kill switch), `LODGINGS_PASSPORT_STATE` (state dir), `LODGINGS_AUDIT_DENYLIST` (self-audit denylist).
