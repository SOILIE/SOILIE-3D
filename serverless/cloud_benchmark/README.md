# Temporary platform benchmark

Private, finite Lambda execution of the tracked SOILIE placement pipeline.
The website and its production renderer are not changed by these tools.

## Frozen functional-use-v2 pilot

This is a bounded calibration step, not a new publication or full review. The
original room-function responses retain their original prompts and stimuli.
The four revised dimensions use the versioned instructions and observation
schema in `study/structured_rubric.py`; v1 remains independently resolvable.

- Select 32 pairs (eight per baseline × room type) with seed
  `functional-use-v2-pilot`. Exclude the 38 earlier inspected controls and all
  32 v1 pilot pairs. The final 480-pair sample is not changed.
- Two streams per dimension each receive 32 main cases and eight reversed
  controls: 256 main judgments and 64 controls. Every judgment has an isolated
  GPT-5.6 Sol context at Extra High effort, without tools or previous answers.
- Numbered objects have the same key in all three views. Geometry and functional
  fronts are unchanged. A room is rasterized once; controls swap identical pixels.
- Every required object or pair receives a short structured observation.
  Computed within-room box-volume ratios are supplied only for relative size.
  Source identities, quality scores and earlier answers are not delivered.
- Acceptance requires **15/16 exact agreements in every dimension, including
  ties**. Tie/preference changes and opposite-winner reversals are also reported
  separately. Decisive agreement excludes pairs with a tie on either viewing;
  it never replaces the exact-agreement gate.

From the backend repository, prepare a new directory, rasterize it using the
website's project-local Playwright installation, and preflight it:

```powershell
$pilotRoot = '.codex/benchmark/soilie-platform-grid-final/review-functional-use-pilot-v2'
python -m serverless.cloud_benchmark.staged_pilot prepare --source .codex/benchmark/soilie-platform-grid-final/review-functional-fronts-v3 --root $pilotRoot --version functional-use-v2 --previous .codex/benchmark/soilie-platform-grid-final/review-functional-use-pilot-v1
node serverless/cloud_benchmark/render_staged_pilot.mjs $pilotRoot PATH_TO_WEBSITE/.codex/browser
node serverless/cloud_benchmark/check_numbered_pilot.mjs $pilotRoot PATH_TO_WEBSITE/.codex/browser
python -m serverless.cloud_benchmark.run_staged_pilot --root $pilotRoot --preflight-only
```

Only with explicit collection authorization, add `--authorize-review --workers 3`
instead of `--preflight-only`. The runner resumes missing assignments only and
halts on invalid or interrupted attempts. `--recover-completed` imports a saved,
completed model response without making another call; it never replaces answers.
Do not alter a frozen prompt or rerun a disagreement.

After all 320 answers validate, `staged_pilot finalize --root ...` creates the
local report, and `staged_pilot checkpoint --root ... --output ...` exports all
answers with exact prompts, schemas, evidence and hashed context identifiers.
`pilot_report --checkpoint ... --output ...` derives the internal Markdown
tables directly from that audited export, without another model call.
If the pilot passes and its protocol remains unchanged, all 256 main judgments
are reusable. Otherwise all answers remain development evidence. Controls never
contribute preference votes. Neither outcome authorizes continuation, a version
bump, push or deployment. This repeat check does not establish accuracy or
population reliability.

## Design

| Room preset | Local | AWS Lambda |
| --- | ---: | ---: |
| Bedroom | 2,500 retained, support-corrected | 2,500 new |
| Living room | 2,500 new or already completed | 2,500 new |

`design.py` freezes requests before examining results. Each new-generation
condition requests 625 scenes at each object count, 3–6, with duplicates enabled.
Living-room shards 00–07 are local; 08–11 split at request 125; 12–19 are cloud.
Independent fixed bedroom seeds start at 120260924. Failures remain evidence,
not completed scenes. None of these runs renders images or uses web room sizing.

