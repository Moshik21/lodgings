# Safety model

Lodgings **observes and advises**. It never starts a job, moves work, merges, deletes, installs or changes anything
on a host it probes, and it writes only its own state directory. Host states, drain requests and evacuation offers
([drain.md](drain.md)) are advice: no runner is told anything and nothing is moved.

## What a probe may run

Every command is a fixed argument list (no shell string, nothing built from file contents), run with a timeout.

| Where | Commands |
| --- | --- |
| Linux, WSL | the version flag of each catalogue tool (`git --version`, `node --version`, ...); `nvidia-smi --query-gpu=name,driver_version --format=csv,noheader`; `lspci -mm`; `<chrome> --version` |
| macOS | the above, plus `sw_vers`, `sysctl -n` (CPU brand, memory, performance cores), `pmset -g batt`, `system_profiler SPDisplaysDataType -json`, `xcode-select -p`, and only when developer tools exist `xcodebuild -version` and `xcrun --find notarytool`, `security find-identity -v -p codesigning` (a count; names and hashes are discarded) |
| Windows | the version flag of each catalogue tool; everything else is read from WMI (`Win32_OperatingSystem`, `Win32_Processor`, `Win32_ComputerSystem`, `Win32_VideoController`, `Win32_LogicalDisk`, `Win32_Battery`), a registry value, file version info of well-known browser paths, a directory listing of Playwright's cache, and a **count** of code-signing certificates in the current user's store |
| deep (opt in) | one headless Chrome run on a throw-away profile in a temporary directory, removed afterwards |

Files read: `/etc/os-release`, `/proc/version`, `/proc/cpuinfo`, `/proc/meminfo`, `/sys/class/power_supply`, application
`Info.plist` files, Playwright's browser cache directory names.

Configuration can add a tool (`[probe.tool.NAME]`) with a bare command name and arguments; it cannot add any other kind
of command. Nothing here ever reads a credential, token, key or the contents of a user's files.

## What `lodgings beat` reads

`lodgings beat` runs on the host it describes and only reads: `/sys/class/power_supply`, `/run/reboot-required`,
`/run/systemd/shutdown/scheduled`, `/proc/sys/kernel/random/boot_id` and `/proc/uptime` on Linux and WSL; `pmset -g batt`
and `sysctl -n kern.boottime` on macOS (each with a 2 s timeout); the load average (`/proc/loadavg`, `getloadavg`) when it
paces itself with `--every`; `GetSystemPowerStatus`, `GetTickCount64` and whether two registry keys exist
(Windows Update's `RebootRequired`, servicing's `RebootPending`) on Windows; and whether the paused marker file exists.
It never changes any of them. With `-- CMD ARG...` it runs exactly that argument list (never a shell string) with the
record on standard input; nothing else is run.

## What `lodgings evacuate` prints

Commands, as text. Lodgings runs none of them: lane moves are `lodgings-passport` steps for a person or their supervisor to
run (the command is the file `core/passport/bin/lodgings-passport` of the source checkout, not installed with Lodgings),
and a job's `resubmit` is shown only if the runner that reported the job supplied it, as an argument list (a shell
string is rejected when it is recorded).

## What is transferred

The probe program travels to the host on standard input and nothing comes back but one `LODGINGS-FACTS` line. No agent
or credential forwarding (`ForwardAgent=no`, `ClearAllForwardings=yes`), no `SendEnv`, `BatchMode=yes` so it never
prompts, and host-key checking is whatever your `ssh` configuration says.

## What is written

Only `<state>/` (permissions 0700 for new directories): current capabilities, last attempt, drift history, a probe log,
and with `--save-raw` the raw facts; received beats and events, drain requests, state transitions and this machine's
clock readings (`observer.json`, [drain.md](drain.md#files)), and the append-only value ledger `lodgings ledger` writes from those
records ([ledger.md](ledger.md)). Nothing is written on the probed host. `lodgings beat`
writes nothing unless given `--out FILE` (that file) or `--record` (the state directory).

## What it will never do

- start, stop, restart or install a service, scheduled task or startup entry (`lodgings watch` and `lodgings beat --every`
  run in the foreground until you stop them, and only lengthen their own interval or delay a probe when the machine is busy
  or on a low battery; the goodbye hooks in `examples/goodbye/` are files for you to read and
  install yourself, and Lodgings never runs them)
- change a runner's placement, requeue a job or move a lane: states and offers are advice
- run a command that is not in the table above, or evaluate text from a host
- send data anywhere you did not configure; there is no telemetry and no update check
- act on a finding: expectations and drift produce chips and alerts, never repairs
