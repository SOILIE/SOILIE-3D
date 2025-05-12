import os, sys
import bpy
import math, mathutils
from math import radians, degrees, pi
from mathutils import Vector
from itertools import combinations
import pickle
import json
import csv
import random
import copy

SIZE_MAP = {'xsmall': 0, 'small': 1, 'medium': 2, 'large': 3, 'xlarge': 4}
SYNONYMS = {'light':'lamp'}

##### HELPER FUNCTIONS

### FOR FILES

def generateFileName(filename):
    '''recursive function to auto-generate output filename
    and avoid overwriting'''
    splitname = filename.split('/')[:-1]
    for i,folder_name in enumerate(splitname):
        if i>0:
            folder_name = os.path.join(splitname[i-1],folder_name)
        if not os.path.exists(folder_name):
            os.mkdir(folder_name)
    if os.path.exists(filename):
        num = str(int(os.path.splitext(filename)[-2][-4:])+1)
        return generateFileName( \
            ''.join(os.path.splitext(filename)[:-1])[:-4]+ \
            '0'*(4-len(num))+num+'.png')
    return filename


def generate_next_output_folder():
    file_list = os.listdir('output')
    directories = [int(f) for f in file_list if f.isdigit() and os.path.isdir(os.path.join(os.getcwd(),'output', f))]
    if len(directories)==0:
        return '1'
    next_dir_name = str(max(directories)+1)
    return next_dir_name


def load_rotations():
    rotations = {}
    with open('assets/asset_rotations.csv', newline='') as csvfile:
        reader = csv.reader(csvfile)
        next(reader)  # Skip the header if there is one
        for row in reader:
            obj_name, asset_name, x, y, z = row
            if obj_name not in rotations:
                rotations[obj_name] = {}
            r = (int(x) if x!='' else 0,
                 int(y) if y!='' else 0,
                 int(z) if z!='' else 0)
            rotations[obj_name][asset_name] = r
    return rotations


### FOR GENERAL BLENDER OBJECTS

def selectObj(obj):
    '''helper function to select an object and set it as active'''
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def reset_object_rotation(obj):
    if obj.type in {'MESH', 'CURVE', 'SURFACE', 'FONT', 'META', 'ARMATURE'}:
        # Select the object
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        # Switch to object mode (safe check)
        bpy.ops.object.mode_set(mode='OBJECT')
        # Apply the rotation
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
        # Reset the rotation values
        obj.rotation_euler = (0, 0, 0)
        # Deselect the object
        obj.select_set(False)


def look_at(objA, locB, away=False):
    '''point objA towards objB'''
    locA = objA.matrix_world.to_translation()
    if away: direction = -locB - locA
    else:    direction = locB - locA
    # point objA's '-Z' and use its 'Y' as up
    rot_quat = direction.to_track_quat('-Z', 'Y')
    # assume we're using euler rotation
    objA.rotation_euler = rot_quat.to_euler()


def make_centroid_the_object_location(obj):
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.origin_set(type='ORIGIN_CENTER_OF_MASS', center='BOUNDS')


def get_centroid(obj):
    # Calculate the centroid from the bounding box
    local_corners = [Vector(corner) for corner in obj.bound_box]
    world_corners = [obj.matrix_world @ corner for corner in local_corners]
    centroid = sum(world_corners, Vector((0, 0, 0))) / len(world_corners)
    return centroid


def get_bbox_corners(obj):
    # Get the corners of the bounding box in world space
    bbox_corners = [obj.matrix_world @ Vector(point) for point in obj.bound_box]
    return bbox_corners


def interpolate_points(p1, p2, divisions=5):
    """ Interpolate points between two corners."""
    return (p1 + (p2 - p1) * i / divisions for i in range(divisions + 1))


def closest_object_to_point(point_vec, objects):
    closest_obj = None
    min_distance = float('inf')

    for obj in objects:
        if not hasattr(obj, 'bound_box'):
            continue

        bbox_corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]

        # Interpolate points along the edges of the bounding box
        interpolated_points = []
        for (corner1, corner2) in combinations(bbox_corners, 2):
            if sum(corner1[i] == corner2[i] for i in range(3)) == 2:  # Check if corners are on the same edge
                interpolated_points.extend(interpolate_points(corner1, corner2))

        # Evaluate all points (corners + interpolated) to find the minimum distance
        all_points = set(tuple(point) for point in bbox_corners + interpolated_points) # Use set to eliminate duplicates
        for point in all_points:
            distance = (Vector(point) - point_vec).length
            if distance < min_distance:
                min_distance = distance
                closest_obj = obj

    return closest_obj


def subtract_overlap(obj_a, obj_b):
    """Remove parts of object B that overlap with object A"""
    # Ensure both objects are mesh types
    if obj_a.type != 'MESH' or obj_b.type != 'MESH':
        raise TypeError("Both objects must be mesh objects.")

    # Add a Boolean modifier to obj_b
    boolean_modifier = obj_b.modifiers.new(name="Boolean", type='BOOLEAN')
    boolean_modifier.operation = 'DIFFERENCE'
    boolean_modifier.object = obj_a

    # Apply the modifier
    bpy.context.view_layer.objects.active = obj_b
    bpy.ops.object.modifier_apply(modifier=boolean_modifier.name)


### FOR INITIAL OBJECT PLACEMENT

def load_variables(inputs):
    # Separate camera, walls, and floor from other object inputs
    og_inputs = copy.deepcopy(inputs)
    params = {x:y for x,y in inputs.items() if x.isupper()}
    inputs = {x:y for x,y in inputs.items() if not x.isupper()}
    # Merge synonymous names
    inputs = {SYNONYMS[x] if x in SYNONYMS else x:y for x,y in inputs.items()}
    return og_inputs, inputs, params


def load_assets(inputs,params):
    rotations = load_rotations()
    valid_assets = [v2 for v1 in rotations.values() for v2 in v1.keys()]
    obj_files = [f for f in os.listdir('assets') if os.path.splitext(f)[1].lower()=='.obj' and f in valid_assets]
    # Load assets into blender
    for obj_name in inputs.keys():
        suffix = '.'+obj_name.split('.')[1] if '.' in obj_name else ''
        # Select random 3d asset file matching object name
        asset_name = random.choice([f for f in obj_files if '_'.join(f.split('_')[:-1])==obj_name.split('.')[0]])
        # Import object into the scene
        bpy.ops.object.select_all(action='DESELECT')
        bpy.ops.import_scene.obj(
            filepath=os.path.join('assets',asset_name),
            use_split_objects=False,
            use_split_groups=False,
            axis_forward='-Y',
            axis_up='Z')
        # Add object to input dictionary for easier access later
        asset_name = os.path.splitext(asset_name)[0]
        matching_objects = [obj.name for obj in bpy.data.objects if obj.name.startswith(asset_name)]
        if len(matching_objects)>1:
            asset_name = sorted(matching_objects, key=lambda s: int(re.search(r'(\d+)$', s).group(1)) if re.search(r'(\d+)$', s) else 0)[-1]
        obj = bpy.data.objects.get(asset_name)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.origin_set(type='ORIGIN_CENTER_OF_MASS', center='BOUNDS')
        obj.location = Vector((0,0,0))
        inputs[obj_name]['asset_name'] = asset_name
        inputs[obj_name]['blender_obj'] = obj
        bpy.context.view_layer.update()
    return inputs,params


