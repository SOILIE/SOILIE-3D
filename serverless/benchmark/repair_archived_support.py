"""Replay final settling from checksummed geometry, without resampling a room.

Run in Blender. Sources and unaffected transforms remain immutable. The output
records before/after controls and exact implementation hashes, so publication
can replace only verified changed scenes, preserving review provenance.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from modules import render, support_settlement
from serverless.benchmark.settle_saved import correction, clear_scene, checksum


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    args.output.mkdir(parents=True,exist_ok=True)
    rotations=render.load_rotations()
    for item in json.loads(args.input.read_bytes())['rows']:
        source=item['record']['scene']
        target=args.output/(source['id']+'.json')
        if target.exists():
            raise ValueError('Do not overwrite a completed replay')
        attempt={'stages':{'final':source}}
        with patch.object(support_settlement,'NON_SUPPORT_CLASSES',frozenset()):
            _,control=correction(attempt,rotations)
        result,report=correction(attempt,rotations)
        moved={m['id'] for m in report['moves']}
        final=result['stages']['final']
        for old,new in zip(source['objects'],final['objects']):
            assert old['id']==new['id']
            if old['id'] not in moved:
                # Preserve exact recorded placements, not restoration roundoff.
                new['corners']=deepcopy(old['corners'])
                new['transform']=deepcopy(old['transform'])
        assert final['room']==source['room']
        output={'scene':final,'source':item['source'], 'control':control,
                'correction':report,'implementation':{
                    'settlementSha256':checksum(ROOT/'modules/support_settlement.py'),
                    'replaySha256':checksum(Path(__file__)),
                    'assetManifestSha256':checksum(ROOT/'serverless/runtime-assets.json')}}
        target.write_text(json.dumps(output,separators=(',',':')),encoding='utf-8')
        print(json.dumps({'id':source['id'],'moves':report['moves'],'controlMoves':control['moves']}),flush=True)
    clear_scene()


if __name__=='__main__': main()
