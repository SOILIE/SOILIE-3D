import os, sys
import bpy
import mathutils
import pickle
import json
import random


def loadObjectSizes():
    '''load object sizes into list'''
    sFile = open(os.path.join(os.getcwd(),"data/object_sizes.csv"),'r')
    sizes = {}
    lines = sFile.readlines()
    sFile.close()
    for line in lines[1:]:
        item = line.split(',')
        sizes[item[0]] = float(item[1])
    return sizes


def look_at(objA, locB, away=False):
    '''point objA towards objB'''
    locA = objA.matrix_world.to_translation()
    if away: direction = -locB - locA
    else:    direction = locB - locA
    # point objA's '-Z' and use its 'Y' as up
    rot_quat = direction.to_track_quat('-Z', 'Y')
    # assume we're using euler rotation
    objA.rotation_euler = rot_quat.to_euler()


def generateFileName(filename):
    '''recursive function to auto-generate output filename
    and avoid overwriting'''
    if os.path.exists(filename):
        num = str(int(os.path.splitext(filename)[-2][-4:])+1)
        return generateFileName( \
            ''.join(os.path.splitext(filename)[:-1])[:-4]+ \
            '0'*(4-len(num))+num+'.png')
    return filename


def selectObj(objs):
    '''helper function to select a non-dummy object to be
    the active object '''
    index = 0
    active = objs[index]
    while active.name[0]=='$':
        index+=1
        active = objs[index]
    return active


