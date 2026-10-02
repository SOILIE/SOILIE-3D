"""Diagnose saved contact outliers without moving furniture or changing scores.

Compare the frozen sparse probes with denser lower-surface probes and signed
height above the actual floor at each XY position. A downward ray can miss a
floor when a foot already penetrates it; its next hit is not a floating gap.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from itertools import product
import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from infinigen.core import tagging
from serverless.benchmark.infinigen_metadata import generated_instances, asset_label, vertically_supported
from serverless.benchmark.export_infinigen import evaluated_mesh, instance_geometry, floor_boundary, room_floor_object
from serverless.benchmark.mesh_support import sample_support, verify_floor_contact


def inspect(state, room_type, contacts_only=False):
    records=json.loads(state.read_bytes())['objs']
    room_id, instances=generated_instances(records,room_type)
    tagging.tag_system.load_tag(state.with_name('MaskTag.json'))
    floor=room_floor_object(room_id)
    room=floor_boundary(floor)
    def tree(points,faces):
        return BVHTree.FromPolygons([Vector(p) for p in points],faces)
    floor_tree=tree(*evaluated_mesh(floor))
    shell_tree=tree(*evaluated_mesh(bpy.data.objects[records[room_id]['obj']]))
    roots={record['obj'] for _,record in instances}
    geometries={}
    for identifier, record in instances:
        _,points,faces,_=instance_geometry(bpy.data.objects[record['obj']],roots)
        geometries[identifier]=(points,tree(points,faces))
    objects=[]
    for identifier,record in instances:
        if not vertically_supported(record):continue
        points,own=geometries[identifier]
        others=[(key,geo[1]) for key,geo in geometries.items() if key!=identifier]
        meta=[{'id':floor.name,'kind':'floor'},{'id':room_id,'kind':'architecture'}]+[{'id':key,'kind':'object'} for key,_ in others]
        trees=[floor_tree,shell_tree]+[bvh for _,bvh in others]
        sparse=sample_support(points,own,trees,room['floorZ'],support_metadata=meta)
        corrected=verify_floor_contact(sparse,own,floor_tree,floor.name)
        if contacts_only:
            objects.append({'id':identifier,'label':asset_label(record),'support':corrected})
            continue
        low,high=points.min(axis=0),points.max(axis=0)
        bottom=points[points[:,2]<=low[2]+1e-7]
        lower=points[points[:,2]<=low[2]+.3*(high[2]-low[2])]
        probes=[Vector(p) for p in lower[::max(1,len(lower)//512)][:512]]
        probes.extend(Vector(p) for p in bottom[::max(1,len(bottom)//128)][:128])
        for u,v in product(np.linspace(.01,.99,21),repeat=2):
            origin=Vector((low[0]+u*(high[0]-low[0]),low[1]+v*(high[1]-low[1]),low[2]-1))
            hit,*_=own.ray_cast(origin,Vector((0,0,1)),float(high[2]-low[2]+2))
            if hit is not None:probes.append(hit)
        signed=[]; dense=[]
        for point in probes:
            # Cast from above BOTH the furniture and floor. This detects feet
            # beneath the floor rather than reporting the slab below as support.
            origin=Vector((point.x,point.y,max(high[2],room['floorZ'])+2))
            hit,*_=floor_tree.ray_cast(origin,Vector((0,0,-1)),100)
            if hit is not None:
                signed.append({'point':list(point),'floorHit':list(hit),'gapM':float(point.z-hit.z)})
            for index,bvh in enumerate(trees):
                hit,*_=bvh.ray_cast(point+Vector((0,0,.001)),Vector((0,0,-1)),100)
                if hit is not None and hit.z<=point.z+.001:
                    dense.append({'gapM':max(0,float(point.z-hit.z)),'point':list(point),'hit':list(hit),'support':meta[index]})
        signed.sort(key=lambda x:x['gapM'])
        dense.sort(key=lambda x:x['gapM'])
        objects.append({'id':identifier,'label':asset_label(record),'sparse':sparse,'support':corrected,
            'meshMinZ':float(low[2]),'meshMaxZ':float(high[2]),'floorDatum':room['floorZ'],
            'denseProbeCount':len(probes),'denseMinimum':dense[0] if dense else None,
            'lowestSignedFloor':signed[0] if signed else None,
            'closestSignedFloor':min(signed,key=lambda x:abs(x['gapM'])) if signed else None,
            'probesBelowFloor':sum(p['gapM'] < -.001 for p in signed),
            'floorSurfaceIntersections':len(own.overlap(floor_tree)),
            'sourceRelations':record.get('relations',[])})
    with open(bpy.data.filepath,'rb') as stream:
        digest=hashlib.sha256()
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            digest.update(chunk)
        blend_sha=digest.hexdigest()
    return {'room':room,'roomId':room_id,'furnitureModified':False,'objects':objects,
            'blendSha256':blend_sha,'stateSha256':hashlib.sha256(state.read_bytes()).hexdigest(),
            'method':'mesh-ray-floor-contact-v3' if contacts_only else 'diagnostic-dense-probes-v1','measurementReplaced':False}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state',type=Path,required=True);p.add_argument('--room-type',required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--contacts-only',action='store_true')
    a=p.parse_args(sys.argv[sys.argv.index('--')+1:])
    result=inspect(a.state,a.room_type,a.contacts_only)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('x',encoding='utf-8') as stream:json.dump(result,stream,separators=(',',':'))
