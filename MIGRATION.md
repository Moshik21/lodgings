# Migrating from Harbor to Lodgings

The project was renamed from Harbor to Lodgings. There is no forwarding shim: nothing answers to the old names any
more, so a host that still runs an old checkout keeps working on that checkout until it is updated, and an updated one
needs the changes below by hand.

| Kind | Old | New |
|---|---|---|
| Command | `harbor`, `bin/harbor`, `bin/harbor.cmd` | `lodgings`, `bin/lodgings`, `bin/lodgings.cmd` |
| Command | `harbor-passport` | `lodgings-passport` |
| Python module | `harbor` (`src/harbor`) | `lodgings` (`src/lodgings`) |
| Python module | `harbor_passport` | `lodgings_passport` |
| Package and archive | `harbor-pool`, `dist/harbor-<version>.zip` | `lodgings`, `dist/lodgings-<version>.zip` |
| Environment | `HARBOR_HOSTS`, `HARBOR_HOST`, `HARBOR_SSH` | `LODGINGS_HOSTS`, `LODGINGS_HOST`, `LODGINGS_SSH` |
| Environment | `HARBOR_PASSPORT`, `HARBOR_PASSPORT_BIN`, `HARBOR_PASSPORT_STATE` | `LODGINGS_PASSPORT`, `LODGINGS_PASSPORT_BIN`, `LODGINGS_PASSPORT_STATE` |
| Environment | `HARBOR_AUDIT_DENYLIST` | `LODGINGS_AUDIT_DENYLIST` |
| Config path | `~/.config/harbor/hosts.toml`, `~/.config/harbor/paused`, `~/.config/harbor/self-audit.denylist` | the same under `~/.config/lodgings/` |
| State path | `~/.local/state/harbor` (macOS: `~/Library/Application Support/harbor`, Windows: `%LOCALAPPDATA%\harbor`) | the same with `lodgings` |
| Passport state path | `.../harbor/passport` | `.../lodgings/passport` |
| Git refs | `refs/harbor/archive/...`, `refs/harbor/import/...` | `refs/lodgings/archive/...`, `refs/lodgings/import/...` |
| Schema ids | `harbor.hosts/1`, `harbor.facts`, `harbor.capabilities`, `harbor.chips/1`, `harbor.beat/1`, `harbor.event/1` | the same with `lodgings.` |
| Schema ids | `harbor.passport/1`, `harbor.lane/1`, `harbor.pipeline/1`, `harbor.custody/1`, `harbor.decisions/1`, `harbor.passport-import/1`, `harbor.passport-confirm/1` | the same with `lodgings.` |
| State file | `<state>/ledger/harbor.jsonl` (the value ledger written by `harbor ledger`), `<state>/ledger/coverage/` | `<state>/ledger/lodgings.jsonl`, `<state>/ledger/coverage/` (the directory keeps its name) |
| Ledger line fields | `"tool": "harbor"`, `"reader": "harbor-ledger-writer/1"` | `"tool": "lodgings"`, `"reader": "lodgings-ledger-writer/1"` |
| Example units | `harbor-beat.service`, `/usr/lib/systemd/system-sleep/harbor-goodbye`, scheduled tasks `\Harbor\HarborGoodbye*` | `lodgings-beat.service`, `.../lodgings-goodbye`, `\Lodgings\LodgingsGoodbye*` |

What breaks, and what to do:

- **Stored state** (`hosts.toml`, recorded beats, events and probes) carries the old schema ids and is read only under
  the new ones. Move the directory to the new path and rewrite the `schema = "harbor.hosts/1"` line in `hosts.toml`;
  records written before the rename are not accepted until their schema ids are rewritten, or they are re-recorded.
- **Passports** exported before the rename carry `harbor.*` schema ids and `refs/harbor/` refs and are refused by
  `lodgings-passport verify` and `import`. Export them again from the lane.
- **Installed hooks, units and scheduled tasks** from `examples/goodbye/` keep calling the old command; reinstall them
  from the new examples and remove the old ones with the removal instructions at the top of each example.
