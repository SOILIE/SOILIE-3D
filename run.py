import os, sys
import bpy
import fnmatch
import math, mathutils
import random
import numpy as np
import pickle
from itertools import permutations
sys.path.append("modules")
import working_combos

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

def calculateCoords(objects):
    '''Calculate the coordinates of a list of objects
    based on angles and distances between triplets
    objects: a list of the object names
    dictionary: a dictionary of all object triplet data
    output: a dictionary of final object locations'''

    # load dictionary of all triplets
    dictionary = pickle.load(open("data/triplets.pickle",'rb'))
    print ("INFO: Loaded pickled triplet dictionary.")

    # generate every permutation of the given objects and try to find a set
    # of triplets that will contain enough data to calculate all object locations
    objMix = list(permutations(objects,len(objects)))
    random.shuffle(objMix)
    enoughData = True
    for i,objs in enumerate(objMix):
        print ("INFO: Attempt "+str(i+1)+"...")
        enoughData = True # set to false if any object isn't in final list
        # create list of necessary triplets based on input objects
        combos = [[objs[0],objs[1],x] for x in objs[2:]]
        good = []
        for combo in combos: # for each combo
            comboStr = ','.join(combo)
            if comboStr not in dictionary:
                print ("WARNING: Triplet not found in dictionary: " + comboStr)
                continue
            good.append(combo)
    
        for obj in objects: # if any of the objects aren't in the final list
            if obj not in [x for sublist in good for x in sublist]:
                print ("ERROR: Not enough data for "+obj+". Retrying...")
                enoughData = False
                break
        if enoughData:
            break

    if not enoughData: print ("ERROR: All tries failed. Exiting..."); sys.exit(1)
    
    combos = good
    print ("INFO: Found good combo.")

    sizes = loadObjectSizes()
    triplets = []
    for combo in combos: # for each combo
        comboStr = ','.join(combo)
        data = dictionary[comboStr] # find triplet data
        print ("INFO: Setting up combo: "+ repr(combo))
        # set up variables
        objA,objB,objC = combo
        d_ab = float(data[0])   # distance between objA and objB
        d_ac = float(data[1])   # distance between objA and objC
        d_ao = float(data[2])   # distance between objA and camera
        aBAC = math.radians(float(data[3])) # angle BAC
        aOAB = math.radians(float(data[4])) # angle OAB
        aOAC = math.radians(float(data[5])) # angle OAC

        # find necessary values using trig
        a = d_ac*math.cos(aBAC)
        b = d_ac*math.sin(aBAC)
        x = d_ao*math.cos(aOAB)
        y = (d_ac*d_ao*math.cos(aOAC) - a*x) / b
        z = math.sqrt(d_ao**2 - x**2 - y**2)

        # establish centroid locations for A, B, C, and camera
        A = [0,0,0]
        B = [d_ab,0,0]
        C = [a,b,0]
        O = [x,y,z]
        # establish scale of objects so they are proportional to camera's 
        # range of sight
        d_bc = math.sqrt(sum((B-C)**2 for B,C in zip(B,C)))
        d_bo = math.sqrt(sum((B-O)**2 for B,O in zip(B,O)))
        d_co = math.sqrt(sum((C-O)**2 for C,O in zip(C,O)))
        s = min([d_ab,d_ac,d_bc,d_ao,d_bo,d_co]) # use smallest distance as scale factor
        triplets.append([objA,objB,objC,A,B,C,O,s]) # objA,objB,objC,camera,scale

    # The following process answers the question:
    # Where would objC2 be located if it were in the coordinate space of objC1, given
    # objA and objB remain in place and camera2 translates to the location of camera1?
    #
    # We will use objA, objB, and camera coordinates to trivially generate a 4th point
    # (midpoint of these 3 points) that will almost certainly be on a different plane 
    # than the other 3 points. We use these points to find an affine transform between
    # baseCoords and every other triplet. objA and objB should be virtually identical 
    # in every triplet. But camera and 4th point will differ which should be enough 
    # to solve for a transformation matrix that can be used to determine where objCX ends
    # up in the base coordinate space.
    pts = [triplets[0][3],triplets[0][4],triplets[0][6]]
    z = list(zip(pts[0],pts[1],pts[2])) # [[x0,x1,x2],[y0,y1,y2],[z0,z1,z2]]
    d = [sum(z[0])/3,sum(z[1])/3,sum(z[2])/3] # trivially generated 4th point
    pts.append(d)
    baseCoords = np.array(pts,np.float32)   # use first triplet as base
    finalCoords = {} # initialize {obj: (x,y,z)} structure
    finalCoords[triplets[0][0]] = triplets[0][3] # add objA coords
    finalCoords[triplets[0][1]] = triplets[0][4] # add objB coords
    finalCoords[triplets[0][2]] = triplets[0][5] # add base objC coords
    finalCoords['CAMERA'] = triplets[0][6] # add camera coords
    finalCoords['SCALE'] = triplets[0][7] # add scale
    for triplet in triplets[1:]:
        pts = [triplet[3],triplet[4],triplet[6]]
        z = list(zip(pts[0],pts[1],pts[2]))
        d = [sum(z[0])/3,sum(z[1])/3,sum(z[2])/3]
        pts.append(d)
        coords = np.array(pts,np.float32)
        
        # pad with ones to make translations possible
        pad = lambda x: np.hstack([x, np.ones((x.shape[0], 1))])
        unpad = lambda x: x[:,:-1]
        base = pad(baseCoords)
        curr = pad(coords)
        # solve the least squares problem X * A = Y
        # to find the transformation matrix A
        A, res, rank, s = np.linalg.lstsq(curr, base)
        transform = lambda x: unpad(np.dot(pad(x), A))
        
        pts.append(triplet[5]) # now add objC back to the set of points
        coords = np.array(pts,np.float32) # and re-apply the transformation so the
        coordsNew = transform(coords) # current triplet lines up with the base triplet

        # determine the outlier - these are the coordinates for objC
        isNear = lambda p1,p2: sum((x1 - x2)**2 for x1, x2 in zip(p1, p2)) < 0.01
        corresp = [] # stores all coords from coordsNew which mapped to a baseCoord
        for coord in coordsNew.tolist():
            for baseCoord in baseCoords.tolist():
                if isNear(coord,baseCoord):
                    corresp.append(coord)
                    break
        finalCoords[triplet[2]] = [x for x in coordsNew.tolist() if x not in corresp][0]
        finalCoords['SCALE']    = min(triplet[7],finalCoords['SCALE']) # update scale

    return finalCoords

