# Controlled comparison campaigns

These tools extend the comparison inputs, not the SOILIE model. All working
data, private API receipts, reviewer sessions and logs stay under `.codex/`.
Do not publish credentials, session tokens, local paths or billing identifiers.

## LayoutGPT living rooms

`serverless.benchmark.layoutgpt_controlled` executes the unchanged prompt-building
functions from pinned LayoutGPT commit `fc31954962553e5b65bf267a904a6930d50b1f5e`.
It retrieves four original training examples and adds one instruction requesting
3–6 furniture instances. Targets are seeded, distinct held-out rooms, represented
by the original rectangular maximum-length/width prompt. No generated quality
score selects a target or changes the prompt.

The initial batch contains 120 calls, 30 per requested count. Its separately
authorized one-call supplement uses the next held-out target and count (3).
The supplement carries forward the original batch's spending; it does not
receive another US$35 allowance. Plans cannot be overwritten or changed during
resume. The API runner reserves a conservative full-call token cost before each
request, records provider usage, and never automatically retries uncertain calls.

```powershell
# Preparation does not invoke the model.
wsl -- .codex/linux-env/bin/python -m serverless.benchmark.layoutgpt_controlled --output .codex/benchmark/layoutgpt-controlled --source .codex/layoutgpt-source/run_layoutgpt_3d.py
# Run only with explicit budget authorization and OPENAI_API_KEY set privately.
node serverless/benchmark/run_layoutgpt_controlled.mjs .codex/benchmark/layoutgpt-controlled
wsl -- .codex/linux-env/bin/python -m serverless.benchmark.import_layoutgpt_controlled --folder .codex/benchmark/layoutgpt-controlled --parser .codex/layoutgpt-source/parse_llm_output.py
```

The importer preserves the original parser, units, rotations and instances.
Unparsed lines and count compliance are recorded, never silently repaired.
Use final frozen matching evidence to count eligible pairs, not successful HTTP
responses. Public cost estimates can use token totals and public prices without
publishing private account data. API round-trip time is not JSON loading time.

## Infinigen expansion

`serverless.benchmark.expand_infinigen` retains the existing 20 matched rooms
of each type and targets 100 additional matched rooms per type. Two local
workers each use four Blender threads. The pinned initial Indoors release,
`controlled-six-fast` profile and six role constraints remain unchanged.
Concurrent runs are for geometry and review coverage, not isolated timing claims.

```powershell
wsl -- .codex/linux-env/bin/python -m serverless.benchmark.expand_infinigen --output .codex/benchmark/infinigen-expanded-120 --evidence .codex/benchmark/soilie-platform-grid-final/evidence --original-protocol .codex/benchmark/soilie-platform-grid-final/review/set-b/protocol.json --supplement-protocol .codex/benchmark/soilie-platform-grid-final/review-extension/set-b/protocol.json --repository .codex/infinigen --site-packages .codex/infinigen-env/lib/python3.10/site-packages --blender .codex/tools/blender-3.6.0-linux-x64/blender
```

The same command resumes checkpoints. A lock prevents duplicate controllers.
Bedrooms start at decimal seed 4000; living rooms start at 5100 (the runner's
100 offset plus 5000). Failed seeds advance the fixed sequence and remain in
private attempt records. Per-room checkpoints report valid outputs and matched
pairs separately. The controller stops at 120 pairs per type or 200 new attempts
per type. Below 15 GiB free disk, workers wait between scenes until verified
uploads restore at least 20 GiB of headroom. This wait does not enter generation
timings or change an active scene's watchdog.

Matching preserves room type, furniture-instance count and bed/sofa count,
requires at least one-third duplicate-aware object-family agreement, and limits
the summed-footprint-density difference to 1. Existing pairs are reserved.
Additional pairs use maximum-cardinality one-to-one matching with deterministic
tie-breaking; overlap and other quality measurements do not affect selection.

After export, `scene.blend` is replaced only by a losslessly compressed
`scene.blend.gz` whose decompressed checksum matches the original. Both hashes
and byte counts are retained in `blend-archive.json`. Decompress and verify the
original hash to recover a scene for a later mesh audit. Geometry, support
measurements, solver state and generation records remain available throughout.

## S3 output streaming and local eviction

`serverless.benchmark.stream_archive` uploads completed normalized geometry,
standardized diagrams and compressed Blender scenes to the dated
`files/outputs/benchmark-YYYY-MM-DD/` archive in `soilie3d-data`. It merges the
published Data index conditionally, preserving unrelated entries. In-progress
Blender files and private execution/reviewer records are not published.

```powershell
wsl -- .codex/linux-env/bin/python -m serverless.benchmark.stream_archive --campaign .codex/benchmark/infinigen-expanded-120 --state .codex/benchmark/soilie-platform-grid-final/stream-archive --measurements .codex/benchmark/soilie-platform-grid-final/evidence/measured-scenes.json --extra-export .codex/benchmark/layoutgpt-controlled/export.json --extra-export .codex/benchmark/layoutgpt-controlled-supplement/export.json --extra-export .codex/benchmark/infinigen-controlled-living-supplement/export.json --date 2026-09-24
```

The uploader uses the saved `darkest` AWS profile. When WSL has no matching
profile configuration, set its AWS shared-credentials/config paths privately
before launching. No credentials enter the archive or its manifests.

