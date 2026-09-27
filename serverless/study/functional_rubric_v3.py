"""Frozen full-rerun protocol. Never mutate earlier prompts or answers."""
from serverless.study import structured_rubric as v2

VERSION = 'functional-use-v3'
STIMULUS_VERSION = 'numbered-unoccluded-fronts-v2'
PROFILES = ('orientation', 'proportions', 'relationships', 'access', 'room_function')
COMMON = (
    'Judge only the assigned dimension of these anonymous rooms. The object inventories are fixed; '
    'assess what is present, never penalize absent counterparts or reward inventory breadth. '
    'Inspect every required object/pair on both sides using the numbered plan, oblique and birdseye views. '
    'Cyan arrows give verified functional fronts; arrow length is a screen-space legibility aid, not clearance. '
    'Numbers identify the same object in every view. Do not infer method identity or hidden mesh details. '
    'Panels are independently fitted; screen size is not physical scale. '
    'Use supplied geometric facts to check visual impressions, not as automatic quality scores. '
    'Boxes enclose empty space: their intersection is not proof of solid-mesh collision, and absence of '
    'intersection does not establish usable clearance. Shared plan footprints alone do not prove interference. '
    'If an apparent problem contradicts a supplied fact, resolve that discrepancy or mark uncertain. '
    'Choose a winner only for a clear meaningful advantage on this dimension. Prioritize established loss '
    'of function over weaker inconvenience, then consider the proportion affected, not raw counts. '
    'Tie for similarly plausible arrangements, balanced important trade-offs or material uncertainty. '
    'Do not invent specialist/miniature furniture to rescue one side, impose a target vote distribution, '
    'or let screen side affect severity. Confidence is not a tiebreaker. '
    'Record short observable evidence, not a reasoning transcript. Do not consult other judgments or external sources. '
)
DIMENSIONS = dict(v2.DIMENSIONS)
DIMENSIONS['relationships'] += (
    ' A nonintersecting table can still block sofa use when it spans nearly the entire seating front '
    'with negligible separation. Conversely a table merely in front is normal when usable separation remains. '
    'Name the actual interfering object and interaction; do not substitute a nearby object. '
    'Use front gap and frontage together, and inspect height before alleging an obstruction.'
)
DIMENSIONS['access'] += (
    ' A box-clear chair candidate is positive evidence of a local translation and empty front patch, '
    'not proof of a person route. Do not call that chair immovable. A not_established search result '
    'is uncertainty, not failure. Compare the actual obstructing objects: a coffee table separated from '
    'a sofa is not interchangeable with a side table touching its front. Check every neighbor listed across '
    'the front. The supplied gaps are normalized by that furniture depth, not metres or an accessibility standard.'
)
DIMENSIONS['proportions'] += (
    ' Supplied catalog ratios are independent examples of assembled furniture envelope volumes, not '
    'population limits or pass/fail thresholds. They come from a small single-retailer convenience sample. '
    'Use them as common reference points on BOTH sides; being outside the example range alone is not a defect. '
    'Describe whether an implausibility claim is well established, or uncertain due to category breadth '
    'or limited reference coverage. Apply the same tolerance to both rooms. Do not switch between treating '
    'a ratio as acceptable variation and a major defect without evidence. No catalog coverage is not a penalty. '
    'Only pairwise volume ratios determine this dimension; do not use aspect ratios, absolute scene scale '
    'or unrelated arrangement quality.'
)
DIMENSIONS['room_function'] = (
    'Given exactly the objects shown, assess whether their placement organizes coherent usable activities '
    'for the stated room type. Identify the functional zone served by each object or mark not_applicable '
    'when no activity can be established. Never penalize missing beds, desks, sofas, televisions or other '
    'counterparts. Do not infer a TV from a TV stand. Judge loss of an existing activity more strongly than '
    'minor convenience; an open walking path alone does not establish that a work or seating arrangement works. '
    'Ordinary tucked chairs are acceptable if local use is possible without moving other furniture. '
    'Beds need one complete non-head access edge; foot access is acceptable. Sofas need front access. '
    'A desk needs one usable working position, not a wholly clear edge. Use uncertain where the evidence '
    'cannot establish function.'
)
CHECKLIST = v2.CHECKLIST.replace('For orientation/access', 'For orientation/access/room_function')
EVIDENCE_INSTRUCTION = (
    'Neutral case evidence follows. Every number refers to the image category key. Volume ratios divide '
    'the first box by the second within one room. Front gaps divide by the approached furniture depth; '
    'negative means projected overlap, not necessarily solid collision. Frontage fractions describe '
    'projected span, not a certified blocked-person area. Chair searches test straight translations up to '
    'two chair depths backward or sideways, plus a clear chair-width/depth front patch, against enclosing '
    'boxes and room boundaries. Turning and connected human routes are untested. These facts preserve '
    'the original layout and do not replace functional interpretation.'
)


def prompt(profile):
    return COMMON + 'Assigned dimension: ' + profile + '. ' + DIMENSIONS[profile] + '\n\n' + CHECKLIST
