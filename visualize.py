import bpy
import fnmatch
import math, mathutils
import random

def look_at(objA, locB, away=False):
    locA = objA.matrix_world.to_translation()
    if away: direction = -locB - locA
    else:    direction = locB - locA
    # point objA's '-Z' and use its 'Y' as up
    rot_quat = direction.to_track_quat('-Z', 'Y')
    # assume we're using euler rotation
    objA.rotation_euler = rot_quat.to_euler()

#===============================================================#
#   READ FROM FILES                                             #
#===============================================================#
tFile = open("/media/mike/Data/SOIL/getObjects3D/output/triplets.csv",'r')
sFile = open("/media/mike/Data/SOIL/getObjects3D/output/object_sizes.csv",'r')

tLines = tFile.readlines()
sLines = sFile.readlines()

triplets = []

for line in tLines[1:]:
    data = line.split(',')
    objA = data[0]
    objB = data[1]
    objC = data[2]
    d_ab = float(data[3])
    d_ac = float(data[4])
    d_ao = float(data[5])
    aBAC = math.radians(float(data[6]))
    aOAB = math.radians(float(data[7]))
    aOAC = math.radians(float(data[8].rstrip('\n')))

    a = d_ac*math.cos(aBAC)
    b = d_ac*math.sin(aBAC)
    x = d_ao*math.cos(aOAB)
    y = (d_ac*d_ao*math.cos(aOAC) - a*x) / b
    z = math.sqrt(d_ao**2 - x**2 - y**2)

    A = [0,0,0]
    B = [d_ab,0,0]
    C = [a,b,0]
    O = [x,y,z]

    d_bc = math.sqrt(sum((B-C)**2 for B,C in zip(B,C)))
    d_bo = math.sqrt(sum((B-O)**2 for B,O in zip(B,O)))
    d_co = math.sqrt(sum((C-O)**2 for C,O in zip(C,O)))
    s = min([d_ab,d_ac,d_bc,d_ao,d_bo,d_co])
    print ("|==================================================|")
    print (objA,A,'\n'+objB,B,'\n'+objC,C,'\ncamera',O,'\nscale',s)
 
    triplets.append([objA,objB,objC,A,B,C,O,s]) # objA,objB,objC,camera,scale

sizes = {}
for line in sLines[1:]:
    data = line.split(',')
    sizes[data[0]] = float(data[1])

tFile.close()
sFile.close()

#=========================================================#
#   Draw Data                                             #
#=========================================================#

scene  = bpy.context.scene
camera = bpy.data.objects['Camera']
colors = {}

for i,triplet in enumerate(triplets):
    c_loc = mathutils.Vector(triplet[6])
    camera.delta_location = c_loc
    look_at(camera,mathutils.Vector((0,0,0)))
    maxSize  = max([sizes[obj] for obj in triplet[0:3]])
    lc = 0 # location counter
    filepath = ''
    for obj,loc in zip(triplet[0:3],triplet[3:6]):
        if obj=='Camera' or obj=='Scale': continue
        filepath+=obj+'-'
        if obj not in colors:
            col = [random.random() for i in range(3)]
            colors[obj] = col
        col = colors[obj]
        bpy.ops.object.select_all(action='DESELECT')
        bpy.ops.object.text_add(radius=0.4,location=\
                    (loc[0],loc[1],loc[2]+1.3+lc))
        lc+=0.4
        text = bpy.context.object
        text.data.body = obj
        look_at(text,mathutils.Vector((0,0,0)))
        #text.rotation_euler[0]+=0.2
        #text.rotation_euler[1]+=0.2
        #text.rotation_euler[2]-=0.2
        bpy.ops.object.select_all(action='DESELECT')
        bpy.ops.mesh.primitive_uv_sphere_add(segments=32,\
            size=(sizes[obj]/maxSize)*triplet[7]/2,location=loc)
        ob = bpy.context.active_object
        ob.name = obj
        mat = bpy.data.materials.new(name=obj+"-mat")
        ob.data.materials.append(mat)
        text.data.materials.append(mat)
        mat.diffuse_color = col
    
    look_at(camera,mathutils.Vector((0,0,0)))
    filepath = 'output/images/'+filepath.rstrip('-')+'.png'
    bpy.data.scenes["Scene"].render.filepath = filepath
    bpy.ops.render.render(write_still=True)

    bpy.ops.object.select_all(action='DESELECT')
    for ob in scene.objects:
        ob.select = ob.type == 'MESH' or ob.name.startswith('Text')
    bpy.ops.object.delete()
    for item in bpy.data.meshes:
        bpy.data.meshes.remove(item)
