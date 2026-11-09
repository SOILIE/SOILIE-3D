"""Freeze a separately linked Lambda continuation of a completed local cohort.

The --run-parity mode invokes Lambda and spends from the shared budget.
Final continuation dispatch remains in authoritative.
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
from pathlib import Path

from serverless.cloud_benchmark import authoritative as a
from serverless.cloud_benchmark.evidence_store import archive_configuration

KIND = 'lambda-continuation-v1'
ORIGINAL_IMAGINE_BLOB = '9d73b379d0083ea9f84ee4f46f44ff4a01d7ca9b'
MENU_FIX_IMAGINE_BLOB = '7b76f4048f7c893104083e2c8ff42269b27010dd'
EXECUTION_CHANGES = {'serverless/cloud_benchmark/authoritative.py',
                     'serverless/cloud_benchmark/continuation.py'}
HISTORICAL_ATTEMPT_COUNT = 422
HISTORICAL_ATTEMPTS_SHA256 = '7633180a0af48d46781c64921904f9c827b3daafc64b058667ffaaf01e41e48a'
BUDGET_ANCHOR_SHA256 = '9a758fb12fd363d70972d00e82110e2709847b3adb18f362034a22940b4ad166'
GATES = {'unit': {'unitTests'}, 'blender': {'blenderTests'}, 'parity': {'localLambdaParity'},
         'source': {'sourceAuditBridge'}}


def allocation(parent):
    if (a.plan_platforms(parent) != ('local',) or parent['phase'] != 'authoritative'
            or parent['maxObjectCount'] != 8
            or parent['requests'] != a.allocation(8, 'authoritative', ('local',))):
        raise ValueError('Continuation requires the exact local-only authoritative allocation')
    tasks = []
    for index, original in enumerate(parent['requests']):
        tasks.append({'id': original['id'].replace('local-', 'lambda-', 1),
                      'platform': 'lambda', 'request': {**original['request'],
                          'seed': 2_000_000_000 + 997 * (5000 + index)}})
    if (len({t['id'] for t in tasks}) != 5000
            or len({t['request']['seed'] for t in tasks}) != 5000
            or {t['id'] for t in tasks} & {t['id'] for t in parent['requests']}
            or {t['request']['seed'] for t in tasks} & {t['request']['seed'] for t in parent['requests']}):
        raise ValueError('Continuation overlaps parent IDs or seeds')
    return tasks


def verified_parent(root):
    parent = a.load_plan(root, verify_runtime=False)
    allocation(parent)
    rows = a.attempt_rows(root, parent, 'local')
    if any(row['status'] != 'complete' for row in rows):
        raise ValueError('Original local ledger must contain only completed attempts')
    if a.verify_completed(root, parent, 'local', rows) != {t['id'] for t in parent['requests']}:
        raise ValueError('All 5000 original requests must have verified completed evidence')
    return parent


def git_blob_sha(path):
    body = Path(path).read_bytes()
    return hashlib.sha1(b'blob ' + str(len(body)).encode() + b'\0' + body).hexdigest()


def validate_git_bridge(runtime, old_commit, new_commit):
    """Only the pinned menu patch and the reviewed test/controller surfaces may differ."""
    output = subprocess.run(['git', '-C', str(runtime), 'diff', '--name-only', '-z', old_commit, new_commit],
                            check=True, capture_output=True).stdout.decode('utf-8')
    paths = {path for path in output.split('\0') if path}
    permitted = {'imagine.py', 'README.md', 'serverless/cloud_benchmark/README.md',
                 'serverless/renderer/asset_smoke.py', *EXECUTION_CHANGES}
    if 'imagine.py' not in paths or any(path not in permitted and not path.startswith(('tests/', 'serverless/tests/')) for path in paths):
        raise ValueError('Git bridge contains changes outside the reviewed menu, tests and controllers')
    old_blob = subprocess.run(['git', '-C', str(runtime), 'rev-parse', old_commit + ':imagine.py'],
                              check=True, capture_output=True, text=True).stdout.strip()
    new_blob = subprocess.run(['git', '-C', str(runtime), 'rev-parse', new_commit + ':imagine.py'],
                              check=True, capture_output=True, text=True).stdout.strip()
    if old_blob != ORIGINAL_IMAGINE_BLOB or new_blob != MENU_FIX_IMAGINE_BLOB or git_blob_sha(Path(runtime)/'imagine.py') != new_blob:
        raise ValueError('Menu source is not the exact reviewed UI-only patch')
    helper = 'serverless/renderer/asset_smoke.py'
    if helper in paths:
        old_helper = subprocess.run(['git', '-C', str(runtime), 'rev-parse', old_commit + ':' + helper],
                                    check=True, capture_output=True, text=True).stdout.strip()
        new_helper = subprocess.run(['git', '-C', str(runtime), 'rev-parse', new_commit + ':' + helper],
                                    check=True, capture_output=True, text=True).stdout.strip()
        if (old_helper != 'dcf573d83b136b98c00dc5879f3beabbfb96673f'
                or new_helper != '475be9bbf4230fe7930070c8ab8da8b8d62147b5'
                or git_blob_sha(Path(runtime)/helper) != new_helper):
            raise ValueError('Test helper is not the exact reviewed change')

def validate_bridge(parent, current, parent_provenance, current_provenance, receipt, runtime):
    old = parent['configuration']['runtimeSnapshot']
    old_path = Path(parent_provenance)
    if a.file_digest(old_path) != old['provenanceSha256']:
        raise ValueError('Original provenance bytes differ from the frozen parent')
    if a.file_digest(Path(current_provenance)) != current['provenanceSha256']:
        raise ValueError('Current provenance bytes differ from the frozen continuation')
    old_doc, new_doc = a.read(old_path), a.read(current_provenance)
    if old_doc.get('files') != new_doc.get('files') or old.get('files') != current.get('files'):
        raise ValueError('Model source, assets or data differ from the original provenance')
    ignored = {'sourceCommit', 'provenanceSha256', 'executionFiles', 'localHardware'}
    if {k:v for k,v in old.items() if k not in ignored} != {k:v for k,v in current.items() if k not in ignored}:
        raise ValueError('Frozen runtime, version, dependencies or hardware differ')
    old_hardware, new_hardware = old.get('localHardware'), current.get('localHardware')
    if old_hardware != new_hardware:
        if (not isinstance(old_hardware, dict) or not isinstance(new_hardware, dict)
                or {k:v for k,v in old_hardware.items() if k != 'memoryBytes'}
                != {k:v for k,v in new_hardware.items() if k != 'memoryBytes'}
                or (old_hardware.get('memoryBytes'), new_hardware.get('memoryBytes'))
                != (12543291392, 12543283200)):
            raise ValueError('Frozen hardware differs beyond the reviewed memory observation')
    previous, now = old['executionFiles'], current['executionFiles']
    if set(previous) - set(now):
        raise ValueError('Original execution files cannot disappear')
    changed = {key: {'from': previous.get(key), 'to': now.get(key)}
               for key in set(previous) | set(now) if previous.get(key) != now.get(key)}
    if set(changed) != EXECUTION_CHANGES or any(value['to'] is None for value in changed.values()):
        raise ValueError('Execution changes must be limited to the reviewed continuation and controller')
    validate_git_bridge(runtime, old['sourceCommit'], current['sourceCommit'])
    expected = {'passed': True, 'parentPlanSha256': parent['planSha256'],
                'parentProvenanceSha256': old['provenanceSha256'],
                'runtimeProvenanceSha256': current['provenanceSha256'],
                'parentSourceCommit': old['sourceCommit'], 'sourceCommit': current['sourceCommit'],
                'executionChanges': changed}
    if receipt != expected:
        raise ValueError('Reviewed execution bridge does not match both exact snapshots')


def parity_tasks(parent):
    selected = {}
    for task in parent['requests']:
        key = (task['request']['roomType'], task['request']['objectCount'])
        selected.setdefault(key, task)
    if set(selected) != {(room, count) for room in a.ROOMS for count in range(3, 9)}:
        raise ValueError('Original cohort does not cover all parity cells')
    return [{'id': 'parity-' + task['id'], 'platform': 'lambda', 'request': task['request']}
            for task in selected.values()]


def parity_plan(parent, config, parent_root, bridge_sha):
    """Bind parity attempts to the original evidence and the reviewed bridge."""
    body = {'schemaVersion':1, 'kind':KIND, 'platforms':['lambda'], 'phase':'pilot',
            'configuration':config, 'parentPlanSha256':parent['planSha256'],
            'parentLedgerSha256':a.file_digest(Path(parent_root)/'local-ledger.json'),
            'parentRequestsSha256':a.digest(parent['requests']), 'bridgeSha256':bridge_sha,
            'requests':parity_tasks(parent)}
    body['planSha256'] = a.digest(body)
    return body


def prefreeze_parity(args):
    """Execute at most one original-seed parity invocation per cell under the shared budget lock."""
    from botocore.config import Config
    import boto3
    from serverless.cloud_benchmark.pilot import invoke_and_download, compare_placements
    from copy import deepcopy
    from datetime import UTC, datetime
    import uuid

    root, parent_root, budget_root = args.root.resolve(), args.parent.resolve(), args.budget_root.resolve()
    if any(root == other or root.is_relative_to(other) or other.is_relative_to(root)
           for other in (parent_root, budget_root)):
        raise ValueError('Parity root must be separate from parent and budget trees')
    with a.run_lock(budget_root):
        parent = verified_parent(parent_root)
        snapshot = a.runtime_snapshot(args.runtime, args.provenance, args.blender)
        validate_bridge(parent, snapshot, args.parent_provenance, args.provenance, a.read(args.bridge), args.runtime)
        for name in ('unit', 'blender', 'source'):
            gate_path = getattr(args, 'blender_gate' if name == 'blender' else name)
            a.passed_gate(gate_path, snapshot['provenanceSha256'], name, GATES[name])
            gate = a.read(gate_path)
            if (gate.get('runtimeSnapshotSha256') != a.digest(snapshot)
                    or gate.get('sourceCommit') != snapshot['sourceCommit']
                    or (name == 'source' and gate.get('parentSourceAuditSha256') != parent['configuration']['sourceAuditSha256'])):
                raise ValueError('Pre-parity qualification is not bound to the exact runtime and audit')
        prior = a.load_plan(args.budget_anchor.resolve(), verify_runtime=False)
        rates = a.validate_rates(a.read(args.rates), args.region)
        config = {'runtime':str(args.runtime.resolve()), 'provenance':str(args.provenance.resolve()),
                  'blender':str(args.blender.resolve()), 'runtimeSnapshot':snapshot, 'region':args.region,
                  'profile':args.profile, 'budgetRoot':str(budget_root), 'rateCard':rates, 'budgetUSD':25.0,
                  'function':args.function, 'image':args.image, 'bucket':args.bucket,
                  'lambdaConcurrency':16, 'memoryMB':4096, 'ephemeralMB':1024,
                  'objectCountDefinition':a.OBJECT_COUNT_DEFINITION, 'objectCountPolicy':a.OBJECT_COUNT_POLICY}
        a.versioned_function(config['function'])
        if (prior['planSha256'] != BUDGET_ANCHOR_SHA256 or prior['configuration']['budgetRoot'] != str(budget_root)
                or prior['configuration']['rateCard'] != rates or prior['configuration']['budgetUSD'] != 25.0):
            raise ValueError('Parity must retain the original budget plan, ledger and rates')
        ledger_path = budget_root / 'budget.json'
        if not ledger_path.is_file():
            raise ValueError('Original cumulative budget ledger is missing')
        plan = parity_plan(parent, config, parent_root, a.file_digest(args.bridge))
        budget = a.budget_document(plan)
        if (len(budget['attempts']) < HISTORICAL_ATTEMPT_COUNT
                or a.digest(budget['attempts'][:HISTORICAL_ATTEMPT_COUNT]) != HISTORICAL_ATTEMPTS_SHA256):
            raise ValueError('Original cumulative budget history was reset or changed')
        if any(row['status'] in a.UNCERTAIN for row in budget['attempts']):
            raise ValueError('Reconcile uncertain budget invocations before parity')
        originals = {(task['id'], task['request']['seed'], task['request']['roomType'],
                      task['request']['objectCount']) for task in plan['requests']}
        for previous in budget['attempts']:
            request = previous.get('request', {})
            identity = (previous.get('requestId'), request.get('seed'), request.get('roomType'),
                        request.get('objectCount'))
            if (previous.get('role') == 'parity' and identity in originals
                    and previous.get('planSha256') != plan['planSha256']):
                raise ValueError('Original parity request already belongs to another plan; reconcile its original root')
        root.mkdir(parents=True, exist_ok=True)
        manifest = root / 'parity-plan.json'
        if manifest.exists() and a.read(manifest) != plan:
            raise ValueError('A different pre-freeze parity plan already exists')
        if not manifest.exists():
            a.write_json(manifest, plan)
        rows = [row for row in budget['attempts'] if row.get('planSha256') == plan['planSha256']]
        if any(row.get('campaignRoot') != str(root) for row in rows):
            raise ValueError('Resume parity from the original campaign root')
        if any(row['status'] != 'complete' for row in rows):
            raise ValueError('A previous parity attempt is not complete; never retry it')
        if len({row['requestId'] for row in rows}) != len(rows):
            raise ValueError('Duplicate parity attempts require reconciliation')
        session = boto3.Session(profile_name=config['profile'], region_name=config['region'])
        a.verify_lambda(config, session)
        verification = {'function':config['function'], 'image':config['image'],
                        'runtimeSnapshotSha256':a.digest(snapshot), 'parentPlanSha256':parent['planSha256'],
                        'parityPlanSha256':plan['planSha256'], 'verifiedAt':datetime.now(UTC).isoformat()}
        verification_path = root / ('lambda-verification-' + uuid.uuid4().hex + '.json')
        a.write_json(verification_path, verification)
        verification_sha = a.file_digest(verification_path)
        clients = {'lambda':session.client('lambda', config=Config(read_timeout=930, connect_timeout=15,
                     retries={'total_max_attempts':1})), 's3':session.client('s3')}
        for task in plan['requests']:
            if any(row['requestId'] == task['id'] for row in rows):
                continue
            reserve = a.invocation_cost(915, rates)
            if a.spent(budget) + reserve > 25.0:
                raise ValueError('Cumulative budget cannot reserve another parity invocation')
            attempt = a.next_attempt(root, plan, task, rows)
            attempt.update(role='parity', campaignRoot=str(root), reservedUSD=reserve,
                           parentPlanSha256=parent['planSha256'],
                           verification=str(verification_path), verificationSha256=verification_sha,
                           function=config['function'], image=config['image'])
            budget['attempts'].append(attempt); rows.append(attempt)
            a.write_json(ledger_path, budget)
            event = {key:task['request'][key] for key in ('seed','objectCount','roomType')}
            try:
                invoke_and_download(None, config['function'], config['bucket'], event,
                                    root / attempt['directory'], False, clients)
                a.finish_cloud(root, plan, attempt)
                identity = a.saved_invocation_identity(root, plan, attempt)
                if not identity or not identity.get('awsRequestId'):
                    raise ValueError('Parity requires durable Lambda invocation identity')
            except Exception as error:
                attempt.update(status='unknown', error=str(error))
            attempt['finishedAt'] = datetime.now(UTC).isoformat()
            a.write_json(ledger_path, budget)
            if attempt['status'] != 'complete':
                raise ValueError('Parity stopped on a failed or uncertain invocation')
        parent_rows = {row['requestId']:row for row in a.attempt_rows(parent_root, parent, 'local')}
        scenes = []
        for task in plan['requests']:
            attempt = next(row for row in rows if row['requestId'] == task['id'])
            local_id = task['id'][len('parity-'):]
            local = a.load_artifact(parent_root, parent_rows[local_id], parent)
            remote = a.load_artifact(root, attempt, plan)
            comparable = deepcopy(remote)
            comparable['implementation']['sourceCommit'] = local['implementation']['sourceCommit']
            error = compare_placements(local, comparable)
            scenes.append({'requestId':local_id, 'roomType':task['request']['roomType'],
                           'objectCount':task['request']['objectCount'], 'seed':task['request']['seed'],
                           'sha256':attempt['sha256'], 'maxGeometryErrorM':error})
        return {'passed':True, 'parentPlanSha256':parent['planSha256'], 'parityPlanSha256':plan['planSha256'],
                'function':config['function'], 'image':config['image'], 'scenes':scenes}

def reconcile_parity(args, *, recover=False):
    """Reconcile original parity attempts without issuing another invocation."""
    root, parent_root, budget_root = args.root.resolve(), args.parent.resolve(), args.budget_root.resolve()
    with a.run_lock(budget_root):
        parent = verified_parent(parent_root)
        plan = a.read(root/'parity-plan.json')
        if (plan.get('planSha256') != a.digest({k:v for k,v in plan.items() if k != 'planSha256'})
                or plan.get('kind') != KIND or plan.get('phase') != 'pilot'
                or plan.get('parentPlanSha256') != parent['planSha256']
                or plan.get('parentLedgerSha256') != a.file_digest(parent_root/'local-ledger.json')
                or plan.get('parentRequestsSha256') != a.digest(parent['requests'])
                or plan.get('bridgeSha256') != a.file_digest(args.bridge)
                or plan.get('requests') != parity_tasks(parent)
                or plan['configuration'].get('budgetRoot') != str(budget_root)):
            raise ValueError('Frozen pre-parity plan or parent differs')
        config = plan['configuration']
        current = a.runtime_snapshot(Path(config['runtime']), Path(config['provenance']), Path(config['blender']))
        if current != config['runtimeSnapshot']:
            raise ValueError('Frozen parity runtime differs')
        validate_bridge(parent, current, args.parent_provenance, Path(config['provenance']),
                        a.read(args.bridge), Path(config['runtime']))
        prior = a.load_plan(args.budget_anchor.resolve(), verify_runtime=False)
        budget = a.budget_document(plan)
        if (prior['planSha256'] != BUDGET_ANCHOR_SHA256
                or prior['configuration']['budgetRoot'] != str(budget_root)
                or prior['configuration']['rateCard'] != config['rateCard']
                or len(budget['attempts']) < HISTORICAL_ATTEMPT_COUNT
                or a.digest(budget['attempts'][:HISTORICAL_ATTEMPT_COUNT]) != HISTORICAL_ATTEMPTS_SHA256):
            raise ValueError('Original cumulative budget history differs')
        args.root = root
        if recover:
            a.recover(args, plan)
        else:
            args.platform = 'lambda'
            a.reconcile(args, plan)

def verify_parity(document, parent, plan, budget):
    """Verify twelve retained cloud artifacts against their original local scenes."""
    from copy import deepcopy
    from serverless.cloud_benchmark.pilot import compare_placements
    scenes = document.get('scenes', [])
    expected = {(room, count) for room in a.ROOMS for count in range(3, 9)}
    if len(scenes) != 12 or {(r['roomType'], r['objectCount']) for r in scenes} != expected:
        raise ValueError('Parity requires exactly one scene per room/count cell')
    parity_config = {key:value for key,value in plan['configuration'].items()
                     if key not in {'outputArchive', 'publicRunNamespace'}}
    parity_sha = parity_plan(parent, parity_config, Path(plan['parent']['root']),
                             plan['bridge']['sha256'])['planSha256']
    if document.get('parityPlanSha256') != parity_sha:
        raise ValueError('Parity result belongs to a different frozen parity plan')
    tasks = {task['id']: task for task in parent['requests']}
    parent_root = Path(plan['parent']['root'])
    local_rows = {row['requestId']: row for row in a.attempt_rows(parent_root, parent, 'local')}
    for scene in scenes:
        task = tasks.get(scene.get('requestId'))
        if (task is None or task['request']['roomType'] != scene['roomType']
                or task['request']['objectCount'] != scene['objectCount']
                or task['request']['seed'] != scene.get('seed')):
            raise ValueError('Parity scene does not identify its original local request')
        matches = [row for row in budget['attempts'] if row.get('role') == 'parity'
                   and row.get('status') == 'complete' and row.get('request') == task['request']
                   and row.get('requestId') == 'parity-' + task['id'] and row.get('attempt') == 1
                   and row.get('sha256') == scene.get('sha256')]
        if len(matches) != 1:
            raise ValueError('Parity scene needs one retained completed budget attempt')
        attempt = matches[0]
        if (attempt.get('planSha256') != parity_sha or attempt.get('parentPlanSha256') != parent['planSha256']
                or not attempt.get('invocationIdentity')
                or not attempt.get('invocationIdentitySha256')
                or attempt.get('function') != plan['configuration']['function']
                or attempt.get('image') != plan['configuration']['image']):
            raise ValueError('Parity attempt differs from the qualified Lambda version or image')
        parity_root = Path(attempt['campaignRoot']).resolve()
        verification_path = Path(attempt['verification']).resolve()
        if not verification_path.is_relative_to(parity_root):
            raise ValueError('Parity verification receipt escaped its campaign')
        if a.file_digest(verification_path) != attempt['verificationSha256']:
            raise ValueError('Parity Lambda verification receipt changed')
        verification = a.read(verification_path)
        if (verification.get('function') != plan['configuration']['function']
                or verification.get('image') != plan['configuration']['image']
                or verification.get('runtimeSnapshotSha256') != a.digest(plan['configuration']['runtimeSnapshot'])
                or verification.get('parentPlanSha256') != parent['planSha256']
                or verification.get('parityPlanSha256') != attempt.get('planSha256')
                or not verification.get('verifiedAt')):
            raise ValueError('Parity verification does not bind the exact Lambda and runtime')
        a.verify_attempt_evidence(parity_root, attempt, plan)
        identity = a.saved_invocation_identity(parity_root, plan, attempt)
        if not identity or not identity.get('awsRequestId') or not attempt.get('receiptSha256'):
            raise ValueError('Parity invocation identity or receipt is missing')
        receipt = a.read(parity_root / attempt['receipt'])
        if receipt.get('awsRequestId') != identity['awsRequestId']:
            raise ValueError('Parity invocation receipt has a different execution identity')
        local = a.load_artifact(parent_root, local_rows[task['id']], parent)
        remote = a.load_artifact(Path(attempt['campaignRoot']), attempt, plan)
        a.validate_artifact(remote, task, plan)
        comparable = deepcopy(remote)
        comparable['implementation']['sourceCommit'] = local['implementation']['sourceCommit']
        error = compare_placements(local, comparable)
        if type(scene.get('maxGeometryErrorM')) not in (int, float) or scene['maxGeometryErrorM'] != error:
            raise ValueError('Parity geometry error does not match retained evidence')

def validate(plan, root, verify_runtime=True, *, verified_parent_plan=None):
    parent_root = Path(plan['parent']['root'])
    parent = verified_parent_plan if verified_parent_plan is not None else verified_parent(parent_root)
    if (parent['planSha256'] != plan['parent']['planSha256']
            or a.digest(parent['requests']) != plan['parent']['requestsSha256']
            or a.file_digest(parent_root / 'local-ledger.json') != plan['parent']['ledgerSha256']
            or plan['requests'] != allocation(parent)):
        raise ValueError('Frozen parent or continuation allocation changed')
    config = plan['configuration']
    if (plan.get('platforms') != ['lambda'] or plan.get('phase') != 'authoritative'
            or plan.get('maxObjectCount') != 8 or config.get('budgetUSD') != 25.0
            or config.get('objectCountDefinition') != a.OBJECT_COUNT_DEFINITION
            or config.get('objectCountPolicy') != a.OBJECT_COUNT_POLICY):
        raise ValueError('Invalid continuation scope or count contract')
    if not (Path(config['budgetRoot']) / 'budget.json').is_file():
        raise ValueError('Continuation must use an existing cumulative budget ledger')
    anchor = plan['budgetAnchor']
    prior = a.load_plan(Path(anchor['root']), verify_runtime=False)
    if (prior['planSha256'] != BUDGET_ANCHOR_SHA256 or prior['planSha256'] != anchor['planSha256']
            or prior['configuration']['budgetRoot'] != config['budgetRoot']
            or prior['configuration']['rateCard'] != config['rateCard']
            or prior['configuration']['budgetUSD'] != config['budgetUSD']):
        raise ValueError('Continuation must retain the pinned original budget and rates')
    budget = a.budget_document(plan)
    snapshot_path = Path(root) / 'budget-snapshot.json'
    if a.file_digest(snapshot_path) != anchor['ledgerSnapshotSha256']:
        raise ValueError('Original budget snapshot changed')
    original_budget = a.read(snapshot_path)
    if (budget.get('sessions', [])[:len(original_budget.get('sessions', []))]
            != original_budget.get('sessions', [])):
        raise ValueError('Original cumulative budget sessions changed')
    count = anchor['attemptCount']
    if (len(original_budget['attempts']) != count
            or a.digest(original_budget['attempts']) != anchor['attemptsSha256']
            or len(original_budget['attempts']) < HISTORICAL_ATTEMPT_COUNT
            or a.digest(original_budget['attempts'][:HISTORICAL_ATTEMPT_COUNT]) != HISTORICAL_ATTEMPTS_SHA256):
        raise ValueError('Original historical budget snapshot differs')
    if (type(count) is not int or count < 0 or len(budget['attempts']) < count
            or a.digest(budget['attempts'][:count]) != anchor['attemptsSha256']):
        raise ValueError('Original cumulative budget history was reset or changed')
    if plan.get('timingPolicy') != parent['timingPolicy'] or plan.get('reviewPolicy') != parent['reviewPolicy']:
        raise ValueError('Continuation timing or review policy differs from its parent')
    a.versioned_function(config['function'])
    for name, checks in GATES.items():
        entry = plan['qualification'][name]
        if a.passed_gate(Path(entry['path']), config['runtimeSnapshot']['provenanceSha256'], name, checks) != entry['sha256']:
            raise ValueError('Qualification receipt changed: ' + name)
        document = a.read(Path(entry['path']))
        binding = {'parent': plan['parent'], 'budgetAnchor': plan['budgetAnchor'],
                   'configuration': config, 'timingPolicy': plan['timingPolicy'],
                   'reviewPolicy': plan['reviewPolicy']}
        if document.get('continuationBindingSha256') != a.digest(binding):
            raise ValueError('Qualification does not bind the continuation configuration: ' + name)
        if name == 'source' and document.get('parentSourceAuditSha256') != parent['configuration']['sourceAuditSha256']:
            raise ValueError('Source qualification does not bind the original source audit')
        if (document.get('runtimeSnapshotSha256') != a.digest(config['runtimeSnapshot'])
                or document.get('sourceCommit') != config['runtimeSnapshot']['sourceCommit']):
            raise ValueError('Qualification does not bind the exact runtime: ' + name)
        if name == 'parity':
            coverage = {(row['roomType'], row['objectCount']) for row in document.get('scenes', [])}
            expected = {(room, count) for room in a.ROOMS for count in range(3, 9)}
            if (document.get('function') != config['function'] or document.get('image') != config['image']
                    or coverage != expected):
                raise ValueError('Parity qualification must bind the Lambda version and cover all room/count cells')
            verify_parity(document, parent, plan, budget)
    expected_gates = {'sourceAuditSha256': parent['configuration']['sourceAuditSha256'],
                      **{name + 'Sha256': plan['qualification'][name]['sha256'] for name in GATES}}
    if plan.get('gates') != expected_gates:
        raise ValueError('Continuation gate bindings differ')
    bridge = plan['bridge']
    if a.file_digest(Path(bridge['path'])) != bridge['sha256']:
        raise ValueError('Reviewed bridge receipt changed')
    snapshot = config['runtimeSnapshot']
    if verify_runtime:
        snapshot = a.runtime_snapshot(Path(config['runtime']), Path(config['provenance']), Path(config['blender']))
        if snapshot != config['runtimeSnapshot']:
            raise ValueError('Current continuation runtime differs from its frozen snapshot')
    validate_bridge(parent, snapshot, Path(bridge['parentProvenance']), Path(config['provenance']), a.read(Path(bridge['path'])), Path(config['runtime']))
    return plan


def qualify(args):
    """Verify raw gates and paid parity, then derive fully bound freeze receipts."""
    import json
    root, parent_root, budget_root = args.qualification_dir.resolve(), args.parent.resolve(), args.budget_root.resolve()
    if any(root == other or root.is_relative_to(other) or other.is_relative_to(root)
           for other in (parent_root, budget_root)):
        raise ValueError('Qualification root must be separate from parent and budget trees')
    with a.run_lock(budget_root):
        parent = verified_parent(parent_root)
        snapshot = a.runtime_snapshot(args.runtime, args.provenance, args.blender)
        validate_bridge(parent, snapshot, args.parent_provenance, args.provenance, a.read(args.bridge), args.runtime)
        rates = a.validate_rates(a.read(args.rates), args.region)
        prior = a.load_plan(args.budget_anchor.resolve(), verify_runtime=False)
        if (prior['planSha256'] != BUDGET_ANCHOR_SHA256 or prior['configuration']['budgetRoot'] != str(budget_root)
                or prior['configuration']['rateCard'] != rates or prior['configuration']['budgetUSD'] != 25.0):
            raise ValueError('Qualification requires the original budget and rates')
        a.versioned_function(args.function)
        config = {'runtime':str(args.runtime.resolve()), 'provenance':str(args.provenance.resolve()),
                  'blender':str(args.blender.resolve()), 'runtimeSnapshot':snapshot,
                  'region':args.region, 'profile':args.profile, 'budgetRoot':str(budget_root),
                  'rateCard':rates, 'budgetUSD':25.0, 'function':args.function, 'image':args.image,
                  'bucket':args.bucket, 'lambdaConcurrency':16, 'memoryMB':4096, 'ephemeralMB':1024,
                  'objectCountDefinition':a.OBJECT_COUNT_DEFINITION, 'objectCountPolicy':a.OBJECT_COUNT_POLICY,
                  'outputArchive':archive_configuration(args.archive_date), 'publicRunNamespace':'plan-sha256-v1'}
        ledger_path = budget_root/'budget.json'
        ledger_bytes = ledger_path.read_bytes()
        budget = json.loads(ledger_bytes)
        if (budget.get('budgetUSD') != 25.0 or budget.get('rateCard') != rates or budget.get('region') != args.region
                or len(budget['attempts']) < HISTORICAL_ATTEMPT_COUNT
                or a.digest(budget['attempts'][:HISTORICAL_ATTEMPT_COUNT]) != HISTORICAL_ATTEMPTS_SHA256
                or any(row['status'] in a.UNCERTAIN for row in budget['attempts'])):
            raise ValueError('Cumulative budget is changed or has uncertain invocations')
        anchor = {'root':str(args.budget_anchor.resolve()), 'planSha256':prior['planSha256'],
                  'ledgerSnapshotSha256':hashlib.sha256(ledger_bytes).hexdigest(),
                  'attemptCount':len(budget['attempts']), 'attemptsSha256':a.digest(budget['attempts'])}
        parent_ref = {'root':str(parent_root), 'planSha256':parent['planSha256'],
                      'requestsSha256':a.digest(parent['requests']), 'ledgerSha256':a.file_digest(parent_root/'local-ledger.json')}
        binding = {'parent':parent_ref, 'budgetAnchor':anchor, 'configuration':config,
                   'timingPolicy':parent['timingPolicy'], 'reviewPolicy':parent['reviewPolicy']}
        result = {}
        for name in ('unit','blender','source'):
            path = getattr(args, 'blender_gate' if name == 'blender' else name)
            a.passed_gate(path, snapshot['provenanceSha256'], name, GATES[name])
            document = a.read(path)
            if (document.get('runtimeSnapshotSha256') != a.digest(snapshot)
                    or document.get('sourceCommit') != snapshot['sourceCommit']
                    or (name == 'source' and document.get('parentSourceAuditSha256') != parent['configuration']['sourceAuditSha256'])):
                raise ValueError('Qualification receipt does not match the current runtime and source audit')
            result[name] = {**document, 'continuationBindingSha256':a.digest(binding),
                            'sourceReceiptSha256':a.file_digest(path)}
        parity = a.read(args.parity)
        if (parity.get('passed') is not True or parity.get('function') != config['function']
                or parity.get('image') != config['image'] or parity.get('parentPlanSha256') != parent['planSha256']):
            raise ValueError('Raw parity result does not match the current Lambda and parent')
        verify_parity(parity, parent, {'parent':parent_ref, 'configuration':config,
                                      'bridge':{'sha256':a.file_digest(args.bridge)}}, budget)
        result['parity'] = {**parity, 'checks':{'localLambdaParity':True},
            'runtimeProvenanceSha256':snapshot['provenanceSha256'], 'runtimeSnapshotSha256':a.digest(snapshot),
            'sourceCommit':snapshot['sourceCommit'], 'continuationBindingSha256':a.digest(binding),
            'sourceReceiptSha256':a.file_digest(args.parity)}
        root.mkdir(parents=True, exist_ok=True)
        for name, document in result.items():
            path = root/(name+'.json')
            if path.exists() and a.read(path) != document:
                raise ValueError('Existing qualification receipt differs: '+name)
            if not path.exists():
                a.write_json(path, document)
        return {name:str(root/(name+'.json')) for name in result}

def freeze(args):
    root, parent_root, budget_root = args.root.resolve(), args.parent.resolve(), args.budget_root.resolve()
    if any(root == other or root.is_relative_to(other) or other.is_relative_to(root)
           for other in (parent_root, budget_root)):
        raise ValueError('Continuation root must be separate from parent and budget trees')
    with a.run_lock(budget_root):
        return _freeze(args)


def _freeze(args):
    root, parent_root = args.root.resolve(), args.parent.resolve()
    parent = verified_parent(parent_root)
    snapshot = a.runtime_snapshot(args.runtime, args.provenance, args.blender)
    bridge = a.read(args.bridge)
    validate_bridge(parent, snapshot, args.parent_provenance, args.provenance, bridge, args.runtime)
    qualification = {}
    for name, checks in GATES.items():
        path = getattr(args, 'blender_gate' if name == 'blender' else name).resolve()
        qualification[name] = {'path': str(path), 'sha256': a.passed_gate(path, snapshot['provenanceSha256'], name, checks)}
    rates = a.validate_rates(a.read(args.rates), args.region)
    a.versioned_function(args.function)
    config = {'runtime': str(args.runtime.resolve()), 'provenance': str(args.provenance.resolve()),
              'blender': str(args.blender.resolve()), 'runtimeSnapshot': snapshot,
              'region': args.region, 'profile': args.profile, 'budgetRoot': str(args.budget_root.resolve()),
              'rateCard': rates, 'budgetUSD': 25.0, 'function': args.function, 'image': args.image,
              'bucket': args.bucket, 'lambdaConcurrency': 16, 'memoryMB': 4096, 'ephemeralMB': 1024,
              'objectCountDefinition': a.OBJECT_COUNT_DEFINITION, 'objectCountPolicy': a.OBJECT_COUNT_POLICY,
              'outputArchive': archive_configuration(args.archive_date), 'publicRunNamespace': 'plan-sha256-v1'}
    anchor_root = args.budget_anchor.resolve()
    prior = a.load_plan(anchor_root, verify_runtime=False)
    if prior['planSha256'] != BUDGET_ANCHOR_SHA256:
        raise ValueError('Original budget plan SHA differs')
    ledger_path = args.budget_root.resolve() / 'budget.json'
    if not ledger_path.is_file():
        raise ValueError('Continuation must use an existing cumulative budget ledger')
    import hashlib
    import json
    ledger_bytes = ledger_path.read_bytes()
    ledger = json.loads(ledger_bytes)
    anchor = {'root': str(anchor_root), 'planSha256': prior['planSha256'],
              'ledgerSnapshotSha256': hashlib.sha256(ledger_bytes).hexdigest(), 'attemptCount': len(ledger['attempts']),
              'attemptsSha256': a.digest(ledger['attempts'])}
    body = {'schemaVersion': 1, 'kind': KIND, 'platforms': ['lambda'], 'phase': 'authoritative',
            'maxObjectCount': 8, 'parent': {'root': str(parent_root), 'planSha256': parent['planSha256'],
                'requestsSha256': a.digest(parent['requests']),
                'ledgerSha256': a.file_digest(parent_root / 'local-ledger.json')}, 'requests': allocation(parent),
            'configuration': config, 'budgetAnchor': anchor, 'bridge': {'path': str(args.bridge.resolve()), 'sha256': a.file_digest(args.bridge), 'parentProvenance': str(args.parent_provenance.resolve())},
            'qualification': qualification,
            'gates': {'sourceAuditSha256': parent['configuration']['sourceAuditSha256'],
                      **{name + 'Sha256': qualification[name]['sha256'] for name in GATES}},
            'timingPolicy': parent['timingPolicy'],
            'reviewPolicy': parent['reviewPolicy']}
    body['planSha256'] = a.digest(body)
    import json
    import os
    import tempfile
    if root.exists():
        path = root / 'plan.json'
        if path.is_file() and a.read(path) == body:
            validate(body, root, verify_runtime=False, verified_parent_plan=parent)
            return body
        raise ValueError('An existing continuation directory cannot be replaced')
    root.parent.mkdir(parents=True, exist_ok=True)
    # Stage both immutable files in the same directory and publish with one
    # rename. A crash cannot leave a visible partial plan or snapshot.
    with tempfile.TemporaryDirectory(prefix='.continuation-stage-', dir=root.parent) as temporary:
        staging = Path(temporary) / 'publication'
        staging.mkdir()
        (staging / 'budget-snapshot.json').write_bytes(ledger_bytes)
        (staging / 'plan.json').write_text(json.dumps(body, sort_keys=True, separators=(',', ':')), encoding='utf-8')
        validate(body, staging, verify_runtime=False, verified_parent_plan=parent)
        os.rename(staging, root)
    return body


def export_evidence(root, plan, output):
    """Export both immutable cohorts with their own source and plan identities."""
    parent_root = Path(plan['parent']['root'])
    parent = verified_parent(parent_root)
    status = a.campaign_status(root, plan)
    if not status['complete']:
        raise ValueError('Every continuation request must be complete before export')
    records, accounting, sources = [], [], []
    for source_root, source_plan, platform in ((parent_root, parent, 'local'), (root, plan, 'lambda')):
        rows = a.attempt_rows(source_root, source_plan, platform)
        complete = a.verify_completed(source_root, source_plan, platform, rows)
        tasks = [task for task in source_plan['requests'] if task['platform'] == platform]
        if complete != {task['id'] for task in tasks}:
            raise ValueError('A frozen cohort has incomplete evidence')
        latest = {row['requestId']: row for row in rows if row['status'] == 'complete'}
        for task in tasks:
            attempt = latest[task['id']]
            records.append({'requestId': task['id'], 'platform': platform, 'planSha256': source_plan['planSha256'],
                            'request': task['request'], 'sha256': attempt['sha256'], 'artifact': attempt['artifact'],
                            'scene': a.load_artifact(source_root, attempt, source_plan)})
        accounting.extend(rows)
        sources.append({'platform': platform, 'planSha256': source_plan['planSha256'],
                        'runtimeSnapshot': source_plan['configuration']['runtimeSnapshot'],
                        'configuration': source_plan['configuration']})
    budget = a.budget_document(plan)
    sessions = {'local': a.read(parent_root / 'local-ledger.json').get('sessions', []),
                'lambda': [row for row in budget.get('sessions', []) if row['planSha256'] == plan['planSha256']]}
    document = {'schemaVersion':1, 'kind':'linked-continuation-evidence-v1', 'complete':True,
                'sources':sources, 'parent':plan['parent'], 'continuationPlanSha256':plan['planSha256'],
                'qualification':plan['qualification'], 'bridge':plan['bridge'], 'budgetAnchor':plan['budgetAnchor'],
                'platforms':['local','lambda'], 'coverage':status, 'scenes':records, 'attempts':accounting,
                'executionSessions':sessions, 'lambdaBudgetAccounting':budget,
                'authoritativeGenerationDate':a.generation_date(accounting, sessions),
                'generationDateTimezone':'America/Toronto', 'timingPolicy':plan['timingPolicy'], 'reviewApproved':False}
    document['evidenceSha256'] = a.digest(document)
    if output.exists() and a.read(output) != document:
        raise ValueError('Existing export differs; preserve the original evidence')
    output.parent.mkdir(parents=True, exist_ok=True)
    a.write_json(output, document)
    return {'complete':True, 'scenes':len(records), 'attempts':len(accounting),
            'evidenceSha256':document['evidenceSha256']}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--run-parity', action='store_true', help='Run the 12 pre-freeze parity probes')
    mode.add_argument('--qualify', action='store_true', help='Verify evidence and derive frozen qualification receipts')
    mode.add_argument('--reconcile-parity', action='store_true', help='Reconcile retained parity evidence without reinvocation')
    mode.add_argument('--recover-parity', action='store_true', help='Recover a named original parity invocation without reinvocation')
    parser.add_argument('--request')
    parser.add_argument('--attempt', type=int, default=1)
    parser.add_argument('--aws-request-id')
    parser.add_argument('--service-failure', action='store_true')
    parser.add_argument('--qualification-dir', type=Path)
    for name in ('root', 'parent', 'runtime', 'provenance', 'parent-provenance', 'blender', 'bridge', 'unit', 'blender-gate', 'source', 'rates', 'budget-root', 'budget-anchor'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--parity', type=Path)
    parser.add_argument('--archive-date')
    for name in ('function', 'image', 'bucket'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--region', default='ca-central-1')
    parser.add_argument('--profile', default='darkest')
    args = parser.parse_args()
    if args.run_parity:
        import json
        document = prefreeze_parity(args)
        output = args.root.resolve() / 'parity-results.json'
        if output.exists() and a.read(output) != document:
            raise ValueError('A different parity result already exists')
        if not output.exists():
            a.write_json(output, document)
        print(json.dumps(document))
    elif args.reconcile_parity or args.recover_parity:
        if args.recover_parity and (not args.request or not args.aws_request_id):
            parser.error('recover-parity requires --request and --aws-request-id')
        reconcile_parity(args, recover=args.recover_parity)
    elif args.qualify:
        if args.parity is None or args.archive_date is None or args.qualification_dir is None:
            parser.error('qualify requires --parity, --archive-date and --qualification-dir')
        import json
        print(json.dumps(qualify(args)))
    else:
        if args.parity is None or args.archive_date is None:
            parser.error('freeze requires --parity and --archive-date')
        print(freeze(args)['planSha256'])

if __name__ == '__main__':
    main()
