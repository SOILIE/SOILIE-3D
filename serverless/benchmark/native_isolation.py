"""Credential-free, network-disabled isolation for the frozen native baseline.

This mitigates exposure of its legacy dependencies; it does not patch Pillow.
Only Blender executes inside the container. The modern host performs geometry
postprocessing and authenticated uploads after that container has stopped.
"""
from __future__ import annotations

import argparse
from datetime import datetime, UTC
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOTS = (
    ROOT/'.codex/benchmark/replacement-baselines-20261023',
    ROOT/'.codex/benchmark/replacement-baselines-20261025',
    ROOT/'.codex/baseline-extension-20261023/infinigen',
)
POLICY = {
    'schemaVersion': 1, 'network': 'none', 'readOnlyRootfs': True,
    'user': '1000:1000', 'capDrop': ['ALL'], 'noNewPrivileges': True,
    'cpus': 4, 'memoryBytes': 8*1024**3, 'pidsLimit': 256,
    'tmpBytes': 512*1024**2, 'homeBytes': 32*1024**2, 'shmBytes': 256*1024**2,
    'fileBytesLimit': 4*1024**3, 'workspaceBytesMonitorLimit': 8*1024**3,
    'freeDiskReserveBytes': 15*1024**3, 'monitorSeconds': 2,
    'heartbeatTimeoutSeconds': 10,
    'pythonBytecodePolicy': 'Read-only derived checked-hash bytecode cache; untimed cache preparation uses fresh /tmp; original unpinned caches are ignored',
    'diskLimitKind': 'Kernel per-file limit plus monitored aggregate bind bytes; not a hard aggregate disk quota',
    'timingStratum': 'Isolated Docker native construction; not equivalent to historical host timing',
    'mitigation': 'Network/credential/filesystem isolation; legacy Pillow remains unpatched',
}
THREADS = {'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1',
           'MKL_NUM_THREADS': '1', 'VECLIB_MAXIMUM_THREADS': '1', 'NUMEXPR_NUM_THREADS': '1'}


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def json_command(command):
    return json.loads(subprocess.check_output(command, text=True))


def configuration(image, cache_receipt=None):
    """Resolve a local immutable image identity, never pull during execution."""
    result = json_command(['docker', 'image', 'inspect', image])[0]
    identity = result['Id']
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', identity):
        raise ValueError('Expected an immutable local Docker image ID')
    cache = None
    if cache_receipt:
        receipt = Path(cache_receipt).resolve()
        if not receipt.is_relative_to(ROOT/'.codex'):
            raise ValueError('Derived cache receipts must be task-owned project evidence')
        record = json.loads(receipt.read_bytes())
        if record['imageId'] != identity or record['kind'] != 'native-derived-bytecode-cache':
            raise ValueError('The bytecode cache was produced with a different native isolation image')
        cache = dict(record['cache'], producerManifest=receipt.relative_to(ROOT).as_posix(),
                     producerManifestSha256=hashlib.sha256(receipt.read_bytes()).hexdigest())
    return dict(POLICY, imageId=identity, bytecodeCache=cache,
                imageRepoDigests=sorted(result.get('RepoDigests') or []))