Fresh completed Blender files take priority over historical uploads. A local
binary is removed only after S3 confirms its length and SHA256 (or a full remote
read matches the hash for older objects). Its `s3-binary-receipt.json` records the
key, compressed hash and original Blender hash. Download that exact object and
decompress it to restore the scene. Compact local checkpoints are retained for
resumption and matching. `observations.json` and the Data index are published
only after their referenced objects exist; AI findings have a separate release
gate.

### Backfill older completed Blender files

The expansion uploader watches its own checkpoints. To archive completed
Blender files from older runs, explicitly name those run directories:

```powershell
python -u -m serverless.benchmark.archive_completed_blends --run .codex/benchmark/infinigen-matched-fast-roomscale-40 --state .codex/benchmark/soilie-platform-grid-final/completed-blend-archive --date 2026-09-24
```

Repeat `--run` for additional inactive Infinigen runs. The tool locks each run,
reads only completed attempt records, and compresses one binary at a time. It
verifies decompression locally and checks the S3 copy's size and SHA256 before
removing the unchanged original. A local `s3-scene-archive.json` receipt contains
the exact remote key and both restoration hashes. Logs, geometry, checkpoints
and review materials remain local. Failed intermediate scenes are not archived
as completed outputs.

The public `blender-scenes.json` manifest and its content-addressed binaries
appear under the dated Data archive. The manifest contains only generated-scene
identifiers, seeds and artifact metadata, not private execution records. Rerun
the same command after interruption; verified receipts make it resumable.

### SOILIE retention boundary

The frozen `soilie-platform-grid-final/evidence/cohort.json` defines the final
10,000 SOILIE rooms: 2,500 in each room-type/platform condition. Retain those
rooms and the source/correction records identified by the cohort's hashes.
Earlier generation records outside that membership are disposable; do not
upload them as extra final-cohort scenes. Keep the compact execution accounting
and frozen reviewer materials independently of these unused generation copies.

## Review release gate

AI reviewers can work on already frozen, validated pairs while the remaining
Infinigen scenes generate. Preserve collected judgements on unchanged pairs;
never append to a packet while its reviewer is running. Each reviewer receives
only their assigned neutral images and fixed prompt. Odd-sized extensions alternate
the extra left/right assignment across the frozen reviewer roster. Existing
assignments never change on reload. Public exports must retain stimulus and
prompt hashes, exclude repeat trials from preferences, and distinguish AI
judgements from human validation.

`serverless.cloud_benchmark.review_work` queues only missing assignments and
persists them through the original study sessions. Before submission it checks
the whole answer array for exact order, unique coverage and valid response
values. To inspect progress without exposing answers or private session data:

```powershell
python -m serverless.cloud_benchmark.review_work status --output .codex/benchmark/soilie-platform-grid-final/review-work
```

This reports written answers separately from hash-matched submission receipts.
It counts only this queue, including repeats, not earlier completed packets or
future extensions. It does not infer whether an external reviewer is running.
Only publish the final aggregate after every included pair has its required
reviews; partial packets are not complete evidence.

## Publication preparation

`serverless.cloud_benchmark.publish` derives the four-condition SOILIE stage
audit from the completed 10,000-room evidence. Preserve its `evidenceDigest`:
it is checked before assembling updated baseline comparisons. Desktop and AWS
Lambda observations remain separate, with 2,500 rooms per room/platform cell.
Successful generation timers and downstream contact-correction timers are
reported separately; concurrent elapsed time is not a serial-batch estimate.

`serverless.cloud_benchmark.publication_views` reuses that audit and already
measured geometry. It does not rerun the model, purchase API calls or recalculate
10,000 mesh measurements. It produces room-specific model inventories and
distributions, isolated Infinigen timings by room, recorded LayoutGPT request
latencies, and a public-rate cost calculation from recorded token counts and
measured cloud generation durations.

```powershell
wsl -- .codex/linux-env/bin/python -m serverless.cloud_benchmark.publication_views --base .codex/benchmark/soilie-platform-grid-final/publication/comparison.json --evidence .codex/benchmark/soilie-platform-grid-final/evidence --layoutgpt .codex/benchmark/layoutgpt-controlled/export.json --layoutgpt .codex/benchmark/layoutgpt-controlled-supplement/export.json --native .codex/benchmark/infinigen-matched-fast-roomscale-40/layouts-support.json --rates .codex/benchmark/soilie-platform-grid-final/public-rates.json --output .codex/benchmark/soilie-platform-grid-final/publication-preview
```

The preview includes released LayoutGPT bedrooms and all recorded controlled
living-room proposals. Its geometric distributions are not limited to pairs
selected for visual review. A closed API batch is required: uncertain, omitted,
duplicate or unparsable calls prevent silently publishing a success-only cost.
Only explicit public fields are exported; account receipts are never copied.

This preview is not a release. The expanded six-object Infinigen corpus must
still be frozen and added, followed by the remaining AI reviews. `aiReview.ready`
stays false until version-matched review exports exist. Website review exports
must carry the same `cohortSha256`; the browser rejects mismatched results.
The website's `scripts/check-balanced-comparison.mjs` accepts the preview folder
through `COMPARISON_PREVIEW_DIR`, serves the built site locally, and tests the
new schema without replacing committed benchmark data. It always closes its
browser and HTTP server.
