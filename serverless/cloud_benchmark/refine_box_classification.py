"""Apply the common wall-mounted target policy to saved box distances.

No generation, geometry, individual distance or AI judgment is changed. This
rebuilds only box aggregations after adding the wall_art alias to the policy.
"""
import argparse
from collections import Counter
from pathlib import Path

from serverless.benchmark.box_support import METHOD, MOUNTED, reclassify_measurement
from serverless.benchmark.geometry import summarize
from serverless.cloud_benchmark.checkpoint import write_json
from serverless.cloud_benchmark.staged_pilot import read, digest


def refine(directory):
    directory = Path(directory)
    payload = read(directory/'box-support-measurements.json')
    old = payload['rooms']
    payload.update(method=METHOD, excludedCategories=sorted(MOUNTED),
                   rooms=[reclassify_measurement(row) for row in old])
    write_json(directory/'box-support-measurements.json', payload)
    document = read(directory/'comparison.json')
    for room, models in document['boxSupport']['byRoomType'].items():
        for model in models:
            rows = [r for r in payload['rooms'] if r['roomType'] == room and r['model'] == model]
            models[model] = {'n': len(rows), 'metrics': {key: summarize([r[key] for r in rows if r[key] is not None])
                for key in ('gapCm','floorGapCm','objectGapCm','belowFloorCm')},
                'objects': dict(Counter(o['supportKind'] for r in rows for o in r['objects'])),
                'excludedObjects': sum(len(r['excludedObjects']) for r in rows)}
    document['boxSupport'].update(method=METHOD, sha256=digest((directory/'box-support-measurements.json').read_bytes()))
    document.pop('evidenceDigest')
    document['evidenceDigest'] = digest(document)
    write_json(directory/'comparison.json', document)
    inputs = read(directory/'publication-inputs.json')
    inputs['boxSupportSha256'] = document['boxSupport']['sha256']
    write_json(directory/'publication-inputs.json', inputs)
    return {'changedRooms': sum(a['objects'] != b['objects'] for a,b in zip(old,payload['rooms'])),
            'excludedTargets': sum(len(a['objects'])-len(b['objects']) for a,b in zip(old,payload['rooms']))}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    print(refine(**vars(parser.parse_args())))
