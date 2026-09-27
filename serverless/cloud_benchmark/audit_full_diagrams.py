"""Verify all real rerun diagrams, not only a representative synthetic fixture."""
import argparse
from collections import defaultdict
import math
from pathlib import Path
import xml.etree.ElementTree as ET

from shapely.geometry import LineString, Point
from serverless.benchmark.stimuli import diagram
from serverless.cloud_benchmark.staged_pilot import load_frozen, read, digest, write_new


def audit(root):
    root = Path(root)
    protocol = load_frozen(root)
    scenes = {s['id']:s for s in read(Path(protocol['sourceRoot'])/'source-scenes.json')['scenes']}
    checked, arrows_checked = set(), 0
    for source in protocol['sceneProvenance'].values():
        url = source['image']
        if url in checked:
            continue
        scene = scenes[source['sceneId']]
        if digest(scene) != source['sceneSha256']:
            raise ValueError('Scene provenance changed')
        old = ET.fromstring(diagram(scene, numbered=True))
        new = ET.fromstring((root/protocol['sourceImages'][url]['file']).read_bytes())
        polygons = lambda r: [n.attrib for n in r.iter() if n.tag.endswith('polygon')]
        if polygons(old) != polygons(new):
            raise ValueError('Diagram changed original room or furniture geometry')
        keys = lambda r: [(n.attrib['data-key'],n.text) for n in r.iter() if 'data-key' in n.attrib]
        if keys(old) != keys(new):
            raise ValueError('Object keys changed')
        old_fronts = defaultdict(list)
        for group in old:
            if 'data-object' in group.attrib:
                for node in group:
                    if node.attrib.get('class') == 'front':
                        old_fronts[group.attrib['data-object']].append(node)
        reservations=[]
        layers=[g for g in new if 'data-front-layer' in g.attrib]
        if len(layers)!=3:
            raise ValueError('Missing front layers')
        for view, layer in enumerate(layers):
            arrows=[n for n in layer if n.attrib.get('class')=='front']
            if len(arrows)!=len(old_fronts):
                raise ValueError('Front count changed')
            for arrow in arrows:
                def ends(node):
                    return [(float(node.get('x'+i)),float(node.get('y'+i))) for i in ('1','2')]
                a,b=ends(arrow)
                c,d=ends(old_fronts[arrow.attrib['data-front-object']][view])
                if math.dist(a,b)<37.98:
                    raise ValueError('Short heading')
                if math.dist(a,c)>.015:
                    raise ValueError('Heading origin moved')
                v,w=[b[i]-a[i] for i in (0,1)],[d[i]-c[i] for i in (0,1)]
                cosine=sum(x*y for x,y in zip(v,w))/(math.hypot(*v)*math.hypot(*w))
                if cosine<.999:
                    raise ValueError('Functional heading changed')
                unit=[x/math.hypot(*v) for x in v]
                reservations.append(LineString([a,[b[i]+5*unit[i] for i in (0,1)]]).buffer(10))
                arrows_checked+=1
        for node in new.iter():
            if node.tag.endswith('circle') and node.get('r')=='11':
                key=Point(float(node.get('cx')),float(node.get('cy'))).buffer(11)
                if any(key.intersects(r) for r in reservations):
                    raise ValueError('Number overlaps heading: '+url)
        checked.add(url)
    report={'passed':True,'diagrams':len(checked),'arrows':arrows_checked,'geometryUnchanged':True,
            'frontDirectionsUnchanged':True,'numberArrowIntersections':0,'minimumArrowShaftPixels':38,
            'protocolSha256':digest(protocol)}
    write_new(root/'diagram-audit.json',report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    print(audit(parser.parse_args().root))
