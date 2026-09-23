# Room-generation comparison

The evaluator consumes untouched final scene geometry. Optional website room
sizing is excluded. The original publication model remains in `modules/` and
is staged, byte-checked, by the existing runtime compiler.

## Evidence boundaries

### Balanced room-type campaign

`balanced_campaign.py` freezes the first 5,000 completed bedrooms in source
filename order, independent of quality scores. Their source bytes and original
timing are retained; only downstream vertical support correction is replayed.
Validated repair checkpoints can be reused if only mesh-restoration code changed.
The model, mesh sampler, solid-overlap evaluator and asset checksums must match.

The 5,000 new living rooms use the ordinary tracked pipeline, with duplicates
allowed and 1,250 completed requests at each requested object count from 3 to 6.
Twenty 250-scene shards use interleaved, non-overlapping seed sequences. Six
local jobs (one Blender thread each, single-threaded BLAS) share an eight-CPU
WSL instance. Repair jobs and generation shards advance together; no images are
rendered. The watchdog remains 900 seconds and unsuccessful attempts are kept.

```bash
.codex/linux-env/bin/python -m serverless.benchmark.balanced_campaign \
  --source .codex/benchmark/soilie-bedroom-v4.0.2 \
  --repaired .codex/benchmark/soilie-support-corrected-v3 \
  --runtime . --blender .codex/tools/blender-3.6.23-linux-x64/blender \
  --output .codex/benchmark/soilie-balanced-v4.0.2 --workers 6
```

Refresh `.codex/runtime/v4-provenance.json` from the committed source before
launch. Resume with the same command and unchanged execution code. The manifest
pins bedroom checksums and the request schedule; each shard checks its own
checkpoints rather than trusting a controller's last progress message. Create
`STOP` inside the campaign directory to stop scheduling after active shards
finish. Interruptions terminate only owned subprocesses; complete scene records
remain available for resume. A 15 GiB disk guard protects the remaining workspace.

Keep the measured serial-bedroom timing distinct from the new parallel living
workload; do not impute mixed-corpus time from the bedroom sample. Before
publication, audit all 5,000 corrected bedrooms and all 5,000 new living rooms,
then create bedroom- and living-room-specific matched review sets. Changed
images require fresh AI votes. The existing comparison remains unchanged until
that complete evidence bundle passes validation.

### Baseline evidence

- SOILIE: final placement after its existing Blender corrections. A fresh
  worker includes object selection, initialization and placement. Rendering is
  excluded. Failed attempts and watchdog terminations are reported as counts
  but do not enter successful-layout latency or throughput.
- LayoutGPT: all 476 official released GPT-4 layouts at commit
  `fc31954962553e5b65bf267a904a6930d50b1f5e`. Native pixel geometry is preserved.
  Inference timing, complete token usage and physical mesh support are absent.
- Infinigen Indoors: initial Indoors release
  `fb7991e06580639202a4687937082cb63e931eb0`. The original single-room `coarse`
  task without `fast_solve` is the full-quality reference. A separately labelled
  room-scale profile uses the release's documented `fast_solve.gin`, retains the
  full primary and medium furniture domain, and skips the decorative small-object
  population stage. A second, controlled profile adds a disclosed six-object
  room constraint through `infinigen_controlled_entry.py`. Infinigen still
  selects assets and performs placement, annealing, collision handling, and
  scene construction; the profile exists only for count-matched comparisons
  and is not presented as the untouched native workload. Objects are never
  removed after solving. Because reduced-iteration solving can serialize an
  unsatisfied constraint graph, the runner verifies the six semantic roles in
  `solve_state.json`; incomplete seeds advance the deterministic sequence and
  do not count toward the 40-room cohort. None of these profiles is only a
  bounding-box proposal or an image-rendering benchmark.
- GRAINS: the paper's 1,027 seconds for 10,000 bedrooms remains a published,
  different-hardware reference. Removed pretrained weights prevent a new run;
  screenshots are not reconstructed as geometry.

## Shared geometry policy

`solid_overlap.py` observes evaluated Blender triangle meshes without moving the
scene. Disjoint world-space bounds prove that two meshes cannot intersect. If
the bounds intersect, closed manifold operands use Blender's exact Boolean
intersection volume. Non-manifold assets use exact triangle-surface tests to
prove separation. Crossing open surfaces remain incomplete because they do not
enclose a mathematically defined volume. Penetration at or below the same
one-part-per-million tolerance used by the final validity check is classified
as numerical contact. Publication requires a complete result for every SOILIE
scene rather than silently reducing its sample size. Run the Blender fixtures
with:

