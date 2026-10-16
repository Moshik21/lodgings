# Capabilities, tags, fingerprint and drift

`lodgings probe` turns **raw facts** from a host into a **capabilities document** (`lodgings.capabilities 1.0`, stored as
`<state>/hosts/<name>/current.json`). Everything after the facts is a pure function of them, so a recorded facts file
replays to the same answer (`fixture = "file"`, `--save-raw`).

## Raw facts (`lodgings.facts 1.0`)

Printed by the probes as one line `LODGINGS-FACTS {json}`. Sections, each optional (a failed section is listed in
`errors` and the others survive):

| Section | Content |
| --- | --- |
| `os` | `family`, `name`, `version`, `build`, `kernel`, `arch` (`x86_64`, `arm64`), `wsl` (null, 1 or 2), `view` |
| `cpu`, `mem`, `disks`, `battery` | model, logical threads, RAM (basis says when it is a VM limit), per-volume size and free space (for WSL also the Windows drive that holds the virtual disk), battery present |
| `gpu` | devices `{vendor, name, driver}`, how they were found, passthrough (`wsl-dxg`) |
| `browsers` | Chrome presence and version, Safari and `safaridriver` (macOS), Edge (Windows), Playwright browser revisions, and with `deep` a measured WebGPU answer |
| `mac` | Command-line tools, Xcode version, `codesign`, `notarytool`, **count** of valid signing identities |
| `windows` | `signtool` version, **count** of valid code-signing certificates, whether an interactive desktop session exists, PowerShell version |
| `tools` | per configured tool: `{present, version, note}` |

No user name, host name, file path, certificate name or hash is ever recorded.

## Tags: probed, declared, and what the probe says about yours

`tags.probed` holds the tags the facts prove. `tags.declared` is what `hosts.toml` asserts. For each declared tag the
probe knows, the document says one of: **verified**, **contradicted** (the probe saw the opposite), **unverified** (it
cannot tell from here). Tags the probe has no rule for (`primary-desk`, `perf-calibrated:x`) are `plain`.
`tags.effective` = probed + declared minus contradicted; **matching uses `effective`**.

| Tag | True when |
| --- | --- |
| `linux`, `wsl`, `macos`, `windows`, `x86_64`, `arm64` | the host's own report of its OS view and architecture (`linux` is also true in WSL) |
| `gpu`, `gpu-nvidia`, `gpu-amd`, `gpu-intel`, `gpu-apple` | a hardware device of that vendor exists (virtual and software adapters do not count) |
| `chrome` | Chrome or Chromium is installed |
| `chrome-webgpu` | **measured**: the deep probe got a hardware WebGPU adapter. Without `--deep` it is *unverified*, never assumed |
| `webkit`, `safari`, `playwright` | Safari (macOS) or a Playwright WebKit build; Safari; any Playwright browser build |
| `macos-signing`, `xcode`, `xcode-clt`, `notarytool` | one or more valid code-signing identities; full Xcode; developer tools; `xcrun --find notarytool` |
| `signtool`, `windows-signing`, `desktop-session` | a Windows SDK `signtool.exe`; a valid code-signing certificate in the user store; an interactive session exists |
| `laptop` | a battery is present |
| a tool name (`node`, `rustc`, `wasm-opt`, ...) | the tool answered with a version (present but unreadable does **not** count) |

`unknown` is never `no` and never green: a section that could not run leaves its tags unverified.

## The toolchain fingerprint

`fingerprint.fields` is a flat map of the stable facts: OS family, name and build; architecture; CPU model, thread
count and RAM in whole GB; GPU names and drivers; every catalogue tool's version (`absent` and `unversioned` are
values); Chrome and Safari major versions; Playwright revisions; the WebGPU answer; signing capability. Free disk,
load and patch levels are excluded. `hash` is `sha256` of the sorted JSON of the fields; `short` is its first 12
characters. Field classes decide how loud a change is:

| Class | Fields | Drift is |
| --- | --- | --- |
| toolchain | `tool.*` | a warning chip |
| os | `os.*`, `arch` | a warning chip |
| hardware | `hw.*` | a warning chip |
| runtime | `browser.*`, `runtime.*`, `sign.*` | information |

If a Lodgings release changes the field set (`fingerprint.fields_version`), the next probe re-baselines instead of
reporting every field as changed.

## Drift

Each probe compares its fingerprint with the stored one. A difference becomes `drift` in the document
(`changed_at`, `from`, `to`, and the changes with their class), one line in `history.jsonl` (`lodgings history HOST`),
and a chip for `drift_visible_h` hours. The first probe is a baseline, not a change.

## Expectations

`[expect]` and `expect = {}` turn "wasm-opt is absent here, never compare" from a learning into a flag. A host that
misses an expectation gets a warning chip and an action alert. `lodgings match --same-toolchain-as HOST` goes further: a
host is eligible only if its toolchain and OS fields equal HOST's, and it says which field differs.

## Honest states

A host is `ok`, `stale` (last good probe older than `stale_after_h`), `failed` (the last attempt failed; the last good
document is kept and shown with its age) or `never` (not probed yet). Probe times come from the collecting host's clock.

## Cost

The Linux and WSL probe takes about half a second; the Windows probe about 3 seconds over ssh (2.9 to 3.1 s in five runs; it
starts a handful of version commands); the deep probe adds 0.3 to 1 second for one headless Chrome. It runs at start and daily, nothing in
between, and holds no memory afterwards.
