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

import numpy as np
from scipy.spatial import ConvexHull, QhullError

from serverless.benchmark.geometry import Box, furniture, room_regions

SEED = 20260913
PALETTE = ("#a3cff5", "#edbe96", "#bdb1eb", "#9fcbb0", "#edd590", "#b9c6d7")

# The two releases use different labels for the same broad furniture role.
# These families are used only to control a comparison confound; they do not
# rename model output or score whether a placement is good.
SEMANTIC_FAMILIES = {
    "double bed": "bed", "single bed": "bed", "kids bed": "bed",
    "night stand": "nightstand", "nightstand": "nightstand",
    "corner side table": "nightstand", "round end table": "nightstand",
    "sidetable desk": "nightstand",
    "wardrobe": "storage", "closet": "storage", "cupboard": "storage",
    "cabinet": "storage", "children cabinet": "storage", "dresser": "storage",
    "wine cabinet": "storage", "single cabinet": "storage", "kitchen cabinet": "storage",
    "pendant lamp": "lamp", "ceiling lamp": "lamp", "floor lamp": "lamp",
    "dining chair": "chair", "dressing chair": "chair", "office chair": "chair",
    "sofa chair": "chair", "armchair": "chair", "lounge chair": "chair",
    "stool": "chair",
    "coffee table": "table", "dining table": "table", "dressing table": "table",
    "console table": "table",
    "bookshelf": "shelf",
    "t v stand": "tv stand", "tv stand": "tv stand",
    "multi seat sofa": "sofa", "l shaped sofa": "sofa", "loveseat sofa": "sofa",
}
MIN_SEMANTIC_SIMILARITY = 2 / 3
MAX_DENSITY_DIFFERENCE = .25


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def semantic_signature(scene):
    """Return a duplicate-aware multiset of comparable furniture roles."""
    labels = []
    for item in furniture(scene):
        label = item["label"].lower().replace("_", " ").strip()
        labels.append(SEMANTIC_FAMILIES.get(label, label))
    return Counter(labels)


def semantic_similarity(first, second):
    """Fraction of instances with a matching role, for equal-size scenes."""
    left, right = semantic_signature(first), semantic_signature(second)
    total = max(sum(left.values()), sum(right.values()))
    return sum((left & right).values()) / total if total else 1.0


def comparable_inventory(first, second):
    """Require the same task scale and room-defining object count."""
    if first["roomType"] != second["roomType"]:
        return False
    left, right = semantic_signature(first), semantic_signature(second)
    if sum(left.values()) != sum(right.values()):
        return False
    anchors = {"bedroom": ("bed",), "living_room": ("sofa",)}.get(first["roomType"], ())
    return all(left[name] == right[name] for name in anchors)


