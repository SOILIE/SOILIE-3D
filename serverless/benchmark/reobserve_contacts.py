"""Recheck saved support flags against triangles without rerunning generation.

Run inside Blender. Raw artifacts, placements and their timing are immutable;
derived observations bind their exact source checksum and observer checksum.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from modules import render
from modules.support_settlement import CONTACT_TOLERANCE_M, FIXED_CLASSES
from serverless.benchmark.settle_saved import correction, clear_scene


def flagged(row):
    if row.get('status')!='complete':
        return False
    return any(obj['label'] not in FIXED_CLASSES and
               any(obj.get('support',{}).get(key) is None or obj['support'][key]>CONTACT_TOLERANCE_M
                   for key in ('gapM','belowFloorM'))
               for obj in row['stages']['final']['objects'])


def audit_observation(source,derived):
    """Forbid every change except final support metadata and this audit stamp."""
    from copy import deepcopy
    before,after=deepcopy(source),deepcopy(derived)
    after.pop('contactObservation',None)
    for row in (before,after):
        for obj in row['stages']['final']['objects']:
            obj.pop('support',None)
    if before!=after:
        raise ValueError('Non-observation data changed during contact audit')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--all',action='store_true')
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    if args.input.resolve()==args.output.resolve():
        raise ValueError('Observations cannot overwrite raw evidence')
    args.output.mkdir(parents=True,exist_ok=True)
    hashes={str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest() for path in
        (Path(__file__),ROOT/'modules/support_settlement.py',ROOT/'serverless/benchmark/mesh_contact.py',
         ROOT/'serverless/benchmark/settle_saved.py',ROOT/'serverless/runtime-assets.json')}
    rotations=render.load_rotations()
    for path in sorted(args.input.glob('*.json')):
        raw=path.read_bytes(); row=json.loads(raw)
        if row.get('status')!='complete' or not (args.all or flagged(row)):
            continue
        target=args.output/path.name
        source_hash=hashlib.sha256(raw).hexdigest()
        if target.exists():
            saved=json.loads(target.read_text())
            if saved['contactObservation']!={'sourceSha256':source_hash,'files':hashes}:
                raise ValueError('Observation checkpoint changed: '+str(path))
            audit_observation(row,saved)
            continue
        result,_=correction(row,rotations,observe_only=True)
        result['contactObservation']={'sourceSha256':source_hash,'files':hashes}
        audit_observation(row,result)
        temporary=target.with_suffix('.tmp')
        temporary.write_text(json.dumps(result,separators=(',',':')))
        temporary.replace(target)
        print(json.dumps({'source':path.name,'remainingFlag':flagged(result),
              'contacts':[{'id':obj['id'],**obj.get('support',{})} for obj in result['stages']['final']['objects']]}),flush=True)
    clear_scene()


if __name__=='__main__':
    main()