def validate_configuration(value):
    if not isinstance(value, dict) or {k: value.get(k) for k in POLICY} != POLICY:
        raise ValueError('The native isolation security/resource policy changed')
    if set(value) != set(POLICY) | {'imageId', 'imageRepoDigests', 'bytecodeCache'}:
        raise ValueError('Unknown native isolation field')
    if not isinstance(value['imageId'], str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', value['imageId']):
        raise ValueError('Native isolation requires an immutable image ID')
    if (not isinstance(value['imageRepoDigests'], list)
            or any(not isinstance(item, str) for item in value['imageRepoDigests'])):
        raise ValueError('Invalid image repository digests')
    cache = value['bytecodeCache']
    if cache is not None:
        if (not isinstance(cache, dict) or set(cache) != {'path', 'sha256', 'files', 'bytes', 'producerManifest', 'producerManifestSha256'}
                or any(not isinstance(cache[key], str) or not re.fullmatch('[a-f0-9]{64}', cache[key])
                       for key in ('sha256', 'producerManifestSha256'))
                or any(type(cache[key]) is not int or cache[key] < 1 for key in ('files', 'bytes'))):
            raise ValueError('Invalid derived native bytecode cache identity')
        for key in ('path', 'producerManifest'):
            path = Path(cache[key])
            if path.is_absolute() or not (ROOT/path).resolve().is_relative_to(ROOT/'.codex'):
                raise ValueError('Derived bytecode cache escaped task-owned storage')
    return value


def platform_fingerprint(value):
    validate_configuration(value)
    actual = configuration(value['imageId'], ROOT/value['bytecodeCache']['producerManifest'] if value['bytecodeCache'] else None)
    if actual != value:
        raise ValueError('Frozen native isolation image identity changed')
    info = json_command(['docker', 'info', '--format', '{{json .}}'])
    return {'configuration': actual, 'daemonVersion': info['ServerVersion'],
            'kernelVersion': info['KernelVersion'], 'osType': info['OSType'],
            'architecture': info['Architecture'], 'logicalCpusVisible': info['NCPU'],
            'memoryBytesVisible': info['MemTotal'],
            'dockerfileSha256': hashlib.sha256(Path(__file__).with_name('infinigen_isolation.Dockerfile').read_bytes()).hexdigest()}


def workspace_path(value):
    path = Path(value)
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError('Native workspace contains a symbolic link')
    path = path.resolve()
    if not any(path.is_relative_to(base) and path != base for base in WORKSPACE_ROOTS):
        raise ValueError('Native workspace must be a task-owned extension child')
    path.mkdir(parents=True, exist_ok=True)
    return path


def mapped_command(command, environment, workspace):
    paths = {name: (ROOT/value).resolve() for name, value in environment.items()}
    if set(paths) != {'repository', 'site_packages', 'blender'}:
        raise ValueError('Unknown native environment paths')
    if any(not path.is_relative_to(ROOT/'.codex') or not path.exists() for path in paths.values()):
        raise ValueError('Native runtime must use existing project-owned bytes')
    if Path(command[0]).resolve() != paths['blender']:
        raise ValueError('Only the pinned original Blender executable may enter this worker')
    if '--python-use-system-env' not in command:
        raise ValueError('Native Blender must honor the isolated bytecode/cache environment')
    replacements = [(workspace, Path('/workspace')),
                    (paths['blender'].parent, Path('/native/blender')),
                    (paths['repository'], Path('/native/repository')),
                    (paths['site_packages'], Path('/native/packages')),
                    (ROOT/'serverless/benchmark', Path('/tooling/serverless/benchmark'))]
    result = []
    for value in command:
        item = str(value)
        for original, target in replacements:
            if item == str(original) or item.startswith(str(original)+os.sep):
                item = str(target/Path(item).relative_to(original))
                break
        result.append(item)
    return paths, result


def docker_command(command, environment, workspace, isolation, name):
    """Closed mounts/environment: no home, cloud credentials or Docker socket."""
    validate_configuration(isolation)
    paths, native = mapped_command(command, environment, workspace)
    args = ['docker', 'create', '--interactive', '--name', name,
            '--label', 'soilie.native-isolation=20261023', '--network', 'none',
            '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
            '--user', '1000:1000', '--init', '--pids-limit', str(POLICY['pidsLimit']),
            '--cpus', str(POLICY['cpus']), '--memory', str(POLICY['memoryBytes']),
            '--memory-swap', str(POLICY['memoryBytes']), '--shm-size', str(POLICY['shmBytes']),
            '--ulimit', f'fsize={POLICY["fileBytesLimit"]}:{POLICY["fileBytesLimit"]}',
            '--tmpfs', f'/tmp:rw,nosuid,nodev,size={POLICY["tmpBytes"]},mode=1777',
            '--tmpfs', f'/home/task:rw,nosuid,nodev,size={POLICY["homeBytes"]},mode=700,uid=1000,gid=1000',
            '--workdir', '/native/repository', '--entrypoint', '/usr/bin/python3']
    mounts = [(paths['repository'], '/native/repository', True),
              (paths['site_packages'], '/native/packages', True),
              (paths['blender'].parent, '/native/blender', True),
              (ROOT/'serverless/benchmark', '/tooling/serverless/benchmark', True),
              (workspace, '/workspace', False)]
    cache = isolation['bytecodeCache']
    if cache:
        cache_path = ROOT/cache['path']
        if cache_path.is_symlink() or any(parent.is_symlink() for parent in cache_path.parents):
            raise ValueError('The derived bytecode cache path contains a symbolic link')
        cache_path = cache_path.resolve()
        if not cache_path.is_dir() or cache_path.is_relative_to(workspace) or workspace.is_relative_to(cache_path):
            raise ValueError('The read-only bytecode cache overlaps native writable work')
        mounts.append((cache_path, '/native/cache', True))
    for source, target, readonly in mounts:
        if ',' in str(source):
            raise ValueError('A bind source cannot contain Docker mount delimiters')
        args += ['--mount', f'type=bind,src={source},dst={target}'+(',readonly' if readonly else '')]
    variables = dict(THREADS, HOME='/home/task', USER='task', LOGNAME='task',
        PYTHONPATH='/native/packages:/native/repository', PYTHONHASHSEED='0',
        PYTHONDONTWRITEBYTECODE='1', PWD='/native/repository',
        PYTHONPYCACHEPREFIX='/native/cache' if cache else '/tmp/python-cache',
        MPLCONFIGDIR='/tmp/matplotlib', XDG_CACHE_HOME='/tmp/cache', NUMBA_CACHE_DIR='/tmp/numba')
    for key in ('SOILIE_INFINIGEN_BEDROOM_COUNT', 'SOILIE_INFINIGEN_OBJECT_COUNT'):
        if key in os.environ:
            if os.environ[key] not in tuple(map(str, range(3, 9))):
                raise ValueError('Unexpected native inventory count environment')
            variables[key] = os.environ[key]
    version = os.environ.get('SOILIE_INFINIGEN_INVENTORY_VERSION')
    if version is not None:
        from serverless.benchmark.infinigen_task import INVENTORY_VERSION, LEGACY_INVENTORY_VERSION
        if version not in (INVENTORY_VERSION, LEGACY_INVENTORY_VERSION):
            raise ValueError('Unexpected native inventory version environment')
        variables['SOILIE_INFINIGEN_INVENTORY_VERSION'] = version
    for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
                'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy'):
        variables[key] = ''  # Do not inherit Docker client proxy credentials.
    for key, value in sorted(variables.items()):
        args += ['--env', key+'='+value]
    args += [isolation['imageId'], '/tooling/serverless/benchmark/native_isolation.py', 'worker', '--', *native]
    return args


