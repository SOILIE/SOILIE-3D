"""Re-read only the native room boundary without changing or generating a scene."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import bpy
from infinigen.core import tagging
from serverless.benchmark.infinigen_metadata import generated_instances
from serverless.benchmark.export_infinigen import floor_boundary, room_floor_object


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state',type=Path,required=True)
    parser.add_argument('--room-type',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    state=args.state.read_bytes()
    room_id,_=generated_instances(json.loads(state)['objs'],args.room_type)
    tagging.tag_system.load_tag(args.state.with_name('MaskTag.json'))
    room=floor_boundary(room_floor_object(room_id))
    checksum=hashlib.sha256()
    with open(bpy.data.filepath,'rb') as stream:
        for chunk in iter(lambda:stream.read(4*1024*1024),b''): checksum.update(chunk)
    record={'room':room,'stateSha256':hashlib.sha256(state).hexdigest(),
            'blendSha256':checksum.hexdigest(),
            'roomId':room_id,'furnitureModified':False}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as stream:
        json.dump(record,stream,separators=(',',':'))


if __name__=='__main__': main()
