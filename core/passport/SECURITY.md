# Security policy

## Reporting

Please report vulnerabilities privately through GitHub's private vulnerability reporting (the repository's Security tab, then "Report a vulnerability"), not in a public issue. The root [SECURITY.md](../../SECURITY.md) has the supported versions and what to include. No response time is guaranteed.

## In scope

- Any path by which `lodgings-passport` writes to an origin before `confirm`, forces, deletes or rewrites a ref, overwrites a branch or worktree, or discards work.
- A passport that verifies after tampering, or a bundle or record that escapes its directory (path traversal in record names or ref names).
- A secret that reaches a passport despite the scan, or a refusal message that echoes one.
- Command injection: any input that reaches a shell. None should; every subprocess is an argv list.

## Known limits (v0)

- **The import receipt is not signed.** `confirm` trusts `after/import-receipt.json` as evidence that a person carried back from the target. It checks the passport id, the verified flag and the lane tree, but it cannot prove which host wrote the receipt. Signing with the target's existing ssh host key is planned.
- **The secret scan is pattern-based.** It catches common key and token shapes, not every secret. Do not put credentials in briefs or status logs.
- **Transport is out of scope.** Copy passports over channels you already trust (ssh with pinned host keys).
