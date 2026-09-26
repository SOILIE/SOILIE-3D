# AI review calibration decisions

Status: awaiting researcher choices. Publication and additional reviews are on
hold. This document records diagnostic evidence and proposed rules, not revised
prompts or new results. No recorded answer has been changed.

## Evidence

The `functional-fronts-neutral-labels-v3` pass contains 4,800 main judgements
and 40 reversed-side controls from ten reviewers. Agreement means preferring
the same physical room after swapping sides; a tie must remain a tie.

| Question | Consistent controls |
| --- | ---: |
| Facing direction | 5/8 |
| Relative size | 7/8 |
| Object relationships | 3/8 |
| Access and circulation | 5/8 |
| Room function | 8/8 |

Overall agreement is 28/40 (70%), below the existing 36/40 release threshold.
Seven disagreements change between a tie and a preference; five reverse the
preferred room. This measures within-reviewer repeat consistency, not agreement
between different reviewers. Eight room-function controls are encouraging but
do not establish population reliability. Preserve those responses and their
original rubric unchanged, as requested; this does not waive the release gate.

The twelve inconsistent explanations support the following seven decisions.
Recommendations concern criteria, not which generator should win. Clearer
criteria may improve stability but have not yet been tested.

## Decisions awaiting confirmation

### D1. Relationships: useful subgroups or one connected cluster?

One reviewer initially preferred separate seating and dining groups, then
preferred the alternative because everything formed a connected sequence.
Other reversals alternately penalized distant storage and praised separation.

**Recommended:** judge functional relationships among objects actually present.
Separate activity groups are acceptable; storage or a floor lamp need not be
beside the bed or sofa without a visible reason. Neither compactness nor
separation is intrinsically better. Never reward missing/extra counterparts.

**Alternative:** favor a single compact, interconnected group when both layouts
are usable.

### D2. Relationships: when should congestion count?

A bed/desk arrangement was first preferred for its distinct activity zones,
then rejected as congested; another explanation praised connected furniture
despite having previously rejected its dense overlap.

**Recommended:** count congestion only when it visibly impairs a specific
relationship, such as a chair's usable position at a desk. Do not add a general
collision penalty unrelated to that relationship. State the affected pair.

**Alternative:** ignore overlap and obstruction entirely in this question,
leaving them to geometry and access measurements.

### D3. Orientation: functional relationships or facing open room space?

A reviewer first prioritized the bed's head/foot relationship to a pillow and
nightstand, then preferred the other bed for pointing into open space. Another
alternated between seating/table alignment and inward-facing storage.

**Recommended:** use the marked front as authoritative and assess its functional
role. Relevant object relationships and usable faces take precedence over a
generic rule that everything should face the room centre. Pillows do not
redefine an arrow; a TV stand does not imply an unshown television. Do not impose
a head-against-wall requirement on every bed.

**Alternative:** prioritize fronts facing away from walls into open room space,
using relationships with present objects as a secondary consideration.

### D4. Orientation: should insufficient clearance change the verdict?

One seating pair changed from a tie because both faced their tables sensibly
to a preference because one correctly facing group was too compressed.

**Recommended:** assess direction here, and approach distance under access.
An object facing the appropriate target but too close to it has an access
problem, not necessarily a direction problem. A usable face pointed into a
wall can still be a direction problem; explain the facing error specifically.

**Alternative:** include sufficient operating/approach clearance as part of
orientation as well as access.

### D5. Access: assess every functional object or prioritize the main activity?

Several judgements first considered only the bed's open area, then penalized a
blocked chair, desk or storage face. A bed's foot arrow was sometimes treated
as its only possible approach route.

**Recommended:** assess role-appropriate access to all present functional
objects, including circulation to them. Beds can be approached from usable
sides; desks and storage have working/access faces. Do not score every object
as if its arrow were an entrance. Compare severity and the proportion affected,
not raw problem counts or the presence of more furniture. Small decorative
items are not independent destinations requiring a person-sized approach.

**Alternative:** prioritize access to the room's main activity (for example,
the bed), using secondary furniture as a tiebreaker.

### D6. Relative size: flexible plausibility or a standard size ordering?

One reviewer accepted both volume hierarchies, then rejected one for very large
numbers normalized to a soda can and a coffee-table box larger than a sofa box.
Normalization to a tiny object necessarily yields large numbers; it is not
evidence of implausible ratios by itself.

**Recommended:** judge plausible pairwise bounding-box volume ratios, allowing
ordinary category variation and empty space inside enclosing boxes. A table
box larger than a sofa box is not automatically wrong. Identify a clearly
implausible pair and its ratio rather than applying an invariant size ranking.
Do not infer shape, aspect ratio or absolute scale from these ratios.

**Alternative:** define a conventional category-volume ordering in advance and
penalize departures, even where unusual but plausible furniture could fit.

Under either choice, multiplying every volume in one room by the same constant
must not change its assessment. Numerical normalization is not a user preference.

### D7. Ties: what constitutes a sufficient advantage?

Seven of the twelve inconsistencies involve a tie. Some explanations call both
arrangements readable; the repeat then promotes a minor or disputed difference.

**Recommended:** prefer a room only for a clear, consequential advantage on the
assigned dimension. Tie when both are plausible or their trade-offs are similar;
state what offsets what. Confidence alone is not a tiebreaker. This must not
become an instruction to choose ties to meet a consistency target.

**Alternative:** choose even a slight defensible preference, reserving ties for
no discernible difference.

## Traceability

These are case-ID suffixes, not model identities or desired answers. Full
assignments, prompts, image hashes and responses remain in the private frozen
`review-functional-fronts-v3` evidence. No session credentials belong here.

| Reviewer | Case suffix | Decisions |
| --- | --- | --- |
| 02 | aad8d8c16ee900e8f44d | D3, D4, D7 |
| 05 | 5f177b05a4662198ba62 | D1, D2 |
| 07 | 408e9148fd187dc27a3d | D5, D7 |
| 01 | 1be7188e50cb30b5a9c9 | D3 |
| 06 | 433d058671e33b95c167 | D1, D2 |
| 02 | 25bff2e082ef26cec9e5 | D3, D4 |
| 08 | 099fcbf5819394cc00f8 | D5, D7 |
| 06 | f29e8a403813536ef4fb | D1, D7 |
| 06 | 88089b97c9d11f8090fb | D1, D7 |
| 07 | e50da899441be26952ab | D5, D7 |
| 04 | ec041259b593b7ce888c | D6, D7 |
| 05 | ab2a5979b3183bbb65a7 | D1, D2 |

## Next protocol, after decisions

- Keep all original responses immutable. Do not replace individual answers
  with whichever explanation appears preferable.
- Freeze the accepted rules and their exact prompt text before new collection.
  Retained room-function responses keep their original protocol identity;
  revised dimensions must not be described as if collected under that rubric.
- Treat these twelve examples as calibration cases, not independent validation.
  Assess the revision on held-out cases and repeats chosen without preference
  scores. Specify the control count and acceptance rule before collection.
- Rasterize each room once and reuse the identical pixels when swapping sides.
  Current SVG redraws have small edge differences; their effect is unproven.
- Inspect each case in an isolated context without previous reviewer choices.
  Report uncertainty and all controls, even if agreement stays below target.
- Do not launch reviewers, revise active scoring prompts, change release gates,
  push, bump versions or publish while these choices are pending.
