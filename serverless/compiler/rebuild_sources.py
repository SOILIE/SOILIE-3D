"""Recover native RGB-D inputs and stage a fresh corpus with the human-written collector.

All downloads and derived files stay in an explicitly named, ignored workspace.
This module orchestrates existing core functions; it does not replace projection,
segmentation, camera pairing, relation extraction, or working-catalog semantics.
No command here publishes artifacts or starts room generation.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import ssl
import subprocess
import sys
import time
from urllib.parse import quote, unquote, urlsplit
from urllib.request import Request, urlopen
from zipfile import ZipFile


REPOSITORY = Path(__file__).resolve().parents[2]
WASHINGTON_BASE = 'https://rgbd-dataset.cs.washington.edu/dataset/rgbd-scenes-v2/'
WASHINGTON_ARCHIVES = ('rgbd-scenes-v2_imgs.zip', 'rgbd-scenes-v2_pc.zip')
SUN3D_BASE = 'https://sun3d.cs.princeton.edu/data/'
# Core import_tools retains a legacy global SSL override. Source recovery always
# supplies its own verified context, even after importing that module later.
TLS_CONTEXT = ssl.create_default_context()
CHUNK_BYTES = 4 * 1024 * 1024


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(CHUNK_BYTES), b''):
            value.update(chunk)
    return value.hexdigest()


def write_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(path.name + '.writing')
    staged.write_text(json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + '\n',
                      encoding='utf-8', newline='\n')
    os.replace(staged, path)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def core_fingerprint() -> str:
    """Bind resumable source work to the exact current production modules."""
    value = hashlib.sha256()
    for path in sorted((REPOSITORY / 'modules').glob('*.py')):
        value.update(path.name.encode('utf-8') + b'\0' + digest(path).encode('ascii') + b'\n')
    return value.hexdigest()


def runtime_info() -> dict:
    packages = {}
    for name in ['numpy', 'pandas', 'scipy', 'scikit-image', 'matplotlib', 'Pillow', 'plyfile']:
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    return {'python': sys.version, 'packages': packages}


def _open(request):
    response = urlopen(request, context=TLS_CONTEXT, timeout=90)
    if urlsplit(response.url).scheme != 'https':
        response.close()
        raise ValueError('Source download redirected away from HTTPS')
    return response


def download(url: str, path: Path, *, resume: bool = False) -> dict:
    """Download one source with verified TLS, bounded memory, and a SHA receipt.

    Resume is allowed only against an unchanged remote identity, with an exact
    Content-Range response. A completed file is reused only after its SHA check.
    """
    if urlsplit(url).scheme != 'https':
        raise ValueError('Only HTTPS source URLs are accepted')
    path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path = path.with_name(path.name + '.download.json')
    partial_path = path.with_name(path.name + '.part')
    with _open(Request(url, method='HEAD')) as response:
        length = int(response.headers['Content-Length'])
        identity = {'url': url, 'resolvedUrl': response.url, 'bytes': length,
                    'etag': response.headers.get('ETag'),
                    'lastModified': response.headers.get('Last-Modified')}
    old = json.loads(receipt_path.read_text(encoding='utf-8')) if receipt_path.exists() else None
    if old is not None and old['source'] != identity:
        raise ValueError(f'Source identity changed: {url}')
    if path.exists():
        if old is None or old.get('state') != 'COMPLETE' or path.stat().st_size != length:
            raise ValueError(f'Existing source has no matching complete receipt: {path}')
        if digest(path) != old['sha256']:
            raise ValueError(f'Existing source SHA differs: {path}')
        return old
    if partial_path.exists() and (not resume or old is None):
        raise ValueError(f'Refusing unapproved/unsealed partial download: {partial_path}')
    offset = partial_path.stat().st_size if partial_path.exists() else 0
    if offset > length:
        raise ValueError(f'Partial source exceeds declared size: {partial_path}')
    write_json(receipt_path, {'state': 'INCOMPLETE', 'source': identity, 'startedAt': utc_now()})
    sha = hashlib.sha256()
    if offset:
        with partial_path.open('rb') as existing:
            for chunk in iter(lambda: existing.read(CHUNK_BYTES), b''):
                sha.update(chunk)
    headers = {}
    if offset:
        headers['Range'] = f'bytes={offset}-'
        validator = identity['etag'] or identity['lastModified']
        if not validator:
            raise ValueError('Cannot resume without an ETag or Last-Modified validator')
        headers['If-Range'] = validator
    started = time.monotonic()
    last_update = started
    if offset < length:
        with _open(Request(url, headers=headers)) as response:
            for header, field in [('ETag', 'etag'), ('Last-Modified', 'lastModified')]:
                if identity[field] is not None and response.headers.get(header) != identity[field]:
                    raise ValueError(f'Source identity changed between HEAD and GET: {url}')
            if offset:
                expected = f'bytes {offset}-{length - 1}/{length}'
                if response.status != 206 or response.headers.get('Content-Range') != expected:
                    raise ValueError(f'Server did not honor exact resume range: {url}')
            elif response.status != 200:
                raise ValueError(f'Unexpected initial download response: {response.status}')
            if int(response.headers['Content-Length']) != length - offset:
                raise ValueError('Source response length differs from sealed HEAD metadata')
            with partial_path.open('ab' if offset else 'xb') as destination:
                for chunk in iter(lambda: response.read(CHUNK_BYTES), b''):
                    destination.write(chunk)
                    sha.update(chunk)
                    if destination.tell() > length:
                        raise ValueError('Source response exceeded declared length')
                    if time.monotonic() - last_update >= 10:
                        print(f'download {path.name}: {destination.tell():,}/{length:,} bytes', flush=True)
                        last_update = time.monotonic()
    if partial_path.stat().st_size != length:
        raise ValueError(f'Incomplete source download: {partial_path}')
    os.replace(partial_path, path)
    receipt = {'state': 'COMPLETE', 'source': identity, 'sha256': sha.hexdigest(),
               'completedAt': utc_now()}
    write_json(receipt_path, receipt)
    print(f'verified source {path.name}: {length:,} bytes, SHA-256 {receipt["sha256"]}', flush=True)
    return receipt


def _workspace(value: Path) -> Path:
    root = value.resolve()
    expected = (REPOSITORY / '.codex').resolve()
    if root == expected or not root.is_relative_to(expected):
        raise ValueError('Workspace must be a dedicated child of this repository .codex directory')
    root.mkdir(parents=True, exist_ok=True)
    return root


def require_task_path(path: Path, workspace: Path) -> None:
    if not path.resolve().is_relative_to(workspace.resolve()):
        raise ValueError(f'Source destination escaped its task workspace: {path}')


def fetch_washington(workspace: Path) -> dict:
    records = []
    for filename in WASHINGTON_ARCHIVES:
        destination = workspace / 'raw' / filename
        require_task_path(destination, workspace)
        records.append(download(WASHINGTON_BASE + filename, destination, resume=True))
    report = {'kind': 'native-washington-archive-recovery', 'archives': records}
    write_json(workspace / 'washington-archives.json', report)
    return report


def _member_target(name: str, candidate: Path) -> Path | None:
    """Map only known official source layouts; never trust ZIP extraction paths."""
    relative = PurePosixPath(name)
    if relative.is_absolute() or '..' in relative.parts or '\\' in name:
        raise ValueError(f'Unsafe ZIP member: {name!r}')
    image = re.fullmatch(r'rgbd-scenes-v2/imgs/(scene_\d{2})/(\d+-(color|depth)\.png)', name)
    if image:
        scene, leaf, kind = image.groups()
        return candidate / 'data' / 'washington' / scene / ('image' if kind == 'color' else 'depth') / leaf
    point_cloud = re.fullmatch(r'rgbd-scenes-v2/pc/(\d{2}\.(?:ply|label|pose))', name)
    if point_cloud:
        return candidate / 'data' / 'washington' / 'pc' / point_cloud.group(1)
    if relative.suffix.casefold() in {'.ply', '.label', '.pose', '.png'}:
        raise ValueError(f'Unrecognized scientific ZIP member layout: {name!r}')
    return None


def extract_washington(workspace: Path) -> dict:
    candidate = workspace / 'candidate'
    inventory = []
    for filename in WASHINGTON_ARCHIVES:
        archive_path = workspace / 'raw' / filename
        source_receipt = json.loads(archive_path.with_name(filename + '.download.json').read_text(encoding='utf-8'))
        if source_receipt['state'] != 'COMPLETE' or digest(archive_path) != source_receipt['sha256']:
            raise ValueError(f'Archive source seal is invalid: {archive_path}')
        with ZipFile(archive_path) as archive:
            for member in archive.infolist():
                if member.is_dir():
                    continue
                target = _member_target(member.filename, candidate)
                if target is None:
                    continue
                require_task_path(target, workspace)
                if (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError('Source ZIP symlinks are not accepted')
                target.parent.mkdir(parents=True, exist_ok=True)
                sha = hashlib.sha256()
                staged = target.with_name(target.name + '.extracting')
                # Reading to EOF asks ZipFile to check the original CRC. The
                # resulting SHA seals uncompressed bytes for subsequent use.
                with archive.open(member) as source, staged.open('wb') as destination:
                    for chunk in iter(lambda: source.read(CHUNK_BYTES), b''):
                        sha.update(chunk)
                        destination.write(chunk)
                if staged.stat().st_size != member.file_size:
                    raise ValueError(f'ZIP member length mismatch: {member.filename}')
                if target.exists():
                    if target.stat().st_size != member.file_size or digest(target) != sha.hexdigest():
                        raise ValueError(f'Existing extracted source differs: {target}')
                    staged.unlink()
                else:
                    os.replace(staged, target)
                inventory.append({'archive': filename, 'member': member.filename,
                                  'path': target.relative_to(candidate).as_posix(),
                                  'bytes': member.file_size, 'crc32': f'{member.CRC:08x}',
                                  'sha256': sha.hexdigest()})
                if len(inventory) % 500 == 0:
                    print(f'extracted and sealed {len(inventory):,} Washington source members', flush=True)
    report = {'kind': 'native-washington-extracted-source-inventory', 'files': inventory}
    write_json(workspace / 'washington-files.json', report)
    return report


def _get_bytes(url: str) -> bytes:
    with _open(Request(url)) as response:
        return response.read()


def fetch_sun3d(workspace: Path, workers: int = 4) -> dict:
    """Recover annotated frame bodies, exact poses, and full pairing inventories."""
    from modules.source_geometry import (pair_rgb_depth_files, pinned_extrinsics_filename,
                                         resolve_source_profile, reshape_c2w_rows, select_c2w_pose)
    from modules.relation_manifest import validate_relation_location

    candidate = workspace / 'candidate'
    scenes = []
    tasks = []
    for annotation in sorted((REPOSITORY / 'json' / 'princeton').glob('*.json')):
        document = json.loads(annotation.read_text(encoding='utf-8'))
        scene = document['name']
        validate_relation_location('princeton', scene)
        extrinsics = pinned_extrinsics_filename(document['extrinsics'])
        base = SUN3D_BASE + quote(scene, safe='/') + '/'
        scene_root = candidate / 'data' / 'princeton' / scene
        copied_annotation = candidate / 'json' / 'princeton' / annotation.name
        require_task_path(scene_root, workspace)
        require_task_path(copied_annotation, workspace)
        copied_annotation.parent.mkdir(parents=True, exist_ok=True)
        if copied_annotation.exists() and copied_annotation.read_bytes() != annotation.read_bytes():
            raise ValueError(f'Candidate annotation differs: {copied_annotation}')
        shutil.copyfile(annotation, copied_annotation)
        metadata = []
        for relative in ['intrinsics.txt', 'extrinsics/' + extrinsics]:
            destination = scene_root.joinpath(*relative.split('/'))
            require_task_path(destination, workspace)
            metadata.append(download(base + relative, destination))
        listing_payload = _get_bytes(base + 'depth/')
        listing_path = workspace / 'source-metadata' / 'princeton' / annotation.stem / 'depth-listing.html'
        listing_path.parent.mkdir(parents=True, exist_ok=True)
        listing_path.write_bytes(listing_payload)
        names = sorted(set(re.findall(r'href=["\']([^"\']+\.png)["\']', listing_payload.decode('utf-8'))))
        profile = resolve_source_profile('princeton')
        pairs = pair_rgb_depth_files(document['fileList'], names, profile.pairing_strategy)
        import numpy as np
        poses = reshape_c2w_rows(np.loadtxt(scene_root / 'extrinsics' / extrinsics, ndmin=2))
        used = []
        for index, pair in enumerate(pairs):
            pose = select_c2w_pose(poses, pair, index, profile.pose_index_strategy)
            if document['frames'][index] and document['frames'][index].get('polygon'):
                used.append({'jsonFrameIndex': index, 'image': pair.image_name, 'depth': pair.depth_name,
                             'imageFrameId': pair.image_frame_id, 'depthFrameId': pair.depth_frame_id,
                             'timestampDelta': pair.timestamp_delta, 'cameraToWorld': pose.tolist()})
        # Only nonempty annotated frames need bodies. The local depth subset
        # contains each true global nearest match, so retained frames cannot
        # silently switch depth images when the collector repeats pairing.
        image_names = sorted({record['image'] for record in used})
        depth_names = sorted({record['depth'] for record in used})
        local_pairs = pair_rgb_depth_files(document['fileList'], depth_names, profile.pairing_strategy)
        for record in used:
            if local_pairs[record['jsonFrameIndex']].depth_name != record['depth']:
                raise ValueError('Selective local depth inventory changed a retained frame pairing')
        for kind, filenames in [('image', image_names), ('depth', depth_names)]:
            for leaf in filenames:
                destination = scene_root / kind / leaf
                require_task_path(destination, workspace)
                tasks.append((base + kind + '/' + leaf, destination))
        scenes.append({'scene': scene, 'annotation': annotation.name, 'annotationSha256': digest(annotation),
                       'metadata': metadata, 'depthListingSha256': hashlib.sha256(listing_payload).hexdigest(),
                       'depthInventory': names, 'retainedFrames': used})
    with ThreadPoolExecutor(max_workers=workers) as pool:
        records = list(pool.map(lambda task: download(*task, resume=True), tasks))
    report = {'kind': 'sun3d-annotated-native-source-recovery', 'scenes': scenes, 'files': records}
    write_json(workspace / 'sun3d-files.json', report)
    return report


@contextmanager
def in_directory(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def preprocess_scene(workspace: Path, scene: str) -> dict:
    from modules import import_tools as core

    if re.fullmatch(r'scene_\d{2}', scene) is None:
        raise ValueError('Expected one canonical Washington scene name')
    candidate = workspace / 'candidate'
    folder = candidate / 'data' / 'washington' / scene
    annotations = candidate / 'json' / 'washington'
    point_clouds = candidate / 'data' / 'washington' / 'pc'
    fingerprint = core_fingerprint()
    receipt_path = folder / core.WASHINGTON_PREPROCESS_RECEIPT
    if receipt_path.exists():
        return core._validate_preprocess_receipt(main_folder=str(folder), fname=scene,
                    jsonDir=str(annotations), pcDir=str(point_clouds), nFrame_samples=30)
    with in_directory(candidate):
        receipt = core.project_annotations_3D_to_2D(str(folder), scene, str(annotations),
                          core._scene_annotation_files(str(point_clouds), scene), 30)
    if core_fingerprint() != fingerprint:
        raise ValueError('Production modules changed while preprocessing')
    # These are the unmodified core's receipt fields, written only after its
    # successful return. No projection or sampling calculation is duplicated.
    write_json(receipt_path, receipt)
    try:
        return core._validate_preprocess_receipt(main_folder=str(folder), fname=scene,
                    jsonDir=str(annotations), pcDir=str(point_clouds), nFrame_samples=30)
    except BaseException:
        receipt_path.unlink(missing_ok=True)
        raise


def preprocess(workspace: Path, jobs: int = 4) -> dict:
    from modules.import_tools import pre_process_input_files

    if not 1 <= jobs <= 4:
        raise ValueError('Source preprocessing concurrency must be between one and four')
    candidate = workspace / 'candidate'
    fingerprint = core_fingerprint()
    pin_path = workspace / 'preprocess-core-pin.json'
    pin = {'coreFingerprint': fingerprint, 'runtime': runtime_info()}
    if pin_path.exists() and json.loads(pin_path.read_text(encoding='utf-8')) != pin:
        raise ValueError('Preprocessing core/dependencies changed; use a fresh source candidate')
    write_json(pin_path, pin)
    scenes = sorted(path.name for path in (candidate/'data'/'washington').iterdir()
                    if path.is_dir() and re.fullmatch(r'scene_\d{2}',path.name))
    expected_scenes = sorted(json.loads(path.read_text(encoding='utf-8'))['name']
                             for path in (REPOSITORY/'json'/'washington').glob('*.json'))
    if scenes != expected_scenes:
        raise ValueError('Recovered Washington scenes differ from the reviewed source scene set')
    environment = dict(os.environ)
    for name in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','VECLIB_MAXIMUM_THREADS']:
        environment[name] = '1'
    def run(scene):
        log = workspace/'logs'/'preprocess'/(scene+'.log')
        log.parent.mkdir(parents=True,exist_ok=True)
        with log.open('wb') as output:
            subprocess.run([sys.executable,'-m','serverless.compiler.rebuild_sources','preprocess-scene',
                            '--workspace',str(workspace),'--scene',scene],cwd=REPOSITORY,env=environment,
                           stdout=output,stderr=subprocess.STDOUT,check=True)
        print(f'preprocessed and core-validated Washington {scene}',flush=True)
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        list(pool.map(run,scenes))
    with in_directory(candidate):
        # Validate the complete set through the normal production entry point.
        receipts = pre_process_input_files(dataDir='data/washington', pcDir='data/washington/pc',
                                          jsonDir='json/washington', nFrame_samples=30, rebuild=False)
    if core_fingerprint() != fingerprint:
        raise ValueError('Production modules changed while preprocessing')
    report = {'kind': 'native-washington-production-preprocess', 'scenes': list(receipts),
              'runtime': runtime_info(), 'coreFingerprint': fingerprint, 'workerCount': jobs,
              'blasThreadsPerWorker': 1}
    write_json(workspace / 'washington-preprocess.json', report)
    return report


def verify_sources(workspace: Path) -> dict:
    """Check actual local source bytes against the recovery seals before use."""
    candidate = workspace / 'candidate'
    washington = json.loads((workspace / 'washington-files.json').read_text(encoding='utf-8'))
    sun3d = json.loads((workspace / 'sun3d-files.json').read_text(encoding='utf-8'))
    files = []
    for record in washington['files']:
        files.append((candidate.joinpath(*PurePosixPath(record['path']).parts), record['bytes'], record['sha256']))
    for record in sun3d['files'] + [metadata for scene in sun3d['scenes'] for metadata in scene['metadata']]:
        url = record['source']['url']
        if not url.startswith(SUN3D_BASE):
            raise ValueError('SUN3D receipt contains an unexpected source URL')
        relative = PurePosixPath(unquote(url[len(SUN3D_BASE):]))
        files.append((candidate / 'data' / 'princeton' / relative, record['source']['bytes'], record['sha256']))
    for path, length, sha256 in files:
        if not path.resolve().is_relative_to(candidate.resolve()):
            raise ValueError('Recovered source path escaped the candidate')
        if path.stat().st_size != length or digest(path) != sha256:
            raise ValueError(f'Recovered source byte seal differs: {path}')
    for scene in sun3d['scenes']:
        annotation = candidate / 'json' / 'princeton' / scene['annotation']
        listing = workspace / 'source-metadata' / 'princeton' / annotation.stem / 'depth-listing.html'
        if digest(annotation) != scene['annotationSha256'] or digest(listing) != scene['depthListingSha256']:
            raise ValueError(f'SUN3D source annotation/listing seal differs: {annotation}')
    report = {'kind': 'verified-native-source-input-seals', 'verifiedAt': utc_now(),
              'fileCount': len(files), 'bytes': sum(length for _path, length, _sha in files),
              'washingtonInventorySha256': digest(workspace / 'washington-files.json'),
              'sun3dInventorySha256': digest(workspace / 'sun3d-files.json')}
    write_json(workspace / 'source-seals-verified.json', report)
    print(json.dumps(report, sort_keys=True), flush=True)
    return report


def verify_colors_state(workspace: Path) -> dict:
    """Verify the one incremental color chain, including resumed dataset edges."""
    colors = workspace/'candidate'/'data'/'object_colors.csv'
    state_path = workspace/'colors-state.json'
    previous_state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else None
    remaining = {}
    sequence = {}
    for path in sorted((workspace/'scene-receipts').glob('*/*.json')):
        document = json.loads(path.read_text(encoding='utf-8'))
        if document['coreFingerprint'] != core_fingerprint():
            raise ValueError('Color contribution was collected with different core modules')
        before_backup = workspace/'scene-colors'/document['dataset']/(Path(document['annotation']).stem+'.before.csv')
        before = document.get('colorsSha256Before', digest(before_backup) if before_backup.exists() else None)
        relative = path.relative_to(workspace).as_posix()
        remaining[relative] = {'receipt':relative,'receiptSha256':digest(path),
                               'before':before,'after':document['colorsSha256After']}
        sequence[relative] = document.get('colorsContributionIndex')
    ordered, current = [], None
    if previous_state is not None:
        if previous_state['coreFingerprint'] != core_fingerprint():
            raise ValueError('Previously sealed color history has a different core fingerprint')
        # The sealed sequence is authoritative. Hashes alone cannot order
        # legitimate no-op scenes whose accepted RGB contribution is empty.
        for index, sealed in enumerate(previous_state['contributions']):
            observed = remaining.pop(sealed['receipt'],None)
            if observed != sealed or observed['before'] != current:
                raise ValueError('Previously sealed color contribution history changed')
            if sequence[sealed['receipt']] not in {None,index}:
                raise ValueError('Color contribution index differs from the sealed sequence')
            ordered.append(observed)
            current = observed['after']
        if remaining:
            pending = workspace/'collector-pending.json'
            if len(remaining) != 1 or not pending.exists():
                raise ValueError('Unexpected unsealed color contribution outside a pending scene transaction')
            transaction = json.loads(pending.read_text(encoding='utf-8'))
            expected = f"scene-receipts/{transaction['dataset']}/{Path(transaction['annotation']).stem}.json"
            chosen = remaining.get(expected)
            if chosen is None or chosen['before'] != current or sequence[expected] not in {None,len(ordered)}:
                raise ValueError('New color contribution does not match the pending scene')
            ordered.append(chosen)
            current = chosen['after']
    else:
        # Recovery of an absent ledger uses explicit sequence numbers. The
        # initial pre-ledger smoke receipts have only before/after snapshots;
        # their bootstrap is accepted only when that legacy order is unique.
        while remaining:
            matches = [record for name,record in remaining.items()
                       if sequence[name] == len(ordered)]
            if not matches:
                matches = [record for name,record in remaining.items()
                           if sequence[name] is None and record['before'] == current]
            if len(matches) != 1 or matches[0]['before'] != current:
                raise ValueError('Cannot recover one unambiguous color contribution sequence')
            chosen = matches[0]
            remaining.pop(chosen['receipt'])
            ordered.append(chosen)
            current = chosen['after']
    if (digest(colors) if colors.exists() else None) != current:
        raise ValueError('Current object_colors.csv differs from the last completed scene contribution')
    state = {'schemaVersion':1,'coreFingerprint':core_fingerprint(),'colorsSha256':current,'contributions':ordered}
    write_json(state_path,state)
    return state


def collect_scene(workspace: Path, dataset: str, annotation_name: str) -> dict:
    from modules.collect_data import processJSON
    from modules.relation_manifest import sha256_file

    candidate = workspace / 'candidate'
    fingerprint = core_fingerprint()
    pin = json.loads((workspace/'preprocess-core-pin.json').read_text(encoding='utf-8')) if (workspace/'preprocess-core-pin.json').exists() else None
    if dataset == 'washington' and pin != {'coreFingerprint':fingerprint,'runtime':runtime_info()}:
        raise ValueError('Washington collection requires the exact verified preprocessing core/dependency pin')
    annotation = candidate / 'json' / dataset / annotation_name
    receipt_path = workspace/'scene-receipts'/dataset/(annotation.stem+'.json')
    if receipt_path.exists():
        raise ValueError('Scene already has a complete receipt; use resume or a fresh workspace, not duplicate color collection')
    color_state = verify_colors_state(workspace)
    colors = candidate / 'data' / 'object_colors.csv'
    backup = workspace / 'scene-colors' / dataset / (annotation.stem + '.before.csv')
    backup.parent.mkdir(parents=True, exist_ok=True)
    existed = colors.exists()
    if existed:
        shutil.copyfile(colors, backup)
    pending = workspace / 'collector-pending.json'
    write_json(pending, {'dataset': dataset, 'annotation': annotation_name,
                         'colorsExisted': existed, 'backup': str(backup)})
    try:
        with in_directory(candidate):
            _objects, records = processJSON(json.loads(annotation.read_text(encoding='utf-8')), [],
                                           str(candidate), dataset, True, 0,
                                           return_relation_records=True, annotation_path=annotation,
                                           annotation_sha256=sha256_file(annotation))
    except BaseException:
        if existed:
            shutil.copyfile(backup, colors)
        else:
            colors.unlink(missing_ok=True)
        pending.unlink(missing_ok=True)
        raise
    report = {'dataset': dataset, 'annotation': annotation_name, 'annotationSha256': digest(annotation),
              'coreFingerprint': fingerprint, 'runtime': runtime_info(),
              'colorsContributionIndex': len(color_state['contributions']),
              'colorsSha256Before': color_state['colorsSha256'],
              'colorsSha256After': digest(colors) if colors.exists() else None, 'frames': records}
    if core_fingerprint() != fingerprint:
        raise ValueError('Production modules changed while collecting a scene')
    write_json(receipt_path, report)
    verify_colors_state(workspace)
    pending.unlink(missing_ok=True)
    return report


def restore_interrupted_colors(workspace: Path) -> None:
    candidate = workspace / 'candidate'
    pending = workspace / 'collector-pending.json'
    if pending.exists():
        interrupted = json.loads(pending.read_text(encoding='utf-8'))
        receipt = workspace / 'scene-receipts' / interrupted['dataset'] / (Path(interrupted['annotation']).stem + '.json')
        # A complete receipt was written last in the child. If absent, roll
        # back incremental colors before retrying, even after a hard shutdown.
        if not receipt.exists():
            colors = candidate / 'data' / 'object_colors.csv'
            backup = Path(interrupted['backup']).resolve()
            if not backup.is_relative_to((workspace / 'scene-colors').resolve()):
                raise ValueError('Interrupted collector backup escaped its workspace')
            if interrupted['colorsExisted']:
                shutil.copyfile(backup, colors)
            else:
                colors.unlink(missing_ok=True)
        else:
            # Complete a crash-interrupted receipt -> ledger transition before
            # deleting the pending marker that authorizes that one new step.
            verify_colors_state(workspace)
        pending.unlink()


def verify_dataset_inputs(workspace: Path, dataset: str) -> None:
    candidate = workspace/'candidate'
    if dataset == 'washington':
        from modules.import_tools import pre_process_input_files
        with in_directory(candidate):
            pre_process_input_files(dataDir='data/washington',pcDir='data/washington/pc',
                                    jsonDir='json/washington',nFrame_samples=30,rebuild=False)
        return
    recovery = json.loads((workspace/'sun3d-files.json').read_text(encoding='utf-8'))
    for scene in recovery['scenes']:
        annotation = candidate/'json'/'princeton'/scene['annotation']
        tracked = REPOSITORY/'json'/'princeton'/scene['annotation']
        if not tracked.is_file() or digest(annotation) != digest(tracked):
            raise ValueError('Candidate SUN3D annotation differs from the reviewed tracked annotation; explicitly resync and reseal source provenance before collection')
        if digest(annotation) != scene['annotationSha256']:
            raise ValueError('SUN3D annotation changed since recovery')
    for record in recovery['files'] + [item for scene in recovery['scenes'] for item in scene['metadata']]:
        relative = PurePosixPath(unquote(record['source']['url'][len(SUN3D_BASE):]))
        path = candidate/'data'/'princeton'/relative
        require_task_path(path, workspace)
        if path.stat().st_size != record['source']['bytes'] or digest(path) != record['sha256']:
            raise ValueError(f'SUN3D input bytes changed since recovery: {path}')


def collect_dataset(workspace: Path, dataset: str) -> list[dict]:
    """Collect one verified dataset serially; never seal a partial full corpus."""
    from modules.relation_manifest import relation_row_count
    if dataset not in {'princeton','washington'}:
        raise ValueError('Expected one reviewed source dataset')
    candidate = workspace / 'candidate'
    seals = json.loads((workspace/'source-seals-verified.json').read_text(encoding='utf-8'))
    for name, key in [('washington-files.json','washingtonInventorySha256'),('sun3d-files.json','sun3dInventorySha256')]:
        if digest(workspace/name) != seals[key]:
            raise ValueError('Source recovery inventory changed after full byte verification')
    annotations = sorted((candidate/'json'/dataset).glob('*.json'))
    expected = sorted(path.name for path in (REPOSITORY/'json'/dataset).glob('*.json'))
    if [path.name for path in annotations] != expected:
        raise ValueError(f'Candidate {dataset} scene set is not complete')
    if dataset == 'washington':
        preprocess_receipt = json.loads((workspace/'washington-preprocess.json').read_text(encoding='utf-8'))
        if preprocess_receipt['coreFingerprint'] != core_fingerprint():
            raise ValueError('Washington preprocessing was produced by different core modules')
    restore_interrupted_colors(workspace)
    verify_colors_state(workspace)
    verify_dataset_inputs(workspace,dataset)
    records = []
    for annotation in annotations:
        receipt = workspace / 'scene-receipts' / dataset / (annotation.stem + '.json')
        log = workspace / 'logs' / dataset / (annotation.stem + '.log')
        log.parent.mkdir(parents=True, exist_ok=True)
        # Resume only byte-sealed scene outputs. The complete manifest's
        # strict verifier checks every relation/annotation before sealing.
        if not receipt.exists():
            with log.open('wb') as output:
                command = [sys.executable, '-m', 'serverless.compiler.rebuild_sources',
                           'collect-scene', '--workspace', str(workspace),
                           '--dataset', dataset, '--annotation', annotation.name]
                if os.environ.get('SOILIE_SOURCE_COLLECTOR_OWNER'):
                    command.append('--controller-owned-worker')
                subprocess.run(command, cwd=REPOSITORY, stdout=output, stderr=subprocess.STDOUT, check=True)
        document = json.loads(receipt.read_text(encoding='utf-8'))
        if document['annotationSha256'] != digest(annotation):
            raise ValueError(f'Collector scene receipt annotation changed: {annotation}')
        if document['coreFingerprint'] != core_fingerprint():
            raise ValueError('Production modules changed; start a fresh candidate rather than mixing collectors')
        current_runtime = runtime_info()
        if document['runtime']['python'] != current_runtime['python']:
            raise ValueError('Collector Python version changed')
        if any(current_runtime['packages'][name] != value for name,value in document['runtime']['packages'].items()):
            raise ValueError('Collector dependency version changed')
        for record in document['frames']:
            relation = candidate/'data'/PurePosixPath(record['relation']['path'])
            require_task_path(relation,workspace)
            if digest(relation) != record['relation']['sha256'] or relation_row_count(relation) != record['relation']['rowCount']:
                raise ValueError(f'Completed scene relation seal changed: {relation}')
        records.extend(document['frames'])
        print(f'collected {dataset}/{annotation.name}: {len(document["frames"])} retained frames', flush=True)
    write_json(workspace/'dataset-receipts'/(dataset+'.json'),{'dataset':dataset,'frames':records,
               'coreFingerprint':core_fingerprint(),'runtime':runtime_info()})
    verify_colors_state(workspace)
    return records


def collect(workspace: Path) -> dict:
    """Seal both complete scene sets and gather with the production functions."""
    from modules.relation_manifest import begin_relation_run, complete_relation_run

    candidate = workspace / 'candidate'
    # Do not let a still-running preprocessor silently shrink Washington's set.
    if not (workspace/'washington-preprocess.json').exists():
        raise ValueError('The complete verified Washington preprocessing run must finish before sealing collection')
    manifest = candidate / 'data' / 'relation-inputs.json'
    collection_id = begin_relation_run(manifest)
    records = []
    for dataset in ['princeton','washington']:
        records.extend(collect_dataset(workspace,dataset))
    # Recheck the actual used source bytes after the potentially long collector
    # run, not just the inventory JSON seals. Washington's production receipt
    # verifier covers original PLY/labels/poses and every selected RGB-D frame.
    for dataset in ['princeton','washington']:
        verify_dataset_inputs(workspace,dataset)
    verify_colors_state(workspace)
    with in_directory(candidate):
        complete_relation_run(manifest, collection_id=collection_id, frames=records, data_root=candidate / 'data')
    from modules.prepare_data import gatherTriplets
    with in_directory(candidate):
        receipt = gatherTriplets(manifest_path=manifest, output_path=candidate/'data'/'triplets.csv',
                                 receipt_path=candidate/'data'/'triplets.sources.json', data_root=candidate/'data')
    write_json(workspace / 'collection-summary.json', {'kind': 'fresh-production-source-collection',
               'collectionId': collection_id, 'frameCount': len(records), 'triplets': receipt['output']})
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['fetch-washington', 'extract-washington', 'fetch-sun3d', 'verify-sources',
                                          'preprocess', 'preprocess-scene', 'collect', 'collect-dataset', 'collect-scene'])
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--dataset', choices=['princeton', 'washington'])
    parser.add_argument('--annotation')
    parser.add_argument('--scene')
    parser.add_argument('--jobs',type=int,default=4)
    parser.add_argument('--controller-owned-worker',action='store_true',help=argparse.SUPPRESS)
    args = parser.parse_args()
    workspace = _workspace(args.workspace)
    if (workspace/'PAUSE_SOURCE').exists() and args.command in {
            'preprocess','preprocess-scene','collect','collect-dataset','collect-scene'}:
        print('Source work is paused at this workspace; no new scene was dispatched.',file=sys.stderr)
        raise SystemExit(75)
    if args.command == 'preprocess-scene':
        if not args.scene:
            parser.error('preprocess-scene requires --scene')
        preprocess_scene(workspace,args.scene)
    elif args.command == 'collect-scene':
        if not args.dataset or not args.annotation or Path(args.annotation).name != args.annotation:
            parser.error('collect-scene requires a dataset and a leaf annotation filename')
        if args.controller_owned_worker:
            if not os.environ.get('SOILIE_SOURCE_COLLECTOR_OWNER'):
                parser.error('Owned worker must be launched by the source collection controller')
            collect_scene(workspace, args.dataset, args.annotation)
        else:
            from serverless.benchmark.run_batch import run_lock
            with run_lock(workspace/'collector-controller'):
                collect_scene(workspace, args.dataset, args.annotation)
    elif args.command == 'preprocess':
        preprocess(workspace,args.jobs)
    elif args.command == 'collect-dataset':
        if not args.dataset:
            parser.error('collect-dataset requires --dataset')
        from serverless.benchmark.run_batch import run_lock
        with run_lock(workspace/'collector-controller'):
            os.environ['SOILIE_SOURCE_COLLECTOR_OWNER'] = str(os.getpid())
            collect_dataset(workspace,args.dataset)
    elif args.command == 'collect':
        from serverless.benchmark.run_batch import run_lock
        with run_lock(workspace/'collector-controller'):
            os.environ['SOILIE_SOURCE_COLLECTOR_OWNER'] = str(os.getpid())
            collect(workspace)
    else:
        {'fetch-washington': fetch_washington, 'extract-washington': extract_washington, 'verify-sources': verify_sources,
         'fetch-sun3d': fetch_sun3d}[args.command](workspace)


if __name__ == '__main__':
    main()