- **Anything that reads these feeds** (adapters, dashboards, scripts) must match on the new schema ids.

## What changes at merge time, per running install

Anyone who updates a checkout (or unpacks a new release zip) over a running install changes the following. Nothing is
renamed for them: do each item on every host that runs the tool, and on every home or repository that calls it.

### Host pool (`lodgings`, formerly `harbor`)

- **Command.** `python3 <dir>/bin/harbor ...` becomes `python3 <dir>/bin/lodgings ...` (Windows: `bin\harbor.cmd` becomes
  `bin\lodgings.cmd`). The install directory name is yours; if it was called `harbor`, rename or re-clone it.
- **Config.** `<config>/harbor/hosts.toml` moves to `<config>/lodgings/hosts.toml` (`<config>` is `$XDG_CONFIG_HOME` or
  `~/.config`, or `%APPDATA%`), or keep the file where it is and pass `--hosts` or set `LODGINGS_HOSTS`. Inside it, change
  `schema = "harbor.hosts/1"` to `schema = "lodgings.hosts/1"`. No other key was renamed.
- **Paused marker.** `<config>/harbor/paused` becomes `<config>/lodgings/paused` (or set `paused_marker` in `hosts.toml`).
- **State directory.** `~/.local/state/harbor` becomes `~/.local/state/lodgings` (macOS `~/Library/Application Support/`,
  Windows `%LOCALAPPDATA%`, same folder-name change), or keep it and pass `--state`. Recorded probes, beats and events
  written under the old schema ids are not read by the new code: move the directory, then re-run `lodgings probe`, and
  replay any beats and events you want to keep after rewriting their `"schema"` values (`harbor.beat/1` to
  `lodgings.beat/1`, `harbor.event/1` to `lodgings.event/1`). A fresh state directory works too; the history starts over
  and is shown as such.
- **Value ledger.** `harbor ledger` is now `lodgings ledger`. The ledger file `<state>/ledger/harbor.jsonl` is read and
  written as `<state>/ledger/lodgings.jsonl`: the new code does not look at the old file name, so on the first run it
  would start a new ledger beside the old one and read the days already written as missing. Move the file first
  (`mv ledger/harbor.jsonl ledger/lodgings.jsonl` inside the state directory); the line ids carry no tool name, so the moved
  lines still count as held. Lines already in it keep `"tool": "harbor"` and `"reader": "harbor-ledger-writer/1"`; only
  lines written after the rename say `lodgings`, so a reader that filters on those fields must accept both. The
  `ledger/coverage/<host>.jsonl` files keep their names and need no change.
- **Environment.** `HARBOR_HOSTS`, `HARBOR_HOST`, `HARBOR_SSH`, `HARBOR_AUDIT_DENYLIST` become `LODGINGS_HOSTS`,
  `LODGINGS_HOST`, `LODGINGS_SSH`, `LODGINGS_AUDIT_DENYLIST`. The old names are ignored.
- **Denylist file.** `<config>/harbor/self-audit.denylist` becomes `<config>/lodgings/self-audit.denylist`.
- **Machine-readable output.** Every `harbor.*` schema id in what the tool prints, records or reads is now `lodgings.*`
  (hosts, facts, capabilities, chips, beat, event). Any script, dashboard or adapter that matches on the old ids needs
  the new ones.

### Scheduled and installed lines (re-install each one; the tool never installs them)

Shape only; paths, host names and the collector command are yours.

