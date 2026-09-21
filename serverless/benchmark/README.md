# Room-generation comparison

The evaluator consumes untouched final scene geometry. Optional website room
sizing is excluded. The original publication model remains in `modules/` and
is staged, byte-checked, by the existing runtime compiler.

## Evidence boundaries

- SOILIE: final placement after its existing Blender corrections. A fresh
  worker includes object selection, initialization and placement. Rendering is
  excluded, and failed attempts and timeouts consume batch time.
- LayoutGPT: all 476 official released GPT-4 layouts at commit
  `fc31954962553e5b65bf267a904a6930d50b1f5e`. Native pixel geometry is preserved.
  Inference timing, complete token usage and physical mesh support are absent.
- Infinigen Indoors: initial Indoors release
  `fb7991e06580639202a4687937082cb63e931eb0`. The original single-room `coarse`
  task without `fast_solve` is the full-quality reference. A separately labelled
  matched-furniture profile uses the release's documented `fast_solve.gin`, skips
  shelf-trinket population, and narrows tags to room-scale furniture. Neither
  profile is only a bounding-box proposal or an image-rendering benchmark.
- GRAINS: the paper's 1,027 seconds for 10,000 bedrooms remains a published,
  different-hardware reference. Removed pretrained weights prevent a new run;
  screenshots are not reconstructed as geometry.

## Shared geometry policy

`solid_overlap.py` observes evaluated Blender triangle meshes without moving the
scene. Disjoint world-space bounds prove that two meshes cannot intersect. If
the bounds intersect, exact occupied-volume percentages are emitted only when
both operands and their Boolean intersection are closed manifold solids. Open
or otherwise invalid source topology is reported as unavailable, never as a
zero-volume collision. Run the Blender fixtures with:

```bash
blender --background --factory-startup --python-exit-code 2 \
  --python serverless/tests/blender_solid_overlap_fixtures.py
```

`geometry.py` also keeps a separate cross-source envelope diagnostic using
eight world-space oriented-box corners per object instance. That diagnostic is
available for LayoutGPT's box-only release, but it is never described as a
solid-mesh collision measurement.

Boundary intrusion is the outside portion of each object's projected footprint,
averaged per scene. The original room polygon is used, not a newly fitted room.
Architecture is explicitly classified in `ARCHITECTURE`; parts of one semantic
assembly do not collide with each other. Importers must preserve instance IDs
and group parts into one semantic object before matching counts.

Connected space erodes the room by 0.3 m and expands obstructions by 0.3 m for
a 0.6 m-wide, 1.8 m-tall circular footprint. It reports the largest connected
component as a percentage of room area, not a building-code or accessibility
certification. Support gaps require real lower-mesh ray samples; unknown
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

Every failed supported selection retains its error trace and attempt record.
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
iteration configuration. `matched-furniture-fast` additionally disables small
shelf items and restricts the workload to room-scale objects. Profile identity
and exact Gin overrides are checkpoint provenance and cannot change on resume.
A missing dependency, timeout, or unfinished population stage is a failure, not
a zero-quality scene. Geometry export must be validated against the final scene
before results are published.

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
this boundary. The earlier two interrupted Infinigen executions are disclosed
in `infrastructure-incidents.json`; their quarantined output is never imported.
Lost timing stays unavailable instead of silently improving campaign throughput.

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
cost. Comparable LLM free quotas or credits must also be considered when they
apply; the existence of an LLM alone does not imply a nonzero API bill.

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
closed evaluated meshes are available. Cross-model rates are calculated only
inside shared room-type, object-count and density strata, with each shared
stratum receiving equal weight. A missing mesh result is unavailable rather
than a pass. Pass/fail classification allows at most 0.0001% (one part per
million) numerical contact. Raw overlap and boundary distributions retain the
unrounded values, so this tolerance does not hide the measured amount.

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

Each run's `generationBreakdown` separates completed-attempt time, failed-attempt
time, and the timeout subset of failures. Their total equals the sum of model
attempt stopwatches, excluding read-only observation overhead. Queue gaps,
pauses, other benchmark cohorts, validation, uploads and image rendering never
enter that sum. Active placement retries are not idle waiting: retain them in
all-attempt throughput, while reporting completed-scene latency separately.

`stimuli.py` freezes a configurable number of unique matched pairs with seeded
sampling and no quality-based selection. A deterministic maximum-cardinality
one-to-one matcher prevents an early flexible match from stranding a baseline
scene that has only one eligible counterpart. Eligibility fixes room type,
furniture count, bedroom bed count, a 0.25 footprint-density difference, and at
least 40% duplicate-aware normalized object-role agreement. Results are also
split at two-thirds role agreement so broader matches cannot hide the more
comparable subset. Neutral plan, oblique and 3D bird's-eye oriented-box views
are immutable and method-blind. Illustrative high-intrusion examples on the
Research page are a separate selection and never feed pilot sampling.

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
