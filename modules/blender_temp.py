import bpy
import os

# Path to the directory containing the .obj files
obj_directory = "/home/mike/Dropbox/Projects/VISUO-3D/SOILIE-3D/3d"

# List of .obj files to load
obj_files = ['coffee_table_0001.obj', 'cap_0001.obj', 'bowl_0004.obj']  # Add your .obj file names

# Set the target diameter for the objects
target_diameter = 1.0  # Adjust this value as needed

# Clear existing objects in the scene
bpy.ops.wm.read_factory_settings(use_empty=True)

# List to store object-scaling factor pairs
object_scaling_pairs = []

# Loop through each .obj file and load it into the scene
for obj_file in obj_files:
    obj_path = os.path.join(obj_directory, obj_file)
    bpy.ops.import_scene.obj(filepath=obj_path,axis_forward='-Y',axis_up='Z',use_split_objects=False,use_image_search=False)

    # Get the maximum dimension of the current imported object
    obj = bpy.context.scene.objects[-1]
    max_dimension = max(obj.dimensions)
    scaling_factor = target_diameter / max_dimension
    object_scaling_pairs.append((obj, scaling_factor))

# Apply the scaling factors to each object
for obj, scaling_factor in object_scaling_pairs:
    obj.scale *= scaling_factor
    if 'coffee_table' in obj.name:
        obj.scale *= 0.809122039857275
        obj.location = (0.9623188236098995,0.7674065582310224,1.4736838630760678)
    if 'cap' in obj.name:
        obj.scale *= 0.29644445306337
        obj.location = (1.2446125504560748,0.5074737290813784,1.7677635746606515)
    if 'bowl' in obj.name:
        obj.scale *= 0.278059761757064
        obj.location = (0.6326732952380943,0.5124128126984133,1.7327682051282058)

# Update the scene to reflect the scaling changes
#bpy.context.view_layer.update()