Local bedrooms keep their original generation durations and separate repair
durations. New local runs have six one-thread worker slots. Cloud timings identify
the Lambda memory configuration, immutable image and source checksums. Placement
duration excludes geometry-measurement overhead; billed duration includes startup,
measurement and result storage. Do not label either as an uncontended local run,
combine platforms into one latency distribution, or substitute old serial timings.

## Execution

1. Keep model/observer source unchanged during active work. Place `STOP` in the
   superseded balanced campaign and stop its owned workers at checkpoints. The
   `local` module refuses active competing workers, reuses successful requests,
   and creates provenance-pinned segments for missing seeds only. It retries a
   failed request at the same seed and stops repairs at 2,500 retained bedrooms.
2. Run `deploy.ps1 -BaseImage <renderer-uri>@sha256:<digest>` with the authorized
   AWS profile and the current, digest-pinned renderer image. The isolated image
   inherits Blender and the checked model assets, copies model code directly from
   the checkout, and checks every runtime asset during the build. There is no
   fallback base digest: an older renderer must fail instead of being reused.
3. Run `pilot` against four local scenes per room type, covering counts 3–6.
   All eight cases must have identical requests, selections and geometry within
   0.00001 m. Lambda denies `PR_SET_PDEATHSIG`; the cloud adapter instead uses an
   owned process group and the service watchdog. It changes no model function.
4. Run `campaign` with its frozen plan, parity receipt and budget. It starts with
   16 active calls, then allows up to the requested concurrency as the budget
   permits. Every active call reserves 915 seconds of compute, not its expected
   average. The default US$25 ceiling includes a US$1 setup allowance; unused
   AWS free tier is not assumed. Reservations are not incurred spending.
5. Ambiguous network outcomes stop new dispatch. Reconcile S3/receipts first;
   never blindly resubmit paid work. Downloads are checked against S3 checksums.
   `recover_response` can reconstruct a missing client receipt from a unique S3
   artifact and its CloudWatch billing report without invoking Lambda again.
   Keep failed-call costs even when a repaired scene is later regenerated.
   After a validated repair and a new eight-scene parity pilot, explicitly name
   a reconciled failure with `--retry-failed-seed`. Its original artifact, receipt
   and charge are archived before retry. `--max-new-calls 1` verifies this repair
   before the remainder is dispatched. Every replacement image starts with a
   16-call wave, even when an earlier image already completed many requests.
6. After completion, `cleanup` verifies all 5,000 downloaded results, archives all
   private bucket objects and CloudWatch logs, and removes only the temporary
   Lambda stack, private bucket and ECR repository. A failed validation prevents
   deletion. Preserve the local evidence for the later cross-model analysis.

All generated inputs, receipts, artifacts and logs belong under the repository's
`.codex/` directory. Keep account/resource identifiers out of website summaries.
The temporary bucket is deliberately separate from the published research data.

Support measurement uses actual triangle contact, including crossing edges that
sparse rays can miss. `benchmark.reobserve_contacts` rechecks flagged archived
scenes without changing geometry or generation timing. Cleanup validates these
source-checksummed observations alongside the immutable cloud receipts.

## Final evidence and AI review

`evidence` verifies the complete cloud allocation and its immutable source
checksums, applies only audited contact/correction overlays, and caches shared
geometry measurements. Supply `--grid`, `--baselines` (the existing measured
baseline export), and `--output`. This step does not publish partial results.

After local completion, `finalize --grid ... --campaign ... --cloud-evidence ...
--output ...` audits all four groups and freezes the review protocols. Add
`--wait` to leave this preparation running behind the local campaign. It starts
no reviewers and performs no cloud calls or deployment. `readiness.json` records
the stage; standard error retains any validation failure. A full-cohort gate
requires exactly 2,500 distinct scenes in each room/platform condition and
complete final support and mesh-intersection observations. Original generation
and downstream correction durations remain separate.

The new review draws from the entire 10,000-room pool, never from the fastest
completed subset. Matching is performed separately for bedrooms and living
rooms, with up to 120 pairs per room type/baseline. Existing object-count,
room-anchor, role and density rules remain unchanged. If a baseline has fewer
eligible cases, report that count; do not loosen matching to fill a quota.

