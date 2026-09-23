"""Finish vertical placement against mesh surfaces without changing sampling.

The overlap pass may place an item on the top of a support's bounding box.
A bed's headboard, for example, is higher than its mattress. This pass moves
the item straight down to the first actual surface. It never rescales objects
or invents a supporting plane outside the finite room floor.
"""
import math

# Ten micrometres is below the reported centimetre precision but above the
# accumulated float32 transform error observed in metre-scale imported meshes.
CONTACT_TOLERANCE_M = 1e-5
FIXED_CLASSES = frozenset({
    'window', 'opaque_window', 'blinds', 'curtain', 'wall', 'wooden_wall',
    'door', 'floor', 'ceiling', 'switch', 'power_outlet', 'clock', 'picture',
    'painting', 'mirror',
})


def _cross(a, b):
    return a[0] * b[1] - a[1] * b[0]


def _clip(first, second):
    """Intersect projected triangles; retain edge/edge contact candidates.

Vertex rays alone miss supports whose edges cross under the object without
any vertex lying inside the other triangle. Clipping includes those crossings.
"""
    output = [tuple(point[:2]) for point in first]
    orientation = _cross((second[1][0]-second[0][0], second[1][1]-second[0][1]),
                         (second[2][0]-second[0][0], second[2][1]-second[0][1]))
    if abs(orientation) < 1e-14:
        return []
    sign = 1 if orientation > 0 else -1
    for i in range(3):
        a, b = second[i], second[(i+1) % 3]
        def side(point):
            return sign * _cross((b[0]-a[0], b[1]-a[1]), (point[0]-a[0], point[1]-a[1]))
        source, output = output, []
        if not source:
            break
        previous = source[-1]
        previous_side = side(previous)
        for current in source:
            current_side = side(current)
            if (current_side >= 0) != (previous_side >= 0):
                weight = previous_side / (previous_side-current_side)
                output.append((previous[0]+weight*(current[0]-previous[0]),
                               previous[1]+weight*(current[1]-previous[1])))
            if current_side >= 0:
                output.append(current)
            previous, previous_side = current, current_side
    return output


def _height(triangle, point):
    a, b, c = triangle
    ab, ac = (b[0]-a[0], b[1]-a[1]), (c[0]-a[0], c[1]-a[1])
    ap = (point[0]-a[0], point[1]-a[1])
    area = _cross(ab, ac)
    return a[2] + _cross(ap, ac)/area*(b[2]-a[2]) + _cross(ab, ap)/area*(c[2]-a[2])


def triangle_drop(upper, lower):
    """First vertical contact between two triangles, or infinity if disjoint."""
    overlap = _clip(upper, lower)
    if not overlap:
        return math.inf
    gaps = [_height(upper, point)-_height(lower, point) for point in overlap]
    if max(gaps) < -CONTACT_TOLERANCE_M:
        return math.inf  # The other surface is wholly above this triangle.
    return max(0.0, min(gaps))


def evaluated_surface(obj):
    import bpy
    import numpy as np
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        mesh.calc_loop_triangles()
        vertices = [tuple(evaluated.matrix_world @ vertex.co) for vertex in mesh.vertices]
        faces = []
        for face in mesh.loop_triangles:
            a, b, c = [vertices[index] for index in face.vertices]
            if abs(_cross((b[0]-a[0], b[1]-a[1]), (c[0]-a[0], c[1]-a[1]))) > 1e-14:
                faces.append(tuple(face.vertices))
        if not faces:
            raise RuntimeError('Mesh has no finite horizontal supporting projection: ' + obj.name)
        triangles = np.asarray([tuple(vertices[index] for index in face) for face in faces])
        lows, highs = triangles.min(axis=1), triangles.max(axis=1)
        def build(indices):
            low, high = lows[indices].min(axis=0), highs[indices].max(axis=0)
            if len(indices) <= 16:
                return (low, high, indices, None)
            axis = int(np.argmax((high-low)[:2]))
            ordered = indices[np.argsort(lows[indices, axis]+highs[indices, axis], kind='stable')]
            middle = len(ordered)//2
            return (low, high, None, (build(ordered[:middle]), build(ordered[middle:])))
        return {
            'vertices': vertices,
            'triangles': triangles,
            # Blender's triangle BVH ignores coplanar containment. A 2D AABB
            # hierarchy supplies conservative candidates for exact clipping.
            'projection': build(np.arange(len(triangles))),
            'triangleLows': lows, 'triangleHighs': highs,
            'low': tuple(min(v[axis] for v in vertices) for axis in range(3)),
            'high': tuple(max(v[axis] for v in vertices) for axis in range(3)),
        }
    finally:
        evaluated.to_mesh_clear()


