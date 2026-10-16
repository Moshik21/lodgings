# Troubleshooting

Messages are quoted from real runs.

| Message | Meaning and fix |
| --- | --- |
| `error: .../hosts.toml: cannot read: [Errno 2] No such file or directory` (then `hosts check: 1 error`, or `lodgings: .../hosts.toml is not usable` with exit 2) | no registry was found. Lodgings looks at `--hosts FILE`, then `$LODGINGS_HOSTS`, then `./hosts.toml`, then the per-user config directory, so run it from the folder that holds `hosts.toml`, pass `--hosts`, or copy `examples/hosts.example.toml` to `hosts.toml` first |
| `error: [host.x]: fixture file .../fixtures/y.json does not exist` | a `fixture = "..."` path is read relative to the `hosts.toml` that names it, not to where you run the command. Fix the path, copy the fixture next to the registry, or drop the host (the shipped `examples/hosts.example.toml` has no fixture host; `examples/demo-hosts.toml` does, and must stay in `examples/`) |
| `hosts.toml: view must be one of linux, wsl, darwin, windows` | the file is wrong; run `lodgings hosts check` for every problem at once |
| `give exactly one of ssh = "alias", local = true, fixture = "file" (found ssh, local)` | a host has two ways to be reached; keep one |
| `warning: [host.x]: unknown key 'ssh_host' (did you mean ssh?)` | a typo; the key is ignored. `lodgings hosts check --strict` fails on it |
| `ssh my-host failed (exit 255): ssh: Could not resolve hostname` | the ssh alias does not exist in `~/.ssh/config`; test `ssh my-host true` |
| `ssh my-host failed (exit 255): Permission denied (publickey)` | probing uses `BatchMode`, so it never asks for a password; fix the key |
| `ssh my-host did not finish within 110 s` | the host is asleep or the network is down; the last good document stays, marked with its age |
| `the host did not answer with facts (no LODGINGS-FACTS line)` | the remote shell did not run the interpreter: for a Windows host check `powershell` is on PATH for ssh logins; for others check `python3` exists |
| `ssh my-host failed (exit 1): python3: command not found` | a Unix host without `python3`; install it or probe through a host that has it |
| `probe note: hosts.toml says view wsl but the host reports linux` | wrong `view` in the file |
| `declared, not seen: chrome-webgpu` | you declared a capability the probe measured as absent (run with `--deep` on that host to see the basis in `lodgings show`) |
| `probe: last good 3d ago` | probes are failing or `lodgings watch` is not running |
| `PSSecurityException` in the ssh output on Windows | the account's outer shell cannot load a profile script (execution policy). Harmless to Lodgings; see [windows.md](windows.md) |