| Where | Old line | New line |
|---|---|---|
| cron or a supervisor | `python3 <dir>/harbor/bin/harbor watch` | `python3 <dir>/lodgings/bin/lodgings watch` |
| systemd user unit `~/.config/systemd/user/harbor-beat.service` | `ExecStart=... %h/harbor/bin/harbor beat --every 15s -- <deliver>` and `ExecStop=... harbor beat --goodbye wsl-stop -- <deliver>` | file `lodgings-beat.service` with `ExecStart=... %h/lodgings/bin/lodgings beat ...`, `ExecStop=... lodgings beat --goodbye wsl-stop -- <deliver>` |
| Linux sleep hook `/usr/lib/systemd/system-sleep/harbor-goodbye` | `HARBOR=<dir>/bin/harbor` | file `lodgings-goodbye`, `LODGINGS=<dir>/bin/lodgings` |
| macOS `~/.sleep`, `~/.wakeup` (sleepwatcher) | `python3 <dir>/bin/harbor beat --host NAME -- <deliver>` | `python3 <dir>/bin/lodgings beat --host NAME -- <deliver>` |
| Windows scheduled tasks | folder `\Harbor\`, tasks `HarborGoodbyeSleep`, `HarborGoodbyeRestart`, `$Harbor = "...\bin\harbor"` | folder `\Lodgings\`, tasks `LodgingsGoodbyeSleep`, `LodgingsGoodbyeRestart`, `$Lodgings = "...\bin\lodgings"` |
| cron or a supervisor, hourly | `17 * * * * python3 <dir>/harbor/bin/harbor ledger --hosts <hosts.toml> --state <state-dir>` | `17 * * * * python3 <dir>/lodgings/bin/lodgings ledger --hosts <hosts.toml> --state <state-dir>` |
| `<deliver>` on the collector | `ssh collector python3 harbor/bin/harbor record -` | `ssh collector python3 lodgings/bin/lodgings record -` |

Remove each old unit, hook or task first (the removal line is at the top of the old example), then install the new one
from `examples/goodbye/`. Until you do, the old line keeps calling a command that no longer exists, so that host sends no
beats and reads `LOST` or `STALE`, not healthy.

### Lane passport (`lodgings-passport`, formerly `harbor-passport`)

- **Command.** `core/passport/bin/harbor-passport` becomes `core/passport/bin/lodgings-passport`; the module
  `harbor_passport` becomes `lodgings_passport`. Callers (any script, home or repository that runs the command by name
  or path) must switch.
- **Environment.** `HARBOR_PASSPORT=off` (kill switch), `HARBOR_PASSPORT_STATE`, `HARBOR_PASSPORT_BIN` become
  `LODGINGS_PASSPORT=off`, `LODGINGS_PASSPORT_STATE`, `LODGINGS_PASSPORT_BIN`.
- **State directory.** `.../harbor/passport` (actions log, kill-switch file `off`, receipts) becomes
  `.../lodgings/passport`. Pass `--state` to keep an old one.
- **Config keys.** The keys keep their names. Two defaults change: `bridge_name` `harbor passport` becomes
  `lodgings passport`, and `bridge_email` `passport@harbor.invalid` becomes `passport@lodgings.invalid` (only affects the
  author of new bridge commits).
- **Receipts and format identifiers.** `harbor.passport/1`, `harbor.lane/1`, `harbor.pipeline/1`, `harbor.custody/1`,
  `harbor.decisions/1`, `harbor.passport-import/1`, `harbor.passport-confirm/1` become the same ids with `lodgings.`.
  `tool` in a manifest reads `lodgings-passport`. A passport, import receipt or lane spec written before the merge is
  refused after it: export again from the lane. A passport that is half done (exported, not yet confirmed) should be
  finished with the old checkout before the update.
- **Git refs.** Archive and import refs move from `refs/harbor/archive/...` and `refs/harbor/import/...` to
  `refs/lodgings/archive/...` and `refs/lodgings/import/...`. Old refs already in a clone are left alone and not read;
  delete them by hand when no passport needs them.

### Verify afterwards

On each host, with the new checkout in place and `LODGINGS_HOSTS` or `--hosts` pointing at the migrated file:

```sh
python3 <dir>/bin/lodgings hosts check --strict
```

It exits 0 and prints `hosts check: 0 errors, 0 warnings` when the command, the renamed `hosts.toml` and its schema id
are all in order. It reads files only. `lodgings states` then shows each host's real beat age (`UNKNOWN` until a beat
arrives under the new line), which is the check that the re-installed lines work.
