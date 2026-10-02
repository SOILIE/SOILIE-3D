"""Integrate verified final-placement replays without changing frozen reviews."""
import argparse
from copy import deepcopy
from pathlib import Path

from serverless.benchmark.archive import packed
from serverless.benchmark.box_support import measure_box_support
from serverless.benchmark.geometry import measure, summarize
from serverless.benchmark.publish_comparison import LABELS, compare
from serverless.cloud_benchmark.checkpoint import write_json
from serverless.cloud_benchmark.publication_views import model_summary, room_models, mesh_check_coverage
from serverless.cloud_benchmark.refine_box_classification import refine
from serverless.cloud_benchmark.staged_pilot import read, digest
from serverless.cloud_benchmark.publish import timing_conditions


def validate_replay(result, original):
    """Reject incidental movement, incomplete restores and changed identities."""
    if result['source'] != original['source'] or result['control']['moves']:
        raise ValueError('Source mismatch or ordinary control placement changed')
    scene = result['scene']
    before = original['record']['scene']
    if scene['room'] != before['room'] or len(scene['objects']) != len(before['objects']):
        raise ValueError('Room or object inventory changed')
    moved = {r['id'] for r in result['correction']['moves']}
    if not moved.issubset({o['id'] for o in before['objects']}):
        raise ValueError('Unknown moved object')
    for old, new in zip(before['objects'], scene['objects']):
        if any(old[k] != new[k] for k in ('id', 'label', 'asset', 'frontDirection')):
            raise ValueError('Identity changed')
        for a in range(4):
            for b in range(4):
                if (a, b) != (2, 3) and abs(old['transform'][a][b] - new['transform'][a][b]) > 1e-5:
                    raise ValueError('Only vertical movement is allowed')
        if old['id'] not in moved and any(old[k] != new[k] for k in ('corners', 'transform')):
            raise ValueError('Unchanged objects must retain exact geometry')
    return moved