def inspect_boundary(container, expected_image, cache=False):
    value = json_command(['docker', 'inspect', container])[0]
    config, host = value['Config'], value['HostConfig']
    if (value['Image'] != expected_image or config['User'] != '1000:1000'
            or not host['ReadonlyRootfs'] or host['NetworkMode'] != 'none'
            or host['CapDrop'] != ['ALL'] or not any('no-new-privileges' in item for item in host['SecurityOpt'])
            or host['Privileged'] or host.get('CapAdd') or host.get('Devices')
            or host['Memory'] != POLICY['memoryBytes'] or host['MemorySwap'] != POLICY['memoryBytes']
            or host['NanoCpus'] != POLICY['cpus']*1_000_000_000 or host['PidsLimit'] != POLICY['pidsLimit']):
        raise ValueError('Docker did not apply the frozen isolation boundary')
    mounts = value['Mounts']
    expected_mounts = {'/native/repository', '/native/packages', '/native/blender', '/tooling/serverless/benchmark', '/workspace'}
    if cache:
        expected_mounts.add('/native/cache')
    if ({item['Destination'] for item in mounts} != expected_mounts
            or any(item['RW'] != (item['Destination'] == '/workspace') for item in mounts)):
        raise ValueError('Docker applied unexpected/writable native mounts')
    forbidden = ('AWS_', 'OPENAI_', 'ANTHROPIC_', 'GOOGLE_', 'AZURE_', 'GITHUB_', 'GH_TOKEN')
    if any(item.split('=', 1)[0].startswith(forbidden) for item in config['Env']):
        raise ValueError('A cloud credential environment variable entered the worker')
    return {'imageId': value['Image'], 'user': config['User'], 'network': host['NetworkMode'],
            'readOnlyRootfs': host['ReadonlyRootfs'], 'capDrop': host['CapDrop'],
            'securityOptions': host['SecurityOpt'], 'memoryBytes': host['Memory'],
            'memorySwapBytes': host['MemorySwap'], 'nanoCpus': host['NanoCpus'],
            'pidsLimit': host['PidsLimit'], 'tmpfs': host['Tmpfs'], 'ulimits': host['Ulimits'],
            'mounts': [{'destination': item['Destination'], 'readOnly': not item['RW']} for item in mounts],
            'environmentVariableNames': sorted(item.split('=', 1)[0] for item in config['Env'])}