def diagram(scene, highlight_ids=frozenset(), show_fronts=True):
    items = furniture(scene)
    boxes = [(item,Box(item)) for item in items]
    smallest_volume = min(box.volume for _, box in boxes)
    label_counts = Counter(item["label"] for item in items)
    label_seen = Counter()
    volume_labels = []
    for item, box in sorted(boxes, key=lambda pair: (pair[0]["label"], pair[0]["id"])):
        label_seen[item["label"]] += 1
        label = item["label"].replace("_", " ")
        if label_counts[item["label"]] > 1:
            label += f" {label_seen[item['label']]}"
        volume_labels.append(f"{label} {box.volume / smallest_volume:.1f}×")
    regions = room_regions(scene["room"])
    floor = scene["room"]["floorZ"]
    description = ("Plan, oblique, and three-dimensional bird’s-eye views of an indoor arrangement "
                   + ("with front-direction arrows and " if show_fronts else "with ")
                   + "relative bounding-box volumes")
    lines = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 720 1080" role="img" aria-label="{description}">',
             '<rect width="720" height="1080" fill="#f2f4f6"/>',
             '<defs><marker id="front-arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L0,6 L7,3 z" fill="#087f8c"/></marker></defs>',
             '<style>text{font-family:Arial,sans-serif;fill:#25384a;font-size:12px} .title{font-size:17px;font-weight:bold} polygon,line{stroke-linejoin:round}.front{stroke:#087f8c;stroke-width:2.4;marker-end:url(#front-arrow)}</style>']
    views = (
        ("plan", "Plan view", 34, lambda p: (p[0], -p[1])),
        ("oblique", "Oblique view", 354, lambda p: ((p[0]-p[1])*.70710678, (p[0]+p[1])*.35355339-p[2]*.8660254)),
        # A higher camera angle exposes more of the floor plane while retaining
        # height. It is still an orthographic neutral box view for both methods.
        ("birdseye", "3D bird’s-eye view", 674, lambda p: ((p[0]-p[1])*.70710678, (p[0]+p[1])*.61237244-p[2]*.5)),
    )
    for view, title, origin_y, project in views:
        label_boxes = []
        room3 = [[[x,y,floor] for x,y in region["polygon"]] for region in regions]
        projected = np.asarray([project(p) for ring in room3 for p in ring]
                               + [project(p) for _,box in boxes for p in box.points])
        low,high = projected.min(axis=0),projected.max(axis=0)
        size = high-low
        scale = min(610/max(size[0],1e-9),235/max(size[1],1e-9))
        offset = [(720-size[0]*scale)/2,origin_y+26+(235-size[1]*scale)/2]
        def point(p):
            p = project(p)
            return (offset[0]+(p[0]-low[0])*scale,offset[1]+(p[1]-low[1])*scale)
        def polygon(points,fill,opacity=1,stroke="#526679",width=1):
            coords = " ".join(f"{x:.2f},{y:.2f}" for x,y in map(point,points))
            return f'<polygon points="{coords}" fill="{fill}" fill-opacity="{opacity}" stroke="{stroke}" stroke-width="{width}"/>'
        def label_position(x, y, label):
            """Keep dense labels legible without moving the depicted geometry."""
            width, height = max(24, len(label) * 7), 15
            candidates = ((0, 0), (0, -18), (0, 18), (24, 0), (-24, 0),
                          (24, -18), (-24, -18), (24, 18), (-24, 18),
                          (0, -36), (0, 36), (42, 0), (-42, 0))
            y_min, y_max = origin_y + 18, origin_y + 276
            for dx, dy in candidates:
                px = min(696 - width / 2, max(24 + width / 2, x + dx))
                py = min(y_max - height / 2, max(y_min + height / 2, y + dy))
                box = (px - width / 2 - 3, py - height / 2 - 2,
                       px + width / 2 + 3, py + height / 2 + 2)
                if all(box[2] < old[0] or box[0] > old[2] or box[3] < old[1] or box[1] > old[3]
                       for old in label_boxes):
                    label_boxes.append(box)
                    return px, py
            label_boxes.append((x - width / 2, y - height / 2, x + width / 2, y + height / 2))
            return x, y
        lines.append(f'<text x="24" y="{origin_y}" class="title">{title}</text>')
        for region, exterior in zip(regions, room3):
            lines.append(polygon(exterior,"#ffffff",1,"#314d62",2.2))
            for ring in region.get("holes", []):
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
            if show_fronts:
                front = item.get("frontDirection")
                if front is None or len(front) != 2 or not all(math.isfinite(float(value)) for value in front):
                    raise ValueError(f"Object {item.get('id')} lacks a finite front direction")
                front = np.asarray(front,dtype=float)
                length = float(np.linalg.norm(front))
                if length <= 1e-9:
                    raise ValueError(f"Object {item.get('id')} has a zero front direction")
                front /= length
                footprint_size = max(float(np.ptp(box.points[:,0])),float(np.ptp(box.points[:,1])))
                arrow_end = center.copy()
                arrow_end[:2] += front * max(footprint_size*.38, .08)
                start_x,start_y = point(center)
                end_x,end_y = point(arrow_end)
                lines.append(f'<line class="front" x1="{start_x:.2f}" y1="{start_y:.2f}" x2="{end_x:.2f}" y2="{end_y:.2f}"/>')
            x,y = point(center)
            label_x, label_y = label_position(x, y, label)
            if abs(label_x - x) + abs(label_y - y) > 3:
                lines.append(f'<line x1="{x:.2f}" y1="{y:.2f}" x2="{label_x:.2f}" y2="{label_y:.2f}" stroke="#6b7d8c" stroke-width=".8"/>')
            lines.append(f'<text x="{label_x:.2f}" y="{label_y:.2f}" text-anchor="middle" paint-order="stroke" stroke="#ffffff" stroke-width="3" stroke-opacity=".9">{escape(label)}</text>')
    lines.extend(['<line x1="24" x2="696" y1="320" y2="320" stroke="#c2cbd3"/>',
                  '<line x1="24" x2="696" y1="640" y2="640" stroke="#c2cbd3"/>',
                  '<text x="24" y="1004">Relative bounding-box volumes, normalized to this room’s smallest object:</text>',
                  f'<text x="24" y="1022">{escape(" · ".join(volume_labels[:3]))}</text>',
                  f'<text x="24" y="1040">{escape(" · ".join(volume_labels[3:]))}</text>' if len(volume_labels) > 3 else '',
                  '<text x="24" y="1058">Judge the relative size differences among the objects present in each room.</text>',
                  ('<text x="24" y="1076">Values are box volume, not shape/aspect ratio. Cyan arrows mark source-defined fronts.</text>'
                   if show_fronts else '<text x="24" y="1076">Values are box volume, not shape/aspect ratio.</text>'),'</svg>'])
    return "\n".join(lines)