```bash
blender --background --factory-startup --python-exit-code 2 \
  --python serverless/tests/blender_solid_overlap_fixtures.py
```

`geometry.py` also keeps a separate cross-source envelope diagnostic using
eight world-space oriented-box corners per object instance. That diagnostic is
available for LayoutGPT's box-only release, but it is never described as a
solid-mesh collision measurement.

Boundary intrusion is the outside portion of each floor-supported object's
projected footprint, averaged per scene. Wall-mounted and ceiling-mounted
instances use other support surfaces and therefore do not enter this floor
measure. The original room geometry is used, not a newly fitted room.
Architecture is explicitly classified in `ARCHITECTURE`; parts of one semantic
assembly do not collide with each other. Importers must preserve instance IDs
and group parts into one semantic object before matching counts.

Orientation stimuli use explicit source headings rather than inferring a front
from an unlabeled box. SOILIE assets are normalized so local +X is their front;
the final world-space direction is derived from the unchanged V4 transform.
LayoutGPT headings use its released orientation angle and local +X convention.
Every stimulus displays these headings as cyan arrows in all three views, and
generation fails if any object lacks a finite direction.

Connected space erodes the room by 0.3 m and expands obstructions by 0.3 m for
a 0.6 m-wide, 1.8 m-tall circular footprint. It reports the largest connected
component as a percentage of room area, not a building-code or accessibility
certification. Explicitly wall-mounted and ceiling-mounted instances are also
excluded from floor-level clearance. Support gaps require real lower-mesh ray samples; unknown
physical units or absent mesh samples produce unavailable values, never zero.

The shared `mesh_support.py` sampler combines actual lowest mesh vertices with
lower-surface rays and checks real supporting meshes, including the actual
floor. An earlier nine-ray grid could miss thin legs and is excluded from
published support scores. Sampling version 2 is validated using a narrow-legged
grounded table, a lifted table, an item supported on the tabletop, below-floor
geometry, and an object beyond the real floor. Run those Blender fixtures with:

```bash
blender --background --factory-startup --threads 1 --python-exit-code 2 \
  --python serverless/tests/blender_support_fixtures.py
```

Old support replays remain useful placement-parity evidence but cannot supply
current support scores. Repeat the same predetermined 40 attempts in a new
`soilie-support-mesh-v2` directory using the original `--support` command and
attach that directory only after parity passes. Never overwrite old checkpoints
or mix these observation replays into generation throughput. Positive sampled
gaps can still miss a contact; contact alone does not establish physical stability.

Support observations identify the nearest sampled mesh below the object as
`floor`, `object`, or `architecture`, with the supporting instance ID. A book
on a table is measured against that table, not against the floor. The floor and
object categories each average only their measured objects within a room;
absent categories remain unavailable rather than zero. Published distributions
then give each measured room equal weight. Wall-mounted objects are not tested
for vertical support, but their meshes may support other objects.

`settle_saved.py` restores saved placements without repeating selection or the
relational solver. It checks asset checksums, reconstructs the import/front
rotation order and both mass-centroid passes (including their normalization
scales), and rejects mismatching recorded bounds. `--observe-only`
adds support identities with no placement changes and checks sampled distances
against the source. `--audit-original` performs the same measurement check before
correction. The Blender restoration fixture additionally compares actual vertices,
including the last selected import whose import-axis rotation is baked first
and thin window coverings with nonuniform final dimensions.

For correction, the finite floor and actual supporting meshes stop vertical
motion; higher objects settle after their supports. Contact uses projected
triangle intersections, including edge crossings missed by vertex rays. The
ten-micrometre numerical contact tolerance is retained in recorded distances.
Changed scenes must pass fresh mesh-intersection measurements. Original attempts
stay untouched; derived records retain source/code checksums and separate
correction time. Do not transfer old AI votes to changed geometry or present
original generation time alone as timing the corrected pipeline.

Every pair involving a moved object is tested against restored mesh geometry.
Pairs with two untouched objects may retain the original disjoint-bounds proof;
otherwise float32 reconstruction can turn exact contact into a false microscopic
intersection. This reuse is counted as `preservedBoundsDisjointPairs`, requires
restored bounds within 0.00001 m of the originals, and never certifies overlapping
original bounds as separated. The solid-overlap implementation is checksummed
alongside the replay and settlement code.

