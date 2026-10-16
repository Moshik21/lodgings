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

## Running Lodgings itself on Windows

The collector (the machine that runs `lodgings`) was compared on native Windows 11 (Python 3.12.4, `py -3`), WSL2 Ubuntu
(3.12.3) and macOS (3.14.6): the same 50 commands of the quickstart, the drain demo, `ledger`, `beat` and the passport
demo loop, each with its exit code and output. Apart from the fixes in the changelog, these are the differences left, and
none changes a decision or a record:

- **Command.** `bin\lodgings.cmd` starts `python`; if only the `py` launcher is on PATH, run `py -3 bin\lodgings`.
- **Line ends.** Output redirected or piped on Windows has CRLF line ends (Python's text mode); on Linux and macOS it has
  LF. JSON is unaffected. State files are written with LF everywhere.
- **Paths.** Windows prints backslashes (`bin\lodgings`, `C:\...`); the records hold what the host reported.
- **`lodgings-passport` encoding.** It writes in the output's own encoding and escapes what that cannot hold, so on a
  Windows pipe the `·` between fields is the code page's byte (cp1252: 0xB7), not UTF-8. `--json` is ASCII and the same
  everywhere; read a line with `encoding="cp1252"`, or set `PYTHONIOENCODING=utf-8` for UTF-8.
- **Resolver wording.** `ssh: Could not resolve hostname x: ...` ends with the platform's own words (macOS: `nodename nor
  servname provided, or not known`; Linux: `No address associated with hostname`; Windows: `No such host is known.`).
- **Timing.** A Windows process starts slower: the commands took 0.12 to 0.17 s against 0.06 s on Linux and macOS, and a
  probe of the machine itself 5.4 s through PowerShell against 0.2 to 1.8 s.
- **Usage text.** `lodgings --help` wraps its first lines differently on Python 3.14 than on 3.12; the words are the same.

What a Windows collector has not shown: a probe that reaches a real ssh host (the machine used had no key to any host), so
the ssh transport is covered there by the stand-in `ssh` of the test suite and by real `ssh.exe` failing to resolve an alias.
