"""Blender mesh-only support observations, shared by both source importers.

Sparse XY rays alone can hit a tabletop while missing every narrow leg. Include
actual lowest mesh vertices as probes so a grounded foot cannot be called a
floating object solely because it lies between the rays. This is still sampled
contact, not a physical stability test or proof that every support was found.
"""
from itertools import product

from mathutils import Vector

SAMPLING_VERSION = 2


def sample_support(vertices, own, supporting, floor_z, *, support_metadata=None):
    """Measure contact with the next real surface below, not height above floor.

Optional IDs/categories label the surface responsible for the minimum gap.
They do not change the probes or distances. A missing hit stays unmeasured;
legacy evidence without identities cannot be retrospectively classified.
"""
    if support_metadata is not None and len(support_metadata) != len(supporting):
        raise ValueError('One support identity is required per supporting mesh')
    if not len(vertices):
        raise ValueError("Support sampling needs real evaluated mesh vertices")
    low = [min(float(point[axis]) for point in vertices) for axis in range(3)]
    high = [max(float(point[axis]) for point in vertices) for axis in range(3)]
    # Real bottom vertices complement the grid without substituting box corners
    # for mesh geometry. The cap bounds work for densely tessellated flat bases.
    bottom = sorted({tuple(float(value) for value in point) for point in vertices
                     if float(point[2]) <= low[2]+1e-7})
    stride = max(1, len(bottom)//64)
    surfaces = [Vector(point) for point in bottom[::stride][:64]]
    for u, v in product((.05, .5, .95), repeat=2):
        origin = Vector((low[0]+u*(high[0]-low[0]), low[1]+v*(high[1]-low[1]), low[2]-1))
        hit, _, _, _ = own.ray_cast(origin, Vector((0, 0, 1)), high[2]-low[2]+2)
        if hit is not None:
            surfaces.append(hit)
    gaps = []
    for point in surfaces:
        for index, surface in enumerate(supporting):
            # A small tolerance lets exact contact survive floating-point noise.
            hit, _, _, _ = surface.ray_cast(point+Vector((0, 0, .001)), Vector((0, 0, -1)), 100)
            if hit is not None and hit.z <= point.z+.001:
                gaps.append((max(0, float(point.z-hit.z)), index))
    nearest = min(gaps) if gaps else None
    result = {"source":"mesh-ray-samples", "samplingVersion":SAMPLING_VERSION,
              "sampleCount":len(surfaces), "gapM":nearest[0] if nearest else None,
              "belowFloorM":max(0, float(floor_z-low[2]))}
    if support_metadata is not None and nearest is not None:
        identity = support_metadata[nearest[1]]
        if identity['kind'] not in {'floor', 'object', 'architecture'} or not identity['id']:
            raise ValueError('Invalid supporting surface identity')
        result.update(supportKind=identity['kind'], supportId=identity['id'])
    return result
