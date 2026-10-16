# Examples

- `hosts.example.toml`: a small pool (a laptop with a WSL distribution, and a Mac) to copy to `hosts.toml` and edit.
- `fixtures/`: recorded raw facts (`lodgings.facts 1.0`) for a Mac, a Windows tower, and a WSL distribution on that tower.
  They are synthetic: invented machines, real field shapes. `macos-arm64-after-update.json` is the same Mac after a
  toolchain update; swapping it in for `macos-arm64.json` between two probes produces a drift entry.
- `drain/demo-records.jsonl`: a synthetic hour of beats and events for the demo pool (a tower with a reboot due, a lane
  parked on its WSL view, a Mac whose lid closes). Replay it with `lodgings record --replay` and judge it at any moment
  with `lodgings states --now EPOCH` ([docs/drain.md](../docs/drain.md)).
- `goodbye/`: goodbye-beat hooks for Linux, WSL, macOS and Windows, as examples with install and removal instructions;
  Lodgings installs none of them.