```bash
blender --background --factory-startup --threads 1 --python-exit-code 2 \
  --python serverless/benchmark/settle_saved.py -- \
  --input .codex/benchmark/soilie-bedroom-v4.0.2 \
  --output .codex/benchmark/soilie-support-corrected
```

Reusing the output directory resumes only when source and correction checksums
match. `--start` and `--limit` allow bounded validation before the full replay.

Audit a running cohort without Blender, and require a complete one before any
publication. The report checks provenance, inventory, original timing, unchanged
room bounds, Z-only movement, contact distances and complete mesh checks. It
also lists the changed scene IDs that need new visual stimuli and AI judgements.
Remove `--require-complete` for a progress report; invalid records still fail.

```bash
python -m serverless.benchmark.audit_support_corrections \
  --source .codex/benchmark/soilie-bedroom-v4.0.2 \
  --derived .codex/benchmark/soilie-support-corrected \
  --output .codex/benchmark/support-correction-audit.json --require-complete
```

Matching uses room type, exact furniture count and 0.25-wide bins of summed
furniture footprint area divided by room area. Each shared stratum has equal
weight in comparisons. Native-output distributions retain unmatched scenes.
No scene is discarded because its quality score is poor. These observational
subsets are not identical-input experiments or an overall model ranking.

## Local execution (Linux / WSL)

Keep runtime assets, virtual environments and all working output in `.codex/`.
Blender 3.6.23 was used for the parity checks. Before a long batch, compare
`--full` and layout-only captures for the same fixed seeds. The original
functions run in both modes; layout-only exits after the final placement.

```bash
python -m serverless.compiler.build_runtime --repository . --output .codex/runtime
python -m serverless.benchmark.run_batch \
  --runtime . \
  --blender .codex/tools/blender-3.6.23-linux-x64/blender \
  --output .codex/benchmark/soilie-bedroom --target 10000 --seed 20260913
python -m serverless.benchmark.import_layoutgpt --output .codex/benchmark/layoutgpt
```

The batch cycles requested counts 3–6 with duplicates enabled. Repeating an
identical command resumes its completed checkpoints. A 900-second watchdog
records a timeout; it never substitutes a solver. `--max-attempts` permits a
bounded checkpoint session without changing the eventual completion target.
An OS lock prevents two writers from overwriting a run. Pauses between sessions
are not included in active-attempt seconds. Keep runs with different settings
in separate directories. Full-render parity runs are rejected by the
placement-timing publisher.

`--runtime .` deliberately executes the model source, data, and assets from the
checked-out commit. `.codex/runtime` contains generated API indexes and the
checksum provenance record only; it is not a second copy of the model.

`timing.py` records new invocation sessions separately from per-attempt timers.
It records the CPU model and visible RAM/CPU allocation without hostnames or
account identifiers. Calendar span includes pauses, while model time sums all
attempts after subtracting read-only observation time. Older checkpoints without
a session ledger do not acquire invented batch wall-time measurements.

For support diagnostics, rerun a predetermined seed range with `--support` into
a separate directory. Pass that directory as `--support-replays` to the
publisher. Every request, selection, and placement stage must match exactly
before support samples can be attached. The replay neither adds scenes nor
changes generation timings. Failed seed replays remain in the parity accounting.

The main SOILIE campaign uses `--solid-mesh-overlap`. Its read-only observation
time is recorded separately and subtracted from model generation timing.
Support remains a predetermined parity-checked replay because sampling every
object in 10,000 scenes would add observation work without changing placement.

Every failed supported selection retains its error trace and attempt record
during diagnosis.
A maintenance fix may continue a checkpoint only when fixed-seed parity proves
that previously successful scenes are unchanged and the affected failure is an
unintended edge case rather than an alternate placement method. The next session
records the new source provenance, and final accounting reports only failures
that remain active under the published maintenance version. Preserve concurrent
workload and machine-state records when interpreting desktop throughput.

