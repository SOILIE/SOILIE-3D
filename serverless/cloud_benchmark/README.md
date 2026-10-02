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
2. Run `deploy.ps1` with the authorized AWS profile. The isolated image inherits
   only dependencies/assets from an immutable renderer image, copies model code
   directly from the checkout, and checks every runtime asset during the build.
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
# Complete comparison coverage

`complete_coverage.py` combines the retained 10,000 SOILIE rooms, all 476 released
LayoutGPT layouts, all 366 recorded LayoutGPT API proposals, and 569 completed
Infinigen CPU outputs. Unmatched proposals stay in geometry distributions.
The 71 completed Infinigen Lambda calls contribute timing and usage records;
their saved artifacts lack the surface-tag dictionary needed by the pinned
native floor exporter, so no geometry/contact scores are imputed to them.

CPU timing distributions retain hardware, thread allocation and workload.
Concurrent latency is not isolated latency or aggregate batch throughput.
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

```powershell
python -m serverless.cloud_benchmark.complete_coverage --backend . --website <website-root> --contacts .codex/contact-audit --output .codex/publication-coverage
python -m serverless.cloud_benchmark.publish_counterbalanced --directory .codex/publication-coverage --version 0.2.2 --corrected .codex/contact-audit/corrected-rows.json
```