def resize_objects_to_unit_scale():
    for obj in bpy.data.objects:
        if obj.type == 'MESH':
            max_dimension = max(obj.dimensions)
            scale_factor = 1 / max_dimension if max_dimension > 0 else 1
            obj.scale *= scale_factor
            bpy.context.view_layer.objects.active = obj
            bpy.ops.object.transform_apply(scale=True)


def transform_objects(inputs):
    # Transform: move, resize, and reorient each object
    rotations = load_rotations() # load "front" rotations
    current_rotations = {}
    for obj_name in inputs.keys():
        # Get the object name and handle
        bpy.ops.object.select_all(action='DESELECT')
        asset_name = inputs[obj_name]['asset_name']
        obj = inputs[obj_name]['blender_obj']
        # Create variables for xyz coords and object size
        xyz = inputs[obj_name]['coords']
        obj_size = inputs[obj_name]['size']['diameter']
        obj_size_cat = inputs[obj_name]['size']['category']
        # Move object to its xyz location
        make_centroid_the_object_location(obj)
        obj.location = Vector(xyz)
        bpy.context.view_layer.update()
        xyz = list(obj.location)
        # Resize object to correct scale
        obj.scale.x *= obj_size
        obj.scale.y *= obj_size
        obj.scale.z *= obj_size
        bpy.context.view_layer.update()
        # Rotate object so that "front" is aligned with X
        x_rot,y_rot,z_rot = rotations[obj_name.split('.')[0]][asset_name+'.obj']
        x_rot_rad = math.radians(x_rot)
        y_rot_rad = math.radians(y_rot)
        z_rot_rad = math.radians(z_rot)
        obj.rotation_euler[0] += x_rot_rad
        obj.rotation_euler[1] += y_rot_rad
        obj.rotation_euler[2] += z_rot_rad
        bpy.context.view_layer.update()
        reset_object_rotation(obj)
        current_rotations[asset_name] = 0
    # Resize blinds and curtains to window
    for obj_name,properties in inputs.items():
        obj = properties['blender_obj']
        if obj_name=='blinds':
            obj.dimensions = inputs['window']['blender_obj'].dimensions
        if obj_name=='curtain':
            # make curtains 5% larger than window
            obj.dimensions = inputs['window']['blender_obj'].dimensions*1.05
        bpy.context.view_layer.update()
    return current_rotations


### FOR WALLS AND FLOOR


def create_wall(name, x, y, z, height, width, depth):
    bpy.ops.object.select_all(action='DESELECT')
    bpy.ops.mesh.primitive_cube_add(size=2, location=(x, y, z + height / 2))
    wall = bpy.context.selected_objects[0]
    wall.name = name
    wall.scale.x = width / 2
    wall.scale.y = depth / 2
    wall.scale.z = height / 2
    return wall


def create_floor(name, x, y, width, depth):
    bpy.ops.object.select_all(action='DESELECT')
    bpy.ops.mesh.primitive_plane_add(size=2, location=(x, y, 0))
    floor = bpy.context.selected_objects[0]
    floor.name = name
    # Rescale and add some extra room around the objects
    floor.scale.x = width / 2 + 0.15
    floor.scale.y = depth / 2 + 0.15


def create_walls_and_floor(min_x, max_x, min_y, max_y, floor_level, wall_height):
    walls = [
        create_wall("Left Wall", (min_x + max_x) / 2, max_y, floor_level, wall_height, max_x - min_x, 0.2),
        create_wall("Right Wall", (min_x + max_x) / 2, min_y, floor_level, wall_height, max_x - min_x, 0.2),
        create_wall("Front Wall", max_x, (min_y + max_y) / 2, floor_level, wall_height, 0.2, max_y - min_y),
        create_wall("Back Wall", min_x, (min_y + max_y) / 2, floor_level, wall_height, 0.2, max_y - min_y)
    ]
    create_floor("Floor", (min_x + max_x) / 2, (min_y + max_y) / 2, max_x - min_x, max_y - min_y)
    return walls


def create_room_boundaries():
    """Calculate room boundaries based on current positions and sizes of objects."""
    scene_objects = bpy.context.scene.objects
    min_x, max_x, min_y, max_y, min_z, max_z = float('inf'), float('-inf'), float('inf'), float('-inf'), float('inf'), float('-inf')
    for obj in scene_objects:
        if obj.type == 'MESH':
            obj_matrix = obj.matrix_world
            for vertex in obj.data.vertices:
                vertex_global = obj_matrix @ vertex.co
                min_x, max_x = min(min_x, vertex_global.x), max(max_x, vertex_global.x)
                min_y, max_y = min(min_y, vertex_global.y), max(max_y, vertex_global.y)
                min_z, max_z = min(min_z, vertex_global.z), max(max_z, vertex_global.z)
    floor_level = min_z
    wall_height = 2
    return create_walls_and_floor(min_x, max_x, min_y, max_y, floor_level, wall_height)


def delete_closest_walls_to_camera(walls, count=2):
    cam = bpy.data.objects.get('Camera')
    if cam:
        sorted_walls = sorted(walls, key=lambda wall: (cam.location - wall.location).length)
        for wall in sorted_walls[:count]:
            bpy.data.objects.remove(wall, do_unlink=True)


def adjust_objects_to_floor():
    floor = bpy.data.objects.get('Floor')
    if floor:
        floor_height = floor.location.z
        for obj in bpy.data.objects:
            if obj.type == 'MESH':
                lowest_z = min((obj.matrix_world @ vertex.co).z for vertex in obj.data.vertices)
                obj.location.z -= (lowest_z - floor_height)


def fit_walls_around_floor():
    floor = bpy.data.objects['Floor']
    walls = [obj for obj in bpy.context.scene.objects if "Wall" in obj.name]
    floor_width = floor.dimensions.x
    floor_height = floor.dimensions.y
    floor_location = get_centroid(floor)
    # Calculate the correct position and scale for each wall
    for wall in walls:
        if 'left' in wall.name.lower():
            wall.location.y = floor_location.y - 0.1 + floor_height / 2
            wall.location.x = floor_location.x
            wall.dimensions.x = floor_width
        elif 'right' in wall.name.lower():
            wall.location.y = floor_location.y + 0.1 - floor_height / 2
            wall.location.x = floor_location.x
            wall.dimensions.x = floor_width
        elif 'front' in wall.name.lower():
            wall.location.x = floor_location.x - 0.1 + floor_width / 2
            wall.location.y = floor_location.y
            wall.dimensions.y = floor_height
        elif 'back' in wall.name.lower():
            wall.location.x = floor_location.x + 0.1 - floor_width / 2
            wall.location.y = floor_location.y
            wall.dimensions.y = floor_height
        bpy.context.view_layer.update()


