"""Opt-in reference-catalog preparation, never a change to a frozen reviewer.

Keep the original catalog byte-for-byte: saved answers consumed that version.
The candidate is deliberately not imported by functional_evidence.py. A later
protocol must explicitly freeze its own compiled catalog and evidence hashes.
"""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
from itertools import combinations
import json
import math
from pathlib import Path
from urllib.parse import urlparse

from serverless.benchmark.geometry import furniture
from serverless.benchmark.review_annotations import presentation_label

ROOT = Path(__file__).parents[1] / 'study'
UNITS = {'in': .0254, 'cm': .01, 'mm': .001, 'm': 1}


def validate(products):
    seen = set()
    for item in products:
        if not item.get('id') or item['id'] in seen:
            raise ValueError('Product IDs must be unique and nonempty')
        seen.add(item['id'])
        dimensions = item.get('dimensions', [])
        if (len(dimensions) != 3 or any(isinstance(d, bool) or not isinstance(d, (int, float))
                or not math.isfinite(d) or d <= 0 for d in dimensions)):
            raise ValueError('Three finite positive exterior dimensions required')
        if item.get('unit') not in UNITS:
            raise ValueError('Explicit supported source units required')
        if not item.get('basis'):
            raise ValueError('The measured configuration must be documented')
        parsed = urlparse(item.get('source', ''))
        if parsed.scheme != 'https' or not parsed.netloc:
            raise ValueError('A direct source URL is required')
        labels = [item['category'], *item.get('alsoCategories', [])]
        if len(set(labels)) != len(labels) or any(label != presentation_label(label) for label in labels):
            raise ValueError('Use unique neutral presentation categories')


def candidate(path=ROOT / 'reference_volumes_candidate.json'):
    path = Path(path)
    document = json.loads(path.read_bytes())
    base_path = path.parent / document['extends']
    if base_path.resolve().parent != path.resolve().parent:
        raise ValueError('Parent catalog must be in the same directory')
    raw = base_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != document['baseSha256']:
        raise ValueError('Frozen parent catalog checksum differs')
    base = json.loads(raw)
    inherited = [dict(item, dimensions=item['dimensionsIn'], unit='in',
                      basis='Assembled exterior dimensions; inherited from the frozen v1 catalog.')
                 for item in base['products']]
    for item in inherited:
        del item['dimensionsIn']
    products = inherited + deepcopy(document['products'])
    validate(products)
    for item in products:
        # Do not substitute liquid capacity or solid-material volume for a box.
        item['dimensionsM'] = [d * UNITS[item['unit']] for d in item['dimensions']]
        item['envelopeM3'] = math.prod(item['dimensionsM'])
    return {**document, 'products': products,
            'sourceSha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def categories(catalog):
    return {label for item in catalog['products'] for label in
            [item['category'], *item.get('alsoCategories', [])]}


def coverage(scenes, catalog):
    """Coverage counts example availability, not correctness or normality."""
    available = categories(catalog)
    groups = defaultdict(Counter)
    missing = defaultdict(Counter)
    for scene in scenes:
        key = scene['model'] + ':' + scene['roomType']
        labels = [presentation_label(item['label']) for item in furniture(scene)]
        group = groups[key]
        group['scenes'] += 1
        group['objects'] += len(labels)
        group['coveredObjects'] += sum(label in available for label in labels)
        missing[key].update(label for label in labels if label not in available)
        for a, b in combinations(labels, 2):
            group['objectPairs'] += 1
            group['coveredObjectPairs'] += a in available and b in available
    return {key: {**counts, 'missingCategories': dict(sorted(missing[key].items()))}
            for key, counts in sorted(groups.items())}


def require_coverage(scenes, catalog):
    missing = sorted({label for group in coverage(scenes, catalog).values()
                      for label in group['missingCategories']})
    if missing:
        raise ValueError('Catalog still lacks complete envelopes for: ' + ', '.join(missing))


def ratios(catalog, first_category, second_category):
    """Illustrative product-pair ratios; never infer hard bounds from examples."""
    def selected(category):
        return [item for item in catalog['products'] if category in
                [item['category'], *item.get('alsoCategories', [])]]
    return [{'first': a['id'], 'second': b['id'], 'ratio': a['envelopeM3'] / b['envelopeM3']}
            for a in selected(first_category) for b in selected(second_category)]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenes', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    catalog = candidate()
    scenes = json.loads(args.scenes.read_bytes())['scenes']
    report = {'version': catalog['version'], 'status': catalog['status'],
              'products': len(catalog['products']), 'categories': sorted(categories(catalog)),
              'candidateSha256': catalog['sourceSha256'], 'baseSha256': catalog['baseSha256'],
              'scenesSha256': hashlib.sha256(args.scenes.read_bytes()).hexdigest(),
              'groups': coverage(scenes, catalog)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf8') as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps(report, indent=2))