def surface_drop(upper, lower, limit=math.inf):
    """Exact vertical triangle contact after projected BVH broad-phase pruning."""
    import numpy as np
    if any(upper['high'][axis] < lower['low'][axis] or lower['high'][axis] < upper['low'][axis]
           for axis in (0, 1)):
        return math.inf
    result = limit
    pending = [(upper['projection'], lower['projection'])]
    while pending:
        first, second = pending.pop()
        if (np.any(first[1][:2] < second[0][:2]) or np.any(second[1][:2] < first[0][:2])
                or first[0][2]-second[1][2] > result
                or first[1][2]-second[0][2] < -CONTACT_TOLERANCE_M):
            continue
        if first[2] is not None and second[2] is not None:
            a, b = first[2], second[2]
            matches = np.all(upper['triangleHighs'][a, None, :2] >= lower['triangleLows'][b, :2], axis=2)
            matches &= np.all(lower['triangleHighs'][b, :2] >= upper['triangleLows'][a, None, :2], axis=2)
            matches &= upper['triangleLows'][a, None, 2]-lower['triangleHighs'][b, 2] <= result
            matches &= upper['triangleHighs'][a, None, 2]-lower['triangleLows'][b, 2] >= -CONTACT_TOLERANCE_M
            for i, j in zip(*np.nonzero(matches)):
                result = min(result, triangle_drop(upper['triangles'][a[i]], lower['triangles'][b[j]]))
                if result <= CONTACT_TOLERANCE_M:
                    return max(0.0, result)
        elif first[3] is not None and (second[3] is None or np.prod((first[1]-first[0])[:2]) >= np.prod((second[1]-second[0])[:2])):
            pending.extend((child, second) for child in first[3])
        else:
            pending.extend((first, child) for child in second[3])
    return result


def settle_objects(inputs):
    """Drop furniture in height order so supporting objects settle first.

Existing floor contacts stay untouched. Wall-mounted objects stay mounted.
Only Z translation changes; ordering and ties use stable instance IDs.
"""
    import bpy
    from mathutils import Vector
    bpy.context.view_layer.update()
    floor = bpy.data.objects.get('Floor')
    if floor is None:
        raise RuntimeError('Support settlement requires the actual floor mesh')
    floor_surface = evaluated_surface(floor)
    entries = [(name, value['blender_obj']) for name, value in inputs.items()
               if name.split('.')[0].lower() not in FIXED_CLASSES]
    def bottom(entry):
        return min((entry[1].matrix_world @ vertex.co).z for vertex in entry[1].data.vertices)
    entries.sort(key=lambda entry: (bottom(entry), entry[0]))
    # Mounted objects must not move, but their real surfaces can still support
    # another item. Eligibility for movement is not eligibility as a support.
    settled = [(name, value['blender_obj']) for name, value in inputs.items()
               if name.split('.')[0].lower() in FIXED_CLASSES]
    moves = []
    surfaces = {}
    for name, obj in entries:
        low = bottom((name, obj))
        floor_gap = low-floor_surface['high'][2]
        if abs(floor_gap) <= CONTACT_TOLERANCE_M:
            settled.append((name, obj))
            continue
        if floor_gap < -CONTACT_TOLERANCE_M:
            # The floor is a hard support surface. Correct penetration as well
            # as floating; subsequent higher objects settle onto this position.
            shift = -floor_gap+CONTACT_TOLERANCE_M/2
            obj.location.z += shift
            bpy.context.view_layer.update()
            moves.append({'id': name, 'dropM': -shift, 'reason': 'below_floor'})
            settled.append((name, obj))
            continue
        own = evaluated_surface(obj)
        # The real finite floor is a support, not an infinite Z=0 shortcut.
        drop = surface_drop(own, floor_surface)
        for base_name, base in settled:
            bounds = [base.matrix_world @ Vector(point) for point in base.bound_box]
            if any(max(v[axis] for v in bounds) < own['low'][axis]
                   or min(v[axis] for v in bounds) > own['high'][axis] for axis in (0, 1)):
                continue
            if base_name not in surfaces:
                surfaces[base_name] = evaluated_surface(base)
            drop = min(drop, surface_drop(own, surfaces[base_name], drop))
        if not math.isfinite(drop):
            raise RuntimeError('No supporting surface beneath ' + name)
        if drop > CONTACT_TOLERANCE_M:
            # Blender stores transforms in float32. Leave half the documented
            # ten-micrometre contact tolerance so rounding cannot push touching
            # open triangles through one another. Measurements retain this gap.
            obj.location.z -= max(0, drop-CONTACT_TOLERANCE_M/2)
            bpy.context.view_layer.update()
            moves.append({'id': name, 'dropM': drop})
        settled.append((name, obj))
    return moves