For Infinigen use the documented standalone-Blender installation when the old
`bpy` wheel is unavailable. `run_infinigen.py` accepts its repository, Blender,
Python site-packages, output directory, and a frozen profile. `default` preserves
the original solver. `tutorial-fast` uses the release's documented reduced-
iteration configuration. `matched-furniture-fast` additionally disables the
small decorative-object stage while retaining the full room-scale furniture
domain. Profile identity
and exact Gin overrides are checkpoint provenance and cannot change on resume.
A missing dependency, timeout, or unfinished population stage is a failure, not
a zero-quality scene. `--per-room` specifies completed scenes for each requested
room type; unsuccessful seeds remain in the internal run evidence while the
deterministic seed sequence advances until that completed sample is reached.
Geometry export must be validated against the final scene
before results are published. Independent scene exports may run concurrently
with `import_infinigen.py --workers` only after generation timing has stopped;
each scene keeps its own geometry receipt and log.
If another benchmark process shared CPU during a completed scene, identify that
scene in an internal `--timing-exclusions` ledger. Its geometry remains in the
40-scene sample, but its contaminated stopwatch cannot enter latency summaries.
The room boundary uses Blender's emitted triangulation of tagged visible floor
faces and selects the dominant coplanar elevation. Every disconnected component
on that emitted layer remains an explicit room region; the observer neither
fills the gaps nor substitutes a convex hull.

`run_campaign.py` sequences the long local work after any existing Infinigen run
releases its OS lock: first-40 support replays and their parity check, the first
Infinigen case and its export, a 200-completion
living-room cohort, the 10,000-completion bedroom batch, and the rest of the
20-bedroom/20-living-room Infinigen workload. It requires explicit runtime,
Blender and pinned Infinigen paths, runs one heavy command at a time, and keeps
stage journals under `.codex/benchmark/campaign/`. Rerunning the same command
resumes unfinished stages; it does not publish, bump versions or deploy. A
finished command is not a substitute for validating its exported geometry.

`supervise.py` sets a Linux parent-death signal before exec, at every controller
and native-worker boundary. An abrupt controller exit therefore cannot release
its lock while a Blender child continues writing. A real parent-kill test covers
this boundary. Interrupted output is quarantined and never imported; lost timing
stays unavailable instead of silently improving campaign throughput.

## Cost accounting

`cost.py` snapshots AWS's public **Canada Central x86 on-demand** rate card and
records the source checksum, offer version and rate identifiers. The separate
GPT-4 tariff snapshot links to official model documentation. All amounts are
USD. The per-room comparison excludes credits, while the monthly hosting
scenarios explicitly show standard allowances available and exhausted.

`lambda_charge` prices a complete billed-invocation ledger, including failed
and skipped invocations, and divides by newly completed scenes. No completions
means cost per completed scene is unavailable, not free. Desktop elapsed time
is never silently converted into Lambda billed time. Parsed LayoutGPT objects
are not complete prompts or billing receipts.

The direct per-room comparison reconstructs the evaluated LayoutGPT GPT-4 call
from its official 3D-bedroom configuration: eight retrieved examples, the
released prompt template, the released room descriptions and the released
parsed layouts. It reports prompt-length percentiles against the checked GPT-4
tariff. The released outputs do not include billing receipts, the exact
retrieved examples, retries or provider-side token counts, so this remains a
reproducible estimate rather than an observed invoice.

SOILIE's side transfers the successful local placement-time distribution to a
generic 4 GB x86 Lambda scenario with 10 GB ephemeral storage and the checked
Canada Central tariff. It is not a measured cloud bill. Both sides stop at a
completed furniture-layout proposal; rendering, animation, storage, API
Gateway, data preparation and training are excluded. The comparison is hidden
when either cost profile fails validation rather than substituting a raw model
price or an unrelated request floor.

The monthly hosting panel includes Lambda, Cloud Run Jobs, AWS Fargate and
Cloudflare Containers. `hosting.py` pins anonymous public tariffs with sources
and a verification date. It applies each platform's per-job billing minimum,
allocated versus consumed CPU rules, included temporary storage and required
monthly plan fee. Cloudflare Workers alone are not treated as a native Blender
runtime. Cloud Run's writable files use RAM, so its illustrative configuration
includes more memory; candidate capacities are not identical hardware.

The same assumed 30-second lifetime is a **pricing scenario**, not measured
cross-platform performance or confirmation that the runtime fits every host.
Cloudflare's $5 plan is included even at zero usage. Startup, sleep delays,
minimum task charges and failed attempts must be accounted for before using
measured durations. Batching can amortize those costs but changes the workload.
Rented VM and local costs remain unpriced without an explicit utilization,
electricity/hardware budget or selected VM tariff. No provider is declared
universally cheapest. No additional paid deployments were made for this work.

