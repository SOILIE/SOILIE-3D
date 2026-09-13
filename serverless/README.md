# SOILIE-3D V4 serverless research demonstrator

This subsystem exposes the repository's original SOILIE-3D V4 implementation
through an asynchronous web API. It does not contain a substitute layout
solver, simplified recency model, reduced asset catalog, or fallback renderer.

## Exact execution boundary

`compiler/build_runtime.py` stages all 191 OBJ assets, the original relation
data, `suggested_setup.blend`, and every V4 module. It records checksums before
adding one reviewed hook to the staged copy of `modules/render.py`. That hook
runs only after V4 has completed its own placement, grounding, wall alignment,
pair rotation, stacking, overlap, and window-adjustment operations.

The optional `modules/room_fit.py` extension measures the completed scene and
redraws only the floor and four walls. It never moves, rotates, rescales,
replaces, or retries an interior object. The original V4 camera,
`change_imagination_focus` recency sequence, and `png2gif` animation builder
remain authoritative.

The staged launcher also skips V4's final in-memory teardown after all artifacts
and output rows have been completed. The original cleanup removes every mesh
datablock during its first loop iteration and then raises while selecting an
already-unlinked object, before it can print the result JSON, on Blender 3.6.
Skipping that process-exit-only cleanup changes no placement, camera, render,
data, or artifact and is recorded separately in runtime provenance.

The SQLite runtime index is limited to API catalog and pre-queue validation.
Original V4 CSV files remain authoritative for object selection and coordinate
generation. Native missing-triplet and render failures are reported; they are
never recovered through another model. The source bathroom combination file
contains no usable rows, so the bathroom preset is intentionally unavailable.

## Service flow

1. API Gateway validates a request, enforces anonymous quotas, and queues one
   SQS message per scene.
2. The renderer maps the request to V4's terminal choices, invokes the original
   coordinate and Blender code, and calls the original GIF builder.
3. Results include both original V4 stills, its formation GIF, a resized WebP
   copy of that same animation, placement CSV, request JSON, and a provenance
   manifest under `s3://soilie3d-data/generated/`.
4. Scenes remain temporary for seven days by default, can be discarded, or can
   be copied into the public gallery. Only manifests marked as original V4 are
   eligible for publication; retired approximate gallery records are hidden.

Publishing uses an optional display name and removal password. Only a salted
PBKDF2-SHA256 verifier is stored in a separate encrypted, public-access-blocked
S3 bucket. The plaintext password is never retained.

The comparison-study endpoints are deployed but closed. Both the environment
and `study/cases.json` must explicitly enable collection after ethics,
recruitment, consent, and frozen-stimulus work is complete.

## Local validation

```powershell
python -m serverless.compiler.build_runtime --repository . --output .codex/runtime
python -m unittest discover -s serverless/tests -v
$env:PYTHONHASHSEED = "0"
python -m serverless.benchmark.evaluate_v4 --runtime .codex/runtime/v4 --output serverless/benchmark/results/soilie-exact-v4.json
```

The fixed 32-attempt machine audit exercises original V4 object selection and
`calculateCoords`. It is an implementation diagnostic, not a plausibility or
final-collision score, because V4 performs important corrections later in
Blender. The room-fitting extension is excluded from the audit.

## Deployment

```powershell
.\serverless\scripts\deploy.ps1 -Profile darkest -Region ca-central-1
```

The renderer targets standard Lambda only while the uncompressed image stays
below 9.5 GiB and representative six-object scenes remain below the 12-minute
p95 gate. If either gate fails, move the same immutable image and message
contract to scale-to-zero AWS Batch on Fargate; do not weaken or replace V4.

Refresh the deliberate public Data Archive index without enabling anonymous S3
listing:

```powershell
.\serverless\scripts\publish-data-index.ps1 -Profile darkest -Region ca-central-1
```