def visualize(coords):
    '''Create 3D renderings of the objects at the given coords
    coords: a dictionary of final object locations
    output: PNG file of the 3D scene in the images folder'''

    def generateFileName(filename):
        '''recursive function to auto-generate output filename
        and avoid overwriting'''
        if os.path.exists(filename):
            num = str(int(os.path.splitext(filename)[-2][-4:])+1)
            return generateFileName( \
                ''.join(os.path.splitext(filename)[:-1])[:-4]+'0'*(4-len(num))+num+'.png')
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

    # initialize parameters
    scene = bpy.context.scene
    camera = bpy.data.objects['Camera']
    sizes = loadObjectSizes()
    obj3d = [f for f in os.listdir('3d') if os.path.splitext(f)[1]=='.3DS']

    # place camera
    c_loc = mathutils.Vector(coords['CAMERA'])
    camera.delta_location = c_loc
    look_at(camera,mathutils.Vector((0,0,0)))

    lc = 0 # location counter for text
    filepath = '' # object names will be added to create image filename
    maxSize = max([sizes[obj] for obj,_ in coords.items() \
                  if obj!='CAMERA' and obj!='SCALE'])
    
    for obj,xyz in coords.items():
        if obj=='CAMERA' or obj=='SCALE': continue
        filepath+=obj+'-'
        
        # if no 3d data exists for object, use spheres:
        if obj not in ['_'.join(f.split('_')[:-1]) for f in obj3d]:
                        
            # add/setup text to identify the object
            bpy.ops.object.select_all(action='DESELECT')
            bpy.ops.object.text_add(radius=0.4,location=\
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
            bpy.ops.mesh.primitive_uv_sphere_add(segments=32,\
                size=(sizes[obj]/maxSize)*coords['SCALE']/2,location=xyz)
            ob = bpy.context.active_object
            ob.name = obj
            
            # create material to color the sphere
            mat = bpy.data.materials.new(name=obj+"-mat")
            ob.data.materials.append(mat)
            text.data.materials.append(mat)
            color = [random.random() for i in range(3)]
            mat.diffuse_color = color    
        
        else: # if 3d data exists for object, use this data

            # select random 3d file for current object type and import it into scene
            fname = random.choice( \
                [f for f in obj3d if '_'.join(f.split('_')[:-1])==obj])
            bpy.ops.object.select_all(action='DESELECT')
            bpy.ops.import_scene.autodesk_3ds(filepath=os.path.join('3d',fname), \
                axis_forward='-Y',axis_up='Z',constrain_size=0,use_image_search=False)
           
            # set object location and scale
            bpy.context.scene.objects.active = selectObj(bpy.context.selected_objects)
            bpy.ops.object.location_clear()
            bpy.ops.object.scale_clear()
            bpy.ops.object.rotation_clear()
            bpy.ops.object.join()
            # selected_objects should be length 1 unless there is dummy object in index 0
            ob = bpy.context.selected_objects[-1] # select the non-dummy object
            ob.name = obj
            #print ("NAME:",ob.name)
            ob.location = mathutils.Vector(xyz)
            #print ("LOC:",ob.location)
            #orig = list(ob.scale) # original object size
            #print ("ORIG:",orig)
            #mid  = np.median(np.array(orig)) # median of x, y, and z values
            #print ("MID:",mid)
            #ratio = [o/mid for o in orig] # make middle value 1, other 2 proportional
            #print ("RATIO:",ratio)
            #print ("OBJSIZE:",sizes[obj],"/MAXSIZE:",maxSize,"*SCALE",coords['SCALE'])
            scale = [(sizes[obj]/maxSize)*coords['SCALE']/2 for r in range(0,3)]
            #print ("SCALE:",scale)
            ob.scale = mathutils.Vector(scale)


    look_at(camera,mathutils.Vector((0,0,0)))

    # render/save image
    filepath = generateFileName('images/'+filepath.rstrip('-')+'_0001.png')
    bpy.data.scenes['Scene'].render.filepath = filepath
    bpy.ops.render.render(write_still=True)

    # remove all objects from scene
    #bpy.ops.object.select_all(action='DESELECT')
    #for ob in scene.objects:
    #    ob.select = ob.type == 'MESH' or ob.name.startswith('Text')
    #bpy.ops.object.delete()
    #for item in bpy.data.meshes:
    #    bpy.data.meshes.remove(item)
    
if __name__=="__main__":

    print ("#=============================#\n" + \
           "#     VISUO 3D v.17.07.17     #\n" + \
           "#=============================#\n" + \
           "#      SELECT AN OPTION       #\n" + \
           "#-----------------------------#\n" + \
           "# 1. Imagine a random scene   #\n" + \
           "# 2. Enter object names       #\n" + \
           "# 3. Determine working combos #\n" + \
           "# 4. Exit                     #\n" + \
           "#=============================#\n")

    def menuLoop():
        option = ''
        while option!='1' and option!='2' and option!='3' and \
              option!='4' and option!='exit' and option!='quit':
            option = input(">> ")
        
        if option=='4' or option=='exit' or option=='quit':
            sys.exit(0) 

        if option=='1': # generate random collection of objects
            num = '' # prompt for number of objects
            while not num.isdigit():
                num = input("How many objects? ")
                if num=='exit' or num=='quit':
                    sys.exit(0)
                if num=='menu':
                    return menuLoop()
                if not num.isdigit() or int(num)<3:
                    print ("Invalid input: Minimum 3 objects required.")
            return working_combos.load(int(num))
        
        if option=='2': # Enter custom list of objects
            names = ''   # string of object names
            objects = [] # list of object names
            while not names:
                names = input("Enter some object names: ")
                if names=='exit' or names=='quit':
                    sys.exit(0)
                if num=='menu':
                    return menuLoop()
                if ',' in names or ';' in names:
                    print ("Invalid format: Use SPACE as delimiter.")
                    names = ''
                else: objects = names.split(' ')
            return objects    

        if option=='3': # determine working combos
            working_combos.determine()
            return menuLoop()

    objects = menuLoop()
    print ("INFO: Imagining '"+','.join(objects)+"'")

    visualize(calculateCoords(objects))