def expand_floor(min=0.2,max=2):

    floor = bpy.data.objects['Floor']
    floor_centroid = get_centroid(floor)

    scene_objects = bpy.context.scene.objects
    objects = [obj for obj in scene_objects if "Wall" not in obj.name
        and "Floor" not in obj.name and obj.type == 'MESH']
    room_blocking_objects = ['sofa', 'couch', 'bookcase', 'bed', 'night_stand',
        'bathtub','cabinet','closet','cupboard','desk','fridge','microwave_oven',
        'sink','toilet','tv','tv_stand']

    # Determine the expansion for each side
    expansions = {
        'left': random.uniform(min, max),
        'right': random.uniform(min, max),
        'front': random.uniform(min, max),
        'back': random.uniform(min, max)
    }
    # Check for objects that block expansion on certain sides
    for obj in objects:
        if any(block_obj in obj.name.lower() for block_obj in room_blocking_objects):
            obj_centroid = get_centroid(obj)
            # Determine which side must be blocked (limited to 0) due to proximity
            if abs(obj_centroid.x - floor_centroid.x) < abs(obj_centroid.y - floor_centroid.y):
                if obj_centroid.y > floor_centroid.y:
                    expansions['right'] = 0
                else:
                    expansions['left'] = 0
            else:
                if obj_centroid.x > floor_centroid.x:
                    expansions['front'] = 0
                else:
                    expansions['back'] = 0

    # Ensure floor location is its centroid so below transformations apply correctly
    make_centroid_the_object_location(floor)
    bpy.context.view_layer.update()
    bpy.ops.object.mode_set(mode='OBJECT')

    # Apply expansion to the floor by adjusting the dimensions (in meters)
    floor.dimensions.x += expansions['front'] + expansions['back']
    bpy.context.view_layer.update()
    floor.dimensions.y += expansions['left'] + expansions['right']
    bpy.context.view_layer.update()
    floor.location.x += (expansions['front']/2) - (expansions['back']/2)
    bpy.context.view_layer.update()
    floor.location.y += (expansions['left']/2) - (expansions['right']/2)
    bpy.context.view_layer.update()

    fit_walls_around_floor()

    return expansions


### FOR OBJECT ROTATION ADJUSTMENT

def objects_back_against_nearest_wall(current_rotations):

    # Separate walls and target objects
    scene_objects = bpy.context.scene.objects
    walls = [obj for obj in scene_objects if "Wall" in obj.name]
    target_objects = [obj for obj in scene_objects if "Wall" not in obj.name
        and "Floor" not in obj.name and obj.type == 'MESH']

    for obj in target_objects:

        # Find the nearest wall to this object using centroids
        make_centroid_the_object_location(obj)
        nearest_wall = closest_object_to_point(obj.location, walls)
        make_centroid_the_object_location(nearest_wall)

        # Get the location and rotation of the object and the location of the nearest wall
        obj_loc_x, obj_loc_y = obj.location.x,obj.location.y
        wall_loc_x, wall_loc_y = nearest_wall.location.x,nearest_wall.location.y
        obj_rot = current_rotations[obj.name]

        # Now it is safe to deselect object and wall
        bpy.ops.object.select_all(action='DESELECT')

        # Find the correct rotation based on the nearest wall
        if 'front' in nearest_wall.name.lower():
            obj_desired_rot = 180
        elif 'back' in nearest_wall.name.lower():
            obj_desired_rot = 0
        elif 'left' in nearest_wall.name.lower():
            obj_desired_rot = 270
        elif 'right' in nearest_wall.name.lower():
            obj_desired_rot = 90
        obj1_required_rot = obj_desired_rot - obj_rot

        # Rotate the object to the nearest 90 degree increment
        obj.rotation_euler[2] += radians(obj1_required_rot)
        bpy.context.view_layer.update()
        current_rotations[obj.name] = obj_desired_rot

    # Make sure windows, curtains, and blinds are attached to the wall
    #adjust_windows_to_walls(chisel_walls=False)

    return current_rotations


def rotate_object_pairs(current_rotations):
    side_to_angle = {
        'front': 0,
        'left': 90,
        'back': 180,
        'right': 270
    }

    curr_object_names = [name[:-5] for name in current_rotations.keys()]
    curr_asset_names = list(current_rotations.keys())

    object_pairs = {
        'front-any': [['chair', 'table'], ['chair', 'coffee_table'], ['chair', 'dining_table'],
            ['sofa', 'coffee_table'], ['sofa', 'table'],['sofa_chair', 'coffee_table'],
            ['sofa_chair', 'table']],
        'front-front': [['chair', 'desk'], ['office_chair', 'desk'], ['sofa', 'tv'],
            ['sofa', 'tv_stand'], ['sofa_chair', 'tv'], ['sofa_chair', 'tv_stand']]
    }

    def normalize_angle(angle):
        return angle % 360

    for rule, pairs in object_pairs.items():
        for pair in pairs:
            obj_name1, obj_name2 = pair[0], pair[1]
            if obj_name1 not in curr_object_names or obj_name2 not in curr_object_names:
                continue # both objects need to exist in scene
            obj1 = bpy.data.objects[curr_asset_names[curr_object_names.index(obj_name1)]]
            obj2 = bpy.data.objects[curr_asset_names[curr_object_names.index(obj_name2)]]
            make_centroid_the_object_location(obj1)
            make_centroid_the_object_location(obj2)
            obj1_loc_x, obj1_loc_y = obj1.location.x,obj1.location.y
            obj2_loc_x, obj2_loc_y = obj2.location.x,obj2.location.y
            obj1_rot = current_rotations[obj1.name]
            obj2_rot = current_rotations[obj2.name]

            x_dist = abs(obj1_loc_x-obj2_loc_x)
            y_dist = abs(obj1_loc_y-obj2_loc_y)

            ## As long as all rules are 'front' for obj1, do this for all cases
            # Rotate obj1 to face obj2
            if x_dist<y_dist:
                obj1_desired_rot = 90 if obj1_loc_y<obj2_loc_y else 270
            else:
                obj1_desired_rot = 0 if obj1_loc_x<obj2_loc_x else 180
            obj1_required_rot = obj1_desired_rot - obj1_rot

            if rule == 'front-front':
                # Rotate obj2 to face obj1
                if x_dist<y_dist:
                    obj2_desired_rot = 90 if obj2_loc_y<obj1_loc_y else 270
                else:
                    obj2_desired_rot = 0 if obj2_loc_x<obj1_loc_x else 180
                obj2_required_rot = obj2_desired_rot - obj2_rot

            elif rule == 'front-any':
                obj2_desired_rot = current_rotations[obj2.name]
                obj2_required_rot = 0

            obj1.rotation_euler[2] += radians(obj1_required_rot)
            bpy.context.view_layer.update()
            current_rotations[obj1.name] = obj1_desired_rot
            obj2.rotation_euler[2] += radians(obj2_required_rot)
            bpy.context.view_layer.update()
            current_rotations[obj2.name] = obj2_desired_rot

    bpy.ops.object.select_all(action='DESELECT')

    return current_rotations


### FOR OBJECT OVERLAP ADJUSTMENT


