"""Freeze seeded, matched pairs and neutral views without consulting quality scores.

These are geometry illustrations for the AI pilot, not replacements for any
model's renderer. Native object transforms and room boundaries remain untouched.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
from html import escape
import json
import math
from pathlib import Path
import random

import numpy as np
from scipy.spatial import ConvexHull, QhullError

from serverless.benchmark.geometry import Box, furniture, measure, stratum

SEED = 20260913
PALETTE = ("#a3cff5", "#edbe96", "#bdb1eb", "#9fcbb0", "#edd590", "#b9c6d7")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def diagram(scene, highlight_ids=frozenset()):
    items = furniture(scene)
    boxes = [(item,Box(item)) for item in items]
    room = scene["room"]["polygon"]
    floor = scene["room"]["floorZ"]
    lines = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 720 720" role="img" aria-label="Plan and oblique views of an indoor arrangement">',
             '<rect width="720" height="720" fill="#f2f4f6"/>',
             '<style>text{font-family:Arial,sans-serif;fill:#25384a;font-size:12px} .title{font-size:17px;font-weight:bold} polygon,line{stroke-linejoin:round}</style>']
    for view in ("plan", "oblique"):
        origin_y = 34 if view == "plan" else 385
        project = (lambda p: (p[0],-p[1])) if view == "plan" else (lambda p: ((p[0]-p[1])*.70710678, (p[0]+p[1])*.35355339-p[2]*.8660254))
        room3 = [[x,y,floor] for x,y in room]
        projected = np.asarray([project(p) for p in room3]+[project(p) for _,box in boxes for p in box.points])
        low,high = projected.min(axis=0),projected.max(axis=0)
        size = high-low
        scale = min(610/max(size[0],1e-9),265/max(size[1],1e-9))
        offset = [(720-size[0]*scale)/2,origin_y+26+(265-size[1]*scale)/2]
        def point(p):
            p = project(p)
            return (offset[0]+(p[0]-low[0])*scale,offset[1]+(p[1]-low[1])*scale)
        def polygon(points,fill,opacity=1,stroke="#526679",width=1):
            coords = " ".join(f"{x:.2f},{y:.2f}" for x,y in map(point,points))
            return f'<polygon points="{coords}" fill="{fill}" fill-opacity="{opacity}" stroke="{stroke}" stroke-width="{width}"/>'
        lines.append(f'<text x="24" y="{origin_y}" class="title">{"Plan view" if view=="plan" else "Oblique view"}</text>')
        lines.append(polygon(room3,"#ffffff",1,"#314d62",2.2))
        for ring in scene["room"].get("holes", []):
            lines.append(polygon([[x,y,floor] for x,y in ring],"#f2f4f6",1,"#314d62",2.2))
        for index,(item,box) in enumerate(sorted(boxes,key=lambda pair: float(pair[1].points.mean(axis=0)[0]+pair[1].points.mean(axis=0)[1]))):
            label = item["label"].replace("_"," ")
            color = PALETTE[int(hashlib.sha256(label.encode()).hexdigest()[:8],16)%len(PALETTE)]
            if item["id"] in highlight_ids:
                color = "#ed998c"
            if view == "plan":
                footprint = [[x,y,floor] for x,y in list(box.footprint.exterior.coords)[:-1]]
                lines.append(polygon(footprint,color,.65))
            else:
                # Coplanar hull triangles are combined into six box faces, so
                # no arbitrary corner-order convention is imposed on importers.
                planes = np.unique(np.round(box.hull.equations,7),axis=0)
                for plane in sorted(planes,key=lambda row: float(row[2])):
                    face = box.points[np.abs(box.points @ plane[:3]+plane[3]) < 1e-5]
                    if len(face) < 3:
                        continue
                    projected_face = np.asarray([point(p) for p in face])
                    try:
                        order = ConvexHull(projected_face).vertices
                    except QhullError:
                        continue # An edge-on face has zero projected area.
                    lines.append(polygon(face[order],color,.3))
            center = box.points.mean(axis=0)
            if view == "plan":
                center[2] = floor
            x,y = point(center)
            lines.append(f'<text x="{x:.2f}" y="{y:.2f}" text-anchor="middle" paint-order="stroke" stroke="#ffffff" stroke-width="3" stroke-opacity=".9">{escape(label)}</text>')
    lines.extend(['<line x1="24" x2="696" y1="350" y2="350" stroke="#c2cbd3"/>',
                  '<text x="24" y="704">Bounding boxes, not solid meshes. Room boundary shown as a dark outline.</text>','</svg>'])
    return "\n".join(lines)


def select_pairs(rows, seed=SEED, limit=12, previous_protocols=()):
    grouped = defaultdict(lambda:defaultdict(list))
    for row in rows:
        scene = row["scene"]
        # Only the matching variables are inspected. Overlap and other quality
        # results do not enter ordering, inclusion or random selection.
        key = stratum(scene,row["metrics"])
        grouped[scene["model"]][key].append(scene)
    selected = []
    for baseline in ("layoutgpt","infinigen"):
        used_left, used_right = set(), set()
        for protocol in previous_protocols:
            conditions = {case['id']:case['comparisonCondition'] for case in protocol['cases']}
            for prior in protocol['stimulusEvidence']:
                if conditions[prior['caseId']] == baseline:
                    used_left.add(prior['soilieScene'])
                    used_right.add(prior['baselineScene'])
        candidates = []
        randomizer = random.Random(f"{seed}:{baseline}")
        for key in sorted(set(grouped["soilie"]) & set(grouped[baseline])):
            left = sorted((scene for scene in grouped["soilie"][key] if scene['id'] not in used_left),key=lambda scene:scene["id"])
            right = sorted((scene for scene in grouped[baseline][key] if scene['id'] not in used_right),key=lambda scene:scene["id"])
            randomizer.shuffle(left)
            randomizer.shuffle(right)
            candidates.extend((key,a,b) for a,b in zip(left,right))
        randomizer.shuffle(candidates)
        selected.extend((baseline,*case) for case in candidates[:limit])
    return selected


def freeze(rows, output, protocol_path, seed=SEED, limit=12, previous_protocols=()):
    cases, evidence = [], []
    output.mkdir(parents=True,exist_ok=True)
    for baseline,key,a,b in select_pairs(rows,seed,limit,previous_protocols):
        pair_id = digest([a,b])[:20]
        paths = []
        for scene in (a,b):
            svg = diagram(scene)
            checksum = hashlib.sha256(svg.encode()).hexdigest()
            name = checksum[:24]+".svg"
            destination = output/name
            if destination.exists() and destination.read_text(encoding="utf-8") != svg:
                raise RuntimeError("Immutable stimulus filename collision")
            destination.write_text(svg,encoding="utf-8",newline="\n")
            paths.append("/benchmarks/stimuli/"+name)
        cases.append({"id":pair_id,"title":key[0].replace("_"," ").title()+" arrangement",
                      "relationImage":paths[0],"comparisonImage":paths[1],"comparisonCondition":baseline})
        evidence.append({"caseId":pair_id,"matchingStratum":list(key),"soilieScene":a["id"],"baselineScene":b["id"],
                         "soilieDigest":digest(a),"baselineDigest":digest(b)})
    document = {"schemaVersion":1,"studyVersion":"spatial-boxes-"+digest({"cases":cases,"seed":seed})[:20],
                "humanEnrollmentEnabled":False,"pilotCollectionEnabled":bool(cases),"cases":cases,
                "sampling":{"seed":seed,"maximumPairsPerBaseline":limit,"withoutReplacementWithinBaseline":True,
                            "qualityScoresUsed":False,"matching":"room type, exact furniture count, 0.25-wide summed footprint density bins",
                            "cohortScenes":dict(Counter(row["scene"]["model"] for row in rows)),
                            "cohortSceneIdsSha256":digest(sorted(row["scene"]["id"] for row in rows)),
                            "scope":"Completed scenes available in this frozen snapshot, not subsequent benchmark completions"},
                "stimulusEvidence":evidence}
    if previous_protocols:
        document['sampling']['excludedPriorStudyVersions'] = sorted(protocol['studyVersion'] for protocol in previous_protocols)
        document['sampling']['noSceneReuseAcrossWavesWithinBaseline'] = True
    if protocol_path.exists():
        previous = json.loads(protocol_path.read_text())
        if previous.get("pilotCollectionEnabled") and previous != document:
            raise RuntimeError("An active pilot is frozen. Use a new protocol path for new stimuli.")
    protocol_path.parent.mkdir(parents=True,exist_ok=True)
    protocol_path.write_text(json.dumps(document,indent=2),encoding="utf-8")
    return document


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--measurements",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--protocol",type=Path,required=True)
    parser.add_argument("--limit",type=int,default=12)
    parser.add_argument("--exclude-protocol",type=Path,nargs="*",default=[])
    args = parser.parse_args()
    result = freeze(json.loads(args.measurements.read_text())["rows"],args.output,args.protocol,
                    limit=args.limit,previous_protocols=[json.loads(path.read_text()) for path in args.exclude_protocol])
    print(json.dumps({"studyVersion":result["studyVersion"],"cases":len(result["cases"])}))


if __name__ == "__main__":
    main()
