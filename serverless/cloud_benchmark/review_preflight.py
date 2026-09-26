"""Fail closed on altered geometry, unbalanced sides or leaked review cues."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from serverless.benchmark.stimuli import digest
from serverless.benchmark.review_annotations import functional_front
from serverless.cloud_benchmark.checkpoint import write_json
from serverless.cloud_benchmark.reviews import PLAN
from serverless.study.store import SQLiteStudyStore


def geometry(path):
    return [node.attrib['points'] for node in ET.fromstring(path.read_bytes()).iter()
            if node.tag.endswith('}polygon')]


def preflight(root, recorded):
    scenes = {scene['id']: scene for scene in json.loads((root / 'source-scenes.json').read_bytes())['scenes']}
    image_references = marked_objects = 0
    for name, filename in (('set-a', 'ai-pilot-responses.json'), ('set-b', 'ai-pilot-infinigen-responses.json')):
        protocol = json.loads((root / name / 'protocol.json').read_bytes())
        old = json.loads((recorded / filename).read_bytes())
        originals = {row['caseId']: row for row in old['stimuli']}
        cases = {row['id']: row for row in protocol['cases']}
        assert set(cases) == set(originals), 'Pair selection changed'
        assert protocol['reviewerPlan'] == PLAN
        for evidence in protocol['stimulusEvidence']:
            case = cases[evidence['caseId']]
            for side, field in (('soilie', 'relationImage'), ('baseline', 'comparisonImage')):
                scene = scenes[evidence[side + 'Scene']]
                assert digest(scene) == evidence[side + 'Digest'], 'Source scene changed'
                assert not scene.get('fixture'), 'Synthetic data in review'
                expected_arrows = sum(functional_front(scene, item) is not None for item in scene['objects']
                                      if item.get('kind', 'furniture') == 'furniture') * 3
                marked_objects += expected_arrows // 3
                old_path = recorded / 'stimuli' / Path(originals[evidence['caseId']][side + 'Image']).name
                for profile, variant in [('default', case), *case['profileImages'].items()]:
                    path = root / 'site' / variant[field].lstrip('/')
                    body = path.read_bytes()
                    text = body.decode()
                    assert hashlib.sha256(body).hexdigest()[:24] == path.stem
                    assert geometry(path) == geometry(old_path), 'Rendered geometry changed'
                    assert text.count('class="front"') == expected_arrows
                    assert ('Relative bounding-box volumes' in text) == (profile == 'proportions')
                    assert not any(cue in text.lower() for cue in ('judge ', 'single bed', 'double bed', 'layoutgpt', 'infinigen', 'soilie', 'simple desk', 'cell shelf'))
                    image_references += 1
        store = SQLiteStudyStore(root / name / 'private/pilot.sqlite3')
        for session in store.sessions():
            counts = Counter((cases[row['caseId']]['balanceStratum'], row['leftCondition'])
                             for row in session['assignments'] if not row['repeatOf'])
            assert len(counts) == 4 and set(counts.values()) == {60}, 'Unequal sides within room type'
            assert session['promptHash'] == hashlib.sha256(
                json.loads((root / 'packets' / session['reviewerId'] / (name + '.json')).read_bytes())['prompt'].encode()).hexdigest()
    result = {'passed': True, 'pairs': 480, 'imageReferences': image_references,
              'geometryChanged': False, 'markedObjectReferences': marked_objects,
              'leftRightPerReviewerPerBaselinePerRoom': '60 / 60',
              'priorVotesReused': False, 'limitations': 'Source conventions establish fronts; box views do not independently expose asset surfaces. Geometry and inventory may still reveal generator tendencies.'}
    write_json(root / 'preflight.json', result)
    print(json.dumps(result), flush=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--recorded', type=Path, required=True)
    args = parser.parse_args()
    preflight(args.root, args.recorded)
