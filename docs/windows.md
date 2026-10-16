# Windows hosts: PowerShell over ssh, nothing installed

A Windows host needs only what OpenSSH for Windows already provides: an ssh server you can reach with a key, and
Windows PowerShell 5.1 (present on every Windows 10 and 11). Lodgings streams `src/lodgings/probe_windows.ps1` into
`powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command -` on standard input, so:

- nothing is installed, copied or written on the host;
- no script file means the execution policy never applies (the policy only guards script files);
- `-NoProfile` skips profile scripts. If the account's *outer* shell still tries to load a blocked profile, you will see
  a `PSSecurityException` on standard error. Lodgings ignores it; it comes from the shell ssh starts, not from the probe;
- Python is not needed on the host. (Many Windows machines only have the Microsoft Store `python.exe` stub; the probe
  reports it as present with no version, and the `python3` tag stays false.)

Run `lodgings probe my-windows-host` once by hand first. Typical failure messages are in
[troubleshooting.md](troubleshooting.md).

## What a Windows probe cannot see

- **Whether Chrome gets the GPU in the logged-in desktop session.** An ssh session is not the desktop. The default probe
  says `webgpu unmeasured` and leaves `chrome-webgpu` *unverified*; the deep probe is off for Windows hosts in the
  examples because it would measure the ssh session, not the desktop.
- Anything inside WSL: give the WSL distribution its own host entry (`view = "wsl"`) with its own ssh alias.
- Battery state beyond presence, pending reboot, who is at the keyboard: live measurements belong to a beat, not to a
  daily capability probe.
