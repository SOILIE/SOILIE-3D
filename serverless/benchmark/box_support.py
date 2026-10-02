"""Deterministic vertical separation of enclosing boxes, never mesh contact.

The floor is the horizontal reference plane, not a triangle surface. A lower
box is a candidate only where its projected footprint overlaps in positive
area. Touching/interpenetrating envelopes have zero separation. No dimensions,
positions, support annotations or model-specific rules are changed.
"""
from itertools import combinations
import math
import statistics

from scipy.optimize import linprog

from serverless.benchmark.geometry import Box, WALL_MOUNTED, canonical_label, furniture

METHOD = 'vertical-enclosing-box-separation-v2'
EPSILON_M = 1e-7
MOUNTED = WALL_MOUNTED | {'ceiling_lamp', 'pendant_lamp', 'ceiling_light',
                          'wall_lamp', 'wall_light', 'wall_shelf', 'wall_art'}


def reclassify_measurement(record):
    """Reapply target eligibility without recomputing unchanged geometry.

    A target's eligibility does not change its availability as a candidate
    support for another object. Existing per-object distances remain valid.
    """
    from copy import deepcopy
    result = deepcopy(record)
    removed = [o for o in result['objects'] if canonical_label(o['label']) in MOUNTED]
    result['objects'] = [o for o in result['objects'] if canonical_label(o['label']) not in MOUNTED]
    result['excludedObjects'] = sorted(set(result['excludedObjects']) | {o['id'] for o in removed})
    result['method'] = METHOD
    for target, source, kind in [('gapCm','gapCm',None), ('floorGapCm','gapCm','floor'),
                                  ('objectGapCm','gapCm','object'), ('belowFloorCm','belowFloorCm',None)]:
        values = [o[source] for o in result['objects'] if kind is None or o['supportKind'] == kind]
        result[target] = statistics.fmean(values) if values else None
    return result


def vertical_gap(upper, lower):
    """Minimum nonnegative vertical separation over their common footprint.

    Upright boxes have flat bottoms/tops. For pitched or sheared boxes, solve
    over both convex hulls at the same x/y instead of using world AABB heights.
    The four variables are x, y, z_upper, z_lower. This also detects a crossing
    (minimum zero), which is not evidence of physically stable support.
    """
    if upper.upright and lower.upright:
        return max(0.0, float(upper.low[2] - lower.high[2]))
    matrix = []; rhs = []
    for box, axis in ((upper, 2), (lower, 3)):
        for a, b, c, d in box.hull.equations:
            row = [a, b, 0.0, 0.0]; row[axis] = c
            matrix.append(row); rhs.append(-d)
    matrix.append([0, 0, -1, 1]); rhs.append(0)
    result = linprog([0, 0, 1, -1], A_ub=matrix, b_ub=rhs,
                     bounds=[(None, None)] * 4, method='highs')
    # A tilted object's lowest corner can be lower elsewhere, while its entire
    # common footprint is above this object. It is not a surface below it.
    if result.status == 2:
        return None
    if not result.success:
        raise ValueError('Cannot establish vertical box separation: ' + result.message)
    return max(0.0, float(result.fun))


def measure_box_support(scene):
    if scene['units'] != 'm':
        raise ValueError('Box support requires independently established metre coordinates')
    floor = scene['room']['floorZ']
    if type(floor) not in (float, int) or not math.isfinite(floor):
        raise ValueError('Finite floor reference required')
    items = furniture(scene)
    if len({o['id'] for o in items}) != len(items):
        raise ValueError('Duplicate object IDs')
    boxes = [Box(item) for item in items]
    overlaps = {}
    for a, b in combinations(range(len(boxes)), 2):
        if items[a].get('assemblyId', items[a]['id']) == items[b].get('assemblyId', items[b]['id']):
            continue
        overlaps[a, b] = boxes[a].footprint.intersection(boxes[b].footprint).area > 1e-12
    observations = []; excluded = []
    for index, (item, box) in enumerate(zip(items, boxes)):
        # Apply one category policy across sources; do not use generator-specific
        # mesh-support tags to give a dataset more favourable box candidates.
        if canonical_label(item['label']) in MOUNTED:
            excluded.append(item['id']); continue
        gap = max(0.0, float(box.low[2] - floor))
        support_id = None; kind = 'floor'
        for other_index, other in enumerate(boxes):
            if other.low[2] >= box.low[2] - EPSILON_M:
                continue
            if not overlaps.get(tuple(sorted((index, other_index))), False):
                continue
            candidate = vertical_gap(box, other)
            if candidate is None:
                continue
            # Prefer floor in numerical ties. Equal candidates are selected by
            # stable object ID, never source order or model identity.
            if candidate < gap - EPSILON_M or (support_id is not None and
                    abs(candidate - gap) <= EPSILON_M and items[other_index]['id'] < support_id):
                gap = candidate; support_id = items[other_index]['id']; kind = 'object'
        observations.append({'id':item['id'], 'label':item['label'],
            'gapCm':0.0 if gap <= EPSILON_M else gap * 100,
            'belowFloorCm':max(0.0, float(floor - box.low[2])) * 100,
            'supportKind':kind, 'supportId':support_id})
    def mean(key, kind=None):
        values = [o[key] for o in observations if kind is None or o['supportKind'] == kind]
        return statistics.fmean(values) if values else None
    return {'method':METHOD, 'objects':observations, 'excludedObjects':excluded,
            'gapCm':mean('gapCm'), 'floorGapCm':mean('gapCm', 'floor'),
            'objectGapCm':mean('gapCm', 'object'), 'belowFloorCm':mean('belowFloorCm')}
