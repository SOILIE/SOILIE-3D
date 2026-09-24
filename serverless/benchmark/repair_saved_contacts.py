"""Apply final floor/support maintenance to flagged saved scenes only.

Uses the existing model contact pass, never regenerates selection or horizontal
placement. Raw attempts and their generation timing remain immutable.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))


def main():
    from modules import render
    from serverless.benchmark.settle_saved import correction,clear_scene
    from serverless.benchmark.reobserve_contacts import flagged
    from serverless.benchmark.audit_support_corrections import audit_record
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    if args.input.resolve()==args.output.resolve():
        raise ValueError('A correction cannot overwrite its source')
    args.output.mkdir(parents=True,exist_ok=True)
    implementation={'observeOnly':False,'files':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
        ('modules/render.py','modules/support_settlement.py','serverless/benchmark/settle_saved.py',
         'serverless/benchmark/repair_saved_contacts.py','serverless/runtime-assets.json')}}
    rotations=render.load_rotations()
    for path in sorted(args.input.glob('*.json')):
        raw=path.read_bytes(); source=json.loads(raw)
        if not flagged(source):
            continue
        digest=hashlib.sha256(raw).hexdigest()
        target=args.output/path.name
        if target.exists():
            saved=json.loads(target.read_bytes())
            if saved['supportCorrection']['implementation']!=implementation:
                raise ValueError('Correction checkpoint implementation changed')
            audit_record(source,saved,digest)
            continue
        result,report=correction(source,rotations)
        result['supportCorrection']={**report,'sourceSha256':digest,'implementation':implementation,
            'originalGenerationSeconds':source['generationSeconds']}
        result['roomGeometryCorrection']={
            'floor':'finite rectangle bounded by the recorded interior wall faces',
            'reason':'repair sequential Blender dimension assignment; settle against final mounted surfaces'}
        audit_record(source,result,digest)
        temporary=target.with_suffix('.tmp')
        temporary.write_text(json.dumps(result,separators=(',',':')))
        temporary.replace(target)
        print(json.dumps({'source':path.name,'moves':report['moves'],'correctionSeconds':report['correctionSeconds']}),flush=True)
    clear_scene()


if __name__=='__main__':
    main()
