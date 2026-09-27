"""Method-blind object keys and scale-invariant numbers from untouched boxes."""
from itertools import combinations
import math

from serverless.benchmark.geometry import Box, furniture
from serverless.benchmark.review_annotations import presentation_label, functional_front, FRONT_MEANINGS


def ordered_objects(scene):
    return sorted(((item, Box(item)) for item in furniture(scene)),
                  key=lambda pair: (presentation_label(pair[0]['label']), tuple(pair[1].points.mean(axis=0))))


def inventory(scene):
    objects, volumes = [], {}
    for index, (item, box) in enumerate(ordered_objects(scene), 1):
        key = str(index)
        label = presentation_label(item['label'])
        front = functional_front(scene, item)
        if not math.isfinite(box.volume) or box.volume <= 0:
            raise ValueError('Positive finite box volume required')
        objects.append({'id': key, 'category': label,
                        'front': FRONT_MEANINGS[label] if front is not None else None})
        volumes[key] = box.volume
    if not 2 <= len(objects) <= 9:
        raise ValueError('Numbered pilot panels support 2-9 furniture objects')
    smallest = min(volumes.values())
    # Six significant digits avoid presenting false precision while preserving
    # the ratios regardless of which object defines the normalization anchor.
    compact = lambda value: float(f'{value:.6g}')
    ratios = [{'objects': [first, second], 'ratio': compact(volumes[first] / volumes[second])}
              for first, second in combinations(volumes, 2)]
    return {'objects': objects, 'normalizedVolumes': {key: compact(v / smallest) for key, v in volumes.items()},
            'volumeRatios': ratios}


def task_evidence(full, profile):
    # Relative-volume values must never leak into other assigned dimensions.
    if profile == 'proportions':
        return full
    return {'objects': full['objects']}