The expandable Lambda calculation considers its 400,000 GB-second and
one-million-request account-wide allowances. It shows the same 600-invocation,
30-second workload with those allowances fully available and already exhausted.
These are generic workload assumptions, not the website owner's deployment
settings. Public artifacts must never include account identifiers, billed usage,
remaining allowance or other private AWS-account information. The publication
path validates the public rate-card schema and does not query AWS credentials
or usage APIs. Additional ephemeral storage is still priced, and all other AWS
services remain outside this worker-only estimate.
This practical low-volume subsidy is distinct from pre-credit computational
cost. Promotional API credits can also reduce cash cost when available; none
are assumed here. The existence of an LLM alone does not prove a nonzero bill.

## Publication and AI pilot

`publish_comparison.py` generates `comparison.json` and `measured-scenes.json`
from checkpoint artifacts, the LayoutGPT import, the checked rate card and
`selection-audit.json`. The latter replays the registered duplicate-disabled
sampler calls and explains all 14 shorter selections; it is not a quality score.
The compact `selection-fixture.json` preserves those inputs and selections.
Replay it
with `PYTHONHASHSEED=0 python -m serverless.benchmark.audit_selection`, supplying
`--runtime`, `--source` and `--output`; the output records the fixture checksum.

The publication also derives four direct pass rates from the same final scene
measurements: no oriented furniture-envelope intersection, full footprint
containment, both conditions together, and no occupied-mesh intersection where
evaluated meshes are available. Cross-model rates are calculated only
inside shared room-type, object-count and density strata, with each shared
stratum receiving equal weight. A missing mesh result is unavailable rather
than a pass; SOILIE publication rejects any such missing result. Pass/fail
classification allows at most 0.0001% (one part per million) numerical contact.
Raw overlap and boundary distributions retain the unrounded values, so this
tolerance does not hide the measured amount.

SOILIE's final placement is additionally compared with its measured relational
proposal. The report gives horizontal object displacement in centimetres,
absolute pair-distance change in centimetres, and pair-direction change in
degrees. These values answer how much collision handling altered the proposed
relations; they are not presented as cross-model scores because released
baseline layouts do not contain an equivalent intermediate proposal. The
number of distinct duplicate-aware object combinations and the most frequent
combination's share describe sampler breadth without claiming spatial quality.
The same diagnostic counts proposals with intersecting oriented furniture
envelopes and reports the fraction for which V4's ordinary separation stage
removes every such intersection. This correction rate is paired with the
relation-change distances rather than presented as evidence of plausibility by
itself.

Category co-occurrence fidelity compares conditional presence probabilities in
completed selections with the published bedroom combination catalog, separately
for requested counts 3 through 6. Duplicate instances count once because the
question is whether a category is present. Pairs below 5% in both source and
generated data are omitted so shared absences cannot inflate agreement. The
mean absolute percentage-point difference measures sampler fidelity to this
catalog only; it is analogous to the GRAINS paper's category statistic but is
not a cross-dataset model ranking or a placement-quality score.

Each run's `generationBreakdown` reports completed-attempt time and separately
counts failures by error code. Failed durations do not enter the reported
successful-layout latency or throughput because an operational watchdog cutoff
is not the generation time of an indefinitely stuck attempt. Queue gaps,
pauses, geometry observation, other cohorts, validation, uploads and image
rendering are also excluded.

`stimuli.py` freezes a configurable number of unique matched pairs with seeded
sampling and no quality-based selection. A deterministic maximum-cardinality
one-to-one matcher prevents an early flexible match from stranding a baseline
scene that has only one eligible counterpart. Eligibility always fixes room
type, furniture count, and bedroom bed count. The LayoutGPT study also caps the
footprint-density difference at 0.25 and requires 40% duplicate-aware role
agreement. The controlled Infinigen study uses all 20 six-object bedrooms and
requires two of the six normalized role families to agree. No bedroom is
excluded by density; after pair count and role agreement, closer density orders
otherwise eligible matches. Reviewers judge only the objects present. Neutral
plan, oblique and 3D bird's-eye oriented-box views are immutable and method-
blind. Illustrative high-intrusion examples on the Research page never feed
pilot sampling.

Controlled Infinigen front arrows use its canonical `Subpart.Front`
convention: positive local X transformed by the final asset-root pose. This is
the facing axis used when the solver tags canonical front surfaces, not a
direction inferred from the finished box shape.

