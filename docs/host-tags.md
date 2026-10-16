# Host tags

A **tag** is one word that says a host can do something: `windows`, `arm64`, `gpu-nvidia`, `node`, `macos-signing`,
`chrome-webgpu`, or a label of your own such as `primary-desk`. Tags are what `lodgings match` and the `requires` of a
job or lane are tested against. Every output below was produced by running the commands shown against
[examples/demo-hosts.toml](../examples/demo-hosts.toml) and the recorded facts in [examples/fixtures](../examples/fixtures)
(synthetic machines); output is trimmed where marked `...`. Run them from the top of a checkout; `lodgings` is
`python3 bin/lodgings` (`bin\lodgings.cmd` on Windows).

## Where tags come from

- **Probed.** A probe reads facts from the host (OS, GPU, browsers, tool versions, signing identities) and a fixed rule
  turns each fact into a tag: a GPU of a vendor gives `gpu-nvidia`, a tool that answers with a version gives its name,
  and so on ([the catalogue](capabilities.md#tags-probed-declared-and-what-the-probe-says-about-yours)).
- **Declared.** `declared = [...]` on a host in `hosts.toml` is what you assert. The probe checks the ones it has a rule
  for and marks each *verified*, *contradicted* (it saw the opposite) or *unverified* (it cannot tell from here). A tag
  with no rule is carried as written.
- **Effective** = probed + declared - contradicted. Matching uses effective tags only.

```
python3 bin/lodgings probe --hosts examples/demo-hosts.toml --state demo-state
python3 bin/lodgings show host-a-win --hosts examples/demo-hosts.toml --state demo-state
```
```
webgpu: yes (hardware adapter acquired in 910 ms)
effective tags: cargo chrome chrome-webgpu desktop-session docker git gpu gpu-nvidia node npm playwright rustc signtool spacetime windows x86_64
declared but contradicted by the probe: windows-signing
```

The demo file declares `windows-signing` for this host; the facts show no code-signing certificate, so it is dropped from
the effective tags and the host reads *lacks declared capabilities* in `lodgings status`. An unknown is never a no: the
same host replayed from facts recorded without the `--deep` WebGPU measurement (`jq 'del(.browsers.chrome_probe)'
examples/fixtures/windows-tower.json > no-deep.json`, then `probe host-a-win --replay no-deep.json`) keeps the
declared `chrome-webgpu` but says so:

```
declared, not measurable here: chrome-webgpu
$ python3 bin/lodgings match --requires chrome-webgpu --hosts examples/demo-hosts.toml --state demo-state
mac            eligible
host-a-win     eligible     chrome-webgpu is declared, not measured
host-a-wsl     not eligible missing: chrome-webgpu
```

## The toolchain fingerprint, and drift

The fingerprint is a hash of the stable facts: OS and build, CPU and RAM, GPU and driver, every tool's version, browser
majors, signing capability. `show HOST` lists the fields behind it; free disk and load are left out.

```
$ python3 bin/lodgings show mac --hosts examples/demo-hosts.toml --state demo-state
...
expect spacetime  want 2.10   have 2.8.2      MISMATCH
fingerprint fields:
  ...
  tool.node              22.23.1
  tool.spacetime         2.8.2
```

When the next probe finds a different hash, the change is recorded, shown for a day (`drift_visible_h`), and classed:
tool, OS and hardware changes are warnings, browser, driver and signing changes are information. The first probe is a
baseline, never a change. Replaying the same Mac after an update:

```
$ python3 bin/lodgings probe mac --replay examples/fixtures/macos-arm64-after-update.json --hosts examples/demo-hosts.toml --state demo-state
mac            ok       fixture  fp 21f87510ac1d  drift: node 22.23→24.18; npm 10.9→11.16; +2 more  (0.0 s)
$ python3 bin/lodgings history mac --hosts examples/demo-hosts.toml --state demo-state
2026-10-16T21:24:51Z  51515edd0bec -> 21f87510ac1d
    browser.chrome         150 -> 151  [runtime]
    tool.node              22.23.1 -> 24.18.0  [toolchain]
    tool.npm               10.9.8 -> 11.16.0  [toolchain]
    tool.spacetime         2.8.2 -> 2.10.2  [toolchain]
```

## Using tags to place work

Lodgings only advises: nothing is started or moved. Three places take tags.

1. **`declared` in `hosts.toml`**, per host: tags the probe cannot see (a label, a calibrated machine, a scarce licence).
   Keep it to what is true; a declared tag the probe can check is checked. From the demo file:

   ```toml
   [expect]
   spacetime = "2.10"

   [host.mac]
   fixture = "fixtures/macos-arm64.json"
   declared = ["macos", "webkit", "macos-signing", "laptop"]
   ```
2. **`lodgings match --requires TAG,...`** answers "who can run this", with the reason for each host that cannot. It
   exits 0 when some host is eligible and 1 when none is.

   ```
   $ python3 bin/lodgings match --requires gpu,chrome-webgpu --hosts examples/demo-hosts.toml --state demo-state
   mac            eligible
   host-a-win     eligible
   host-a-wsl     not eligible missing: chrome-webgpu
   ```
3. **`--same-toolchain-as HOST`** adds "and its toolchain and OS equal that host's", for results that must be comparable.
   It names the first fields that differ and points to `lodgings compare` for the rest.

   ```
   $ python3 bin/lodgings match --requires macos-signing --same-toolchain-as mac --hosts examples/demo-hosts.toml --state demo-state
   mac            eligible
   host-a-win     not eligible missing: macos-signing; arch is arm64 there, x86_64 here; os.build is 24B83 there, 26100 here; ...
   ```

`[expect]` (pool-wide) and `expect = {...}` (per host) state the tool versions you want: `spacetime = "2.10"` matches
2.10.2, `"*"` means present. A miss is a warning chip, as the Mac's `MISMATCH` above shows; it does not change the tags.
Beats and events can also carry `requires` (tags) on a job or a lane, and `lodgings evacuate` then offers only hosts
that take work now and meet them ([drain.md](drain.md)). `match` answers capability only: combine it with
`lodgings states` to see whether a matching host is up.
