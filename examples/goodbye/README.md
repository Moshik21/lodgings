# Goodbye beats: examples, not installed

A goodbye is the last thing a host says before an expected absence, so the pool reads its silence as **ASLEEP**
(expected, no alarm, no requeue storm) instead of **LOST** ([docs/drain.md](../../docs/drain.md)). It is one command:

```
lodgings beat --host NAME --goodbye REASON -- DELIVER...
```

`REASON` is one of `sleep`, `lid`, `hibernate`, `shutdown`, `reboot`, `wsl-stop`, `stop`, `other`. `DELIVER...` is how a
record reaches the machine that runs `lodgings states` (the collector): an argument list that receives the record on
standard input, for example `ssh collector python3 lodgings/bin/lodgings record -`. On the collector itself use `--record`.

Lodgings installs none of these files and never runs them. Each one says at its top how to install it and how to remove
it again; read it before you do.

| File | Where | When it says goodbye |
| --- | --- | --- |
| [`linux-system-sleep`](linux-system-sleep) | Linux with systemd, as `/usr/lib/systemd/system-sleep/lodgings-goodbye` | before suspend and hibernate (a lid close is a suspend); beats once on resume |
| [`lodgings-beat.service`](lodgings-beat.service) | systemd user unit (Linux, or WSL with systemd enabled) | beats every 15 s while running; goodbye `wsl-stop` (or `stop`) when the unit is stopped |
| [`macos-sleep`](macos-sleep) | macOS, as sleepwatcher's `~/.sleep` (sleepwatcher is a third-party tool) | before sleep (lid close included) |
| [`windows-goodbye.ps1`](windows-goodbye.ps1) | Windows, registers two scheduled tasks when *you* run it | System event 42 (entering sleep) and event 1074 (a restart or shutdown started, Windows Update included) |

## What a goodbye covers

- `sleep`, `lid`, `hibernate`, `shutdown`, `reboot`, `other` speak for the **whole machine** (`scope = "machine"`): the
  Windows and WSL views of one PC both read ASLEEP when either says it. `wsl-stop` and `stop` speak for **this view
  only** (`scope = "host"`). `--scope` overrides the default.
- The first regular beat after a goodbye ends it. When one view of a machine is back, the others are counted from that
  moment, so a WSL distribution that never comes back after the PC wakes reads LOST in `lost_after_s`, not at once and
  not never.

## When a goodbye does not arrive

Every hook here is best effort: an operating system gives sleep hooks seconds, not minutes, and a WSL distribution can be
killed before its unit stops. A missing goodbye costs one false LOST, which clears with the next beat; nothing is ever
moved by it (Lodgings is advice only). Measure it: `lodgings states HOST -v` after a lid close shows which one you got.