def visualize(coords):
    '''Create 3D renderings of the objects at the given coords
    coords: a dictionary of final object locations
    output: PNG file of the 3D scene in the output folder'''

    # initialize parameters
    scene = bpy.context.scene
    camera = bpy.data.objects['Camera']
    sizes = loadObjectSizes()
    obj3d = [f for f in os.listdir('3d') if os.path.splitext(f)[1].lower()=='.obj']

    # place camera
    c_loc = coords['CAMERA']
    camera.location = c_loc
    look_at(camera,mathutils.Vector((0,0,0)))

    # variables used in for loop below
    lc = 0 # location counter for text
    filepath = '' # object names will be added to create image filename
    maxSize = max([sizes[obj] for obj,_ in coords.items() if obj!='CAMERA' and obj!='SCALE'])
    avgLoc  = [sum(l)/len(l) for l in list(zip(*[xyz for obj,xyz in coords.items() \
                if obj!='CAMERA' and obj!='SCALE' and obj!='floor']))] # x,y of floor
    floor = 0 # keeps track of lowest point to place floor (z of floor)
    newDims = {} # will store dims to update after for loop: workaround for Blender bug

    obj_sizes = {}
    obj_dims = {}
    for obj,xyz in coords.items():

        if obj=='CAMERA' or obj=='SCALE': continue
        filepath+=obj+'-'

        if obj=='lamp' or obj=='light':
            bpy.ops.object.select_all(action='DESELECT')
            bpy.data.objects["Lamp"].location = xyz
            if obj=='light': continue

        # if no 3d data exists for object, use spheres:
        if obj not in ['_'.join(f.split('_')[:-1]) for f in obj3d]:
            if obj=='floor':
                bpy.ops.object.select_all(action='DESELECT')
                bpy.ops.mesh.primitive_plane_add()
                ob = bpy.context.selected_objects[0]
                ob.name = 'floor'
                #ob.scale[0] = ob.scale[1] = \
                #    sizes[obj]/maxSize*coords['SCALE']/4
                #ob.scale = (1,1,1)
                #obj_sizes['floor'] = ob.scale[0]
                obj_sizes[obj] = ob.scale
                obj_dims[obj] = ob.dimensions

            else:
                # add/setup text to identify the object
                bpy.ops.object.select_all(action='DESELECT')
                bpy.ops.object.text_add(radius=0.4,location=
                    (xyz[0],xyz[1],xyz[2]+1.3+lc))
                lc+=0.4
                text = bpy.context.object
                text.data.body = obj
                look_at(text,mathutils.Vector((0,0,0)))
                #text.rotation_euler[0]+=0.2
                #text.rotation_euler[1]+=0.2
                #text.rotation_euler[2]-=0.2

                # add/setup sphere to represent the object
                bpy.ops.object.select_all(action='DESELECT')
                bpy.ops.mesh.primitive_uv_sphere_add(segments=32,
                    radius=sizes[obj]/maxSize*coords['SCALE']/2,location=xyz)
                ob = bpy.context.active_object
                ob.name = obj
                #ob.scale = (1,1,1)
                #ob.dimensions = tuple([sizes[obj]]*3)
                obj_sizes[obj] = ob.scale
                obj_dims[obj] = ob.dimensions

                # create material to color the sphere
                mat = bpy.data.materials.new(name=obj+"-mat")
                ob.data.materials.append(mat)
                text.data.materials.append(mat)
                color = [random.random() for i in range(3)] + [1]
                mat.diffuse_color = color

                floor = min(floor,ob.location[2]-(ob.dimensions[2]/2))

        else: # if 3d data exists for object, use this data

            # select random 3d file for current object type and import it into scene
            fname = random.choice( \
                [f for f in obj3d if '_'.join(f.split('_')[:-1])==obj])
            bpy.ops.object.select_all(action='DESELECT')
            bpy.ops.import_scene.obj(filepath=os.path.join('3d',fname),
                axis_forward='-Y',axis_up='Z',use_image_search=False)

            # clear object location and scale
            bpy.ops.object.location_clear()
            bpy.ops.object.scale_clear()
            bpy.ops.object.rotation_clear()
            bpy.context.view_layer.objects.active = bpy.context.window.scene.objects[0]
            bpy.ops.object.join()

            # selected_objects should be length 1 unless there is dummy object in index 0
            ob = selectObj(bpy.context.selected_objects) # select the non-dummy object
            ob.name = obj
            ob.location = xyz
            maxDim = max(ob.dimensions)
            newDims[obj] = [x/maxDim for x in ob.dimensions] # normalize to 1
            obj_sizes[obj] = ob.scale
            obj_dims[obj] = ob.dimensions
            #ob.scale = (1,1,1)
            #ob.dimensions = tuple([sizes[obj]]*3)
            #scale = [sizes[obj]/maxSize*coords['SCALE']/2 for r in range(0,3)]
            #obj_sizes[obj] = scale
            #ob.scale = scale
            floor = min(floor,ob.location[2]-(newDims[obj][2]/2))

    if 'floor' in [n.name for n in bpy.context.scene.objects]:
        bpy.ops.object.select_all(action='DESELECT')
        bpy.data.objects['floor'].location = avgLoc[0],avgLoc[1],floor
        bpy.context.scene.objects['floor'].dimensions = (12,12,0)
        bpy.context.view_layer.update()

    bpy.ops.object.select_all(action='DESELECT')
    for o in bpy.context.scene.objects:
        if (o.type == 'MESH' and \
            o.name in ['_'.join(f.split('_')[:-1]) for f in obj3d]):
            o.select_set(True)
            newDim = newDims[o.name]
            #o.dimensions = newDim
            bpy.context.view_layer.update()

    # render/save image
    bpy.data.cameras['Camera'].type = 'ORTHO'
    bpy.ops.view3d.camera_to_view_selected()
    # look_at(camera,mathutils.Vector((0,0,0)))
    filepath = generateFileName('output/'+filepath.rstrip('-')+'_0001.png')
    bpy.data.scenes['Scene'].render.filepath = filepath
    bpy.ops.render.render(write_still=True)

    # save coordinates to file
    with open(os.path.splitext(filepath)[0]+'.csv','w') as f:
        f.write('object,x,y,z,scale,dimensions\n')
        f.write('CAMERA,'+str(coords['CAMERA'][0])+','+
                          str(coords['CAMERA'][1])+','+
                          str(coords['CAMERA'][2])+',0,0\n')
        for obj,xyz in coords.items():
            if obj != 'CAMERA' and obj != 'SCALE':
                f.write(obj+','+str(xyz[0])+','+
                                str(xyz[1])+','+
                                str(xyz[2])+','+
                                #(str(obj_sizes[obj]) if obj=='floor' else str(obj_sizes[obj][0]))+','+
                                str(list(obj_sizes[obj])).replace(',',' ')+','+
                                #str(1)+'\n')
                                str(list(obj_dims[obj])).replace(',',' ')+'\n')

    # remove all objects from scene
    bpy.ops.object.select_all(action='DESELECT')
    for ob in scene.objects:
        ob.select_set(ob.type == 'MESH' or ob.name.startswith('Text'))
    bpy.ops.object.delete()
    for item in bpy.data.meshes:
        bpy.data.meshes.remove(item)


if __name__=="__main__":

    args = sys.argv
    coords = json.loads(args[args.index('--python')+2])
    visualize(coords)
    sys.exit(0)
