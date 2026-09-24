"""Non-mutating mesh-contact measurements in metres, including edge crossings.

The reported gap is the downward translation to first contact with an actual
surface below. It is not distance to the floor when another object supports
the item, and not a stability or centre-of-mass test.
"""
import math

from modules.support_settlement import CONTACT_TOLERANCE_M, evaluated_surface, surface_drop


def rectangular_plane(surface):
    """Certify a finite two-triangle rectangle, not just its enclosing box."""
    triangles=surface['triangles']
    if len(triangles)!=2 or surface['low'][2]!=surface['high'][2]:
        return False
    first,second=({tuple(point[:2]) for point in triangle} for triangle in triangles)
    corners=first|second
    shared=first&second
    if len(first)!=3 or len(second)!=3 or len(corners)!=4 or len(shared)!=2:
        return False
    xs={point[0] for point in corners}; ys={point[1] for point in corners}
    a,b=sorted(shared)
    return len(xs)==len(ys)==2 and corners=={(x,y) for x in xs for y in ys} and a[0]!=b[0] and a[1]!=b[1]


def measure_contacts(objects, supports, floor_z):
    """Measure many final objects with shared immutable evaluated-mesh caches."""
    import bpy
    from mathutils import Vector
    graph=bpy.context.evaluated_depsgraph_get()
    candidates=[obj for obj in supports if obj.type=='MESH' and not obj.hide_render]
    bounds={obj.name:[obj.matrix_world@Vector(point) for point in obj.bound_box] for obj in candidates}
    surfaces={}
    def surface(obj):
        if obj.name not in surfaces:
            surfaces[obj.name]=evaluated_surface(obj)
        return surfaces[obj.name]
    floor=next((obj for obj in candidates if obj.name=='Floor'),None)
    floor_shape=surface(floor) if floor else None
    finite_rectangle=floor_shape is not None and rectangular_plane(floor_shape)
    result={}
    for identity,obj in objects:
        evaluated=obj.evaluated_get(graph)
        mesh=evaluated.to_mesh()
        try:
            low=min(float((evaluated.matrix_world@vertex.co).z) for vertex in mesh.vertices)
        finally:
            evaluated.to_mesh_clear()
        own_bounds=bounds[obj.name]
        on_floor=(finite_rectangle and abs(low-floor_z)<=CONTACT_TOLERANCE_M
                  and all(floor_shape['low'][axis]<=point[axis]<=floor_shape['high'][axis]
                          for point in own_bounds for axis in (0,1)))
        if on_floor:
            # This uses a real mesh extremum AND a proven finite floor surface.
            # No need to build a million-face hierarchy for a grounded object.
            gap,base=max(0.0,low-floor_z),floor
            method='mesh-extremum-floor-contact'
        else:
            own=surface(obj)
            gap,base=math.inf,None
            for other in candidates:
                if other==obj:
                    continue
                bound=bounds[other.name]
                if any(max(point[axis] for point in bound)<own['low'][axis] or
                       min(point[axis] for point in bound)>own['high'][axis] for axis in (0,1)):
                    continue
                candidate=surface_drop(own,surface(other),gap)
                if candidate<gap:
                    gap,base=candidate,other
                if gap<=CONTACT_TOLERANCE_M:
                    break
            method='mesh-vertical-contact'
        item={'source':method,'samplingVersion':3,'gapM':gap if math.isfinite(gap) else None,
              'belowFloorM':max(0.0,floor_z-low),'contactToleranceM':CONTACT_TOLERANCE_M}
        if base:
            item.update(supportId=base.name,supportKind='floor' if base==floor else
                        'architecture' if base.name.endswith(' Wall') else 'object')
        result[identity]=item
    return result