Ten independent review contexts cover five dimensions, two contexts each:
orientation, relative size, relationships, access, and room function. Frozen
packets retain the exact prompt and method-blind three-view illustrations with
within-room volume annotations. Sessions independently shuffle and balance
left/right presentation; two reversed repeats per baseline check consistency.
Private session credentials stay separate from the reviewer packets and public
exports. No votes carry over from changed stimuli.

`node serverless/cloud_benchmark/render_packets.mjs REVIEW_ROOT PLAYWRIGHT_ROOT`
renders the packets using an existing project-local Playwright installation.
Each independent reviewer receives only their own `packets/reviewer-NN/`
prompt/case list and the referenced neutral PNGs. They write `answers.json`
with `set`, `caseId`, `judgement`, `errorChoice`, `confidence`, and `note`.
`submit_reviews --root REVIEW_ROOT --reviewer reviewer-NN` validates complete,
unique coverage and persists those answers through the same immutable study
service used by the API. It never synthesizes judgements. Use the existing
`study.export_pilot --require-complete` for each baseline only after all ten
contexts finish. Publication must also pass the website evidence/browser checks.

## Complete comparison coverage

`complete_coverage.py` combines the retained 10,000 SOILIE rooms, all 476 released
LayoutGPT layouts, all 366 recorded LayoutGPT API proposals, and 569 completed
Infinigen CPU outputs. Unmatched proposals stay in geometry distributions.
The 71 completed Infinigen Lambda calls contribute timing and usage records;
their saved artifacts lack the surface-tag dictionary needed by the pinned
native floor exporter, so no geometry/contact scores are imputed to them.

CPU timing distributions retain hardware, thread allocation and workload.
Concurrent latency is not isolated latency or aggregate batch throughput.
Remote inventory timings remain a separate `cpu-vm` stratum described as an
x86-64 KVM virtual machine in the Intel Xeon Sapphire Rapids family with 32
vCPUs and 118 GiB RAM. Each row also records its Blender thread allocation and
that workers ran concurrently. Those receipts do not establish a
provider-specific price, so the VM stratum remains unpriced.
Unused requests in deliberately stopped API plans are not attempted calls:
every actual ledger entry must have a matching, verified completed response.
Uncertain charges or missing actual calls block publication.

Read saved Infinigen scenes with `inspect_infinigen_support.py --contacts-only`
through `recheck_native_floors.py`. Every native object keeps its transforms.
Triangle crossings with the floor have zero positive separation, while
below-floor depth remains separate. The dense diagnostic mode checks outliers
without replacing other measurements. Never describe zero separation as proof
of stable support. Frozen AI packets and responses are not modified by this
quantitative measurement pass.

Pass the campaign receipt root explicitly with `--inventory-root`; its neutral
layout has `local/` and `remote/` children. `complete_coverage` also accepts an
explicit audited `--review-source` and repeatable `--api-export` arguments, so
publication assembly does not depend on a machine-specific scratch layout. Omit
`--api-export` to use all six standard export directories; if overriding them,
pass the option once for each required export directory.

```powershell
python -m serverless.cloud_benchmark.complete_coverage --backend . --website <website-root> --contacts .codex/contact-audit --output .codex/publication-coverage --inventory-root .codex/remote-inventory --review-source .codex/remote-inventory/review-functional-use-v4-audited
python -m serverless.cloud_benchmark.publish_counterbalanced --directory .codex/publication-coverage --version 0.2.2 --corrected .codex/contact-audit/corrected-rows.json
```

### Common enclosing-box support diagnostic

`compile_box_support` downloads the exact quantitative cohort from the public
archive, verifies every source hash, and computes vertical box separation on
local CPUs. LayoutGPT uses the already verified source-room metre scale.
No meshes, layouts or AI judgments are regenerated. Boxes with tilted faces
use convex-face constraints, not world-axis-aligned height approximations.
Ceiling/wall-mounted categories are excluded by one shared category policy.
The floor is a reference plane: floor holes and furniture's empty internal
spaces are outside this diagnostic. Touching or crossing boxes have zero
separation, which is not evidence of real mesh contact or stable support.

