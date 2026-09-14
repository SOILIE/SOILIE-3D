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
  `fb7991e06580639202a4687937082cb63e931eb0`, original single-room `coarse` task,
  without `fast_solve`. This includes procedural mesh construction and scene
  serialization, not just a bounding-box proposal and not image rendering.
- GRAINS: the paper's 1,027 seconds for 10,000 bedrooms remains a published,
  different-hardware reference. Removed pretrained weights prevent a new run;
  screenshots are not reconstructed as geometry.

## Shared geometry policy

`geometry.py` uses eight world-space oriented-box corners per object instance.
For each furniture object it finds the largest intersection with a different
object and divides that volume by its own box volume. The mean of those object
fractions is the scene score. This is a bounding-volume proxy; empty space
inside a chair or a bed's enclosing box is not solid furniture.

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
  --runtime .codex/runtime/v4 \
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

The original model can fail even on supported selections. Error traces and
every attempt remain in checkpoint files, including the first 10,000 attempts.
Do not rename failed attempts as successful replacements or change source
code to make a benchmark finish. Preserve a record of concurrent workload and
machine state when interpreting desktop throughput.

For Infinigen use the documented standalone-Blender installation when the old
`bpy` wheel is unavailable. `run_infinigen.py` accepts its repository, Blender,
Python site-packages and an output directory. It attempts 20 seeded bedrooms
and 20 living rooms by default. A missing runtime dependency is a setup failure,
not a successful zero-quality scene. Geometry export must be validated against
its final scene before results are published.

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
USD; conditional per-scene bounds exclude credits, while the monthly hosting
scenarios explicitly show standard allowances available and exhausted.

`lambda_charge` prices a complete billed-invocation ledger, including failed
and skipped invocations, and divides by newly completed scenes. No completions
means cost per completed scene is unavailable, not free. Desktop elapsed time
is never silently converted into Lambda billed time. Parsed LayoutGPT objects
are not complete prompts or billing receipts.

The website's cost bounds use an explicitly assumed minimum output length.
Their break-even durations are budget thresholds, not measured savings or a
claim that SOILIE is cheaper than all current LLMs. Layout generation, image
rendering, animation, data preparation, training and serving costs must remain
separately labelled.

`conditional_bound` also tests an LLM-favouring output-charge floor against an
explicit SOILIE worker-cost ceiling. Its default assumptions are at least 250
billable output tokens, zero input charge, and at most one 30-second, 10 GB
Lambda invocation per newly completed scene. Neither the output minimum nor
the Lambda runtime ceiling is an observed benchmark result. At the recorded
standard GPT-4 tariff those assumptions imply lower SOILIE worker charges;
at the GPT-5 nano tariff they do not. The latter is price sensitivity only,
not an evaluation of its room-generation quality. This is not a claim that
all LLMs cost more, that retries are free, or that scene quality is equivalent.

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
`selection-audit.json`. The latter replays the older duplicate-disabled sampler
calls and explains all 14 shorter selections; it is not a quality score.
The compact `selection-fixture.json` preserves those historical inputs and
selections without retaining the retired preliminary score reports. Replay it
with `PYTHONHASHSEED=0 python -m serverless.benchmark.audit_selection`, supplying
`--runtime`, `--source` and `--output`; the output records the fixture checksum.

`stimuli.py` freezes up to 12 matched pairs per eligible baseline with seeded
sampling and no quality-based selection. Neutral plan and oblique box views
are immutable and method-blind. Illustrative high-intrusion examples on the
Research page are a separate selection and never feed pilot sampling.

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
unavailable, not guessed from the product name.

The website scripts `pilot-browser.mjs` and `check-comparison-browser.mjs` use
Playwright, store artifacts in `.codex/`, and close their browser processes.
Stop the loopback server when finished. Browser automation fills only choices
supplied by a reviewer; it does not manufacture pilot judgments.

## Checks

```bash
python -m unittest discover -s serverless/tests -v
```

Fixtures cover touching, rotated, contained, stacked and intersecting boxes,
boundary fractions, physical-unit availability, clearance, checkpoint resume,
watchdogs, cost units, failed-attempt accounting, immutable submissions,
balanced blinded assignments, reloads and exclusion of human/test versions
from AI exports. Do not mark the release complete until the actual batches,
ten-reviewer pilot, browser checks and live deployment checks have finished.