def review_metrics(row):
    """Publish only symmetric, per-room measurements available to both models."""
    values = row["metrics"]
    definitions = (
        ("meanWorstEnvelopeOverlapPct", "Mean worst object-envelope intrusion", "%", "lower"),
        ("meanOutsideFootprintPct", "Mean furniture footprint outside the room", "%", "lower"),
        ("connectedClearancePct", "Largest connected 0.6 m clearance area", "%", "context"),
        ("belowFloorCm", "Mean depth below the floor", "cm", "lower"),
        ("supportGapCm", "Mean sampled support gap", "cm", "lower"),
    )
    return [{"id": key, "label": label, "value": values.get(key), "unit": unit,
             "direction": direction,
             "availability": "measured" if values.get(key) is not None else "unavailable"}
            for key, label, unit, direction in definitions]


def symmetric_review_metrics(first, second):
    """Return only measurements that can be interpreted on both sides."""
    left, right = review_metrics(first), review_metrics(second)
    pairs = [(a, b) for a, b in zip(left, right)
             if a["id"] == b["id"] and a["availability"] == b["availability"] == "measured"]
    return [pair[0] for pair in pairs], [pair[1] for pair in pairs]


def select_pairs(rows, seed=SEED, limit=12, previous_protocols=(),
                 minimum_semantic_similarity=MIN_SEMANTIC_SIMILARITY,
                 baselines=("layoutgpt", "infinigen"),
                 maximum_density_difference=MAX_DENSITY_DIFFERENCE):
    grouped = defaultdict(list)
    for row in rows:
        scene = row["scene"]
        grouped[scene["model"]].append(row)
    selected = []
    for baseline in baselines:
        used_left, used_right = set(), set()
        for protocol in previous_protocols:
            conditions = {case['id']:case['comparisonCondition'] for case in protocol['cases']}
            for prior in protocol['stimulusEvidence']:
                if conditions[prior['caseId']] == baseline:
                    used_left.add(prior['soilieScene'])
                    used_right.add(prior['baselineScene'])
        candidates = []
        # Only matching variables are inspected. Overlap, containment and all
        # other quality results are deliberately absent from this ordering.
        for left in grouped["soilie"]:
            a = left["scene"]
            if a["id"] in used_left:
                continue
            for right in grouped[baseline]:
                b = right["scene"]
                if b["id"] in used_right or not comparable_inventory(a, b):
                    continue
                similarity = semantic_similarity(a, b)
                density_delta = abs(left["metrics"]["furnitureDensity"] - right["metrics"]["furnitureDensity"])
                if similarity < minimum_semantic_similarity or density_delta > maximum_density_difference:
                    continue
                tie = digest([seed, baseline, a["id"], b["id"]])
                candidates.append((-similarity, density_delta, tie, a, b))
        # A greedy best-edge pass can consume a flexible SOILIE scene that a
        # later baseline scene uniquely needs. Find a maximum-cardinality
        # bipartite matching first, using only the predeclared matching
        # variables, then take the best deterministic prefix of that matching.
        # This increases power without inspecting either model's quality score.
        adjacency = defaultdict(list)
        lookup = {}
        for candidate in candidates:
            negative_similarity, density_delta, tie, a, b = candidate
            adjacency[b["id"]].append(candidate)
            lookup[(b["id"], a["id"])] = candidate
        for right_id in adjacency:
            adjacency[right_id].sort(key=lambda row: row[:3])

        matched_left = {}
        matched_right = {}

        def augment(right_id, seen_left, seen_right):
            if right_id in seen_right:
                return False
            seen_right.add(right_id)
            for candidate in adjacency[right_id]:
                left_id = candidate[3]["id"]
                if left_id in seen_left:
                    continue
                seen_left.add(left_id)
                previous_right = matched_left.get(left_id)
                if previous_right is None or augment(previous_right, seen_left, seen_right):
                    matched_left[left_id] = right_id
                    matched_right[right_id] = left_id
                    return True
            return False

        right_order = sorted(adjacency, key=lambda scene_id: digest([seed, baseline, scene_id]))
        for right_id in right_order:
            augment(right_id, set(), set())

        matched = [lookup[(right_id, left_id)] for right_id, left_id in matched_right.items()]
        for negative_similarity, density_delta, _, a, b in sorted(matched)[:limit]:
            key = (a["roomType"], len(furniture(a)), -negative_similarity, density_delta)
            selected.append((baseline, key, a, b))
    return selected


