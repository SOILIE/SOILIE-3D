# Source recovery and runtime compilation

`rebuild_sources.py` stages a fresh RGB-D corpus using the existing model's
preprocessing, collector, manifest, and triplet-gathering functions. It never
publishes results, deploys a renderer, or generates rooms. The project checkout's
current model data is not modified.

Choose a dedicated workspace below the repository's ignored `.codex/` directory
and use one fixed Python environment with the root requirements installed. For
example, with a task environment already prepared:

```powershell
$sourcePython = '.codex/source-rebuild/env/Scripts/python.exe'
$sourceWork = '.codex/source-rebuild'
& $sourcePython -m serverless.compiler.rebuild_sources fetch-washington --workspace $sourceWork
& $sourcePython -m serverless.compiler.rebuild_sources extract-washington --workspace $sourceWork
& $sourcePython -m serverless.compiler.rebuild_sources fetch-sun3d --workspace $sourceWork
& $sourcePython -m serverless.compiler.rebuild_sources verify-sources --workspace $sourceWork
& $sourcePython -m serverless.compiler.rebuild_sources preprocess --workspace $sourceWork --jobs 4
& $sourcePython -m serverless.compiler.rebuild_sources collect --workspace $sourceWork
```

The inputs are the original University of Washington RGB-D Scenes v2 image and
point-cloud archives, and the exact SUN3D annotations tracked in `json/princeton/`
with their pinned intrinsics, extrinsics, RGB frames, and timestamp-paired depths.
Washington recovery retains every native frame and original pose row. The core
preprocessor then selects its deterministic 30-frame sample from each scene.
SUN3D recovery needs frame bodies only for nonempty annotated frames; it retains
the full official depth-directory inventories and proves that selective caching
does not change any retained RGB/depth pairing.

Outputs stay under the chosen workspace:

- `raw/`: original Washington ZIP archives and resumable download receipts.
- `candidate/data/` and `candidate/json/`: native source files and freshly
  collected geometry, relations, annotations, and gathered `triplets.csv`.
- Source inventories and receipts: exact byte lengths, SHA-256 hashes, ZIP CRCs,
  source URLs, actual camera poses, dependency versions, and module fingerprints.
- `logs/`, `scene-receipts/`, and color-state checkpoints: bounded-process logs
  and resumable collection state.

Only Washington scene preprocessing runs concurrently, with at most four workers
and one BLAS/OpenMP thread each. Collection is serial because the core incrementally
updates `object_colors.csv`; an OS-owned controller lock protects that shared state.
Its before/after hashes form one checked contribution chain across both datasets.
Completed scenes are reused only with matching annotations, core modules, runtime
dependencies, relation seals, and current color state.

To overlap SUN3D collection with Washington preprocessing, run the optional
`collect-dataset --dataset princeton` command after source verification. Run the
full `collect` command afterward: it checks both complete scene sets, rechecks
actual source bytes, seals the production relation manifest, and gathers only
that manifest's exact relations. Never run two collection controllers at once.

Inspect and independently validate the candidate before promoting any files.
Then use the existing [`scripts/rebuild_working_combos.py`](../../scripts/rebuild_working_combos.py)
with the reviewed candidate triplet SHA-256 to build 3–8-object catalogs. The
runtime compiler remains a separate step:

```powershell
python -m serverless.compiler.build_runtime --repository . --output .codex/runtime-candidate
```

Do not treat a successful download, a header migration, or a compiled runtime as
proof that the scientific source audit or campaign acceptance gates passed.

## Pausing and recovery

A workspace `PAUSE_SOURCE` marker makes every new preprocessing/collection
command exit with status 75 before dispatch. Already-running workers can finish
their current scene and seal it. Keep the marker until the source issue has been
reviewed and the user has asked to resume; a partial checkpoint is not a complete
source corpus.

Downloads fail closed. In particular, a crash between renaming a completed
`.part` file and sealing its download receipt can leave a final file beside an
`INCOMPLETE` receipt. The helper deliberately refuses to bless that file. Recover
only after checking its complete byte length and SHA-256 against independent
trusted source/receipt evidence. If that proof is unavailable, quarantine or
discard only the incomplete task-owned download and reacquire it. Renaming an
unverified complete file into the partial path is not a substitute for that
proof. Never change an incomplete receipt to `COMPLETE` merely because a
plausible file exists.

For a reviewed annotation-only correction, no full corpus redownload is needed:

1. Preserve the pre-correction candidate annotation bytes and old source
   inventory under `source-metadata/` before replacing anything. Record the
   original and curated SHA-256 values, the human-authored Git commit, and the
   reason for the correction. Do not describe curated bytes as byte-identical
   to the original source annotation.
2. Confirm the exact reviewed annotation diff. Stage only that corrected tracked
   JSON into its candidate location, then rerun `fetch-sun3d` to rebuild the source
   inventory. It reuses complete, SHA-verified frame bodies and metadata;
   `fetch-sun3d` itself refuses silent replacement of a different candidate JSON.
3. Re-run source seals and the all-annotation identity audit before removing the
   pause marker. Unchanged completed scenes can resume only if their annotation,
   module, dependency, relation, and color seals still match. If production
   modules changed, use a fresh candidate rather than weakening those checks.

Changing an annotation intentionally invalidates its previous pinned SHA; retain
that provenance transition rather than editing a receipt to pretend nothing
changed. Resume missing Washington scenes with `preprocess --jobs 4`, then resume
collection and complete the independent full-corpus audit before catalog building.

## Audited runtime-data promotion

[`promote_runtime.py`](promote_runtime.py) is a separate maintenance-window
workflow for an independently audited candidate. Its `prepare` command is
read-only against S3 and freezes a plan; `apply` and `rollback` require that exact
plan's SHA-256 confirmation. Consult its command help for the current arguments:

```powershell
python -m serverless.compiler.promote_runtime --help
python -m serverless.compiler.promote_runtime prepare --help
```

Fixed-key S3 replacement is not atomic. Arrange a maintenance window, verify all
private original-byte backups before replacement, and require consumers to fail
closed during any partial update. The tracked runtime checksum manifest is
installed last, after local and remote candidate bytes agree. Keep the journal
and verified rollback material until the promotion is accepted. This operation
does not itself deploy a renderer or start a campaign.