def calculate_overlap_percentage(obj1, obj2):

    # Calculate the world-space bounding box for each object
    bbox1 = get_bbox_corners(obj1)
    bbox2 = get_bbox_corners(obj2)

    # Calculate min and max vectors for each bounding box
    min_vec1 = Vector([min([corner[i] for corner in bbox1]) for i in range(3)])
    max_vec1 = Vector([max([corner[i] for corner in bbox1]) for i in range(3)])
    min_vec2 = Vector([min([corner[i] for corner in bbox2]) for i in range(3)])
    max_vec2 = Vector([max([corner[i] for corner in bbox2]) for i in range(3)])

    # Determine the overlap region
    overlap_min = Vector([max(min_vec1[i], min_vec2[i]) for i in range(3)])
    overlap_max = Vector([min(max_vec1[i], max_vec2[i]) for i in range(3)])

    # Calculate the volume of the overlap region
    if all(overlap_max[i] > overlap_min[i] for i in range(3)):
        overlap_volume = (overlap_max.x - overlap_min.x) * (overlap_max.y - overlap_min.y) * (overlap_max.z - overlap_min.z)
    else:
        overlap_volume = 0

    # Calculate the volume of the first object's bounding box
    volume1 = (max_vec1.x - min_vec1.x) * (max_vec1.y - min_vec1.y) * (max_vec1.z - min_vec1.z)

    # Calculate the percentage overlap relative to the first object
    overlap_percentage = (overlap_volume / volume1) * 100 if volume1 != 0 else 0

    return overlap_percentage


def is_indoors(obj, shift_vector=Vector((0, 0, 0))):
    """Check if the object is surrounded by walls on all 4 sides."""
    # Get the bounding box corners
    bbox_corners = get_bbox_corners(obj)
    shifted_bbox_corners = [corner + shift_vector for corner in bbox_corners]

    # Calculate the axis-aligned bounding box extents
    min_x = min(corner.x for corner in shifted_bbox_corners)
    max_x = max(corner.x for corner in shifted_bbox_corners)
    min_y = min(corner.y for corner in shifted_bbox_corners)
    max_y = max(corner.y for corner in shifted_bbox_corners)
    min_z = min(corner.z for corner in shifted_bbox_corners)
    max_z = max(corner.z for corner in shifted_bbox_corners)

    # Find objects named 'Wall' in the scene
    walls = [obj for obj in bpy.context.scene.objects if "Wall" in obj.name]

    # Check for walls on all sides
    left_wall = right_wall = front_wall = back_wall = False

    for wall in walls:
        wall_bbox_corners = get_bbox_corners(wall)
        wall_min_x = min(corner.x for corner in wall_bbox_corners)
        wall_max_x = max(corner.x for corner in wall_bbox_corners)
        wall_min_y = min(corner.y for corner in wall_bbox_corners)
        wall_max_y = max(corner.y for corner in wall_bbox_corners)
        wall_min_z = min(corner.z for corner in wall_bbox_corners)
        wall_max_z = max(corner.z for corner in wall_bbox_corners)

        # Check for wall on the front side (+X)
        if not front_wall and wall_min_x >= max_x and wall_min_y <= max_y and wall_max_y >= min_y and wall_min_z <= max_z and wall_max_z >= min_z:
            front_wall = True
        # Check for wall on the back side (-X)
        if not back_wall and wall_max_x <= min_x and wall_min_y <= max_y and wall_max_y >= min_y and wall_min_z <= max_z and wall_max_z >= min_z:
            back_wall = True
        # Check for wall on the left side (+Y)
        if not left_wall and wall_min_y >= max_y and wall_min_x <= max_x and wall_max_x >= min_x and wall_min_z <= max_z and wall_max_z >= min_z:
            left_wall = True
        # Check for wall on the right side (-Y)
        if not right_wall and wall_max_y <= min_y and wall_min_x <= max_x and wall_max_x >= min_x and wall_min_z <= max_z and wall_max_z >= min_z:
            right_wall = True

    bpy.context.view_layer.update()
    return left_wall and right_wall and front_wall and back_wall


def move_obj_on_top(obj_to_move, base_obj):
    # Get world space bounding box corners
    move_bbox = get_bbox_corners(obj_to_move)
    base_bbox = get_bbox_corners(base_obj)

    # Calculate the minimum z-coordinate of the moving object and the maximum z-coordinate of the base object
    z_move_min = min(corner.z for corner in move_bbox)
    z_base_max = max(corner.z for corner in base_bbox)

    # Calculate the necessary vertical displacement
    vertical_displacement = z_base_max - z_move_min

    # Update the z-location of the object to move
    obj_to_move.location.z += vertical_displacement
    bpy.context.view_layer.update()


def move_obj_to_fit_on_top(obj_to_move, base_obj):
    # Get the extents of both bounding boxes
    base_bbox = get_bbox_corners(base_obj)
    move_bbox = get_bbox_corners(obj_to_move)

    min_base_x = min(corner.x for corner in base_bbox)
    max_base_x = max(corner.x for corner in base_bbox)
    min_base_y = min(corner.y for corner in base_bbox)
    max_base_y = max(corner.y for corner in base_bbox)

    min_move_x = min(corner.x for corner in move_bbox)
    max_move_x = max(corner.x for corner in move_bbox)
    min_move_y = min(corner.y for corner in move_bbox)
    max_move_y = max(corner.y for corner in move_bbox)

    # Calculate the minimum shift required in X direction
    if min_move_x < min_base_x:
        shift_x = min_base_x - min_move_x
    elif max_move_x > max_base_x:
        shift_x = max_base_x - max_move_x
    else:
        shift_x = 0

    # Calculate the minimum shift required in Y direction
    if min_move_y < min_base_y:
        shift_y = min_base_y - min_move_y
    elif max_move_y > max_base_y:
        shift_y = max_base_y - max_move_y
    else:
        shift_y = 0

    # Apply the horizontal shifts
    obj_to_move.location.x += shift_x
    obj_to_move.location.y += shift_y
    bpy.context.view_layer.update()