def freeze(rows, output, protocol_path, seed=SEED, limit=12, previous_protocols=(),
           minimum_semantic_similarity=MIN_SEMANTIC_SIMILARITY, evidence_mode="visual_only",
           decision_scope="overall", reviewer_plan=None, reviewer_model=None, reasoning_effort=None,
           baselines=("layoutgpt", "infinigen"),
           maximum_density_difference=MAX_DENSITY_DIFFERENCE):
    if evidence_mode not in {"visual_only", "metrics_only", "combined"}:
        raise ValueError("Unknown evidence mode")
    if decision_scope not in {"overall", "focus_only"}:
        raise ValueError("Unknown decision scope")
    if decision_scope == "focus_only" and evidence_mode != "visual_only":
        raise ValueError("Focused dimension reviews must remain visual-only")
    cases, evidence = [], []
    output.mkdir(parents=True,exist_ok=True)
    for baseline,key,a,b in select_pairs(rows,seed,limit,previous_protocols,
                                         minimum_semantic_similarity,baselines,
                                         maximum_density_difference):
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
        row_by_id = {row["scene"]["id"]: row for row in rows}
        relation_metrics, comparison_metrics = symmetric_review_metrics(
            row_by_id[a["id"]], row_by_id[b["id"]])
        cases.append({"id":pair_id,"title":key[0].replace("_"," ").title()+" arrangement",
                      "relationImage":paths[0],"comparisonImage":paths[1],
                      "relationMetrics":relation_metrics,
                      "comparisonMetrics":comparison_metrics,
                      "comparisonCondition":baseline})
        evidence.append({"caseId":pair_id,"matchingStratum":list(key),"soilieScene":a["id"],"baselineScene":b["id"],
                         "semanticSimilarity":key[2],
                         "normalizedObjectFamilies":{"soilie":sorted(semantic_signature(a).elements()),
                                                     baseline:sorted(semantic_signature(b).elements())},
                         "soilieDigest":digest(a),"baselineDigest":digest(b)})
    reviewer_plan = list(reviewer_plan or [])
    reviewer_configuration = ({"model":reviewer_model,"reasoningEffort":reasoning_effort}
                              if reviewer_model and reasoning_effort else None)
    document = {"schemaVersion":2,"studyVersion":"spatial-evidence-"+digest({"cases":cases,"seed":seed,"mode":evidence_mode,
                                                                              "decisionScope":decision_scope,"reviewerPlan":reviewer_plan,
                                                                              "reviewerConfiguration":reviewer_configuration,
                                                                              "minimumSemanticSimilarity":minimum_semantic_similarity,
                                                                              "maximumDensityDifference":maximum_density_difference})[:20],
                "evidenceMode":evidence_mode,"decisionScope":decision_scope,
                "humanEnrollmentEnabled":False,"pilotCollectionEnabled":bool(cases),"cases":cases,
                "sampling":{"seed":seed,"maximumPairsPerBaseline":limit,"withoutReplacementWithinBaseline":True,
                            "baselines":list(baselines),
                            "qualityScoresUsed":False,
                            "matching":("maximum-cardinality one-to-one matching by room type, exact furniture and room-anchor counts, "
                                        "and a frozen minimum normalized object-family agreement"
                                        + f", with no more than {maximum_density_difference:g} summed-footprint-density difference"),
                            "minimumSemanticSimilarity":minimum_semantic_similarity,
                            "maximumFurnitureDensityDifference":maximum_density_difference,
                            "semanticFamilyPolicy":"Documented duplicate-aware aliases only; unmatched labels remain distinct",
                            "cohortScenes":dict(Counter(row["scene"]["model"] for row in rows)),
                            "cohortSceneIdsSha256":digest(sorted(row["scene"]["id"] for row in rows)),
                            "scope":"Completed scenes available in this frozen snapshot, not subsequent benchmark completions",
                            "visualEvidence":"Method-blind final oriented boxes shown as plan, oblique, and high-angle bird’s-eye views. A cyan arrow marks every source-defined object front. Each room is independently fitted to the same canvas; absolute cross-panel scale is not implied.",
                            "numericEvidence":"Only per-room measurements available for both rooms in a pair are shown. Unavailable values are omitted rather than displayed as zero or used as evidence for either side."},
                "stimulusEvidence":evidence}
    if reviewer_plan:
        document["reviewerPlan"] = reviewer_plan
    if reviewer_configuration:
        document["reviewerConfiguration"] = reviewer_configuration
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
    parser.add_argument("--minimum-semantic-similarity",type=float,default=MIN_SEMANTIC_SIMILARITY)
    parser.add_argument("--maximum-density-difference",type=float,default=MAX_DENSITY_DIFFERENCE)
    parser.add_argument("--evidence-mode",choices=("visual_only","metrics_only","combined"),default="visual_only")
    parser.add_argument("--decision-scope",choices=("overall","focus_only"),default="overall")
    parser.add_argument("--reviewer-plan",nargs="*",default=[])
    parser.add_argument("--reviewer-model")
    parser.add_argument("--reasoning-effort")
    parser.add_argument("--baselines",nargs="+",default=["layoutgpt","infinigen"],
                        choices=("layoutgpt","infinigen","infinigen_controlled"))
    parser.add_argument("--exclude-protocol",type=Path,nargs="*",default=[])
    args = parser.parse_args()
    if not 0 <= args.maximum_density_difference <= 1:
        parser.error("--maximum-density-difference must be between zero and one")
    result = freeze(json.loads(args.measurements.read_text())["rows"],args.output,args.protocol,
                    limit=args.limit,previous_protocols=[json.loads(path.read_text()) for path in args.exclude_protocol],
                    minimum_semantic_similarity=args.minimum_semantic_similarity,evidence_mode=args.evidence_mode,
                    decision_scope=args.decision_scope,reviewer_plan=args.reviewer_plan,
                    reviewer_model=args.reviewer_model,reasoning_effort=args.reasoning_effort,
                    baselines=args.baselines,
                    maximum_density_difference=args.maximum_density_difference)
    print(json.dumps({"studyVersion":result["studyVersion"],"cases":len(result["cases"])}))


if __name__ == "__main__":
    main()
