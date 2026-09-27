# Functional-use-v2 pilot: internal checkpoint

**Not accepted for continuation.**

All 320 judgments completed: 256 main judgments and 64 controls. Acceptance requires at least 15/16 exact repeat agreements in **every** dimension, including ties.

## Design

The seeded, quality-blind sample contains 32 pairs, eight per baseline × room type. The 70 development pairs were excluded; the final 480-pair sample remains unchanged. Each revised dimension has two streams of 32 main cases and eight reversed controls. Each judgment used GPT-5.6 Sol at Extra High effort in a fresh context.

Numbered plan, oblique and bird’s-eye views preserve the original geometry and fronts. Controls exchange identical room pixels. Structured observations cover every required object or pair; only relative-size judgments receive computed volume ratios.

## Exact-agreement gate

| Dimension | Exact agreement | Required | Outcome |
| --- | ---: | ---: | --- |
| Orientation | 11/16 (68.8%) | 15/16 | Below threshold |
| Relative size | 12/16 (75.0%) | 15/16 | Below threshold |
| Object relationships | 8/16 (50.0%) | 15/16 | Below threshold |
| Access | 10/16 (62.5%) | 15/16 | Below threshold |

## Repeat transitions

Exact agreement means selecting the same physical room after the swap, or tying both times. A tie/preference change is distinct from choosing opposite rooms. Decisive agreement uses only controls with a winner on both viewings; a zero denominator is unavailable, not zero.

| Group | Exact | Tie ↔ preference | Opposite winner | Same winner among decisive |
| --- | ---: | ---: | ---: | ---: |
| All | 41/64 | 20/64 | 3/64 | 28/31 (90.3%) |
| Access | 10/16 | 5/16 | 1/16 | 6/7 (85.7%) |
| Orientation | 11/16 | 5/16 | 0/16 | 7/7 (100.0%) |
| Relative size | 12/16 | 3/16 | 1/16 | 8/9 (88.9%) |
| Object relationships | 8/16 | 7/16 | 1/16 | 7/8 (87.5%) |

### By reviewer stream

| Group | Exact | Tie ↔ preference | Opposite winner | Same winner among decisive |
| --- | ---: | ---: | ---: | ---: |
| reviewer-01 · Orientation | 4/8 | 4/8 | 0/8 | 2/2 (100.0%) |
| reviewer-02 · Orientation | 7/8 | 1/8 | 0/8 | 5/5 (100.0%) |
| reviewer-03 · Relative size | 6/8 | 1/8 | 1/8 | 4/5 (80.0%) |
| reviewer-04 · Relative size | 6/8 | 2/8 | 0/8 | 4/4 (100.0%) |
| reviewer-05 · Object relationships | 3/8 | 5/8 | 0/8 | 3/3 (100.0%) |
| reviewer-06 · Object relationships | 5/8 | 2/8 | 1/8 | 4/5 (80.0%) |
| reviewer-07 · Access | 7/8 | 0/8 | 1/8 | 5/6 (83.3%) |
| reviewer-08 · Access | 3/8 | 5/8 | 0/8 | 1/1 (100.0%) |

### By baseline

| Group | Exact | Tie ↔ preference | Opposite winner | Same winner among decisive |
| --- | ---: | ---: | ---: | ---: |
| Controlled Infinigen | 20/32 | 11/32 | 1/32 | 13/14 (92.9%) |
| LayoutGPT | 21/32 | 9/32 | 2/32 | 15/17 (88.2%) |

## Retention and limits

All answers remain development evidence. The pilot does not pass the agreed continuation gate; no main votes are admitted under a changed protocol.

Controls never contribute preference votes. Original room-function answers and their prompt/stimulus provenance are unchanged. No disagreement was removed or reanswered. This small repeat check measures stability, not accuracy or population reliability.

[The complete checkpoint](functional-use-pilot-v2.json) includes all observations, answers, exact prompts, response schema, stimulus hashes and subgroup counts. Private context identifiers are hashed. Original execution transcripts remain in the frozen local pilot directory. No full campaign, version bump, push or publication was performed.

Protocol SHA-256: `78fd7f07a5f73b26e496df18986572d91025d7686bbc88bc19ec63a60bc061d8`.

Retained room-function SHA-256: `fb631ffabf90e826576d986224eeb991d2b4a9f8c3853e7f98f97bace0e61945`.