The same frozen pairs can be evaluated in three explicitly separate evidence
conditions: views only, symmetric measurements only, and views plus
measurements. Numeric evidence includes only measurements available for both
rooms in each pair. In the current LayoutGPT comparison this permits envelope
and boundary intrusion; physical-unit clearance, floor penetration and support
are omitted when equivalent baseline evidence is absent. Missing values are
never treated as zero. Keeping the conditions separate reveals whether
measurements change a visual preference; combining their votes into one
undifferentiated score is not permitted.

Ten fresh-context reviewers use the common rubric and registered prompt
profiles in `serverless/study/service.py`. Signed invitations control identity,
model provenance and prompt profile. The server balances side assignment,
preserves sessions across reloads and rejects conflicting resubmissions.
Two reversed-side repeated cases test consistency and are excluded from main
vote totals. Human enrollment remains closed.

`serverless.study.local_server` serves the real service on loopback with SQLite;
Lambda uses the same service with DynamoDB. Keep browser smoke-test databases
and actual pilot databases separate. `export_pilot.py --require-complete`
requires all ten reviewers and exports AI-labelled records without private
invitations or bearer credentials. Do not publish smoke-test votes as independent
reviewers. A provider model ID that is not exposed must be reported as
unavailable, not guessed from the product name. Preference intervals resample
whole room pairs. The exact sign test first collapses repeated ratings to one
majority outcome per distinct pair, so ten ratings do not become ten room
samples.

The website scripts `pilot-browser.mjs` and `check-comparison-browser.mjs` use
Playwright, store artifacts in `.codex/`, and close their browser processes.
Stop the loopback server when finished. Browser automation fills only choices
supplied by a reviewer; it does not manufacture pilot judgments.

## Broad scene exploration and rolling evidence

`diversity.py` freezes a separate, finite 260-attempt workload before observing
quality: all four original presets and random mode, counts 3–6, both duplicate
policies, and 128 coverage-selected explicit inputs. Explicit inputs preserve
the order of existing catalog prefixes or recorded triplets with registered
assets and size entries. This reaches classes omitted by the refined preset
catalog without rebuilding it. Labels and label-pair coverage guide selection;
overlap scores and reviewer preferences never do.

The publication runtime's bathroom preset is empty. Four original-path probes
document that limitation; no replacement combinations are injected into its
sampler. An explicit bathroom-object input is not called a working bathroom
preset. `diversity.py --run ... --plan ... --output ...` exports actual completed
class coverage and all-attempt accounting separately. `publish_comparison.py`
rejects diversity runs, preventing exploratory input choices from changing the
controlled bedroom timing or geometric comparison cohort.

`run_campaign.py` sequences diversity before the long bedroom batch, and includes
the updated 40-seed mesh-support replay/parity checks. On Linux, the optional
`--handoff-after-stage PID` verifies an owned older controller, pauses only that
controller, lets its active child finish, and resumes checkpoints under the new
plan. It never pauses or terminates an active model worker to change the queue.

Additional pilot waves use `stimuli.py --exclude-protocol ... --limit ...` to
exclude previously reviewed scenes on both sides within each baseline. Do not
relax matching or reuse scenes merely to reach a requested sample count.
`serverless.study.combine_pilots` requires complete ten-reviewer waves, rejects
reused scenes, excludes reversed controls, and bootstraps whole pairs rather
than pretending that correlated votes are independent room samples.

`archive.py` publishes completed geometry and neutral plan/oblique diagrams
under `files/outputs/benchmark-YYYY-MM-DD/`. These are not full rendered rooms
or recency animations. It stores original AI-labelled exports, exact stimulus
paths, content checksums, combined pilot data and a generated discussion note.
`--extra-measurements` accepts separately labelled diversity evidence without
adding it to the benchmark comparison. Public geometry is allow-checked for
private execution metadata; invitations, session credentials and logs never
enter the archive.

Content-addressed artifacts are immutable, the manifest advances last, and
the existing gzip data index is merged with an ETag condition. No bucket policy,
listing access, deletion or lifecycle rule is changed. The dated archive lies
outside the temporary `generated/` prefix and therefore persists.

## Validation

```bash
python -m unittest discover -s serverless/tests -v
```

Fixtures cover touching, rotated, contained, stacked and intersecting boxes,
boundary fractions, physical-unit availability, clearance, checkpoint resume,
watchdogs, cost units, failed-attempt accounting, immutable submissions,
balanced blinded assignments, reloads and exclusion of human/test versions
from AI exports. Do not mark the release complete until the actual batches,
ten-reviewer pilot, browser checks and live deployment checks have finished.
