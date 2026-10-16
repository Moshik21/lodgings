# `hosts.toml`: the host registry (`lodgings.hosts/1`)

One file, the same content on every host that reads it, changed by a reviewed edit, never by the tool. Lodgings finds it
through `--hosts FILE`, then `$LODGINGS_HOSTS`, then `./hosts.toml`, then the per-user config directory
(`~/.config/lodgings/hosts.toml`, `%APPDATA%\lodgings\hosts.toml`). `lodgings hosts check` validates it; unknown keys are
warnings (`--strict` makes them errors) so a newer file still loads on an older reader.

Python 3.11 and newer read it with `tomllib`; older Pythons use the small reader in `src/lodgings/toml_lite.py`, which
supports exactly what this file uses (`--builtin-toml` forces it on any version).

```toml
[pool]
schema = "lodgings.hosts/1"      # the only schema this reader accepts
state_dir = "state"            # relative to this file; default: the per-user state directory
probe_interval_h = 24          # `lodgings watch` probes this often
drift_visible_h = 24           # a fingerprint change stays visible this long
stale_after_h = 48             # a probe older than this reads "stale", never green

[expect]                       # pool-wide: tool -> version prefix, "*" means present
spacetime = "2.10"

[probe]
timeout_s = 20                 # per command, 1..300
deep = false                   # also start Chrome once to measure WebGPU
tools = ["git", "node"]        # optional: limit the built-in catalogue

[drain]                        # host states and drain inputs (docs/drain.md); these are the defaults
beat_interval_s = 15           # how often hosts beat, unless a beat says its own interval_s
late_after_beats = 2           # LATE after this many missed beats
stale_after_s = 60             # STALE: no new placements
lost_after_s = 900             # LOST: running jobs requeue elsewhere
canary_failures = 2            # this many failed canaries in a row drain the host
fallback_share = 0.2           # drain when over this share of placements fell back...
fallback_window_s = 3600       # ...in this window...
fallback_min_placements = 5    # ...once at least this many were made
on_battery = true              # a machine on battery drains (false: a note only)
reboot_window_s = 3600         # drain when a pending reboot is due within this
reboot_unknown_due = "note"    # a pending reboot with no known time: "note" or "drain"

[probe.tool.spacetime]         # add a tool to the catalogue
argv = ["spacetime", "--version"]   # an argument list, never a shell string; argv[0] is a bare command name
pattern = "tool version (\\d+\\.\\d+\\.\\d+)"   # one capture group around the version

[host.my-wsl]
label = "My WSL"               # shown in dashboards
machine = "my-pc"              # the physical machine; a PC has a windows view and a wsl view
view = "wsl"                   # linux | wsl | darwin | windows
local = true                   # exactly one of: local = true, ssh = "alias", fixture = "file"
classes = { build = 2, farm = 4 }          # slots per job class (read by later slices)
declared = ["linux", "wsl", "gpu-nvidia"]  # tags you assert; the probe checks the ones it can
expect = { node = "22" }       # per-host expectations, merged over [expect]
policy = { pressure_from_machine = true }  # free-form table, owned by later slices
confine = "~/lodgings"           # where Lodgings' tools may write on that host (passports, imported lanes)
repos = { app = "~/src/app" }  # project -> its clone on that host (evacuation offers)
drain = { on_battery = false } # per-host [drain] overrides (a desktop on a UPS, say)
paused_marker = "~/.config/lodgings/paused"  # `lodgings beat` on that host reads PAUSED while this file exists
host_class = "laptop-rtx"      # optional label; default is "<view>-<arch>"
deep = false                   # optional per-host override of [probe] deep
```

## Hosts

| Key | Meaning |
| --- | --- |
| `view` | Required. What the probe will find: `linux`, `wsl`, `darwin` or `windows`. A mismatch with what the host reports is shown as a probe note. |
| `local` / `ssh` / `fixture` | Exactly one. `local`: this machine. `ssh`: an alias from your `~/.ssh/config` (letters, digits, `. _ @ -` only; options never come from this file). `fixture`: replay recorded facts from a file. |
| `machine` | Groups views of one physical machine. Default: the host name. |
| `declared` | Tags you assert. Letters, digits and `. _ : + -`. A tag the probe understands is checked (see [capabilities.md](capabilities.md)); the rest are carried as written. |
| `expect` | `tool = "prefix"`: `"2.10"` matches `2.10.2` but not `2.100.0`; `"*"` means present. `chrome` is also accepted. |
| `classes` | Slots per job class. Evacuation offers only hosts with a slot for the job's class (a host with no `classes` takes any). |
| `confine` | Where Lodgings' tools may write on that host: an evacuation offer puts passports in `<confine>/passports` and imported lanes in `<confine>/lanes`. Kept as written, never expanded here. |
| `repos` | `project = "path"`: the project's clone on that host, used by evacuation offers. A missing one is a `<placeholder>` in the offer, never a guess. |
| `drain` | A table of [drain] keys that apply to this host only. |
| `paused_marker` | The file whose presence makes `lodgings beat` on that host report `paused`. Default `~/.config/lodgings/paused` (`%APPDATA%\lodgings\paused` on Windows). |
| `policy` | Validated and carried; owned by later slices. |

## Drain

`[drain]` sets the thresholds for host states and drain inputs pool-wide; a host's `drain = { ... }` overrides keys for
that host. Every value is checked by `lodgings hosts check`: numbers above 0, `fallback_share` between 0 and 1,
`reboot_unknown_due` `"note"` or `"drain"`, and ages that grow (`late_after_beats` × `beat_interval_s` < `stale_after_s`
< `lost_after_s`), for the pool and for each host after its overrides. What each threshold does and where the defaults
come from is in [drain.md](drain.md); `lodgings thresholds` shows what your own history says about them.

Host names are lower-case letters, digits, `. _ -`, up to 48 characters.

## Transport

`ssh` hosts are reached with `ssh -T -o BatchMode=yes -o ForwardAgent=no -o ClearAllForwardings=yes alias <interpreter> -`
and the probe program on standard input. Nothing is installed or written on the host. Set `LODGINGS_SSH` to use another
ssh program. Windows hosts need PowerShell 5.1 or newer, other hosts need `python3` 3.8 or newer.
See [windows.md](windows.md).
