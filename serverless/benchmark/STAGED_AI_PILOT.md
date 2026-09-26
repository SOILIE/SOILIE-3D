# Staged AI review

The approved pilot tests the four `functional-use-v1` questions on 32 frozen
pairs before authorizing the remaining 448. Existing room-function responses
retain their original prompt, study identity and stimulus provenance.

The [completed local checkpoint](results/functional-use-pilot-v1.md) did not
meet the per-question continuation gate. It remains development evidence;
no remaining reviews or publication have been authorized.

## Invariants

Selection uses only a fixed seed, pair IDs, room type and comparison baseline.
All inspected control pairs are excluded from this pilot, not from the final
480-pair sample. Eight pairs per stratum receive two judgments per revised
question, plus two reversed controls per reviewer and stratum: 256 main votes
and 64 controls. Each question must agree on at least 15/16 controls.

Prompts, model configuration and assignments are frozen before collection.
Room SVGs are rasterized once; both original and reversed packets copy the same
pixels. The preflight compares every panel crop exactly. Relative-volume tables
remain exclusive to the size question; the other views contain no task-specific
instruction. Geometry, labels, front arrows and pair membership are unchanged.

Every judgment uses a new ephemeral Codex conversation with the requested
`gpt-5.6-sol` model and `xhigh` effort. No previous answers, source identities or
other reviewers' notes enter its packet. Any tool use, invalid output or runner
error stops collection for inspection; there is no automatic model fallback or
resampling of completed answers. The local runner uses existing Codex account
authentication and removes unrelated API-key environment settings.

## Commands

Run from the backend repository. Keep all execution artifacts under its
`.codex/benchmark/soilie-platform-grid-final/` directory. Commands below use
`SOURCE` for the frozen `review-functional-fronts-v3` directory and `PILOT` for
the new `review-functional-use-pilot-v1` directory.

```powershell
python -m serverless.cloud_benchmark.staged_pilot prepare --source SOURCE --root PILOT
node serverless/cloud_benchmark/render_staged_pilot.mjs PILOT PATH_TO_PLAYWRIGHT_PACKAGE_ROOT
python -m serverless.cloud_benchmark.run_staged_pilot --root PILOT --preflight-only
python -m serverless.cloud_benchmark.run_staged_pilot --root PILOT --authorize-review --workers 3
python -m serverless.cloud_benchmark.staged_pilot status --root PILOT
python -m serverless.cloud_benchmark.staged_pilot finalize --root PILOT
python -m serverless.cloud_benchmark.staged_pilot checkpoint --root PILOT --output CHECKPOINT_JSON
```

The renderer dependency root is the directory containing `node_modules/playwright`
(the website's `.codex/browser` in this workspace). No development server is used.
Re-running the collection command processes only missing assignments. An
unsubmitted execution directory causes a stop: recover its existing valid
answer with `run_staged_pilot --root PILOT --recover-completed` (as a Python
module) rather than overwriting or rerunning it. Recovery makes no model calls
and rejects incomplete/error transcripts. Protocol preparation and final
checkpoint creation also refuse to overwrite existing evidence.

## Outputs and interpretation

`pilot-results.json` contains every judgment, all control outcomes, subgroup
counts, exact prompts and hashes. `private/retained-room-function.json` is an
immutable snapshot of the original responses; private sessions and credentials
must never enter website assets. Runner outputs remain available for audit.
The `checkpoint` command exports compact internal evidence for Git, hashing
private context identifiers and retaining all judgments, including controls and
disagreements. It does not update website results or upload anything.

A successful pilot is not a completed review or a public release. A passing,
unchanged protocol permits reusing **all** main pilot judgments; continuation
must collect only missing pairs. If prompts change, do not transfer those votes
to the changed protocol. `continuation_inventory` returns an unauthorized
inventory for planning that later step, never starts it automatically.

Repeat agreement measures stability, not correctness or population reliability.
Preserve failures, disagreements, ties and original explanations. No outcome is
selected to favour a generator. The website displays the exact per-dimension
prompt and system instructions rather than substituting the latest global text.

Execution flags follow the [official non-interactive Codex documentation](https://developers.openai.com/codex/noninteractive).