def move_objects_apart(objA, objB, directions_to_skip={}):
    '''Move objA away from objB in the smallest direction that is still surrounded by walls'''
    # Find walls so we know our boundaries
    walls = [obj for obj in bpy.context.scene.objects if "Wall" in obj.name]

    # Get bounding boxes of both objects
    bboxA = get_bbox_corners(objA)
    bboxB = get_bbox_corners(objB)

    # Calculate the extremities of both objects
    minA_x, maxA_x = min(corner.x for corner in bboxA), max(corner.x for corner in bboxA)
    minA_y, maxA_y = min(corner.y for corner in bboxA), max(corner.y for corner in bboxA)
    minB_x, maxB_x = min(corner.x for corner in bboxB), max(corner.x for corner in bboxB)
    minB_y, maxB_y = min(corner.y for corner in bboxB), max(corner.y for corner in bboxB)

    # Calculate the shifts required to separate the two bounding boxes horizontally
    shifts = {}
    shifts['front'] = maxB_x - minA_x + 0.01
    shifts['back'] = maxA_x - minB_x + 0.01
    shifts['left'] = maxB_y - minA_y + 0.01
    shifts['right'] = maxA_y - minB_y + 0.01

    # Sort shifts from smallest to largest
    shifts = dict(sorted(shifts.items(),key=lambda item: item[1]))

    # Try shifting in only one direction, then break out of the loop
    continueLoop = True
    while continueLoop:
        directions_attempted = 0
        for direction,shift in shifts.items():
            skip = objA.name in directions_to_skip and direction==directions_to_skip[objA.name]
            # Object must be indoors after the shift, otherwise try next smallest shift
            if direction=='front' and not skip and is_indoors(objA, shift_vector=Vector((shift,0,0))):
                objA.location.x += shift # shift forward
                # Skip opposite direction next time to avoid moving it back to its original location
                directions_to_skip[objA.name] = 'back'
                print('---| Move ' + objA.name, direction)
                continueLoop=False
                break
            elif direction=='back' and not skip and is_indoors(objA, shift_vector=Vector((-shift,0,0))):
                objA.location.x -= shift # shift back
                directions_to_skip[objA.name] = 'front'
                print('---| Move ' + objA.name, direction)
                continueLoop=False
                break
            elif direction=='left' and not skip and is_indoors(objA, shift_vector=Vector((0,shift,0))):
                objA.location.y += shift # shift left
                directions_to_skip[objA.name] = 'right'
                print('---| Move ' + objA.name, direction)
                continueLoop=False
                break
            elif direction=='right' and not skip and is_indoors(objA, shift_vector=Vector((0,-shift,0))):
                objA.location.y -= shift # shift right
                directions_to_skip[objA.name] = 'left'
                print('---| Move ' + objA.name, direction)
                continueLoop=False
                break
            directions_attempted+=1
            if directions_attempted==len(shifts.keys()):
                # All directions result in objA going outside of wall boundaries
                print('expand')
                expand_floor(0.2,0.3) # Expand out the walls again, but only slightly

    bpy.context.view_layer.update()


def separate_objects(objA, objB, sizeA='medium', sizeB='medium', directions_to_skip={}):

    # Define list of objects with surfaces
    surface_objs = ['bed','bookcase','cabinet','coffee_table','cupboard','desk','dining_table',
        'fridge','microwave_oven','night_stand','speaker','table','tv_stand']

    # Define list of objects that always sit on the floor regardless of size
    ground_objs = ['bathtub','bed','bin','bookcase','boots','cabinet','chair','closet',
        'coffee_table','cupboard','desk','dining_table','dust_bin','fridge','lamp','light',
        'night_stand','office_chair','sink','sofa','sofa_chair','stand','table','toilet',
        'trash_bin','trash_can','tv_stand']

    # Get object names from asset names
    objA_name = objA.name[:-5].lower()
    objB_name = objB.name[:-5].lower()

    # Determine whether either object is a surface object
    objA_has_surface = objA_name in surface_objs
    objB_has_surface = objB_name in surface_objs
    objA_is_grounded = objA_name in ground_objs
    objB_is_grounded = objB_name in ground_objs

    # Check if objects overlap
    overlap_percentage = calculate_overlap_percentage(objA, objB)
    if overlap_percentage == 0:
        return  # No overlap, no action needed

    print('---|',round(overlap_percentage,2))
    print('---|',directions_to_skip)

    # Mapping sizes to integers for comparison
    rankA = SIZE_MAP[sizeA]
    rankB = SIZE_MAP[sizeB]

    # Apply the rules based on size comparison
    if rankA < rankB and objB_has_surface and not objA_is_grounded:
        # Move objA on top of objB
        move_obj_on_top(objA, objB)
        move_obj_to_fit_on_top(objA, objB)
    elif rankB < rankA and objA_has_surface and not objB_is_grounded:
        # Move objB on top of objA
        move_obj_on_top(objB, objA)
        move_obj_to_fit_on_top(objB, objA)
    else:
        # Same size, grounded, or no surface
        # Move the smaller object away from the larger one
        if rankA <= rankB:
            move_objects_apart(objA, objB, directions_to_skip)
        else:
            move_objects_apart(objB, objA, directions_to_skip)
    #bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    bpy.context.view_layer.update()


def adjust_overlapping_objects(inputs):
    # Sort by size_cat, then by size - move largest objects first, then smaller ones
    sorted_objects = dict(
        sorted(inputs.items(),
        key=lambda item: (SIZE_MAP[item[1]['size']['category']])|int(item[1]['size']['diameter']*10000),
        reverse=True))
    # Store walls in a list
    walls = [obj for obj in bpy.context.scene.objects if "Wall" in obj.name]
    # Repeat operation until no overlaps remain across all objects
    locs_before = [Vector((0,0,0))]
    locs_after = [Vector((1,1,1))]
    directions_to_skip = {}
    while locs_before!=locs_after:
        # Keep adjusting until all objects stop moving
        locs_before = [obj.location for obj in bpy.context.scene.objects]
        pairs_checked = []
        for obj_nameA,valsA in sorted_objects.items():
            objA = valsA['blender_obj']
            obj_size_catA = valsA['size']['category']
            # Next, make sure object is not overlapping with any other object
            for obj_nameB,valsB in sorted_objects.items():
                if obj_nameA==obj_nameB or {obj_nameA,obj_nameB} in pairs_checked:
                    continue
                print(obj_nameA,'--',obj_nameB)
                objB = valsB['blender_obj']
                obj_size_catB = valsB['size']['category']
                separate_objects(objA, objB, obj_size_catA, obj_size_catB, directions_to_skip)
                pairs_checked.append({obj_nameA,obj_nameB})
        locs_after = [obj.location for obj in bpy.context.scene.objects]
        bpy.context.view_layer.update()


def adjust_windows_to_walls(chisel_walls=True):
    # Separate walls and target objects
    scene_objects = bpy.context.scene.objects
    walls = [obj for obj in scene_objects if "Wall" in obj.name]
    target_objects = [obj for obj in scene_objects
        if obj.name.lower().split('_')[0] in ['window','blinds','curtain']]

    for obj in target_objects:

        # Find the nearest wall to this object using centroids
        make_centroid_the_object_location(obj)
        nearest_wall = closest_object_to_point(obj.location, walls)
        make_centroid_the_object_location(nearest_wall)
        bpy.ops.object.select_all(action='DESELECT')

        # Make sure windows, curtains, and blinds are attached to the wall
        obj_name = obj.name.lower().split('_')[0]
        slight_shift = 0.1 if obj_name=='window' else 0.17
        if 'front' in nearest_wall.name.lower():
            obj.location.x = nearest_wall.location.x-slight_shift
        elif 'back' in nearest_wall.name.lower():
            obj.location.x = nearest_wall.location.x+slight_shift
        elif 'left' in nearest_wall.name.lower():
            obj.location.y = nearest_wall.location.y-slight_shift
        elif 'right' in nearest_wall.name.lower():
            obj.location.y = nearest_wall.location.y+slight_shift
        bpy.context.view_layer.update()
        obj.location.z = 1.3
        bpy.context.view_layer.update()
        if chisel_walls:
            subtract_overlap(obj, nearest_wall)
            bpy.context.view_layer.update()


### FOR RENDERING

