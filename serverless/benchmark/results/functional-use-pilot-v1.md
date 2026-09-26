# Clarified review pilot: internal checkpoint

**Not accepted for continuation.** The pilot completed all 320 judgments, but
only access reached the required 15/16 consistent reversed-side controls.
Overall consistency was 51/64 (79.7%). This is a small stability check, not a
measure of accuracy or population reliability.

## Design and outcome

The fixed, quality-blind selection contains 32 pairs: eight per baseline
(LayoutGPT or controlled Infinigen) and room type (bedroom or living room).
The 38 previously inspected control pairs were excluded from pilot selection;
the final 480-pair sample remains unchanged.

Each revised question received two streams of 32 main judgments and eight
controls, using GPT-5.6 Sol at Extra High effort in fresh isolated contexts.
The total is 256 main judgments and 64 controls. Controls exchange identical
room pixels; agreement means choosing the same physical room, or keeping a tie.

| Dimension | Consistent controls | Required | Outcome |
| --- | ---: | ---: | --- |
| Orientation | 12/16 | 15/16 | Below threshold |
| Relative size | 11/16 | 15/16 | Below threshold |
| Object relationships | 13/16 | 15/16 | Below threshold |
| Access | 15/16 | 15/16 | Met threshold |

## Consistency by stream and baseline

| Stream | Dimension | LayoutGPT pairs | Controlled Infinigen pairs | Total |
| --- | --- | ---: | ---: | ---: |
| 01 | Orientation | 4/4 | 3/4 | 7/8 |
| 02 | Orientation | 2/4 | 3/4 | 5/8 |
| 03 | Relative size | 3/4 | 3/4 | 6/8 |
| 04 | Relative size | 2/4 | 3/4 | 5/8 |
| 05 | Object relationships | 4/4 | 3/4 | 7/8 |
| 06 | Object relationships | 3/4 | 3/4 | 6/8 |
| 07 | Access | 4/4 | 4/4 | 8/8 |
| 08 | Access | 3/4 | 4/4 | 7/8 |
| **All** | | **25/32** | **26/32** | **51/64** |

Bedroom controls agreed on 22/32; living-room controls agreed on 29/32.
No answers were replaced, disagreements removed, or thresholds changed.
The pilot remains development evidence; its main votes are not admitted to a
final evaluation under a changed protocol. Controls never add preference votes.
Original room-function responses and their instruction provenance are retained
unchanged. No full campaign or publication was launched.

[The checkpoint JSON](functional-use-pilot-v1.json) preserves all answers,
explanations, subgroup counts, exact prompts and stimulus hashes. Private context
identifiers are hashed. Execution transcripts and the original room-function
snapshot remain in the local frozen pilot directory for audit.
