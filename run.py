import os, sys
import bpy
import fnmatch
import math, mathutils
import random
import numpy as np
import pickle
from itertools import permutations

def loadObjectSizes():
    # load object sizes into list
    sFile = open(os.path.join(os.getcwd(),"data/object_sizes.csv"),'r')
    sFile.close()
    sizes = {}
    lines = sFile.readlines()
    for line in lines:
        item = line.split(',')
        sizes[item[0]] = item[1]
    return sizes

def look_at(objA, locB, away=False):
    locA = objA.matrix_world.to_translation()
    if away: direction = -locB - locA
    else:    direction = locB - locA
    # point objA's '-Z' and use its 'Y' as up
    rot_quat = direction.to_track_quat('-Z', 'Y')
    # assume we're using euler rotation
    objA.rotation_euler = rot_quat.to_euler()

def calculateCoords(objects):
    '''Calculate the final coordinates of a list of objects
    objects: a list of the object names
    output: a dictionary of final object locations'''

    # load dictionary of all triplets
    dictionary = open("data/triplets.csv",'r')
    # uncomment to load from pickle file
    # TODO: won't work on python2 pickle file
    # dictionary = pickle.load(open("data/triplets.pickle"))
    print ("INFO: Loaded triplet dictionary.")

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
                print ("ERROR: Not enough data for " + obj + ". Retrying...")
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
    # Where would objC2 be located if it were in the coordinate space of objC1?
    #
    # We will use objA, objB, and camera coordinates to trivially generate a 4th point
    # (midpoint of other 3 points) that will almost certainly be on a different plane 
    # than the other 3 points. We use these points to find an affine transform between
    # baseCoords and every other triplet. objA and objB should be similar if not the 
    # same in every triplet. But camera and 4th point will differ which should be enough 
    # to solve for a transformation matrix that can be used to determine where objCX ends
    # up in the base coordinate space.
    pts = [triplets[0][3],triplets[0][4],triplets[0][6]]
    z = zip(pts[0],pts[1],pts[2]) # [[x0,x1,x2],[y0,y1,y2],[z0,z1,z2]]
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
        z = zip(pts[0],pts[1],pts[2])
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
    output: PNG file(s) in the output folder of the 3D scene'''

    # initialize parameters
    scene = bpy.context.scene
    camera = bpy.data.objects['Camera']
    sizes = loadObjectSizes()

    # place camera
    c_loc = mathutils.Vector(coords['CAMERA'])
    camera.delta_location = c_loc
    look_at(camera,mathutils.Vector((0,0,0)))

    maxSize = max([sizes[obj] for obj in triplet[0:3]])
    lc = 0 # location counter
    filepath = ''
    for obj,xyz in coords.items():
        if obj=='CAMERA' or obj=='SCALE': continue
        filepath+=obj+'-'
        color = [random.random() for i in range(3)]
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
        bpy.ops.object.select_all(action='DESELECT')
        bpy.ops.mesh.primitive_uv_sphere_add(segments=32,\
            size=(sizes[obj]/maxSize)*coords['SCALE']/2,location=xyz)
        ob = bpy.context.active_object
        ob.name = obj
        mat = bpy.data.materials.new(name=obj+"-mat")
        ob.data.materials.append(mat)
        text.data.materials.append(mat)
        mat.diffuse_color = color
        look_at(camera,mathutils.Vector((0,0,0)))

        # render/save image
        filepath = 'data/images/'+filepath.rstrip('-')+'.png'
        bpy.data.scenes['Scene'].render.filepath = filepath
        bpy.ops.render.render(write_still=True)

        # remove all objects from scene
        bpy.ops.object.select_all(action='DESELECT')
        for ob in scene.objects:
            ob.select = ob.type == 'MESH' or ob.name.startswith('Text')
        bpy.ops.object.delete()
        for item in bpy.data.meshes:
            bpy.data.meshes.remove(item)
    
if __name__=="__main__":

    objects = input("Enter some object names: ")
    visualize(calculateCoords(objects.split(' ')))