def take_snapshot_from_above(filepath):
    min_coord = Vector((float('inf'), float('inf'), float('inf')))
    max_coord = Vector((-float('inf'), -float('inf'), -float('inf')))
    for obj in bpy.context.scene.objects:
        if obj.type == 'MESH':
            for corner in [obj.matrix_world @ Vector(bound) for bound in obj.bound_box]:
                min_coord = Vector(map(min, zip(min_coord, corner)))
                max_coord = Vector(map(max, zip(max_coord, corner)))
    scene_center = (min_coord + max_coord) / 2
    scene_size = max_coord - min_coord
    # Create and set up the camera
    camera_data = bpy.data.cameras.new(name='BirdsEye')
    camera_data.type = 'ORTHO'
    camera_data.ortho_scale = max(scene_size.x, scene_size.y) + 9
    camera_object = bpy.data.objects.new('BirdsEye', camera_data)
    bpy.context.collection.objects.link(camera_object)
    # Position and point the camera
    camera_height = scene_center.z + scene_size.z / 2 + max(scene_size.x, scene_size.y) + 6
    camera_object.location = (scene_center.x+6, scene_center.y-6, camera_height)
    camera_object.rotation_euler = (0.39,0.36, 0.1)
    # Save the current active camera
    original_camera = bpy.context.scene.camera
    # Set the new camera as the active camera
    bpy.context.scene.camera = camera_object
    # Set render settings and filepath and render the scene
    bpy.context.scene.render.image_settings.file_format = 'PNG'
    bpy.context.scene.render.filepath = filepath
    bpy.ops.render.render(write_still=True)
    # Reset the original camera, then unlink and delete the new camera
    bpy.context.scene.camera = original_camera
    bpy.data.objects.remove(camera_object, do_unlink=True)
    #return original_camera,camera_object


### FOR TRANSPARENCY AND OTHER EFFECTS


def add_light_to_lamps_and_windows(max_lum_lamp = 400, max_lum_window=75):
    # Add spot light (shining downwards) in location of lamps
    lamps = [o for o in bpy.context.scene.objects if 'lamp' in o.name.lower()]
    lights = []
    for lamp in lamps:
        location = Vector(list(lamp.location)[:2]+[lamp.location[2]*2+4])
        bpy.ops.object.light_add(type='SPOT', radius=0.35, align='WORLD',
                                 location=location, scale=(1, 1, 1))
        light_object = bpy.context.active_object
        light_object.name=lamp.name+'.Light'
        light_object.data.energy = max_lum_lamp
        bpy.context.view_layer.update()
        lights.append(light_object)

    windows = [o for o in bpy.context.scene.objects if 'window' in o.name.lower()]
    walls =[o for o in bpy.context.scene.objects if 'wall' in o.name.lower()]
    for window in windows:
        nearest_wall = closest_object_to_point(window.location, walls)
        slight_shift = 0.1
        location = window.location.copy()
        if 'front' in nearest_wall.name.lower():
            location.x -= slight_shift
            rotation = Vector((0,radians(60),0))
        elif 'back' in nearest_wall.name.lower():
            location.x += slight_shift
            rotation = Vector((0,radians(-60),0))
        elif 'left' in nearest_wall.name.lower():
            location.y -= slight_shift
            rotation = Vector((radians(-60),0,0))
        elif 'right' in nearest_wall.name.lower():
            location.y += slight_shift
            rotation = Vector((radians(60),0,0))
        location.z += window.dimensions.z/2
        bpy.ops.object.light_add(type='AREA', align='WORLD',location=location,rotation=rotation)
        light_object = bpy.context.active_object
        light_object.name=window.name+'.Light'
        #light_object.data.shape = 'RECTANGLE'
        new_size = window.dimensions.x if nearest_wall.name.lower()[:-5] in ['left','right'] else window.dimensions.y
        light_object.data.size = new_size
        #light_object.data.size_y = window.dimensions.y
        bpy.context.view_layer.update()
        light_object.data.color = Vector((1,1,0.6))
        bpy.context.view_layer.update()
        light_object.data.energy = max_lum_window
        bpy.context.view_layer.update()
        lights.append(light_object)
    return lights, max_lum_lamp, max_lum_window



def focus_walls_floor(focus_point,colors):
    scene_objects = bpy.context.scene.objects
    walls_floor = [obj for obj in scene_objects if "Wall" in obj.name]+[bpy.data.objects['Floor']]
    for obj in walls_floor:
        # Check if the object has a material, if not, create one
        mat = obj.data.materials[0] if obj.data.materials else bpy.data.materials.new(name=f"{obj.name}_material")
        obj.data.materials.append(mat) if not obj.data.materials else None
        # Use nodes to set up transparency
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        nodes.clear()
        # Create necessary nodes
        shader_node = nodes.new(type='ShaderNodeBsdfPrincipled')
        output_node = nodes.new(type='ShaderNodeOutputMaterial')
        geometry_node = nodes.new(type='ShaderNodeNewGeometry')
        vector_math_node = nodes.new(type='ShaderNodeVectorMath')
        map_range_node = nodes.new(type='ShaderNodeMapRange')
        color_ramp = nodes.new(type='ShaderNodeValToRGB')
        # Configure vector math to calculate distance
        vector_math_node.operation = 'DISTANCE'
        vector_math_node.inputs[1].default_value = focus_point
        # Configure the Map Range node
        map_range_node.inputs['From Min'].default_value = 0.0  # Start of range
        map_range_node.inputs['From Max'].default_value = 3.0  # End of range (3 meters)
        map_range_node.inputs['To Min'].default_value = 0.0  # Color Ramp start
        map_range_node.inputs['To Max'].default_value = 1.0  # Color Ramp end
        # Determine if wall or floor color should be used
        color = colors['wall'] if 'Wall' in obj.name else colors['floor']
        # Set up the color ramp
        # Adjust this ramp to control the transparency based on mapped distance
        color_ramp.color_ramp.elements.new(0.3)
        color_ramp.color_ramp.elements.new(0.7)
        color_ramp.color_ramp.elements[0].position = 0.0
        color_ramp.color_ramp.elements[0].color = tuple(color)+tuple([1])  # Opaque at center
        color_ramp.color_ramp.elements[1].position = 0.3
        color_ramp.color_ramp.elements[1].color = tuple(color)+tuple([0.8]) # Semi-transparent near the middle
        color_ramp.color_ramp.elements[2].position = 0.7
        color_ramp.color_ramp.elements[2].color = tuple(color)+tuple([0.1])
        color_ramp.color_ramp.elements[3].position = 1.0
        color_ramp.color_ramp.elements[3].color = tuple(color)+tuple([0]) # Transparent far from center
        # Position nodes for clarity
        geometry_node.location = (-400, 0)
        vector_math_node.location = (-200, 0)
        map_range_node.location = (0, 0)
        color_ramp.location = (200, 0)
        shader_node.location = (400, 0)
        output_node.location = (600, 0)
        # Connect nodes
        links.new(geometry_node.outputs['Position'], vector_math_node.inputs[0])
        links.new(vector_math_node.outputs['Value'], map_range_node.inputs['Value'])
        links.new(map_range_node.outputs['Result'], color_ramp.inputs['Fac'])
        links.new(color_ramp.outputs['Alpha'], shader_node.inputs['Alpha'])
        links.new(shader_node.outputs['BSDF'], output_node.inputs['Surface'])
        # Material settings for Eevee
        mat.blend_method = 'HASHED'
        mat.shadow_method = 'HASHED'
        mat.node_tree.nodes.update()