def workspace_bytes(workspace):
    size = 0
    for directory, children, files in os.walk(workspace, followlinks=False):
        if not Path(directory).resolve().is_relative_to(workspace):
            raise ValueError('Native workspace escaped its task directory')
        for name in [*children, *files]:
            path = Path(directory)/name
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue  # An active native writer may atomically rename files.
            if stat.S_ISLNK(info.st_mode):
                raise ValueError('Native worker created a workspace symlink')
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise ValueError('Native worker created a special workspace file')
            if stat.S_ISREG(info.st_mode):
                size += info.st_size
    return size


def receipt_path(value, workspace):
    path = Path(value)
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError('Isolation receipt contains a symbolic link')
    path = path.resolve()
    if not any(path.is_relative_to(base) for base in WORKSPACE_ROOTS) or path.is_relative_to(workspace):
        raise ValueError('Keep host isolation receipts task-owned but outside the native writable mount')
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def write_receipt(path, value):
    """Never follow a native-controlled symlink through host receipt writes."""
    if path.is_symlink():
        raise ValueError('Isolation receipt target became a symlink')
    temporary = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(encoded(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def execute_native(command, environment, workspace, isolation, receipt, *, timeout=1200, capture=False):
    """One owned container; host heartbeats limit orphans after controller loss."""
    if sys.platform != 'linux':
        raise ValueError('Use the pinned Linux/WSL interpreter for native isolation execution')
    from serverless.benchmark.supervise import command as supervised
    workspace = workspace_path(workspace)
    receipt = receipt_path(receipt, workspace)
    workspace_bytes(workspace)  # Refuse pre-existing links/special files too.
    validate_configuration(isolation)
    actual_image = configuration(isolation['imageId'], ROOT/isolation['bytecodeCache']['producerManifest'] if isolation['bytecodeCache'] else None)
    if actual_image != isolation:
        raise ValueError('The local native isolation image changed')
    name = 'soilie-infinigen-'+uuid.uuid4().hex
    container = None
    process = None
    lease_stop = threading.Event()
    lease_thread = None
    started = time.monotonic()
    row = {'schemaVersion': 1, 'startedAt': datetime.now(UTC).isoformat(), 'configuration': isolation,
           'timingMeaning': 'Includes Docker create/start/inspect/stop overhead; never assumed zero',
           'status': 'starting', 'workspaceLimitKind': POLICY['diskLimitKind']}
    output = tempfile.TemporaryFile(dir=workspace) if capture else None
    try:
        create = docker_command(command, environment, workspace, isolation, name)
        row['dockerCreateCommand'] = create
        container = subprocess.check_output(create, text=True).strip()
        if not re.fullmatch('[0-9a-f]{64}', container):
            raise ValueError('Docker did not return an owned container ID')
        row['containerId'] = container
        row['boundary'] = inspect_boundary(container, isolation['imageId'], cache=bool(isolation['bytecodeCache']))
        row['status'] = 'running'
        write_receipt(receipt, row)
        process = subprocess.Popen(supervised(['docker', 'start', '--attach', '--interactive', container]),
                                   stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT)

        def renew_lease():
            # Directory monitoring can be slow on large NTFS workspaces. It
            # must never starve the independent parent-ownership heartbeat.
            while not lease_stop.is_set() and process.poll() is None:
                try:
                    process.stdin.write(b'PING\n')
                    process.stdin.flush()
                except BrokenPipeError:
                    return
                lease_stop.wait(POLICY['monitorSeconds'])

        lease_thread = threading.Thread(target=renew_lease, daemon=True)
        lease_thread.start()
        while process.poll() is None:
            if time.monotonic()-started > timeout:
                raise subprocess.TimeoutExpired(command, timeout)
            if workspace_bytes(workspace) > POLICY['workspaceBytesMonitorLimit']:
                raise RuntimeError('Native workspace exceeded its monitored aggregate byte limit')
            if shutil.disk_usage(workspace).free < POLICY['freeDiskReserveBytes']:
                raise RuntimeError('Native workspace crossed the required free-disk reserve')
            time.sleep(POLICY['monitorSeconds'])
        process.wait(timeout=30)
        inspection = json_command(['docker', 'inspect', container])[0]
        row['containerState'] = {key: inspection['State'][key] for key in ('Status', 'ExitCode', 'OOMKilled', 'Error')}
        if inspection['State']['Running'] or process.returncode != inspection['State']['ExitCode']:
            raise RuntimeError('Native Docker attachment ended without a matching terminal container exit')
        row['finalWorkspaceBytes'] = workspace_bytes(workspace)
        row['finalWorkspaceValidated'] = True
        code = inspection['State']['ExitCode']
        row['status'] = 'complete' if code == 0 else 'failed'
        row['returnCode'] = code
        if output:
            output.seek(0)
            data = output.read(16*1024**2)
            if output.read(1):
                raise ValueError('Native import probe output exceeded its capture limit')
        else:
            data = None
        return subprocess.CompletedProcess(command, code, stdout=data)
    except BaseException as error:
        row['status'] = 'interrupted'
        row['errorType'] = type(error).__name__
        raise
    finally:
        lease_stop.set()
        if lease_thread:
            lease_thread.join(timeout=3)
        if process and process.stdin:
            try:
                process.stdin.close()
            except BrokenPipeError:
                pass
        removed = False
        if container:
            # Exact returned ID only: never enumerate/stop other Docker work.
            cleanup = subprocess.run(['docker', 'rm', '--force', container], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
            removed = cleanup.returncode == 0
        if process and process.poll() is None:
            process.wait(timeout=30)
        row['elapsedSeconds'] = time.monotonic()-started
        row['finishedAt'] = datetime.now(UTC).isoformat()
        row['containerRemoved'] = removed
        if not row.get('finalWorkspaceValidated'):
            try:
                row['finalWorkspaceBytes'] = workspace_bytes(workspace)
                row['finalWorkspaceValidated'] = True
            except ValueError as error:
                row['finalWorkspaceValidated'] = False
                row['workspaceValidationError'] = str(error)
        write_receipt(receipt, row)
        if output:
            output.close()
        if container and not removed:
            raise RuntimeError('Owned native container cleanup failed; do not start another timed workload')
        if not row['finalWorkspaceValidated']:
            raise ValueError('Native workspace failed final post-exit containment validation')


def wrapped_command(command, plan_path, workspace, receipt, timeout):
    return [sys.executable, '-m', 'serverless.benchmark.native_isolation', 'execute',
            '--plan', str(plan_path), '--workspace', str(workspace), '--receipt', str(receipt),
            '--timeout', str(timeout), '--', *map(str, command)]


def prepare_cache(args):
    from serverless.benchmark.run_batch import run_lock
    with run_lock(workspace_path(args.root)):
        return _prepare_cache(args)


def _prepare_cache(args):
    """Untimed derived artifacts, compiled by the original embedded interpreter."""
    from serverless.benchmark.baseline_extension_run import byte_tree, digest
    from serverless.benchmark.infinigen_task import COMMIT
    root = workspace_path(args.root)
    cache = root/'cache'
    if cache.exists() and any(cache.iterdir()):
        raise ValueError('Derived cache already contains evidence; never silently rebuild it')
    environment = {name: getattr(args, name).resolve().relative_to(ROOT).as_posix()
                   for name in ('repository', 'site_packages', 'blender')}
    if subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=args.repository, text=True).strip() != COMMIT:
        raise ValueError('Expected the original pinned Infinigen source for cache preparation')
    if subprocess.check_output(['git', 'diff', '--name-only', 'HEAD'], cwd=args.repository, text=True).strip():
        raise ValueError('Original native source changed before bytecode preparation')
    image = configuration(args.image)
    expression = '''
import hashlib,importlib.util,json,os,pathlib,py_compile,struct,sys
sys.pycache_prefix='/workspace'
sources=[]
skipped=[]
vendor_build_roots=('/native/repository/infinigen/datagen/customgt/dependencies/','/native/packages/')
roots=('/native/repository','/native/packages','/native/blender/3.6/python/lib/python3.10','/native/blender/3.6/scripts')
for root in roots:
    for directory,children,files in os.walk(root,followlinks=False):
        children[:]=sorted(name for name in children if name not in ('.git','__pycache__','test','tests'))
        for name in sorted(files):
            if not name.endswith('.py'): continue
            source=pathlib.Path(directory)/name
            try:
                py_compile.compile(str(source),doraise=True,optimize=0,invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH)
            except py_compile.PyCompileError as error:
                if error.exc_type_name!='SyntaxError' or not any(str(source).startswith(prefix) for prefix in vendor_build_roots):
                    raise
                skipped.append(dict(source=str(source),sourceSha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                    reason='SyntaxError in original optional third-party dependency or vendor/build utility; not rewritten',errorType=error.exc_type_name))
                continue
            output=pathlib.Path(importlib.util.cache_from_source(str(source),optimization=''))
            payload=output.read_bytes()
            assert payload[:4]==importlib.util.MAGIC_NUMBER and struct.unpack('<I',payload[4:8])[0]==3
            assert payload[8:16]==importlib.util.source_hash(source.read_bytes())
            sources.append(dict(source=str(source),sourceSha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                cache=output.relative_to('/workspace').as_posix(),cacheSha256=hashlib.sha256(payload).hexdigest()))
    print('DERIVED_CACHE_COMPILED '+root+' '+str(len(sources)),flush=True)
pathlib.Path('/workspace/compiled-sources.json').write_text(json.dumps(dict(schemaVersion=1,pythonVersion=sys.version,
    pythonMagic=importlib.util.MAGIC_NUMBER.hex(),invalidationMode='CHECKED_HASH',optimization=0,sourceRoots=roots,
    excludedDirectories=['.git','__pycache__','test','tests'],vendorBuildSyntaxSkipRoots=vendor_build_roots,
    sources=sources,skippedSources=skipped),sort_keys=True,separators=(',',':')))
'''
    result = execute_native([str(args.blender.resolve()), '--background', '--factory-startup', '--threads', '1',
        '--python-use-system-env', '--python-exit-code', '2', '--python-expr', expression],
        environment, cache, image, root/'preparation-isolation.json', timeout=1800)
    if result.returncode:
        raise ValueError('Untimed original-interpreter cache compilation failed; preserve its evidence')
    source_index = cache/'compiled-sources.json'
    compiled = json.loads(source_index.read_bytes())
    if len(compiled['sources']) != len(list(cache.rglob('*.pyc'))):
        raise ValueError('Derived cache has missing or unexpected bytecode artifacts')
    record = {'schemaVersion': 1, 'kind': 'native-derived-bytecode-cache', 'createdAt': datetime.now(UTC).isoformat(),
        'imageId': image['imageId'], 'modelCommit': COMMIT, 'environment': environment,
        'nativeBlenderSha256': digest(args.blender.resolve()), 'pythonVersion': compiled['pythonVersion'],
        'pythonMagic': compiled['pythonMagic'], 'invalidationMode': 'CHECKED_HASH', 'optimization': 0,
        'compiledSourceIndexSha256': digest(source_index), 'compiledSources': len(compiled['sources']),
        'skippedUnsupportedVendorSources': len(compiled['skippedSources']),
        'cache': dict(path=cache.relative_to(ROOT).as_posix(), **byte_tree(cache, include_bytecode=True)),
        'timingMeaning': 'One shared untimed setup; ordinary native startup/container overhead remains measured per room'}
    receipt = root/'producer-receipt.json'
    write_receipt(receipt, record)
    return {'producerReceipt': str(receipt), 'cache': record['cache'], 'roomSolves': 0, 'awsWrites': 0}


def worker(command):
    """Container-side lease watchdog, independent of native Blender's Python."""
    heartbeat = {'time': time.monotonic(), 'seen': False, 'eof': False}

    def read_lease():
        for line in sys.stdin:
            if line != 'PING\n':
                heartbeat['eof'] = True
                return
            heartbeat.update(time=time.monotonic(), seen=True)
        heartbeat['eof'] = True

    threading.Thread(target=read_lease, daemon=True).start()
    wait_started = time.monotonic()
    while not heartbeat['seen'] and not heartbeat['eof']:
        if time.monotonic()-wait_started > POLICY['heartbeatTimeoutSeconds']:
            return 125
        time.sleep(.05)
    if heartbeat['eof']:
        return 125
    if not command or command[0] != '/native/blender/blender':
        raise ValueError('Only the pinned native Blender is executable by this worker')
    cpu = next((line.partition(':')[2].strip() for line in Path('/proc/cpuinfo').read_text().splitlines()
                if line.startswith('model name')), None)
    print('__SOILIE_ISOLATION__'+json.dumps({'kernel': os.uname().release, 'uid': os.getuid(),
        'cpu': cpu, 'logicalCpusVisible': os.cpu_count(), 'threads': THREADS,
        'cgroupCpuMax': Path('/sys/fs/cgroup/cpu.max').read_text().strip(),
        'cgroupMemoryMax': Path('/sys/fs/cgroup/memory.max').read_text().strip()}), flush=True)
    child = subprocess.Popen(command, stdin=subprocess.DEVNULL, start_new_session=True)
    try:
        while child.poll() is None:
            if heartbeat['eof'] or time.monotonic()-heartbeat['time'] > POLICY['heartbeatTimeoutSeconds']:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
                return 125
            time.sleep(.2)
        return child.returncode
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    run = sub.add_parser('execute')
    run.add_argument('--plan', type=Path, required=True)
    run.add_argument('--workspace', type=Path, required=True)
    run.add_argument('--receipt', type=Path, required=True)
    run.add_argument('--timeout', type=int, required=True)
    run.add_argument('command', nargs=argparse.REMAINDER)
    native = sub.add_parser('worker')
    native.add_argument('command', nargs=argparse.REMAINDER)
    cache = sub.add_parser('prepare-cache')
    cache.add_argument('--root', type=Path, required=True)
    cache.add_argument('--image', required=True)
    for name in ('repository', 'site-packages', 'blender'):
        cache.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'prepare-cache':
        print(json.dumps(prepare_cache(args)))
        return
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if args.action == 'worker':
        raise SystemExit(worker(command))
    plan = json.loads(args.plan.read_bytes())
    body = {key: value for key, value in plan.items() if key != 'planSha256'}
    if hashlib.sha256(encoded(body)).hexdigest() != plan.get('planSha256'):
        raise ValueError('The frozen native extension plan changed')
    result = execute_native(command, plan['environment'], args.workspace, plan['nativeIsolation'],
                            args.receipt, timeout=args.timeout)
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
