"""Frozen, one-attempt native Infinigen extensions with verified cloud recovery.

Preparation and status are local/read-only. ``run`` is an explicit start, never
a retry-until-success sampler: every planned seed is attempted at most once.
Only allowlisted geometry observations/diagrams are public; native scenes and
exact process evidence remain private because blend files can embed paths.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, UTC
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import sysconfig
import tarfile
import time

from serverless.benchmark.infinigen_task import COMMIT, INVENTORY_VERSION, controlled_role_counts
from serverless.benchmark.run_batch import run_lock, write_json
from serverless.benchmark.supervise import command as supervised

ROOT = Path(__file__).resolve().parents[2]
SCRATCH = ROOT/'.codex/benchmark/replacement-baselines-20261023'
DEFAULT_ROOT = SCRATCH/'infinigen'
BUCKET = 'soilie3d-data'
PUBLIC_PREFIX = 'files/outputs/2026-10-23/infinigen/'
PRIVATE_PREFIX = 'private/new-benchmark-20261023/infinigen/'
CELLS = tuple((room, count) for room in ('bedroom', 'living_room') for count in (7, 8))
IMPLEMENTATIONS = (
    'baseline_extension_run.py', 'run_infinigen.py', 'infinigen_controlled_entry.py',
    'infinigen_task.py', 'import_infinigen.py', 'export_infinigen.py',
    'infinigen_metadata.py', 'geometry.py', 'mesh_support.py', 'solid_overlap.py',
    'stimuli.py', 'review_annotations.py', 'archive.py', 'stream_archive.py',
    'supervise.py', 'timing.py', 'run_batch.py', 'expand_infinigen.py',
    'native_isolation.py', 'infinigen_isolation.Dockerfile',
)


def stamp():
    return datetime.now(UTC).isoformat()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(path):
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            checksum.update(block)
    return checksum.hexdigest()


def owner(path):
    path = Path(path).resolve()
    if not path.is_relative_to(SCRATCH.resolve()) or path == SCRATCH.resolve():
        raise ValueError('Extension state must be a task-owned child of replacement-baselines-20261023')
    return path


def hashes():
    return {name: digest(Path(__file__).with_name(name)) for name in IMPLEMENTATIONS}


def byte_tree(path, *, include_bytecode=False, python_sources=None, native_prefix=None):
    """Fingerprint actual runtime bytes, not package claims or file mtimes.

    Generated Python bytecode is excluded; source, extension binaries, data,
    distribution records and executable search-path files are all included.
    Native imports use the pinned read-only derived cache (fresh /tmp only
    during untimed preparation), never these excluded pre-existing caches.
    No size/mtime cache is accepted as evidence that runtime bytes are equal.
    """
    path = path.resolve()
    if not path.is_dir():
        raise ValueError('Runtime fingerprint needs an existing directory')
    result = hashlib.sha256()
    count = size = 0
    for directory, children, files in os.walk(path, followlinks=False):
        children[:] = sorted(name for name in children
                             if include_bytecode or name not in ('__pycache__', '.git'))
        if any((Path(directory)/name).is_symlink() for name in children):
            raise ValueError('Runtime directory links require an explicit fingerprint policy')
        for name in sorted(files):
            if not include_bytecode and name.endswith(('.pyc', '.pyo')):
                continue
            source = Path(directory)/name
            link = None
            if source.is_symlink():
                if not source.resolve().is_relative_to(path):
                    raise ValueError('Runtime file link escaped its pinned byte tree')
                link = os.readlink(source)
            before = source.stat()
            checksum = digest(source)
            after = source.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError('Runtime file changed during its byte fingerprint')
            result.update(encoded({'path': source.relative_to(path).as_posix(),
                                  'sha256': checksum, 'bytes': after.st_size, 'symlink': link}))
            if python_sources is not None and source.suffix == '.py':
                python_sources[str(Path(native_prefix)/source.relative_to(path))] = checksum
            count += 1
            size += after.st_size
    return {'sha256': result.hexdigest(), 'files': count, 'bytes': size}


def runtime_fingerprint(environment, isolation):
    """Pin the actual WSL/native execution context before any model attempts."""
    if sys.platform != 'linux':
        raise ValueError('Prepare/freeze the extension with its actual Linux/WSL generation interpreter; status remains portable')
    import site
    from email.parser import BytesParser
    paths = {name: (ROOT/value).resolve() for name, value in environment.items()}
    if any(not path.is_relative_to(ROOT) for path in paths.values()):
        raise ValueError('Native runtime paths escaped the project')
    packages = paths['site_packages']
    distributions = []
    for metadata in sorted(packages.glob('*.dist-info/METADATA')):
        record = BytesParser().parsebytes(metadata.read_bytes(), headersonly=True)
        distributions.append({'name': record['Name'], 'version': record['Version'],
                              'metadataSha256': digest(metadata),
                              'recordSha256': digest(metadata.with_name('RECORD'))
                              if metadata.with_name('RECORD').exists() else None})
    stdlib = Path(sysconfig.get_path('stdlib')).resolve()
    sites = sorted({Path(value).resolve() for value in [*site.getsitepackages(), site.getusersitepackages()]
                    if Path(value).is_dir() and str(Path(value).resolve()) in sys.path})
    known = [ROOT, stdlib, *sites]
    extra = [Path(value).resolve() for value in sys.path if value and Path(value).exists()
             and not any(Path(value).resolve().is_relative_to(parent) for parent in known)]
    if extra:
        raise ValueError('Unexpected external Python import paths; use the dedicated campaign environment')
    from serverless.benchmark import native_isolation
    from tempfile import TemporaryDirectory
    cache = isolation['bytecodeCache']
    cache_bytes = byte_tree(ROOT/cache['path'], include_bytecode=True) if cache else None
    if cache and cache_bytes != {key: cache[key] for key in ('sha256', 'files', 'bytes')}:
        raise ValueError('Derived native bytecode bytes changed')
    marker = '__SOILIE_NATIVE_RUNTIME__'
    expression = ('import bpy,json,sys;print('+repr(marker)+'+json.dumps('
                  '{"blenderVersion":bpy.app.version_string,"pythonVersion":sys.version}))')
    # Even the version probe stays inside the frozen security boundary. Native
    # dependencies are never imported into the modern host interpreter.
    SCRATCH.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='runtime-isolation-', dir=SCRATCH) as temporary:
        workspace = Path(temporary)/'native'
        result = native_isolation.execute_native(
            [str(paths['blender']), '--background', '--factory-startup', '--threads', '1',
             '--python-use-system-env', '--python-expr', expression],
            environment, workspace, isolation, Path(temporary)/'isolation.json', timeout=120, capture=True)
        if result.returncode:
            raise ValueError('Isolated native Blender version probe failed: '+str(result.stdout))
        output = result.stdout.decode()
    native = next((json.loads(line[len(marker):]) for line in output.splitlines() if line.startswith(marker)), None)
    if native is None:
        raise ValueError('Native Blender did not report its actual Python version')
    native_sources = {}
    result = {'schemaVersion': 1, 'pythonVersion': sys.version,
            'pythonExecutableSha256': digest(Path(sys.executable)),
            'pythonStdlib': byte_tree(stdlib),
            'pythonSitePackages': [byte_tree(path) for path in sites],
            'nativeBlenderSha256': digest(paths['blender']), 'nativeVersions': native,
            'nativeBlenderDistribution': byte_tree(paths['blender'].parent, python_sources=native_sources, native_prefix='/native/blender'),
            'nativeSourceDistribution': byte_tree(paths['repository'], python_sources=native_sources, native_prefix='/native/repository'),
            'nativeIsolation': native_isolation.platform_fingerprint(isolation),
            'nativeBytecodeCache': cache_bytes,
            'nativeSitePackages': byte_tree(packages, python_sources=native_sources, native_prefix='/native/packages'), 'nativeDistributions': distributions,
            'nativeThreadEnvironment': native_isolation.THREADS,
            'threadEnvironment': {name: os.environ.get(name) for name in (
                'OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS',
                'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS')}}
    if cache:
        producer = json.loads((ROOT/cache['producerManifest']).read_bytes())
        source_index = ROOT/cache['path']/'compiled-sources.json'
        if (producer['modelCommit'] != COMMIT or producer['environment'] != environment
                or producer['nativeBlenderSha256'] != result['nativeBlenderSha256']
                or producer['pythonVersion'] != native['pythonVersion']
                or digest(source_index) != producer['compiledSourceIndexSha256']):
            raise ValueError('Derived cache producer does not match the actual frozen native runtime')
        compiled_index = json.loads(source_index.read_bytes())
        compiled = compiled_index['sources'] + compiled_index['skippedSources']
        if any(native_sources.get(row['source']) != row['sourceSha256'] for row in compiled):
            raise ValueError('Native Python sources changed after the shared bytecode cache was compiled')
    return result


def load_plan(root, verify_code=False):
    plan = json.loads((owner(root)/'plan.json').read_bytes())
    body = {key: value for key, value in plan.items() if key != 'planSha256'}
    if hashlib.sha256(encoded(body)).hexdigest() != plan.get('planSha256'):
        raise ValueError('Frozen extension plan changed')
    if (plan.get('schemaVersion') != 1 or plan.get('modelCommit') != COMMIT
            or plan.get('inventoryVersion') != INVENTORY_VERSION
            or plan.get('publicPrefix') != PUBLIC_PREFIX or plan.get('privatePrefix') != PRIVATE_PREFIX
            or plan.get('bucket') != BUCKET):
        raise ValueError('Unknown extension contract or archive destination')
    from serverless.benchmark.native_isolation import validate_configuration
    validate_configuration(plan.get('nativeIsolation'))
    if not plan['nativeIsolation']['bytecodeCache']:
        raise ValueError('A timed native extension requires its frozen derived bytecode cache')
    if (len({task['id'] for task in plan['tasks']}) != len(plan['tasks'])
            or len({task['seed'] for task in plan['tasks']}) != len(plan['tasks'])):
        raise ValueError('Extension tasks must have disjoint identities and seeds')
    if Counter((task['roomType'], task['objectCount']) for task in plan['tasks']) != Counter(
            {cell: plan['attemptsPerCell'] for cell in CELLS}):
        raise ValueError('Frozen extension target counts do not match its tasks')
    for task in plan['tasks']:
        if (not re.fullmatch('[a-z0-9_-]+', task['id'])
                or (task['roomType'], task['objectCount']) not in CELLS
                or task['roleCounts'] != controlled_role_counts(task['roomType'], task['objectCount'], INVENTORY_VERSION)):
            raise ValueError('Unknown or unsafe extension task inventory')
    if verify_code and plan['implementationSha256'] != hashes():
        raise ValueError('Implementation changed after the extension was frozen')
    if verify_code and plan.get('runtimeFingerprint') != runtime_fingerprint(plan['environment'], plan['nativeIsolation']):
        raise ValueError('Actual Blender/Python/dependency bytes changed after the extension was frozen')
    return plan


def prepare(args):
    root = owner(args.root)
    if args.attempts_per_cell < 1 or args.seed_base < 1_000_000 or args.timeout < 1:
        raise ValueError('Use positive attempt/timeout values and an explicit fresh seed base >= 1000000')
    if not re.fullmatch('[a-z0-9_-]+', args.campaign_id):
        raise ValueError('Unsafe campaign identifier')
    tasks = []
    for cell, (room, count) in enumerate(CELLS):
        for index in range(args.attempts_per_cell):
            tasks.append({'id': f'{args.campaign_id}-{room}-{count}-{index:04d}',
                          'roomType': room, 'objectCount': count,
                          'seed': args.seed_base + cell*args.attempts_per_cell + index,
                          'roleCounts': controlled_role_counts(room, count, INVENTORY_VERSION)})
    environment = {}
    for name in ('repository', 'site_packages', 'blender'):
        path = getattr(args, name).resolve()
        if not path.is_relative_to(ROOT):
            raise ValueError('Use the project-owned pinned source/environment/tools so the plan is Windows/WSL portable')
        environment[name] = path.relative_to(ROOT).as_posix()
    from serverless.benchmark.native_isolation import configuration
    isolation = configuration(args.isolation_image, args.bytecode_cache_receipt)
    if not isolation['bytecodeCache']:
        raise ValueError('Prepare the shared untimed native bytecode cache before freezing a campaign')
    plan = {'schemaVersion': 1, 'campaignId': args.campaign_id, 'createdAt': stamp(),
            'model': 'infinigen', 'modelCommit': COMMIT, 'inventoryVersion': INVENTORY_VERSION,
            'condition': 'model-native-count-conditioned', 'attemptsPerCell': args.attempts_per_cell,
            'seedBase': args.seed_base, 'tasks': tasks, 'timeoutSeconds': args.timeout,
            'blenderThreads': args.blender_threads, 'bucket': BUCKET,
            'publicPrefix': PUBLIC_PREFIX, 'privatePrefix': PRIVATE_PREFIX,
            'environment': environment,
            'nativeIsolation': isolation,
            'runtimeFingerprint': runtime_fingerprint(environment, isolation),
            'implementationSha256': hashes(),
            'generationStage': 'Native original coarse task: solving, procedural meshes and scene serialization; no image rendering',
            'sampling': 'One attempt per frozen task; failures are retained privately, never replaced by new seeds',
            'nativeSceneAccess': 'Private exact native scene; public geometry and labelled arrangement diagrams only'}
    plan['planSha256'] = hashlib.sha256(encoded(plan)).hexdigest()
    with run_lock(root):
        if (root/'plan.json').exists():
            raise ValueError('Plan already exists; use dry-run/status or a new task-owned directory')
        write_json(root/'plan.json', plan)
    return {'root': str(root), 'planSha256': plan['planSha256'], 'tasks': len(tasks), 'awsWrites': 0}


def commands(root, plan, task):
    shard = owner(root)/'tasks'/task['id']
    env = {name: str((ROOT/value).resolve()) for name, value in plan['environment'].items()}
    if any(not Path(path).is_relative_to(ROOT) for path in env.values()):
        raise ValueError('Frozen native environment escaped the project')
    common = ['--repository', env['repository'], '--site-packages', env['site_packages'], '--blender', env['blender']]
    common += ['--native-isolation-plan', str(owner(root)/'plan.json')]
    # The native runner adds 100 to living-room offsets. Freeze the actual
    # integer seed, not that implementation's command-line offset convention.
    offset = task['seed'] - (100 if task['roomType'] == 'living_room' else 0)
    generation = [sys.executable, '-m', 'serverless.benchmark.run_infinigen', *common,
                  '--output', str(shard), '--per-room', '1', '--max-attempts', '1',
                  '--room-types', task['roomType'], '--profile', 'controlled-inventory-fast',
                  '--object-count', str(task['objectCount']), '--blender-threads', str(plan['blenderThreads']),
                  '--timeout', str(plan['timeoutSeconds']),
                  '--'+task['roomType'].replace('_', '-')+'-seed-offset', str(offset)]
    export = [sys.executable, '-m', 'serverless.benchmark.import_infinigen', *common,
              '--run', str(shard), '--output', str(shard/'export.json'), '--timing-ineligible']
    return generation, export


def execute(command, log, timeout):
    with log.open('ab') as stream:
        result = subprocess.run(supervised(command), cwd=ROOT, stdout=stream,
                                stderr=subprocess.STDOUT, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f'Stage exit {result.returncode}; inspect its private controller log')


def public_scene(raw, task):
    """Rebuild every public leaf from numeric types, closed enums or hashes.

    Native object names, source tags and free-form diagnostics stay private.
    Public instance aliases preserve each instance's geometry and relationships
    without treating an arbitrary source string as a safe publication field.
    """
    from serverless.benchmark.geometry import measure

    def number(value):
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError('Invalid public numeric geometry leaf')
        return value

    def vector(value, width):
        if not isinstance(value, list) or len(value) != width:
            raise ValueError('Invalid public numeric vector')
        return [number(item) for item in value]

    def polygon(value):
        if not isinstance(value, list) or len(value) < 3:
            raise ValueError('Invalid public polygon')
        return [vector(point, 2) for point in value]

    def region(value):
        return {'polygon': polygon(value['polygon']),
                'holes': [polygon(hole) for hole in checked_list(value.get('holes', []))]}

    def checked_list(value):
        if not isinstance(value, list):
            raise ValueError('Expected a public schema list')
        return value

    def boolean(value):
        if type(value) is not bool:
            raise ValueError('Expected a public schema Boolean')
        return value

    def sha(value):
        if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value):
            raise ValueError('Invalid public implementation/content hash')
        return value

    expected_id = f"controlled-infinigen-inventory-v2-{task['roomType']}-{task['seed']:x}"
    provenance = raw['provenance']
    if (raw.get('schemaVersion') != 2 or raw.get('id') != expected_id
            or raw.get('model') != 'infinigen_controlled' or raw.get('roomType') != task['roomType']
            or raw.get('units') != 'm' or raw.get('stage') != 'original-final-coarse'
            or provenance.get('inventoryVersion') != INVENTORY_VERSION
            or provenance.get('controlledRoleCounts') != task['roleCounts']
            or provenance.get('controlledObjectCount') != task['objectCount']
            or provenance.get('commit') != COMMIT):
        raise ValueError('Export does not match its immutable extension task')
    source_objects = checked_list(raw['objects'])
    if (len(source_objects) != task['objectCount']
            or any(not isinstance(item.get('id'), str) or not item['id'] for item in source_objects)
            or len({item['id'] for item in source_objects}) != task['objectCount']):
        raise ValueError('Missing or duplicated native semantic instance')
    aliases = {item['id']: f'object-{index:03d}' for index, item in enumerate(source_objects)}
    labels = {'bed': 'bed', 'simple_desk': 'desk', 'floor_lamp': 'floor_lamp', 'rug': 'rug',
              'sofa': 'sofa', 't_v_stand': 'tv_stand', 'coffee_table': 'coffee_table',
              'side_table': 'side_table', 'sidetable_desk': 'side_table',
              'simple_bookcase': 'storage', 'cell_shelf': 'storage', 'large_shelf': 'storage',
              'kitchen_cabinet': 'storage', 'single_cabinet': 'storage'}
    if Counter(labels.get(item.get('label')) for item in source_objects) != Counter(
            {role: count for role, count in task['roleCounts'].items() if count}):
        raise ValueError('Native asset labels differ from the frozen role multiplicities')
    scene = {'schemaVersion': 2, 'id': expected_id, 'model': 'infinigen_controlled',
             'roomType': task['roomType'], 'units': 'm', 'stage': 'original-final-coarse',
             'room': {'floorZ': number(raw['room']['floorZ']),
                      'boundarySource': 'XY projection of native tagged floor support triangles',
                      'boundaryExtractionVersion': 2}, 'objects': []}
    if 'regions' in raw['room']:
        scene['room']['regions'] = [region(item) for item in checked_list(raw['room']['regions'])]
    else:
        scene['room'].update(region(raw['room']))
    for item in source_objects:
        if item.get('kind') != 'furniture':
            raise ValueError('Non-furniture object in a controlled native inventory')
        corners = checked_list(item['corners'])
        if len(corners) != 8:
            raise ValueError('Expected eight native root-envelope corners')
        obj = {'id': aliases[item['id']], 'assemblyId': aliases[item['id']], 'label': item['label'],
               'kind': 'furniture', 'corners': [vector(point, 3) for point in corners]}
        if 'transform' in item:
            transform = checked_list(item['transform'])
            if len(transform) != 4:
                raise ValueError('Expected a numeric 4x4 native transform')
            obj['transform'] = [vector(row, 4) for row in transform]
        if 'supportEligible' in item:
            obj['supportEligible'] = boolean(item['supportEligible'])
        if item.get('frontConvention') != 'Infinigen canonical Subpart.Front local +X axis':
            raise ValueError('Unknown native front convention')
        obj.update(frontDirection=vector(item['frontDirection'], 2),
                   frontConvention='Infinigen canonical Subpart.Front local +X axis')
        # Source tags, mesh-part names and support/overlap diagnostics remain in
        # the private exact export. This initial public schema is geometry, not
        # a release of quantitative comparison/contact evidence.
        scene['objects'].append(obj)
    version = provenance.get('blenderVersion')
    if not isinstance(version, str) or not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('Invalid native Blender version')
    public_provenance = {'blenderVersion': version, 'commit': COMMIT, 'inventoryVersion': INVENTORY_VERSION,
        'controlledObjectCount': task['objectCount'], 'controlledRoleCounts': dict(task['roleCounts']),
        'geometryPolicy': 'Unchanged native geometry; public object IDs are neutral one-to-one aliases',
        'inventoryScope': 'Model-native count-conditioned Infinigen inventory; not cross-model semantic equivalence'}
    for key in ('stateSha256', 'exporterSha256', 'metadataImporterSha256', 'supportSamplerSha256', 'blendSha256'):
        public_provenance[key] = sha(provenance[key])
    implementation = provenance['controlImplementation']
    allowed = ('infinigen_controlled_entry.py', 'infinigen_task.py')
    if not isinstance(implementation, dict) or set(implementation) != set(allowed):
        raise ValueError('Unknown controlled implementation provenance')
    public_provenance['controlImplementation'] = {name: sha(implementation[name]) for name in allowed}
    scene['provenance'] = public_provenance
    measure(scene)  # Structural/physical validation, not a published comparison.
    return scene


def verify_private_archive(archive, evidence, index_path):
    expected = {row['path']: row for row in evidence['files']}
    found_index = False
    with tarfile.open(archive, 'r:gz') as packed:
        for member in packed:
            if member.name == index_path.name:
                if found_index or packed.extractfile(member).read() != index_path.read_bytes():
                    raise ValueError('Recovery index did not survive compression')
                found_index = True
                continue
            row = expected.pop(member.name)
            check = hashlib.sha256()
            with packed.extractfile(member) as body:
                for block in iter(lambda: body.read(1024*1024), b''):
                    check.update(block)
            if member.size != row['bytes'] or check.hexdigest() != row['sha256']:
                raise ValueError('Exact attempt file did not survive compression')
    if expected or not found_index:
        raise ValueError('Recovery archive is missing attempt evidence')


def private_archive(shard, plan, task, outcome):
    """Keep every exact attempt file before evicting owned heavy payloads."""
    archive = shard/'private-evidence.tar.gz'
    index_path = shard/'private-evidence.json'
    if archive.exists() and index_path.exists():
        evidence = json.loads(index_path.read_bytes())
        verify_private_archive(archive, evidence, index_path)
        return archive, evidence
    rows = []
    excluded = {'controller.json', 'private-evidence.json', 'private-evidence.tar.gz', 'private-evidence.tar.gz.tmp'}
    for path in sorted(shard.rglob('*')):
        if path.is_symlink() or not path.resolve().is_relative_to(shard.resolve()):
            raise ValueError('Private evidence contains a symlink; retain locally for explicit recovery review')
        if not path.is_file() or path.name in excluded:
            continue
        rows.append({'path': path.relative_to(shard).as_posix(), 'sha256': digest(path), 'bytes': path.stat().st_size})
    evidence = {'schemaVersion': 1, 'plan': plan, 'task': task, 'outcome': outcome, 'files': rows}
    write_json(index_path, evidence)
    temporary = archive.with_suffix('.gz.tmp')
    with temporary.open('wb') as stream, gzip.GzipFile(filename='', fileobj=stream, mode='wb', compresslevel=1, mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode='w') as packed:
            for row in [*rows, {'path': index_path.name}]:
                path = shard/row['path']
                info = packed.gettarinfo(str(path), arcname=row['path'])
                info.uid = info.gid = 0
                info.uname = info.gname = ''
                info.mtime = 0
                with path.open('rb') as body:
                    packed.addfile(info, body)
    temporary.replace(archive)
    verify_private_archive(archive, evidence, index_path)
    return archive, evidence


def evict(shard, state):
    """Delete only byte-matched files contained in verified private evidence."""
    shard = shard.resolve()
    if not shard.is_relative_to(owner(state['root'])/'tasks'):
        raise ValueError('Eviction escaped this extension')
    freed = 0
    evidence = json.loads((shard/'private-evidence.json').read_bytes())
    if digest(shard/'private-evidence.json') != state['evidenceIndexSha256']:
        raise ValueError('Recovery index changed before eviction')
    for row in evidence['files']:
        original = shard/row['path']
        path = original.resolve()
        if not path.is_relative_to(shard) or original.is_symlink():
            raise ValueError('Unsafe evidence member')
        keep = (path.suffix == '.json' and row['bytes'] <= 4*1024**2) or (path.suffix == '.log' and row['bytes'] <= 128*1024)
        if keep or not path.exists():
            continue
        if path.stat().st_size != row['bytes'] or digest(path) != row['sha256']:
            raise ValueError('Generated file changed after recovery upload')
        path.unlink()
        freed += row['bytes']
    archive = shard/'private-evidence.tar.gz'
    receipt = state['privateArchive']
    if archive.exists():
        if archive.stat().st_size != receipt['bytes'] or digest(archive) != receipt['sha256']:
            raise ValueError('Private archive changed before eviction')
        archive.unlink()
        freed += receipt['bytes']
    return freed


def run_task(root, plan, task, client):
    from serverless.benchmark.archive import merge_index
    from serverless.benchmark.stimuli import diagram
    from serverless.benchmark.stream_archive import upload, verify_remote
    shard = owner(root)/'tasks'/task['id']
    shard.mkdir(parents=True, exist_ok=True)
    checkpoint = shard/'controller.json'
    state = json.loads(checkpoint.read_bytes()) if checkpoint.exists() else {
        'schemaVersion': 1, 'root': str(owner(root)), 'taskId': task['id'],
        'planSha256': plan['planSha256'], 'status': 'planned', 'publicArtifacts': {}}
    if state['planSha256'] != plan['planSha256'] or state['taskId'] != task['id']:
        raise ValueError('Attempt checkpoint belongs to a different immutable task')
    if state['status'] in ('complete', 'failed'):
        for receipt in [state['privateArchive'], state['privateReceipt'], *state['publicArtifacts'].values()]:
            verify_remote(client, BUCKET, receipt['key'], receipt['sha256'], receipt['bytes'])
        state['evictedBytes'] = state.get('evictedBytes', 0) + evict(shard, state)
        write_json(checkpoint, state)
        return state
    generation, export = commands(root, plan, task)
    attempt_path = shard/f"attempt-{task['roomType']}-000.json"
    if state['status'] == 'planned':
        state.update(status='generating', startedAt=stamp())
        write_json(checkpoint, state)  # An interrupted seed must never silently restart.
        try:
            execute(generation, shard/'generation-controller.log', plan['timeoutSeconds']+180)
        except (RuntimeError, subprocess.TimeoutExpired) as error:
            state['stageError'] = str(error)
    attempt = json.loads(attempt_path.read_bytes()) if attempt_path.exists() else None
    if attempt is None:
        state.setdefault('stageError', 'INTERRUPTED_NATIVE_ATTEMPT: no terminal checkpoint; this seed will not be restarted')
    successful = bool(attempt and attempt.get('status') == 'complete')
    if attempt and (attempt.get('seed') != task['seed'] or attempt.get('roomType') != task['roomType']):
        raise ValueError('Native checkpoint does not match the frozen seed/type')
    if state.get('outcome'):
        successful = state['outcome']['status'] == 'complete'
    if successful and not state.get('outcome') and not (shard/'export.json').exists():
        state['status'] = 'exporting'
        write_json(checkpoint, state)
        try:
            execute(export, shard/'export-controller.log', 1200)
        except (RuntimeError, subprocess.TimeoutExpired) as error:
            state['stageError'] = str(error)
            successful = False
    scene = None
    if successful:
        try:
            document = json.loads((shard/'export.json').read_bytes())
            if document.get('invalidArtifacts') or len(document.get('scenes', [])) != 1:
                raise ValueError('Expected exactly one validated native export')
            scene = public_scene(document['scenes'][0], task)
        except (OSError, KeyError, ValueError, TypeError) as error:
            state['stageError'] = str(error)
            successful = False
    outcome = state.get('outcome') or {
        'status': 'complete' if successful else 'failed', 'nativeAttempt': attempt,
        'stageError': state.get('stageError'), 'startedAt': state['startedAt']}
    state['outcome'] = outcome
    state['status'] = 'archiving'
    write_json(checkpoint, state)
    if 'privateArchive' not in state:
        archive, _evidence = private_archive(shard, plan, task, outcome)
        checksum = digest(archive)
        prepared = {'sha256': checksum, 'bytes': archive.stat().st_size,
                    'evidenceIndexSha256': digest(shard/'private-evidence.json')}
        if state.get('privateArchivePrepared') not in (None, prepared):
            raise ValueError('Prepared private archive changed while an upload was pending')
        state['privateArchivePrepared'] = prepared
        state['evidenceIndexSha256'] = prepared['evidenceIndexSha256']
        write_json(checkpoint, state)
        key = PRIVATE_PREFIX+plan['planSha256'][:24]+'/'+task['id']+'/'+checksum+'.tar.gz'
        with archive.open('rb') as body:
            state['privateArchive'] = upload(client, BUCKET, key, body, 'application/gzip', checksum,
                                             archive.stat().st_size, cache_control='private,max-age=0,no-store')
        write_json(checkpoint, state)
    else:
        receipt = state['privateArchive']
        verify_remote(client, BUCKET, receipt['key'], receipt['sha256'], receipt['bytes'])
    evidence = json.loads((shard/'private-evidence.json').read_bytes())
    if digest(shard/'private-evidence.json') != state['evidenceIndexSha256']:
        raise ValueError('Recovery index changed after upload')
    for row in evidence['files']:
        path = shard/row['path']
        if not path.is_file() or digest(path) != row['sha256']:
            raise ValueError('Attempt evidence changed after its recovery snapshot')
    if successful:
        state['status'] = 'publishing'
        write_json(checkpoint, state)
        started = datetime.fromisoformat(attempt['startedAt'])
        elapsed = attempt['generationSeconds']
        if (started.utcoffset() is None or started.utcoffset().total_seconds() != 0
                or type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0):
            raise ValueError('Invalid native generation timestamp or duration')
        generation_record = {'startedAt': started.isoformat(), 'generationSeconds': elapsed, 'seed': task['seed'],
            'stage': 'Native original coarse task: solving, procedural meshes and scene serialization; no image rendering',
            'requestedObjectCount': task['objectCount'], 'inventoryVersion': INVENTORY_VERSION,
            'controlledRoleCounts': controlled_role_counts(task['roomType'], task['objectCount'], INVENTORY_VERSION),
            'timingEligible': False}
        packet = {'schemaVersion': 1, 'status': 'comparisons-pending', 'condition': plan['condition'],
                  'scene': scene, 'generation': generation_record}
        folder = PUBLIC_PREFIX+task['id']+'/'
        payloads = [('geometry', encoded(packet), 'json', 'application/json'),
                    ('diagram', diagram(scene).encode(), 'svg', 'image/svg+xml')]
        for label, body, suffix, content_type in payloads:
            checksum = hashlib.sha256(body).hexdigest()
            state['publicArtifacts'][label] = upload(client, BUCKET, folder+checksum+'.'+suffix,
                                                       body, content_type, checksum, len(body))
            write_json(checkpoint, state)
        manifest = {'schemaVersion': 1, 'id': task['id'], 'model': 'infinigen',
                    'roomType': task['roomType'], 'requestedObjectCount': task['objectCount'],
                    'status': 'comparisons-pending', 'condition': plan['condition'],
                    'representation': 'Native geometry observations and labelled arrangement diagram; not a rendered image',
                    'nativeSceneAccess': 'Exact native scene retained privately; public binary awaits a separate privacy audit',
                    'artifacts': {key: state['publicArtifacts'][key] for key in ('geometry', 'diagram')}}
        body = encoded(manifest)
        checksum = hashlib.sha256(body).hexdigest()
        state['publicArtifacts']['manifest'] = upload(client, BUCKET, folder+checksum+'.manifest.json',
                                                       body, 'application/json', checksum, len(body))
        merge_index(client, BUCKET, [row['key'] for row in state['publicArtifacts'].values()])
    final_status = 'complete' if successful else 'failed'
    state['status'] = 'recording-receipt'
    state.setdefault('finishedAt', stamp())
    write_json(checkpoint, state)
    body = encoded(dict(state, status=final_status))
    checksum = hashlib.sha256(body).hexdigest()
    key = PRIVATE_PREFIX+plan['planSha256'][:24]+'/'+task['id']+'/'+checksum+'.receipt.json'
    state['privateReceipt'] = upload(client, BUCKET, key, body, 'application/json', checksum, len(body),
                                     cache_control='private,max-age=0,no-store')
    state['status'] = final_status
    write_json(checkpoint, state)
    state['evictedBytes'] = state.get('evictedBytes', 0) + evict(shard, state)
    write_json(checkpoint, state)
    return state


def status(root, plan):
    cells = []
    for room, count in CELLS:
        values = []
        for task in plan['tasks']:
            if (task['roomType'], task['objectCount']) != (room, count):
                continue
            path = owner(root)/'tasks'/task['id']/'controller.json'
            values.append(json.loads(path.read_bytes())['status'] if path.exists() else 'planned')
        counts = Counter(values)
        cells.append({'roomType': room, 'objectCount': count, 'target': len(values),
                      'complete': counts['complete'], 'failed': counts['failed'],
                      'active': sum(n for key, n in counts.items() if key not in ('planned', 'complete', 'failed'))})
    return {'planSha256': plan['planSha256'], 'cells': cells,
            'finished': all(row['complete']+row['failed'] == row['target'] for row in cells)}


def render_status(value, color):
    lines = ['Infinigen 7/8 native-count extension | '+value['planSha256'][:12]]
    for row in value['cells']:
        done = row['complete']+row['failed']
        filled = int(30*done/row['target'])
        bar = '█'*filled+'░'*(30-filled)
        if color:
            bar = '\033[38;5;141m'+bar+'\033[0m'
        lines.append(f"{row['roomType']:<12} {row['objectCount']} [{bar}] {done:>3}/{row['target']}  "
                     f"public {row['complete']} | failed {row['failed']} | active {row['active']}")
    lines.append('Finished.' if value['finished'] else 'Ctrl+C closes only this monitor; the generation controller continues.')
    return '\n'.join(lines)


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    setup = sub.add_parser('prepare', help='Write an immutable local plan; no generation/AWS calls')
    for name in ('repository', 'site-packages', 'blender'):
        setup.add_argument('--'+name, type=Path, required=True)
    setup.add_argument('--attempts-per-cell', type=int, required=True)
    setup.add_argument('--seed-base', type=int, required=True)
    setup.add_argument('--campaign-id', default='infinigen-extension-20261023')
    setup.add_argument('--timeout', type=int, default=3600)
    setup.add_argument('--blender-threads', type=int, choices=range(1, 9), default=1)
    setup.add_argument('--isolation-image', required=True,
                       help='Existing local isolation image tag/ID; frozen to its immutable sha256 image identity')
    setup.add_argument('--bytecode-cache-receipt', type=Path, required=True,
                       help='Untimed native-derived cache producer receipt; original native caches are never trusted')
    for name in ('prepare', 'dry-run', 'run', 'status', 'watch'):
        item = setup if name == 'prepare' else sub.add_parser(name)
        item.add_argument('--root', type=Path, default=DEFAULT_ROOT)
        if name == 'run':
            item.add_argument('--profile', default='darkest')
            item.add_argument('--max-tasks', type=int, default=0)
            item.add_argument('--confirm-local-generation-isolated', action='store_true', required=True,
                              help='Explicit scheduling gate: do not overlap SOILIE local timing jobs')
        if name in ('status', 'watch'):
            if name == 'status':
                item.add_argument('--watch', action='store_true')
            item.add_argument('--once', action='store_true')
            item.add_argument('--json', action='store_true')
            item.add_argument('--color', choices=('auto', 'always', 'never'), default='auto')
            item.add_argument('--interval', type=float, default=2)
    args = parser.parse_args()
    if args.command == 'prepare':
        print(json.dumps(prepare(args)))
        return
    plan = load_plan(args.root, verify_code=args.command == 'run')
    if args.command == 'dry-run':
        print(json.dumps({'planSha256': plan['planSha256'], 'tasks': [
            {'task': task, 'commands': commands(args.root, plan, task)} for task in plan['tasks']], 'awsWrites': 0}, indent=2))
    elif args.command == 'run':
        if sys.platform != 'linux' or args.max_tasks < 0:
            raise ValueError('Native generation requires Linux/WSL and a nonnegative max-tasks value')
        import boto3
        client = boto3.Session(profile_name=args.profile).client('s3', region_name='ca-central-1')
        # Shared scheduler directory is an OS lock, not a stale PID file. The
        # SOILIE controller must take this same lock before local timing work.
        with run_lock(owner(args.root)), run_lock(ROOT/'.codex/benchmark/local-timing-scheduler'):
            count = 0
            for task in plan['tasks']:
                checkpoint = owner(args.root)/'tasks'/task['id']/'controller.json'
                terminal = checkpoint.exists() and json.loads(checkpoint.read_bytes())['status'] in ('complete', 'failed')
                if args.max_tasks and count >= args.max_tasks and not terminal:
                    break
                row = run_task(args.root, plan, task, client)
                if not terminal:
                    count += 1
                print(json.dumps({'task': task['id'], 'status': row['status']}), flush=True)
    else:
        if not .2 <= args.interval <= 60:
            raise ValueError('Monitor interval must be 0.2..60 seconds')
        color = args.color == 'always' or (args.color == 'auto' and sys.stdout.isatty() and 'NO_COLOR' not in os.environ)
        previous = 0
        try:
            while True:
                value = status(args.root, plan)
                output = json.dumps(value) if args.json else render_status(value, color)
                if previous and sys.stdout.isatty():
                    print(f'\033[{previous}F\033[J', end='')
                print(output, flush=True)
                previous = output.count('\n')+1
                if args.once or (args.command != 'watch' and not args.watch):
                    break
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print('\nMonitor closed; generation was not changed.', flush=True)


if __name__ == '__main__':
    main()
