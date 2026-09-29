# Counterbalanced functional review

The frozen 480 pairs contain 120 bedrooms and 120 living rooms for each baseline:
LayoutGPT and controlled Infinigen. Each pair compares that baseline with SOILIE.
Original scene geometry, fronts, inventories and matching remain unchanged.

## Collection

Five dimensions (orientation, relative volume, relationships, access and room
function), two independent contexts each: **4,800 judgments**. All are newly
collected with GPT-5.6 Sol, Extra High reasoning effort. The two presentations of
every pair/dimension use opposite sides and identical room pixels. Ten reviewer
stream IDs organize assignments, not ten distinct model architectures.

The full sample includes cases used during rubric development. Their identities
are recorded so a non-development subset can also be reported. This is not a
new held-out validation sample. Earlier answers remain unchanged and are not
mixed into this collection, including the earlier room-function answers.

Each isolated call receives one anonymous image, one dimension-specific prompt,
neutral case evidence, a complete object/pair checklist and a JSON response
schema. No previous answers, generator names, desired outcome or target agreement
rate are supplied. Concise observations precede the preference. Exact inputs,
response, configuration and hashes are retained per call. Invalid/interrupted
attempts stop collection for inspection; completed answers are never replaced.

## Evidence

- Numbered boxes retain the source geometry. Front arrows have a 38-pixel shaft,
  are drawn above furniture and key leaders, and reserve space before numbers
  are positioned. Arrow length is only a display aid.
- Non-size tasks receive box intersections, front gaps normalized by the
  approached object's depth, projected frontage fractions and bounded chair
  movement checks. These are geometric facts, not automatic preference scores.
- Chair checks test straight backward/sideways translations up to two chair
  depths in quarter-depth increments. A candidate must clear all vertically
  overlapping furniture boxes and the room boundary throughout its sweep, and
  leave a chair-width, one-chair-depth front patch. They do not certify a person's
  connected route or ergonomic suitability. Finding no candidate is uncertainty,
  not proof of immobility; turning is not tested.
- Relative size uses all within-room bounding-box volume ratios, excluding
  aspect ratio and absolute scene scale. `reference_volumes.json` preserves
  independently published assembled-product dimensions and source URLs. Ratios
  of those example volumes provide common reference points, not acceptable-size
  limits. This small single-retailer convenience sample has incomplete category
  coverage and does not establish population norms. Reviewers receive ratios,
  not absolute dimensions to fit either room to.

The comparison remains about box-level spatial arrangements. Empty box space,
unseen mesh detail and unmeasured human reachability limit its interpretation.

## Four buckets

Map screen choices back to the physical generator before combining them:

| Two judgments | Bucket | Retained detail |
| --- | --- | --- |
| A/A | Model A | win/win |
| A/tie or tie/A | Model A | win/tie |
| B/B | Model B | win/win |
| B/tie or tie/B | Model B | win/tie |
| tie/tie | Tie | exact agreement |
| A/B or B/A | Disagreement | opposite winners |

A is SOILIE and B is the named baseline only in the private routing/export;
reviewers see LEFT and RIGHT. Every pair contributes one bucket per dimension,
not two independent votes. Pending partners remain pending, never ties.

The main comparison is directional support for A versus B. Its denominator,
tie/disagreement shares, exact agreement and win/win versus win/tie split must
remain visible. These buckets do **not** imply calibrated confidence or accuracy.
Do not discard reversals or rename win/tie as unanimous agreement. Do not pool
dimensions as independent room samples. The previous pilot acceptance gate is
not silently passed: the user authorized a new descriptive full-run protocol.

## Running and monitoring

Prepare using `python -m serverless.cloud_benchmark.full_review --prepare
--source <frozen-v3-fronts-root> --root <new-root>`. Rasterize with
`node serverless/cloud_benchmark/render_staged_pilot.mjs <root> <website-root>`.
Validate using `python -m serverless.cloud_benchmark.run_staged_pilot
--root <root> --preflight-only`.

Collection requires `python -m serverless.cloud_benchmark.run_full_review --root
<root> --authorize-review --workers 5 --codex <existing-codex-executable>`.
It uses the existing Codex account, with no new API key, model fallback or paid
endpoint. Resume only missing assignments after inspecting any saved attempts.

Run `scripts/watch-ai-reviews.ps1` from PowerShell to monitor the default campaign.
It renders a colored Unicode terminal dashboard with an overall bar and one
bar for each of the five dimensions (not host-dependent `Write-Progress`),
checks each second, estimates
ETA from this session's observed throughput (excluding preflight and pauses),
reports stale heartbeats/errors, and exits only when complete or interrupted
with Ctrl+C. Each dimension has 960 judgments: 480 pairs, each shown twice.
Early ETAs are provisional; dimensions share the worker pool. Use `-Once` for
a single diagnostic refresh. It adapts to terminal width, refreshes in place,
and restores the terminal on exit. `-Color Always` forces ANSI color; `-Color
Never` keeps monochrome block bars. The monitor permits atomic file replacement on
Windows; a locked heartbeat cannot stop the collector or discard answers.
Closing the monitor does not stop the collector. On completion the collector
writes a validated local `full-results.json`; it never pushes or publishes.

When an inspected operational repair or Git attribution migration changes the
collector's provenance, supply `--resume-note "<reason>"` once. The original
receipt is preserved and a separate immutable upgrade record is written.
Only orchestration code may differ: protocol, model, effort, judgment delivery,
validation, and aggregation remain frozen. Each launch records its worker count,
executable checksum, and starting completion count. This is not permission to
change prompts or replace any previous answer.

Methodological reference: [OpenAI evaluation guidance](https://developers.openai.com/api/docs/guides/evaluation-best-practices#llm-as-a-judge-and-model-graders).