```powershell
python -m serverless.cloud_benchmark.compile_box_support --publication .codex/publication-coverage --output .codex/publication-box --cache .codex/box-support/inputs
python -m serverless.cloud_benchmark.publish_counterbalanced --directory .codex/publication-box --version 0.2.2
```

The box export retains per-object distances, source geometry hashes, exclusions,
and floor versus other-box candidates. Once the verified export is archived,
the downloaded geometry cache can be removed; it is reproducible from S3.


## Controlled comparison campaigns

These tools extend the comparison inputs, not the SOILIE model. All working
data, private API receipts, reviewer sessions and logs stay under `.codex/`.
Do not publish credentials, session tokens, local paths or billing identifiers.

### LayoutGPT living rooms

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

### Infinigen expansion

`serverless.benchmark.expand_infinigen` retains the existing 20 matched rooms
of each type and targets 100 additional matched rooms per type. Two local
workers each use four Blender threads. The pinned initial Indoors release and
native asset generation, placement and solving remain unchanged. Living rooms
retain `controlled-six-fast`. New bedrooms use `controlled-count-fast`, with
counts assigned in a repeating 3, 4, 5, 6 sequence before generation.
Concurrent runs are for geometry and review coverage, not isolated timing claims.

```powershell
wsl -- .codex/linux-env/bin/python -m serverless.benchmark.expand_infinigen --output .codex/benchmark/infinigen-expanded-120 --evidence .codex/benchmark/soilie-platform-grid-final/evidence --original-protocol .codex/benchmark/soilie-platform-grid-final/review/set-b/protocol.json --supplement-protocol .codex/benchmark/soilie-platform-grid-final/review-extension/set-b/protocol.json --repository .codex/infinigen --site-packages .codex/infinigen-env/lib/python3.10/site-packages --blender .codex/tools/blender-3.6.0-linux-x64/blender --vary-bedroom-counts
```

The same command resumes checkpoints. A lock prevents duplicate controllers.
Bedrooms start at decimal seed 4000; living rooms start at 5100 (the runner's
100 offset plus 5000). Failed seeds advance the fixed sequence and remain in
private attempt records. Per-room checkpoints report valid outputs and matched
pairs separately. The controller stops at 120 pairs per type or 200 new attempts
per type. Below 15 GiB free disk, workers wait between scenes until verified
uploads restore at least 20 GiB of headroom. This wait does not enter generation
timings or change an active scene's watchdog.

Bedroom inventories are nested: bed, bedside table and floor lamp (3), then
storage (4), desk (5), and rug (6). These are solver input constraints; no object
is deleted from a generated room. The same bedside-distance objective remains
active at every size. Composition validation requires exactly one of every
requested role, rejecting missing, extra or duplicate instances.

`bedroom-count-schedule.json` freezes the amendment's first attempt, count cycle,
role inventories, source campaign hash and pre-generation capacity audit.
Already-started six-object attempts finish under their original configuration;
completed rooms and frozen reviews are unchanged. Failed attempts advance the
same fixed schedule. The source SOILIE cohort remains exactly 10,000 rooms.
Inventory capacity is an upper bound, not a promise of eligible matches: the
semantic and density requirements below still apply. A deficient capacity stops
that room worker rather than generating indefinitely. A per-room `STOP` file
requests a clean stop after its current checkpoint; remove it before resuming.

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

### S3 output streaming and local eviction

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

#### Backfill older completed Blender files

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

#### SOILIE retention boundary

The frozen `soilie-platform-grid-final/evidence/cohort.json` defines the final
10,000 SOILIE rooms: 2,500 in each room-type/platform condition. Retain those
rooms and the source/correction records identified by the cohort's hashes.
Earlier generation records outside that membership are disposable; do not
upload them as extra final-cohort scenes. Keep the compact execution accounting
and frozen reviewer materials independently of these unused generation copies.

### Review release gate

#### Parallel expansion after one room stratum finishes