def refresh(directory, replays, sources, protocol, cohort, output):
    directory,replays,output=map(Path,(directory,replays,output))
    original={r['record']['scene']['id']:r for r in read(sources)['rows']}
    records=[read(p) for p in sorted(replays.glob('*.json'))]
    if {r['scene']['id'] for r in records} != set(original): raise ValueError('Incomplete replay audit')
    reviewed={r['sceneId'] for r in read(protocol)['sceneProvenance'].values()}
    document=read(directory/'comparison.json')
    public=read(directory/'room-measurements.json')
    by_id={r['sceneId']:r for r in public['rows']}
    supports=read(directory/'support-measurements.json')
    support_index={r['sceneId']:r for r in supports['rooms']}
    boxes=read(directory/'box-support-measurements.json')
    box_index={r['sceneId']:r for r in boxes['rooms']}
    timing=deepcopy(read(cohort)['sources'])
    changed=[]
    for result in records:
        scene=result['scene']; identifier=scene['id']; prior=original[identifier]['record']
        moved=validate_replay(result,original[identifier])
        if not moved: continue
        if box_index[identifier]['geometrySha256']!=original[identifier]['source']['sha256']:
            raise ValueError('Room was already changed or does not match archived source')
        if identifier in reviewed: raise ValueError('Changed room needs fresh frozen review judgments: '+identifier)
        check=scene['solidMeshOverlap']
        if not check['complete'] or check['maxOverlapPct']>.0001: raise ValueError('Residual mesh collision')
        metrics=measure(scene)
        if metrics['maxOutsideFootprintPct']>.0001 or metrics['supportGapCm']>.001 or metrics['belowFloorCm']>.001:
            raise ValueError('Final containment or support unresolved')
        row={'scene':scene,'metrics':metrics,'finalPlacement':{
            'implementation':result['implementation'], 'sourceSha256':result['source']['sha256'],
            'computeSeconds':result['correction']['correctionSeconds']}}
        changed.append(row)
        old_metrics=by_id[identifier]['metrics']
        by_id[identifier]['metrics']=metrics
        support_index[identifier]['objects']=[dict(id=o['id'],label=o['label'],**o['support']) for o in scene['objects'] if o.get('support')]
        box_index[identifier].update(geometrySha256=digest(packed(row)),sourceSceneSha256=digest(scene),**measure_box_support(scene))
        for stage in document['beforeAfter']:
            if stage['id']==identifier: stage['final']=metrics['meanWorstEnvelopeOverlapPct']
        # Other diagnostic dimensions are horizontal or inventory-only.
        resolution=document['nonHumanDiagnostics']['overlapResolution']
        stage=next(s for s in document['beforeAfter'] if s['id']==identifier)
        # A mean over at most six nonnegative overlaps can certify membership
        # in the original max-overlap gate when it exceeds that gate itself.
        if 0 < stage['beforeSeparation'] <= resolution['numericalTolerancePct']:
            raise ValueError('Need original per-object stage measurements for borderline membership')
        if stage['beforeSeparation']>resolution['numericalTolerancePct']:
            values=resolution['finalMeanWorstEnvelopeOverlapPct']['values']
            values.remove(old_metrics['meanWorstEnvelopeOverlapPct'])
            values.append(metrics['meanWorstEnvelopeOverlapPct'])
            resolution['finalMeanWorstEnvelopeOverlapPct']=summarize(values)
            resolution['scenesResolvedToNoEnvelopeOverlap']+=int(metrics['maxEnvelopeOverlapPct']<=.0001)-int(old_metrics['maxEnvelopeOverlapPct']<=.0001)
            resolution['resolutionPct']=100*resolution['scenesResolvedToNoEnvelopeOverlap']/resolution['scenesWithInitialEnvelopeOverlap']
        for group in [document['meshCheckCoverage']['soilie'],document['meshCheckCoverageByRoomType'][scene['roomType']]['soilie']]:
            before=mesh_check_coverage([prior])['soilie']; after=mesh_check_coverage([row])['soilie']
            for key in group: group[key]+=after[key]-before[key]
        source_time=next(r for r in timing if str(r['seed'])==identifier.rsplit('-',1)[1])
        source_time['correctionSeconds']+=result['correction']['correctionSeconds']
    rows=[{'scene':{'id':r['sceneId'],'model':r['model'],'roomType':r['roomType']},'metrics':r['metrics']} for r in public['rows']]
    document['models']={m:model_summary(m,[r for r in rows if r['scene']['model']==m]) for m in LABELS}
    document['modelsByRoomType']=room_models(rows);document['comparisons']=compare(rows)
    document['timing']['soilieConditions']=timing_conditions(timing)
    public['cohortSha256']=digest(public['rows']);document['aiReview']['geometryLedgerSha256']=public['cohortSha256']
    document['cohort']['modelVersion']=read(Path(__file__).resolve().parents[2]/'package.json')['version']
    document['cohort']['finalPlacementImplementation']=records[0]['implementation']
    audit={'schemaVersion':1,'replayedRooms':len(records),'changedRooms':len(changed),
           'changedReviewedRooms':0,'implementation':records[0]['implementation'],
           'rooms':[{'sceneId':r['scene']['id'],'sourceSha256':r['source']['sha256'],
                     'moves':r['correction']['moves'],'seconds':r['correction']['correctionSeconds']} for r in records]}
    output.mkdir(parents=True,exist_ok=True)
    write_json(output/'corrected-rows.json',{'rows':changed})
    write_json(output/'audit.json',audit)
    for name,data in [('room-measurements.json',public),('support-measurements.json',supports),('box-support-measurements.json',boxes)]:
        write_json(directory/name,data)
    document['cohort']['finalPlacementAuditSha256']=digest(audit)
    document.pop('evidenceDigest');document['evidenceDigest']=digest(document)
    write_json(directory/'comparison.json',document)
    inputs=read(directory/'publication-inputs.json')
    inputs['roomMeasurementsSha256']=digest((directory/'room-measurements.json').read_bytes())
    inputs['finalPlacementAuditSha256']=digest(audit)
    write_json(directory/'publication-inputs.json',inputs)
    refine(directory)  # Rebuild all box aggregates and final evidence digest.
    return {'replayed':len(records),'changed':len(changed),'changedReviewedRooms':0}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('directory','replays','sources','protocol','cohort','output'): p.add_argument('--'+name,type=Path,required=True)
    print(refresh(**vars(p.parse_args())))
