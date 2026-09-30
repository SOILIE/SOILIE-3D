"""Method-blind box facts; diagnostics do not silently become usability scores.

All distances are normalized within a room. LayoutGPT's source pixel units
cannot be compared to another source's metres without an additional scale
assumption. Clear swept boxes establish a candidate, not human reachability.
"""
from itertools import combinations
import json
import math
from pathlib import Path

import numpy as np
from shapely.affinity import translate
from shapely.geometry import Polygon
from shapely.ops import unary_union

from serverless.benchmark.geometry import intersection_volume, room_geometry
from serverless.benchmark.numbered_evidence import inventory, ordered_objects
from serverless.benchmark.review_annotations import functional_front, presentation_label

VERSION = 'functional-box-facts-v1'
compact = lambda value: float(f'{float(value):.6g}')


def reference_catalog():
    return json.loads((Path(__file__).parents[1] / 'study/reference_volumes.json').read_bytes())


def volume_examples(objects, catalog=None):
    catalog = reference_catalog() if catalog is None else catalog
    def examples(category):
        return [p for p in catalog['products'] if category in [p['category'], *p.get('alsoCategories', [])]]
    pairs = []
    for a, b in combinations(objects, 2):
        first, second = examples(a['category']), examples(b['category'])
        def volume(product):
            # A new protocol may explicitly supply the compiled SI catalog.
            # The historical default and all frozen v3 answers stay unchanged.
            return product['envelopeM3'] if 'envelopeM3' in product else math.prod(product['dimensionsIn'])
        ratios = [{'first': x['id'], 'second': y['id'],
                   'ratio': compact(volume(x) / volume(y))}
                  for x in first for y in second]
        pairs.append({'objects': [a['id'], b['id']], 'catalogRatios': ratios,
                      'coverage': 'illustrative_only' if ratios else 'no_catalog_reference'})
    return pairs


def basis(bounds, front):
    front = np.asarray(front)
    side = np.array([-front[1], front[0]])
    p, q = bounds.points[:, :2] @ front, bounds.points[:, :2] @ side
    return front, side, p, q


def chair_candidate(scene, objects, index):
    item, bounds = objects[index]
    front, side, p, q = basis(bounds, functional_front(scene, item))
    depth = np.ptp(p)
    room = room_geometry(scene['room'])
    eps = room.area * 1e-10
    # Ignore surfaces entirely below the chair bottom (e.g. a supporting rug).
    obstacles = [other.footprint for j, (_, other) in enumerate(objects) if j != index
                 and min(bounds.high[2], other.high[2]) > max(bounds.low[2], other.low[2]) + depth * 1e-8]
    occupied = unary_union(obstacles)
    for name, direction in [('backward', -front), ('sideways_positive', side), ('sideways_negative', -side)]:
        for factor in (.25, .5, .75, 1, 1.25, 1.5, 1.75, 2):
            delta = direction * depth * factor
            end = translate(bounds.footprint, *delta)
            swept = unary_union([bounds.footprint, end]).convex_hull
            if swept.difference(room).area > eps or swept.intersection(occupied).area > eps:
                continue
            face = p.max() + float(delta @ front)
            left, right = q.min() + float(delta @ side), q.max() + float(delta @ side)
            approach = Polygon([front*u + side*v for u, v in
                                [(face,left),(face+depth,left),(face+depth,right),(face,right)]])
            if approach.difference(room).area <= eps and approach.intersection(occupied).area <= eps:
                return {'status': 'box_clear_candidate', 'direction': name, 'distanceInChairDepths': factor,
                        'frontStripDepthInChairDepths': 1,
                        'meaning': 'Straight swept box and a chair-width/depth front patch are clear. Person route, turning and ergonomic fit are not certified.'}
    return {'status': 'not_established', 'meaning': 'No candidate in this bounded straight-translation search. This does not prove a chair is immovable; turning and other paths were not tested.'}


def spatial_facts(scene):
    objects = ordered_objects(scene)
    pairs, fronts, chairs = [], [], []
    for i, (a, A) in enumerate(objects):
        f = functional_front(scene, a)
        if f is not None:
            front, side, p, q = basis(A, f)
            neighbors = []
            for j, (b, B) in enumerate(objects):
                if i == j:
                    continue
                bp, bq = B.points[:, :2] @ front, B.points[:, :2] @ side
                coverage = max(0, min(q.max(), bq.max()) - max(q.min(), bq.min())) / np.ptp(q)
                if coverage > 1e-6 and bp.max() > p.max() + np.ptp(p)*1e-8:
                    neighbors.append({'object': str(j+1), 'frontageFraction': compact(coverage),
                        'signedGapInObjectDepths': compact((bp.min()-p.max())/np.ptp(p)),
                        'verticalOverlapFraction': compact(max(0,min(A.high[2], B.high[2])-max(A.low[2], B.low[2]))/(A.high[2]-A.low[2]))})
            fronts.append({'object': str(i+1), 'neighborsAcrossFront': neighbors})
        if presentation_label(a['label']) == 'chair' and f is not None:
            chairs.append({'object': str(i+1), **chair_candidate(scene, objects, i)})
        for j, (b, B) in enumerate(objects[i+1:], i+1):
            overlap = intersection_volume(A, B)
            pairs.append({'objects': [str(i+1), str(j+1)],
                          'boxIntersectionFractions': [compact(overlap/A.volume), compact(overlap/B.volume)],
                          'footprintGapInMeanBoxWidths': compact(A.footprint.distance(B.footprint) /
                              ((math.sqrt(A.footprint.area)+math.sqrt(B.footprint.area))/2)),
                          'verticalGapInMeanBoxHeights': compact(max(0, A.low[2]-B.high[2], B.low[2]-A.high[2]) /
                              ((A.high[2]-A.low[2]+B.high[2]-B.low[2])/2))})
    return {'version': VERSION, 'pairs': pairs, 'fronts': fronts, 'chairCandidates': chairs}


def task_evidence(scene, profile, facts=None, catalog=None):
    evidence = inventory(scene)
    if profile == 'proportions':
        evidence['catalogPairExamples'] = volume_examples(evidence['objects'], catalog)
    else:
        evidence = {'objects': evidence['objects'], 'spatialFacts': facts or spatial_facts(scene)}
    return evidence
