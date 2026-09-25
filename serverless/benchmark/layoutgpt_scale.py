"""Recover LayoutGPT's per-room pixel scale, without changing its predictions.

The authors' render_from_files.py denormalizes every pixel layout using its
source room's shorter floor side / 256. Match by ID, never furniture size.
Only clearance can then join the existing physical-space comparison: no mesh
contact or material-intersection evidence is manufactured from output boxes.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import io
import json
import math
from pathlib import Path
import struct
import zipfile
import zlib

import numpy as np

from serverless.benchmark.geometry import measure
from serverless.benchmark.import_layoutgpt import COMMIT, verify_source
from serverless.cloud_benchmark.checkpoint import write_json


def physical_scene(scene, vertices, *, require_prompt_match=True):
    """Apply the pinned author's denormalizer, preserving the prompt rectangle.

Released prompts do not always equal current metadata's normalized dimensions.
The official renderer still uses metadata's shorter side / 256 for those rows.
New controlled calls MUST reproduce their known prompt preprocessing exactly.
"""
    vertices = np.asarray(vertices, dtype=float)
    if vertices.ndim != 2 or vertices.shape[1] != 3 or not np.isfinite(vertices).all():
        raise ValueError('Finite 3D floor vertices required')
    sides = np.ptp(vertices[:, [0, 2]], axis=0)
    shorter = float(min(sides))
    if shorter <= 0 or not math.isfinite(shorter) or scene['units'] != 'px':
        raise ValueError('Positive source floor size and native pixel predictions required')
    expected = [int(float(side) / shorter * 256) for side in sides]
    # Float32 preprocessing can truncate one unit differently from float64.
    # Reproduce its original precision too; reject any other dimension mismatch.
    original = np.asarray(vertices, dtype=np.float32)
    sides32 = np.ptp(original[:, [0, 2]], axis=0)
    expected32 = [int(side / min(sides32) * 256) for side in sides32]
    polygon = scene['room']['polygon']
    actual = [max(p[i] for p in polygon)-min(p[i] for p in polygon) for i in (0, 1)]
    if require_prompt_match and actual != expected and actual != expected32:
        raise ValueError(f"Source floor dimensions do not reproduce {scene['id']}: prompt {actual}, metadata {expected}/{expected32}")
    scale = shorter / 256
    result = deepcopy(scene)
    result['units'] = 'm'
    for item in result['objects']:
        item['corners'] = [[value * scale for value in point] for point in item['corners']]
    result['room']['polygon'] = [[value * scale for value in point] for point in polygon]
    result['room']['floorZ'] *= scale
    return result, scale


def apply_clearance(rows, evidence):
    """Attach only the new metric; frozen review geometry/digests stay unchanged."""
    selected = [row for row in rows if row['scene']['model'] == 'layoutgpt']
    entries = {entry['sceneId']: entry for entry in evidence['rooms']}
    if len(entries) != len(evidence['rooms']) or set(entries) != {row['scene']['id'] for row in selected}:
        raise ValueError('Scale evidence must cover exactly the published LayoutGPT set')
    for row in selected:
        entry = entries[row['scene']['id']]
        checksum = hashlib.sha256(json.dumps(row['scene'], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if checksum != entry['sceneSha256']:
            raise ValueError('Scale evidence belongs to a different prediction')
        value = entry['connectedClearancePct']
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100:
            raise ValueError('Invalid physical clearance result')
        row['metrics']['connectedClearancePct'] = value
        row['metrics']['unavailable'].pop('clearance', None)
        row['metrics']['unavailable']['supportGap'] = 'Output boxes do not identify the real supporting mesh surfaces.'
        row['metrics']['unavailable']['belowFloor'] = 'Output boxes are not evaluated mesh vertices.'


def compile_scale(evidence, released, exports, cache, living_cache, output):
    from serverless.benchmark.layoutgpt_controlled import download_url, RemoteArchive, ranged, DATA_URL
    from urllib.request import Request, urlopen
    raw = released.read_bytes()
    verify_source(raw, 'bedroom')
    original = json.loads(raw)
    rows = [row for row in json.loads(evidence.read_bytes())['rows']
            if row['scene']['model'] == 'layoutgpt' and row['scene']['roomType'] == 'bedroom']
    rows.extend(row for path in exports for row in json.loads(path.read_bytes())['rows'])
    identities = {}
    for row in rows:
        scene = row['scene']
        name = original[scene['provenance']['row']]['query_id'] if scene['roomType'] == 'bedroom' else scene['provenance']['sourceRoomId']
        if Path(name).name != name or '..' in name:
            raise ValueError('Invalid source room ID')
        identities[scene['id']] = (name, 'bedroom' if scene['roomType'] == 'bedroom' else 'livingroom')
    url = download_url()
    with urlopen(Request(url, headers={'Range': 'bytes=-1'}), timeout=60) as response:
        size = int(response.headers['Content-Range'].split('/')[-1])
    with zipfile.ZipFile(RemoteArchive(url, size)) as archive:
        entries = {info.filename: info for info in archive.infolist()}
    cache.mkdir(parents=True, exist_ok=True)

    def one(row):
        scene = row['scene']; name, kind = identities[scene['id']]
        entry = entries[f'data_output/{kind}/{name}/boxes.npz']
        path = (living_cache / name / 'boxes.npz') if kind == 'livingroom' else cache / kind / name / 'boxes.npz'
        if path.exists():
            raw = path.read_bytes()
        else:
            header = ranged(url, entry.header_offset, entry.header_offset + 29)
            if header[:4] != b'PK\x03\x04':
                raise ValueError('Invalid archive local header')
            lengths = struct.unpack_from('<HH', header, 26)
            start = entry.header_offset + 30 + sum(lengths)
            compressed = ranged(url, start, start + entry.compress_size - 1)
            if entry.compress_type == zipfile.ZIP_DEFLATED:
                raw = zlib.decompress(compressed, -15)
            elif entry.compress_type == zipfile.ZIP_STORED:
                raw = compressed
            else:
                raise ValueError('Unsupported metadata compression')
        if len(raw) != entry.file_size or zlib.crc32(raw) != entry.CRC:
            raise ValueError('Metadata CRC or size mismatch')
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
        with np.load(io.BytesIO(raw), allow_pickle=False) as data:
            scaled, scale = physical_scene(scene, data['floor_plan_vertices'], require_prompt_match=kind=='livingroom')
        metric = measure(scaled)
        return {'sceneId': scene['id'], 'roomType': scene['roomType'], 'sourceRoomId': name,
                'sceneSha256': hashlib.sha256(json.dumps(scene, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
                'metadataSha256': hashlib.sha256(raw).hexdigest(), 'metresPerPixel': scale,
                'roomAreaM2': metric['roomArea'], 'connectedClearancePct': metric['connectedClearancePct']}

    with ThreadPoolExecutor(max_workers=6) as pool:
        records = []
        for record in pool.map(one, rows):
            records.append(record)
            if len(records) % 50 == 0:
                print(json.dumps({'scaledRooms': len(records), 'total': len(rows)}), flush=True)
    document = {'schemaVersion': 1, 'sourceCommit': COMMIT, 'metadataSource': DATA_URL,
                'scaleImplementation': f'https://github.com/UCSB-AI/LayoutGPT/blob/{COMMIT}/ATISS/scripts/render_from_files.py#L65-L87',
                'method': 'Use the authors\' denormalization: metres per pixel = shorter metadata floor side / 256, matched by source room ID. Preserve the predicted boxes and supplied prompt rectangle under uniform scaling; do not substitute a different floor outline or retrieve replacement meshes. Controlled-call dimensions must also reproduce their prompt preprocessing. Clearance uses the shared 0.6 m diameter, 1.8 m height diagnostic.',
                'rooms': records}
    apply_clearance(rows, document)
    write_json(output, document)
    print(json.dumps({'complete': True, 'rooms': len(records)}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('evidence', 'released', 'cache', 'living-cache', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--exports', type=Path, action='append', required=True)
    compile_scale(**vars(parser.parse_args()))


if __name__ == '__main__':
    main()