def change_imagination_focus(current,assets_in_order,curr_frame,total_frames,colors,lights,max_lum_lamp,max_lum_window):
    # Determine 8-bit depth values
    curr_index = assets_in_order.index(current)
    t = curr_frame/total_frames # Used below to adjust focus point and object alpha
    depths = [i+8-curr_index if i<curr_index else 0 if i==curr_index else -1 for i in range(len(assets_in_order))]
    # Determine alpha values
    depth_alpha_map = {2:0.03,3:0.05,4:0.1,5:0.2,6:0.35,7:0.5,0:1,-1:0,1:0.03}
    aord = list(depth_alpha_map.values())[::-1]
    alphas = [depth_alpha_map[d] for d in depths]
    alphas = [a + t * (aord[(aord.index(a)+1)%len(aord)] - aord[aord.index(a)]) if a!=0 or i==alphas.index(1)+1 else 0 for i,a in enumerate(alphas)]
    # Get the current focus object
    focus_obj = bpy.data.objects[os.path.splitext(current)[0]]
    focus_obj_loc = get_centroid(focus_obj)
    # Get the next focus object
    next_obj_name = assets_in_order[(curr_index+1)%len(assets_in_order)]
    next_obj = bpy.data.objects[os.path.splitext(next_obj_name)[0]]
    next_obj_loc = get_centroid(next_obj)
    # Calculate focus point location
    ## Curr_frame/total_frames (t) = the fraction of the way between
    ## the current focus object and the next one
    focus_point = focus_obj_loc + t * (next_obj_loc - focus_obj_loc)
    # Make walls and floor most opaque around the focus object
    focus_walls_floor(focus_point,colors)
    # Point camera at focus point
    camera = bpy.data.objects['Camera']
    look_at(camera,focus_point)
    # Re-apply colors to all objects
    apply_colors_to_objects(colors)
    for i, obj_name in enumerate(assets_in_order):
        # Ensure your object is selected and active
        obj = bpy.data.objects[os.path.splitext(obj_name)[0]]
        # Check if the object has a material, if not, create one
        mat = obj.data.materials[0] if obj.data.materials else bpy.data.materials.new(name=f"{obj.name}_material")
        obj.data.materials.append(mat) if not obj.data.materials else None
        # Use nodes to set up transparency
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        # Clear existing nodes
        nodes.clear()
        # Create a principled BSDF shader node
        shader_node = nodes.new(type='ShaderNodeBsdfPrincipled')
        shader_node.location = (0,0)
        # Create an output node
        output_node = nodes.new(type='ShaderNodeOutputMaterial')
        output_node.location = (400,0)
        # Connect the shader to the output
        links.new(shader_node.outputs['BSDF'], output_node.inputs['Surface'])
        # Assign color
        color = colors[obj_name]
        # Adjust the alpha value to change opacity, where 0 is fully transparent and 1 is fully
        ## Reasonable mapping from octree depth (8-bit effect) to opacity value:
        ## 3: 0.03, 4: 0.05, 5: 0.1, 6: 0.2, 7: 0.35, 8: 0.5
        ## NOTE: Adjusted for partial transparency for frames in between two objects
        shader_node.inputs['Alpha'].default_value = alphas[i] # Set to desired transparency level
        shader_node.inputs['Base Color'].default_value = Vector(list(color) + [alphas[i]]) # RGBA
        obj.color = Vector(list(color) + [alphas[i]]) # RGBA
        # Set blend mode for transparency in Eevee
        mat.blend_method = 'BLEND'
        mat.shadow_method = 'HASHED'
        # Adjust lamp lighting and window
        if obj_name.lower()[:4]=='lamp' or obj_name.lower()[:4]=='window':
            light_object = [l for l in lights if obj_name in l.name]
            if light_object:
                light_object = light_object[0]
                max_lum = max_lum_lamp if 'lamp' in obj_name.lower() else max_lum_window
                lum_fac = (1-(abs(i-curr_index)*0.25)) if abs(i-curr_index)*0.25<1 else 0
                new_lum = max_lum * lum_fac
                light_object.data.energy = new_lum
        ##### 8-BIT EFFECT (CURRENTLY TURNED ON)
        if depths[i]>0: # and abs(curr_index-i)>2 and False: # UNCOMMENT TO TURN OFF 8-BIT
            # Add a Remesh modifier
            remesh_mod = obj.modifiers.new(name='Remesh', type='REMESH')
            # Configure the Remesh modifier
            remesh_mod.mode = 'BLOCKS'  # Creates blocky features
            # Adjust depth for more or less detail
            remesh_mod.octree_depth = depths[i] # use between 3-8 and turn off for max resolution (most recent case)
        else: # If this is the focus object or a 'future' object
            # Remove the blocky features to ensure object is full resolution
            remesh_mod = obj.modifiers.get('Remesh')
            if remesh_mod:
                obj.modifiers.remove(remesh_mod)
        mat.node_tree.nodes.update()


def apply_colors_to_objects(colors):
    scene_objects = bpy.context.scene.objects
    walls_colors = {obj.name:colors['wall'] for obj in scene_objects if "wall" in obj.name.lower()}
    floor_colors = {obj.name:colors['floor'] for obj in scene_objects if "floor" in obj.name.lower()}
    colors.update(walls_colors)
    colors.update(floor_colors)
    # Loop over all objects in the scene
    for obj in bpy.context.scene.objects:
        if obj.name not in colors:
            continue
        color = colors[obj.name]
        if not obj.data.materials:
            mat = bpy.data.materials.new(name=obj.name + "_Material")
            obj.data.materials.append(mat)
        else:
            mat = obj.data.materials[0]
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        shader = nodes.get('Principled BSDF')
        if shader is None:
            shader = nodes.new('ShaderNodeBsdfPrincipled')
        shader.inputs['Base Color'].default_value = Vector((color[0], color[1], color[2], 1))
        obj.color = Vector((color[0], color[1], color[2], 1))
        mat.node_tree.nodes.update()
        bpy.context.view_layer.update()


### MAIN CODE

## Load an input
'''
with open(f'output/11/00_blender_inputs.json','rb') as f:
    inputs = json.load(f)
'''

## MAIN FUNCTION