Resume `expand_infinigen` with `--workers 6 --blender-threads 1` when only
one room type remains unfinished. Six native processes receive consecutive
seeds from the frozen inventory schedule. Checkpoints commit in seed order,
not completion order. A started run retains its original thread setting;
new runs record the requested setting. The original campaign and model inputs
are unchanged; separate `execution-*.json` receipts record scheduling changes.
These concurrent expansion timings remain excluded from isolated latency plots.

Per-attempt `expansion-entry.json` receipts preserve finished work across a
controller interruption. Only the coordinator updates the room checkpoint.
Keep the S3 streaming uploader running alongside generation. On Windows, use
a hidden `Start-Process` with logs redirected into the campaign directory to
keep both workers independent of an IDE terminal session.

#### Freeze a completed room type while the other continues

`serverless.cloud_benchmark.expanded_reviews` checks the completed room's
checkpoint, geometry checksums and exact selected pairs before preparing ten
reviewer sessions with the existing prompts. It rejects unfinished strata and
does not modify earlier pairs or responses. Use a new output directory for
each frozen increment, then render its neutral image packets:

```powershell
python -m serverless.cloud_benchmark.expanded_reviews --evidence .codex/benchmark/soilie-platform-grid-final/evidence --original .codex/benchmark/soilie-platform-grid-final/review --extension .codex/benchmark/soilie-platform-grid-final/review-extension --campaign .codex/benchmark/infinigen-expanded-120 --output .codex/benchmark/soilie-platform-grid-final/review-wave-3 --room-types living_room --layoutgpt .codex/benchmark/layoutgpt-controlled-supplement/export.json
node serverless/cloud_benchmark/render_packets.mjs .codex/benchmark/soilie-platform-grid-final/review-wave-3 <website-root>/.codex/browser
```

The optional LayoutGPT supplement contributes just its remaining unreviewed
pair. For the subsequent bedroom increment, omit `--layoutgpt` and use
`--room-types bedroom` in another output directory. Submit each complete
answer file through `serverless.cloud_benchmark.submit_reviews`; combine
finished increments through `review_reports --additional`, including their
`source-scenes.json` files as `--extra-export` inputs. Private session files
are not public research artifacts.

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

#### Combined, room-specific review reports

`serverless.cloud_benchmark.review_reports` reads the immutable study databases,
checks every selected response against its assignment and exact prompt, and
combines disjoint reviewed pairs. Source versions, case IDs and protocol hashes
remain in the export. The original LayoutGPT bedroom cases and controlled living
cases are included; the seven released living cases are outside this selection.
Repeated presentations contribute only to consistency checks. Preferences and
pair-clustered intervals are recomputed per question and room type; questions
are never merged into an overall score.

```powershell
python -m serverless.cloud_benchmark.review_reports --evidence .codex/benchmark/soilie-platform-grid-final/evidence --original .codex/benchmark/soilie-platform-grid-final/review --extension .codex/benchmark/soilie-platform-grid-final/review-extension --extra-export .codex/benchmark/layoutgpt-controlled/export.json --extra-export .codex/benchmark/infinigen-controlled-living-supplement/export.json --output .codex/benchmark/soilie-platform-grid-final/review-preview --preview
```

An explicit `--preview` permits completed subsets for local inspection, but sets
`releaseEligible: false`. Without it, each baseline must have exactly 120 pairs
per room type and all ten reviewer slots complete on every pair. Use
`--additional` for a later frozen review directory and `--extra-export` for its
source geometry. Identical questions, reviewer configuration and matching rules
are required across inputs. Reused scene IDs or incomplete judgements are errors.

Outputs contain compact summaries, downloadable responses, selected SVG stimuli
and `review-manifest.json` with report hashes and the final geometry-cohort hash.
Private credentials, session IDs and database files are not exported. These files
do not enable website publication automatically; the final comparison build must
verify the manifest and completed generation coverage before setting `aiReview.ready`.

### Publication preparation

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

#### Final release gate and publication

Physical clearance can include LayoutGPT without retrieving substitute meshes.
Prepare its source-room metadata and scale evidence:

