"""Frozen v2 instructions and concise observation schema, not hidden reasoning."""
from copy import deepcopy
from itertools import combinations

from serverless.study.clarified_rubric import COMMON as V1_COMMON, DIMENSIONS as V1_DIMENSIONS

VERSION = 'functional-use-v2'
STIMULUS_VERSION = 'numbered-functional-fronts-v1'
SEED = 'functional-use-v2-pilot'
COMMON = V1_COMMON + (
    'Object numbers identify the same object across the three views of one room; consult its category key. '
    'Check every required inventory entry before choosing a preference. Record concise observable evidence, '
    'not a reasoning transcript. Prioritize clear loss of functional use over weaker convenience or grouping '
    'problems; then consider the proportion affected, not raw counts. If material uncertainty prevents '
    'distinguishing the rooms, tie. Different defects are not automatically equivalent, and two plausible '
    'arrangements need not have a winner. Check the 3D views: shared plan-view footprints alone do not '
    'establish interference, especially at different heights or with normal support/nesting. '
)
DIMENSIONS = {
    'orientation': V1_DIMENSIONS['orientation'] + (
        ' Identify wrong functional direction independently of the available gap. A desk or cabinet merely '
        'pointing toward another object is not a direction defect; insufficient approach or working clearance '
        'belongs under access. A front turned directly into an adjacent wall can still be a direction error. '
        'A nightstand serves the head area of a bed; use that role when the direction establishes their '
        'relationship, without imposing a head-against-wall rule.'),
    'proportions': V1_DIMENSIONS['proportions'] + (
        ' Assess EVERY supplied unordered pair of objects using its computed first/second volume ratio. '
        'Use ordinary furniture assumptions. Broad chair labels include ordinary armchairs, and beds vary '
        'normally; do not invent unshown miniature, oversized or specialist variants to excuse an implausible '
        'ratio. No universal numerical cutoff is asserted. Consider all pairs on both sides, including lights '
        'and accessories, rather than selectively noticing a different object on each viewing.'),
    'relationships': V1_DIMENSIONS['relationships'] + (
        ' Use clear functional roles: a nightstand serves someone near the bed head, and a work chair serves '
        'a desk. Generic tables and lamps need not serve that activity unless the arrangement supports that '
        'interpretation. For every supplied pair, identify a relevant activity/relationship or mark it '
        'not_applicable. Do not infer a required relationship merely because a pair appears in the checklist. '
        'A claimed obstruction must identify the specific interaction impaired, with support from the 3D views.'),
    'access': V1_DIMENSIONS['access'] + (
        ' A desk requires a reachable, usable sitting or standing working position; its entire working edge '
        'need not be unobstructed. For local chair movement, require visible evidence of a route and usable '
        'end position without moving other furniture. If the views establish neither feasibility nor '
        'blockage, record uncertain, not assumed success or failure. Check all functional furniture; mark '
        'decorative objects not_applicable rather than penalizing their lack of a separate walking approach.'),
}
CHECKLIST = (
    'Return observations first, then judgement, errorChoice, confidence and note. For orientation/access '
    'include exactly one observation per numbered object on each side; orientation entries without an '
    'asserted front are not_applicable. For relationships/proportions include exactly one observation per '
    'unordered object pair on each side. No missing, duplicate or extra entries. Use severity none, minor, '
    'major, uncertain, or not_applicable. Major means a clear substantial defect on this dimension; minor '
    'means a limited disadvantage; none means plausible; uncertain means the evidence cannot establish it. '
    'For size every pair is relevant, so not_applicable is not allowed. Evidence is a concise factual '
    'observation of at most 180 characters. Use object numbers as strings, not category names. '
    'Judgement is left/tie/right; errorChoice is left/right/both/neither/uncertain; confidence is integer '
    '1 through 5; note is a brief explanation of at most 500 characters. Do not force a preference or tie '
    'to achieve any target distribution.'
)
EVIDENCE_INSTRUCTION = (
    'The following is the complete neutral inventory and required checklist for this case. '
    'Numbers refer to the category keys in the image. Pair ratios, when present, divide the first '
    "object's bounding-box volume by the second's within the same room; they are not quality scores."
)


def schema(base):
    observation = {'type': 'object', 'additionalProperties': False,
                   'properties': {'side': {'type': 'string', 'enum': ['left', 'right']},
                                  'objects': {'type': 'array', 'minItems': 1, 'maxItems': 2,
                                              'items': {'type': 'string'}},
                                  'severity': {'type': 'string', 'enum': ['none', 'minor', 'major', 'uncertain', 'not_applicable']},
                                  'evidence': {'type': 'string', 'minLength': 1, 'maxLength': 180}},
                   'required': ['side', 'objects', 'severity', 'evidence']}
    result = deepcopy(base)
    result['properties'] = {'observations': {'type': 'array', 'minItems': 1, 'items': observation}, **result['properties']}
    result['required'] = ['observations', *result['required']]
    return result


def checklist_keys(evidence, profile):
    keys = []
    for side in ('left', 'right'):
        ids = [row['id'] for row in evidence[side]['objects']]
        groups = combinations(ids, 2) if profile in ('relationships', 'proportions') else ((key,) for key in ids)
        keys.extend((side, tuple(group)) for group in groups)
    return keys


def validate_observations(answer, evidence, profile):
    from collections import Counter
    expected = Counter(checklist_keys(evidence, profile))
    seen = Counter()
    observations = answer.get('observations')
    if not isinstance(observations, list):
        raise ValueError('Structured observations are required')
    for row in observations:
        if (not isinstance(row, dict) or set(row) != {'side', 'objects', 'severity', 'evidence'}
                or row['side'] not in ('left', 'right') or not isinstance(row['objects'], list)
                or not all(isinstance(key, str) and key.isdigit() for key in row['objects'])
                or row['severity'] not in ('none', 'minor', 'major', 'uncertain', 'not_applicable')
                or not isinstance(row['evidence'], str) or not row['evidence'].strip() or len(row['evidence']) > 180):
            raise ValueError('Invalid structured observation')
        key = row['side'], tuple(sorted(row['objects'], key=int))
        seen[key] += 1
        if profile == 'proportions' and row['severity'] == 'not_applicable':
            raise ValueError('Every volume pair is relevant')
    if seen != expected:
        raise ValueError('Checklist coverage is incomplete, duplicate, or references unknown objects')