def visualize(inputs):
    '''Create 3D renderings of the objects at the given inputs
    inputs: a dictionary of final object locations
    output: PNG file of the 3D scene in the output folder'''

    bpy.context.view_layer.update()
    og_inputs, inputs, params = load_variables(inputs)

    #-----------------------
    # Place and adjust objects, and create a room around them
    inputs,params = load_assets(inputs,params)

    ## Get object/wall/floor colors into one dictionary and normalize to 0-1 scale
    colors = {
        vals['asset_name'] if 'asset_name' in vals else obj_name.lower():Vector(vals['color'])/255
        for obj_name,vals in dict(inputs,**params).items()}

    # Make all objects uniform size
    resize_objects_to_unit_scale()

    # Place loaded assets to their correct location, rotation, and size
    current_rotations = transform_objects(inputs)

    # Create floor and walls, and adjust objects to the floor
    walls = create_room_boundaries()
    fit_walls_around_floor()
    adjust_objects_to_floor()

    # Expand out the floor and walls randomly in each direction
    expansions = expand_floor()

    # Rotate objects to face the middle of the room
    current_rotations = objects_back_against_nearest_wall(current_rotations)

    # Rotate pairs of objects to face each other correctly
    current_rotations = rotate_object_pairs(current_rotations)

    # Translate overlapping objects
    adjust_overlapping_objects(inputs)

    # Do a final adjustment of windows, blinds, and curtain to walls
    adjust_windows_to_walls()

    # Add light for any lamps, if present
    lights, max_lum_lamp, max_lum_window = add_light_to_lamps_and_windows()

    #-----------------------
    # Imagination Sequence

    output = [] # Create a list for output

    ## Set up rendering engine and camera
    bpy.context.scene.render.engine = 'BLENDER_EEVEE'
    bpy.data.cameras['Camera'].type = 'ORTHO'

    # Apply colors to walls, floor, and objects
    apply_colors_to_objects(colors)
    bpy.context.view_layer.update()

    # Determine output folder
    curr_out_folder = generate_next_output_folder()

    # Render a bird's eye view before transparency and 8-bit effects applied
    take_snapshot_from_above(f'output/{curr_out_folder}/01_birds_eye_view.png')

    # Save original inputs as JSON
    json.dump(og_inputs,open(f'output/{curr_out_folder}/00_blender_inputs.json','w'))

    ## Add walls and floor data to output
    # This is done before we delete the closest walls to camera so all 4 walls are included
    scene_objects = bpy.context.scene.objects
    walls_floor = [obj for obj in scene_objects if "Wall" in obj.name or "Floor" in obj.name]
    for obj in walls_floor:
        wall_or_floor = 'WALL' if 'Wall' in obj.name else 'FLOOR'
        lx,ly,lz,rx,ry,rz,sx,sy,sz=[
            obj.location.x,obj.location.y,obj.location.z,
            degrees(obj.rotation_euler.x),
            degrees(obj.rotation_euler.y),
            degrees(obj.rotation_euler.z),
            obj.dimensions.x,obj.dimensions.y,obj.dimensions.z]
        output.append({
        'obj_name':'_'.join(obj.name.upper().split()),
        'asset_name':'_'.join(obj.name.upper().split()),
        'color_r':params[wall_or_floor]['color'][0],
        'color_g':params[wall_or_floor]['color'][1],
        'color_b':params[wall_or_floor]['color'][2],
        'og_x':lx,'og_y':ly,'og_z':lz,
        'loc_x':lx,'loc_y':ly,'loc_z':lz,
        'rot_x':rx,'rot_y':ry,'rot_z':rz,
        'dim_x':sx,'dim_y':sy,'dim_z':sz,
        'sizecat':'xlarge'})

    ## Add camera data to output
    camera = bpy.data.objects['Camera']
    output.append({
        'obj_name':'CAMERA',
        'asset_name':'CAMERA',
        'color_r':255,
        'color_g':255,
        'color_b':255,
        'og_x':camera.location.x,'og_y':camera.location.y,'og_z':camera.location.z,
        'loc_x':camera.location.x,'loc_y':camera.location.y,'loc_z':camera.location.z,
        'rot_x':degrees(camera.rotation_euler.x),
        'rot_y':degrees(camera.rotation_euler.y),
        'rot_z':degrees(camera.rotation_euler.z),
        'dim_x':0,'dim_y':0,'dim_z':0,
        'sizecat':''})

    ## Delete walls closest to the camera for better visibility inside the room
    delete_closest_walls_to_camera(walls)

    ## Randomly shuffle objects to simulate different order of "imagining" items
    objects_in_order = [obj_name for obj_name in inputs.keys()]
    random.shuffle(objects_in_order)
    assets_in_order = [inputs[obj_name]['asset_name'] for obj_name in objects_in_order]

    filename = '-'.join([o.split('.')[0] for o in objects_in_order])

    fpo = 4 # frames per object
    for obj_i,asset_name in enumerate(assets_in_order):
        obj_name = objects_in_order[obj_i]
        for i in range(fpo):
            # Change focus by i/fpo of the way to the next object
            change_imagination_focus(asset_name,assets_in_order,i,fpo,colors,lights,max_lum_lamp,max_lum_window)
            # If it's the first frame of final object
            if obj_i==len(assets_in_order)-1 and i==0:
                # Render a final bird's eye view of the imagined scene
                take_snapshot_from_above(f'output/{curr_out_folder}/02_birds_eye_view.png')
            # Render/save snapshots for gif animation
            filepath = generateFileName(f'output/{curr_out_folder}/{filename}_0001.png')
            bpy.data.scenes['Scene'].render.filepath = filepath
            bpy.ops.render.render(write_still=True)
        # Update final object locations, rotations, and sizes for output
        obj = bpy.data.objects[os.path.splitext(asset_name)[0]]
        lx,ly,lz,rx,ry,rz,sx,sy,sz=[
            obj.location.x,obj.location.y,obj.location.z,
            degrees(obj.rotation_euler.x),
            degrees(obj.rotation_euler.y),
            degrees(obj.rotation_euler.z),
            obj.dimensions.x,obj.dimensions.y,obj.dimensions.z]
        sizecat = inputs[obj_name]['size']['category']
        output.append({
            'obj_name':obj_name,
            'asset_name':asset_name,
            'color_r':inputs[obj_name]['color'][0],
            'color_g':inputs[obj_name]['color'][1],
            'color_b':inputs[obj_name]['color'][2],
            'og_x':inputs[obj_name]['coords'][0],
            'og_y':inputs[obj_name]['coords'][1],
            'og_z':inputs[obj_name]['coords'][2],
            'loc_x':lx,'loc_y':ly,'loc_z':lz,
            'rot_x':rx,'rot_y':ry,'rot_z':rz,
            'dim_x':sx,'dim_y':sy,'dim_z':sz,
            'sizecat':sizecat})

    ## Remove all objects from scene
    for obj in bpy.context.scene.objects:
        if obj==None:
            continue
        if obj.name.lower() not in {'camera', 'light'}:
            obj.select_set(True)
        else:
            obj.select_set(False) # Deselect camera and light objects
        bpy.ops.object.delete() # Delete all selected objects
        # Remove all mesh data blocks
        for mesh in bpy.data.meshes:
            bpy.data.meshes.remove(mesh)
        # Remove all material data blocks
        for material in bpy.data.materials:
            bpy.data.materials.remove(material)
        bpy.context.view_layer.update()

    ## Print output to return it to triggering script
    result_dict = {
        "status": "success",
        "path": os.path.join(os.getcwd(),'output',curr_out_folder),
        "filename":filename,
        "data": output}
    result_json = json.dumps(result_dict)
    print(result_json) # Print the JSON string to stdout


if __name__=="__main__":

    args = sys.argv
    inputs = json.loads(args[args.index('--python')+2])
    visualize(inputs)
    sys.exit(0)


## IF CODE UPDATED OR BLENDER CRASHES:
#import os
#import sys
#sys.path.append(os.path.join(os.getcwd(),'modules'))
#import render
#import importlib
#importlib.reload(render)
#from render import *
