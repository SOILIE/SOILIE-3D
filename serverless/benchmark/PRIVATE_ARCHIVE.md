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

## Retiring comparison evidence privately

`retire_comparisons.py` archives an explicit snapshot in the existing Data bucket
before retiring its public copies. The public Data service can continue to serve
the historic `files/outputs/2024-06-27/` demonstrations.

The tool requires separate phases. Use a task directory under the project's
ignored `.codex/`, one immutable timestamped recovery prefix, and authenticated
same-account credentials. Never commit private recovery manifests or receipts.

```powershell
$state = '.codex/retirement-YYYYMMDD'
python -m serverless.benchmark.retire_comparisons prepare --state $state --prefix private/retired-comparisons/YYYYMMDDTHHMMSSZ/
python -m serverless.benchmark.retire_comparisons upload-local --state $state --source C:/path/to/website/benchmarks --label website-benchmarks
python -m serverless.benchmark.retire_comparisons inventory --state $state
python -m serverless.benchmark.retire_comparisons copy --state $state --workers 24
python -m serverless.benchmark.retire_comparisons baselines --state $state
```

`prepare` preserves the prior bucket policy and adds explicit read denies for
external/anonymous principals and the CloudFront service on `private/*`. It
preserves unrelated statements. Verify signed recovery and failed unauthenticated
S3/CloudFront requests before uploading bulk evidence. A directory label passed
to `upload-local` creates a complete hashed recovery packet without removing its
source files; this allows the website to enter a pending-results state safely.

`inventory` paginates actual S3 objects, including unindexed outputs. It freezes
all keys, sizes, ETags, and the public catalog; it cannot overwrite an existing
snapshot. `copy` reads the source with an ETag precondition, computes SHA-256,
copies within S3 under the same precondition, and verifies the destination's
full-object checksum. Missing or multipart composite checksums trigger streamed
verification. ETags only pin source identity; they are never content hashes.
Verified batches are checkpointed both locally and in the private prefix. Re-run
`copy` with the same state to resume without changing its frozen inventory.
`baselines` creates a separately usable recovery index for the retained LayoutGPT
and Infinigen collections, including source geometry, native Blender archives,
declared hashes cross-checked against actual recovery content, and historical
execution receipts. It excludes withdrawn SOILIE rooms and old AI judgments.

After the website has withdrawn retired comparison evidence and every copy is
verified, remove the public snapshot and rebuild the catalog:

```powershell
python -m serverless.benchmark.retire_comparisons retire --state $state --workers 24
python -m serverless.benchmark.retire_comparisons index --state $state
```

Deletion rechecks each private SHA-256 and uses the original source ETag as a
precondition. A changed source is not overwritten or removed. The recovery
manifest exists remotely before deletion; a separate resumable deletion journal
is also backed up by batch. The historic prefix and the root folder marker are
excluded by both inventory and deletion guards. The catalog write requires its
original ETag and refuses concurrent catalog changes or unexpected new output
keys. Invalidate CloudFront's output paths and catalog after successful removal,
and verify old direct URLs as well as the Data browser.

The private snapshot retains original paths, model names, geometry, scene
archives, numerical results, review evidence, and timing/cost provenance. Reusable
baseline rooms retain their original source identity; old AI judgments must not
be reused for the replacement campaign. Git history is not rewritten by this
procedure. Local recovery receipts are operational records, not public evidence.

For inactive local outputs, name every approved source root relative to the
project's `.codex/`. This phase freezes an explicit scientific-file inventory,
excludes source code, environments, credentials, links, and working asset caches,
then creates bounded one-GiB recovery tar shards and a separate baseline index:

```powershell
python -m serverless.benchmark.retire_comparisons archive-local --state $state --local-root benchmark --local-root publication-022
```

Inspect `local-evidence-inventory.json` and its exclusions. Only after confirming
that every root is inactive, repeat the identical command with `--evict` to remove
unchanged inventory members after remote checksum verification. Changed or newly
created files and excluded source/cache files remain. Do not pass raw source
datasets, active campaign roots, or another agent's working directory. Recovery
state cannot be nested inside a source root, and interrupted packing never grants
permission to remove a source file.

Use `--inventory-only` first when the approved roots have not yet been inspected.
This freezes member hashes without packing, uploading, or evicting local files.
Browser profiles are excluded using both directory names and browser-state
markers, so an arbitrarily named profile cannot be mistaken for research output.
Completed `upload-local` labels are immutable: re-running a label verifies the
original private recovery packet rather than replacing it with newer contents.
