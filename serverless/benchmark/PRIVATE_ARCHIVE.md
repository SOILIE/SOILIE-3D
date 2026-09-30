# Private research recovery

Published geometry and review stimuli belong in the Data-page comparison
archive. Raw reviewer executions, development runs and local recovery copies
belong in the separate bucket managed by `serverless/research-archive.yaml`.
That bucket is encrypted, versioned and blocks all public access. It has no
CloudFront route or automatic expiry. Never upload credentials.

For an explicitly inspected, inactive path under the backend's `.codex/`:

```powershell
python -m serverless.benchmark.private_archive --source .codex/path-to-inactive-run --bucket PRIVATE_BUCKET --evict
```

Omit `--evict` to back up without deleting. The command verifies the bundle's
members, uploads it, verifies the S3 SHA-256, saves local and remote recovery
receipts, and only then removes the exact unchanged source files. It rejects
links, credential-like paths, collector locks and targets outside project scratch.
No recursive directory deletion is used. Interrupted eviction is resumable.

Receipts remain in `.codex/private-archive/`, including every original member's
hash and a version-specific recovery pointer. To restore, download that exact
S3 object version, check its SHA-256 against the receipt, and extract into a
fresh project `.codex/restore/` directory before replacing any working paths.
Members are relative to the original `.codex/` root.

Keep current collection inputs, answers and provenance local until collection
finishes. Do not change the frozen collector or remove its execution evidence
to save space. Snapshot only inactive runs, or explicitly completed immutable
files. Private archival is not publication of research results.