```powershell
wsl -- .codex/linux-env/bin/python -m serverless.benchmark.layoutgpt_scale --evidence .codex/benchmark/soilie-platform-grid-final/evidence/measured-scenes.json --released .codex/benchmark/layoutgpt/gpt4.bedroom.k-similar.k_8.px_regular.json --exports .codex/benchmark/layoutgpt-controlled/export.json --exports .codex/benchmark/layoutgpt-controlled-supplement/export.json --cache .codex/layoutgpt-scale-cache --living-cache .codex/benchmark/layoutgpt-controlled/data --output .codex/layoutgpt-scale.json
```

Pass `--layoutgpt-scale .codex/layoutgpt-scale.json` to `publication_views`.
It checks exact prediction hashes and complete coverage. Only the clearance
metric changes; the frozen geometry and AI review hashes remain identical.
The authors' `render_from_files.py` uses the shorter metadata floor side / 256
to denormalize pixel predictions. Released prompt dimensions need not reproduce
the current metadata's normalized extent; preserve that supplied rectangle
rather than substitute another floor. Controlled calls additionally validate
their known prompt preprocessing. Metadata files are CRC-checked against the
authors' archive and individually SHA-256-pinned in the public supplement.
Mesh support, below-floor mesh depth and solid collision remain unavailable
for these box-only LayoutGPT outputs. Do not assign them synthetic zeroes.

Add `--expansion .codex/benchmark/infinigen-expanded-120`,
`--controlled .codex/benchmark/infinigen-controlled-living-supplement/export.json`
and `--reviews <completed-review-export>` to the publication command. Both
expansion checkpoints must be complete; the review manifest must certify exactly
120 bedroom and 120 living-room pairs per baseline, with ten completed reviewer
slots. Every reviewed scene digest is checked against the published geometry.
The output sets `aiReview.ready` only after those checks.

To queue only unanswered assignments from multiple frozen increments, use:

```powershell
python -m serverless.cloud_benchmark.review_work prepare-additional --evidence .codex/benchmark/soilie-platform-grid-final/evidence --source .codex/benchmark/soilie-platform-grid-final/review-wave-3 --source .codex/benchmark/soilie-platform-grid-final/review-wave-4 --output .codex/benchmark/soilie-platform-grid-final/review-final-work
python -m serverless.cloud_benchmark.review_work submit --output .codex/benchmark/soilie-platform-grid-final/review-final-work --reviewer reviewer-01
```

Never regenerate this queue while reviewers are using it. Partial answer files
must remain a prefix of the frozen assignment order. The submitted receipt
verifies every answer was persisted in its original study session.

The final website bundle includes comparison summaries, per-room metrics,
object support measurements, review prompts/responses, and hash-named stimulus
images. Geometry charts include all valid scenes, not only visually reviewed
pairs. Concurrent expansion timings do not enter isolated latency comparisons.
A three-to-six-object bedroom inventory is requested before generation;
the original six-object observations remain included, with their configurations
identified in source evidence.

Publish the public bundle to the dated Data archive after validation:

```powershell
python -m serverless.cloud_benchmark.publish_release --directory .codex/benchmark/soilie-platform-grid-final/publication-final --date 2026-09-24 --version 0.2.1 --publish
```

This uploads only an explicit public allowlist, verifies remote SHA-256 receipts,
then merges the Data index conditionally. It does not replace the streaming scene
manifest, delete objects, or upload private sessions. A changed file cannot
overwrite an immutable release object.

Copy those same public artifacts into the website's `benchmarks/` folder and
run its unit tests and build. `scripts/check-comparison-browser.mjs` then serves
the actual built release locally and checks room-specific votes, common graph
scales, exact reviewer inputs, keyboard controls and mobile reflow. Set
`PLAYWRIGHT_ENGINE` to `chromium`, `firefox` or `webkit`. It closes its browser
and loopback server in all cases.

For a deliberately incomplete preview, omit `--reviews` and use
`scripts/check-balanced-comparison.mjs` with `COMPARISON_PREVIEW_DIR`.
The website must not fetch or display AI results when the preview gate is closed.
