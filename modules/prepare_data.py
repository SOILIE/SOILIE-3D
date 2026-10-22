''' SOILIE 3D
    v.24.07.05
    Written by Mike Cichonski
    With contributions from Tae Bourque and Isil Sanusoglu
    for the Science of Imagination Laboratory
    Carleton University

    File: prepare_data.py
	This file processes all the csv files output by collect_data.py and
	averages/fuzzifies the values of the object triplets.
	It also estimates relative object sizes using the object's
	largest measured diameter (unable to find true volume due
	to incomplete 3d data). Requires output from collect_data.py.'''

import fnmatch
import copy
import os
from os.path import join
import sys
import ast
import pickle
import json
import random
import math
from math import sqrt
import numpy as np
import pandas as pd
from collections import Counter
from itertools import permutations

from modules.timer import *
from modules import progress_bar
from modules.triplet_schema import (
    TRIPLET_COLUMNS,
    validate_triplet_columns as _validate_triplet_columns,
)


ALIGNMENT_EPSILON = 1e-12


class TripletGeometryError(ValueError):
    '''Stored relationship geometry cannot produce a valid rigid scene.'''


def require_canonical_triplet_columns(columns, *, source):
    '''Expose schema failures through the model's geometry error boundary.'''

    try:
        _validate_triplet_columns(columns, source=source)
    except ValueError as error:
        raise TripletGeometryError(str(error)) from error


def _coordinate3(name, value):
    '''Return a finite float64 xyz coordinate or reject the malformed input.'''

    coordinate = np.asarray(value, dtype=np.float64)
    if coordinate.shape != (3,) or not np.all(np.isfinite(coordinate)):
        raise TripletGeometryError(f'{name} must contain exactly three finite coordinates')
    return coordinate


def _vector_angle_degrees(first, second, tolerance):
    '''Return the unsigned angle between vectors, or None for a zero vector.'''

    first_norm = float(np.linalg.norm(first))
    second_norm = float(np.linalg.norm(second))
    if first_norm <= tolerance or second_norm <= tolerance:
        return None
    cosine = float(np.dot(first, second) / (first_norm * second_norm))
    return float(math.degrees(math.acos(float(np.clip(cosine, -1.0, 1.0)))))


def _rotate_about_axis(point, origin, axis, angle):
    '''Rotate a point around an origin/axis using Rodrigues' rigid formula.'''

    relative = point - origin
    axial = float(np.dot(relative, axis))
    perpendicular = relative - axial * axis
    cosine = math.cos(angle)
    sine = math.sin(angle)
    return origin + (
        axial * axis
        + perpendicular * cosine
        + np.cross(axis, perpendicular) * sine
    )


def align_triplet_about_axis(
        base_a, base_b, base_camera,
        source_a, source_b, source_camera, source_c,
        *, object_name, base_object_name, anchor_names):
    '''Rigidly align a triplet's camera roll while keeping shared A/B fixed.

    A rotation around A--B is the only rigid transform that can preserve the
    shared anchors exactly. It can align the cameras' radial directions but
    cannot change their axial offsets or ranges; those residuals are returned
    explicitly for downstream scientific review.
    '''

    base_a = _coordinate3('base_a', base_a)
    base_b = _coordinate3('base_b', base_b)
    base_camera = _coordinate3('base_camera', base_camera)
    source_a = _coordinate3('source_a', source_a)
    source_b = _coordinate3('source_b', source_b)
    source_camera = _coordinate3('source_camera', source_camera)
    source_c = _coordinate3('source_c', source_c)

    if not np.array_equal(source_a, base_a) or not np.array_equal(source_b, base_b):
        raise TripletGeometryError('Triplet A/B anchors must exactly match the base anchors')

    axis_vector = base_b - base_a
    axis_length = float(np.linalg.norm(axis_vector))
    if axis_length <= ALIGNMENT_EPSILON:
        raise TripletGeometryError('The shared A/B alignment axis must have non-zero length')
    axis = axis_vector / axis_length

    base_vector = base_camera - base_a
    source_vector = source_camera - source_a
    base_axial = float(np.dot(base_vector, axis))
    source_axial = float(np.dot(source_vector, axis))
    base_perpendicular = base_vector - base_axial * axis
    source_perpendicular = source_vector - source_axial * axis
    base_radial = float(np.linalg.norm(base_perpendicular))
    source_radial = float(np.linalg.norm(source_perpendicular))
    base_range = float(np.linalg.norm(base_vector))
    source_range = float(np.linalg.norm(source_vector))
    if not all(math.isfinite(value) for value in (
            axis_length, base_axial, source_axial, base_radial,
            source_radial, base_range, source_range)):
        raise TripletGeometryError('Triplet geometry exceeds the finite numeric range')
    tolerance = ALIGNMENT_EPSILON * max(
        1.0, axis_length, base_range, source_range,
    )

    status = 'aligned'
    if base_radial <= tolerance or source_radial <= tolerance:
        # Camera roll is not observable when either camera lies on the axis.
        rotation = 0.0
        status = 'underdetermined_camera_axis'
    else:
        source_direction = source_perpendicular / source_radial
        base_direction = base_perpendicular / base_radial
        sine_term = float(np.dot(axis, np.cross(source_direction, base_direction)))
        cosine_term = float(np.dot(source_direction, base_direction))
        if abs(sine_term) <= ALIGNMENT_EPSILON and cosine_term < 0.0:
            # atan2 can return either signed pi for a signed zero. Pin this
            # physically identical case so diagnostics are reproducible.
            rotation = math.pi
        else:
            rotation = math.atan2(sine_term, cosine_term)

    aligned_c = _rotate_about_axis(source_c, source_a, axis, rotation)
    rotated_source_camera = _rotate_about_axis(
        source_camera, source_a, axis, rotation,
    )
    if not np.all(np.isfinite(aligned_c)) or not np.all(np.isfinite(rotated_source_camera)):
        raise TripletGeometryError('Rigid alignment produced non-finite coordinates')
    source_object_camera_distance = float(np.linalg.norm(source_c - source_camera))
    rotated_object_camera_distance = float(np.linalg.norm(aligned_c - rotated_source_camera))
    retained_base_camera_distance = float(np.linalg.norm(aligned_c - base_camera))
    if not all(math.isfinite(value) for value in (
            source_object_camera_distance, rotated_object_camera_distance,
            retained_base_camera_distance, rotation)):
        raise TripletGeometryError('Alignment diagnostics exceed the finite numeric range')

    diagnostic = {
        'objectName': str(object_name),
        'baseObjectName': str(base_object_name),
        'anchorNames': [str(name) for name in anchor_names],
        'status': status,
        'rotationDegrees': float(math.degrees(rotation)),
        'sourceCameraRange': source_range,
        'baseCameraRange': base_range,
        'cameraRangeDelta': source_range - base_range,
        'sourceCameraAxialOffset': source_axial,
        'baseCameraAxialOffset': base_axial,
        'cameraAxialDelta': source_axial - base_axial,
        'sourceCameraRadialDistance': source_radial,
        'baseCameraRadialDistance': base_radial,
        'cameraRadialDelta': source_radial - base_radial,
        'postRotationCameraPositionResidual': float(
            np.linalg.norm(rotated_source_camera - base_camera)
        ),
        'postRotationCameraDirectionResidualDegrees': _vector_angle_degrees(
            rotated_source_camera - base_a, base_vector, tolerance,
        ),
        'sourceObjectCameraDistance': source_object_camera_distance,
        'rotatedSourceObjectCameraDistance': rotated_object_camera_distance,
        'retainedBaseCameraObjectDistance': retained_base_camera_distance,
        'objectCameraDistanceDelta': (
            retained_base_camera_distance - source_object_camera_distance
        ),
    }
    return (
        [float(component) for component in aligned_c],
        [float(component) for component in rotated_source_camera],
        diagnostic,
    )


def reconstruct_triplet_coordinates(
        distance_ab, distance_ac, distance_ao,
        angle_bac, angle_oab, angle_oac):
    '''Reconstruct canonical A/B/C/camera coordinates from one named row.'''

    values = np.asarray([
        distance_ab, distance_ac, distance_ao,
        angle_bac, angle_oab, angle_oac,
    ], dtype=np.float64)
    if not np.all(np.isfinite(values)):
        raise TripletGeometryError('Triplet geometry must contain only finite values')

    d_ab, d_ac, d_ao, a_bac, a_oab, a_oac = [float(value) for value in values]
    if d_ab <= 0.0 or d_ac <= 0.0 or d_ao <= 0.0:
        raise TripletGeometryError('Triplet distances must be greater than zero')
    a_bac, a_oab, a_oac = map(math.radians, (a_bac, a_oab, a_oac))

    c_x = d_ac * math.cos(a_bac)
    c_y = d_ac * math.sin(a_bac)
    if abs(c_y) <= ALIGNMENT_EPSILON:
        raise TripletGeometryError('angleBAC must place object C away from the A/B axis')
    camera_x = d_ao * math.cos(a_oab)
    camera_y = (d_ac * d_ao * math.cos(a_oac) - c_x * camera_x) / c_y
    camera_z_squared = d_ao ** 2 - camera_x ** 2 - camera_y ** 2
    tolerance = ALIGNMENT_EPSILON * max(1.0, d_ao ** 2)
    if camera_z_squared < -tolerance:
        raise TripletGeometryError('Triplet angles and distances cannot form real coordinates')
    camera_z = math.sqrt(max(0.0, camera_z_squared))
    return (
        [0.0, 0.0, 0.0],
        [d_ab, 0.0, 0.0],
        [c_x, c_y, 0.0],
        [camera_x, camera_y, camera_z],
    )


def gatherTriplets():
    # gather all triplet values into a single file =======================###
    print("Gathering all triplets into single file...")
    timer = startTimer()
    files = []
    for root, dirnames, filenames in os.walk('data'):
        for filename in fnmatch.filter(filenames, '*.csv'):
            files.append(os.path.join(root, filename))

    files = sorted(f for f in files if not ('data/object_' in f or
                   'data/triplets' in f or f=='data/working-combos.csv' or
                   'centroids.csv' in f))
    files.sort(key=len)

    master_df = pd.DataFrame()
    for i, filename in enumerate(files):
        print(filename)
        df = pd.read_csv(filename)
        require_canonical_triplet_columns(df.columns, source=filename)
        df['objectA'] = df['objectA'].apply(lambda x: x.rsplit('_',1)[0])
        df['objectB'] = df['objectB'].apply(lambda x: x.rsplit('_',1)[0])
        df['objectC'] = df['objectC'].apply(lambda x: x.rsplit('_',1)[0])
        master_df = pd.concat([master_df,df])
        progress_bar.update(i+1,len(files),suffix=filename+'                       ')

    master_df = master_df.loc[:, list(TRIPLET_COLUMNS)]
    master_df.sort_values(by=list(TRIPLET_COLUMNS)).to_csv('./data/triplets.csv', index=False)
    sys.stdout.write("Done! (%s)\n"%str(endTimerPretty(timer))); sys.stdout.flush()


def averageTriplets():

    # average triplet values =======================###
    sys.stdout.write("Averaging triplets..."); sys.stdout.flush()
    timer = startTimer()
    files = []
    for root, dirnames, filenames in os.walk('data'):
        for filename in fnmatch.filter(filenames, '*.csv'):
            files.append(os.path.join(root, filename))
    files = sorted(files)

    averages = {}
    for filename in files:
        if 'data/object_' in filename or 'data/triplets' in filename or filename=='data/working-combos.csv' or 'centroids.csv' in filename:
            continue # skip these because they are the output of the current script
        df_trip = pd.read_csv(filename)
        require_canonical_triplet_columns(df_trip.columns, source=filename)

        for i, row in df_trip.iterrows():
            objA = row['objectA'].rsplit('_',1)[0]
            objB = row['objectB'].rsplit('_',1)[0]
            objC = row['objectC'].rsplit('_',1)[0]
            triplet = ','.join([objA,objB,objC])
            distAB = float(row['distanceAB'])
            distAC = float(row['distanceAC'])
            distAO = float(row['distanceAO'])
            angBAC = float(row['angleBAC'])
            angOAB = float(row['angleOAB'])
            angOAC = float(row['angleOAC'])
            if triplet not in averages:
                averages[triplet] = [[distAB],[distAC],[distAO],[angBAC],[angOAB],[angOAC],1]
                continue
            averages[triplet][0].append(distAB)
            averages[triplet][1].append(distAC)
            averages[triplet][2].append(distAO)
            averages[triplet][3].append(angBAC)
            averages[triplet][4].append(angOAB)
            averages[triplet][5].append(angOAC)
            averages[triplet][6]+=1

    triplets = {'objectA':[],'objectB':[],'objectC':[],'distanceAB':[],'distanceAC':[],
                'distanceAO':[],'angleBAC':[],'angleOAB':[],'angleOAC':[],'count':[]}

    for triplet, values in averages.items():
        objA, objB, objC = triplet.split(',')
        triplets['objectA'].append(objA)
        triplets['objectB'].append(objB)
        triplets['objectC'].append(objC)
        triplets['distanceAB'].append(sum(values[0])/values[6])
        triplets['distanceAC'].append(sum(values[1])/values[6])
        triplets['distanceAO'].append(sum(values[2])/values[6])
        triplets['angleBAC'].append(sum(values[3])/values[6])
        triplets['angleOAB'].append(sum(values[4])/values[6])
        triplets['angleOAC'].append(sum(values[5])/values[6])
        triplets['count'].append(values[6])

    df_triplets = pd.DataFrame(triplets)
    df_triplets.to_csv('./data/triplets_avg.csv', index=False)

    with open ('data/triplets_avg.pickle','wb') as handle:
        pickle.dump(triplets,handle,protocol=pickle.HIGHEST_PROTOCOL)
    handle.close()

    sys.stdout.write("Done! (%s)\n"%str(endTimerPretty(timer))); sys.stdout.flush()


def approximateObjectSizes():
    # approximate object sizes =============================###

    sys.stdout.write("Approximating object diameters...\n"); sys.stdout.flush()
    timer = startTimer()
    files3D = []
    cenFiles = []
    count = 0
    for root, dirnames, filenames in os.walk('data'):
        for filename in fnmatch.filter(filenames,'*.3d'):
            files3D.append(os.path.join(root,filename))
        for filename in fnmatch.filter(filenames,'*centroids.csv'):
            cenFiles.append(os.path.join(root,filename))

    files3D.sort() # regular in-place sort first
    files3D.sort(key=len) # then sort by filename length, as this correctly sorts number strings alphabetically

    cenFiles.sort()
    cenFiles.sort(key=len)
    cen_dfs = {f:pd.read_csv(f) for f in cenFiles}

    progress_bar.update(0,len(files3D),prefix='Progress:')
    sizes = {}
    for index, filename in enumerate(files3D):
        sceneDir = filename.rsplit('/',1)[0]
        cen_df = cen_dfs[join(sceneDir,'centroids.csv')]
        centroids = {c['object']:[c['X'],c['Y'],c['Z']] for _,c in cen_df.iterrows()}
        theFile = open(filename,'r')
        contents = theFile.read()
        contents = contents.replace(')[',')\n[').replace(') [',')\n[')
        lines = contents.splitlines()
        distances = {} # of each object's 3d points from centroid
        for line in lines:
            if line=='':
                continue
            try:
                elem = list(json.loads(line.replace("'","\"").replace('(','[').replace(')',']').replace('None','null')))
            except Exception as e:
                print(line)
                raise e
            if len(elem)!=3:
                name = repr(elem).rstrip('\n')
            else:
                try:
                    x1,y1,z1 = [centroids[name][0],centroids[name][1],centroids[name][2]]
                except Exception as e:
                    print(cenFile)
                    print(name)
                    raise e
                x2,y2,z2 = [elem[0],elem[1],elem[2]]
                if name not in distances:
                    distances[name] = [sqrt((x2-x1)**2 + (y2-y1)**2 + (z2-z1)**2)]
                    continue
                distances[name].append(sqrt((x2-x1)**2 + (y2-y1)**2 + (z2-z1)**2))

        for obj,dist in distances.items():
            newName = ast.literal_eval(obj)[0].replace(' ','_')
            if newName not in sizes:
                sizes[newName] = dist
                continue
            sizes[newName].extend(dist)
        theFile.close()
        progress_bar.update(index,len(files3D),\
                            prefix='Progress:',suffix=str(index)+'/'+str(len(files3D)))

    progress_bar.update(len(files3D),len(files3D),\
                         prefix='Progress:',suffix=str(len(files3D))+'/'+str(len(files3D)))

    # For testing:
    #import matplotlib.pyplot as plt
    # vals = pd.Series(sizes['plant']).sort_values().reset_index(drop=True)
    # vals = vals[:len(vals)-int(len(vals)/5)]
    # len(vals)
    # vals.mean()
    # vals.median()
    # vals.mode()[round(len(vals.mode())/2)]
    # vals.plot(kind='bar')
    # plt.show()

    #mean_sizes = {}
    median_sizes = {}
    #mode_sizes = {}
    for obj,dist in sizes.items():
        numObjects = len(dist)
        vals = pd.Series(dist).sort_values().reset_index(drop=True)
        vals = vals[:len(vals)-int(len(vals)/5)] # trim 20% off the larger end to exclude outliers
        #multiply by 2 below to change radius to diameter
        #mean_sizes[obj] = str(vals.mean()*2)+','+str(numObjects)
        median_sizes[obj] = str(vals.median())+','+str(numObjects) # removed the x2 to make values more reasonable
        #mode = vals.mode()
        #mode = mode if isinstance(mode,int) else mode[int(len(mode)/2)]
        #mode_sizes[obj] = str(mode*2)+','+str(numObjects)

    maxCount = max([int(x.split(',')[-1]) for x in median_sizes.values()])
    maxDiameter = max([float(x.split(',')[0]) for x in median_sizes.values()])
    #meanDiameter = np.mean([float(x.split(',')[0]) for x in mean_sizes.values()])
    medianDiameter = np.median([float(x.split(',')[0]) for x in median_sizes.values()])

    outFile = open("data/object_sizes.csv",'w')
    outFile.write("object,diameter,count,confidence\n")
    for obj,dist in median_sizes.items():
        diameter = float(dist.split(',')[0]) if float(dist.split(',')[0])>0 else medianDiameter
        confidence = (int(dist.split(',')[-1])/maxCount) * (maxDiameter/diameter)
        outFile.write(obj+','+str(dist)+','+str(confidence)+'\n')
    outFile.close()
    sys.stdout.write("Processed in %s\n"%str(endTimerPretty(timer)));
    sys.stdout.flush()

def suffix_duplicates(items,nested=False):
    result = []
    if not nested:
        item_count = {}
        for item in items:
            if item not in item_count:
                item_count[item] = 0
                result.append(item)
            else:
                item_count[item] += 1
                result.append(f"{item}.{item_count[item]}")
    else:
        item_list = items.copy()
        first_list = item_list.pop(0)
        result.append(suffix_duplicates(first_list))
        third_item_count = Counter(first_list)

        for sublist in item_list:
            # Process the third item
            item_3 = sublist[2]
            if item_3 not in third_item_count:
                third_item_count[item_3] = 1
                modified_item_3 = item_3
            else:
                modified_item_3 = f"{item_3}.{third_item_count[item_3]}"
                third_item_count[item_3] += 1

            # Append the modified sublist to the result
            result.append(result[0][:2] + [modified_item_3])

    return result


def loadObjectSizes(objects):
    cat_dict = {
        'sizecat': {0: 'xsmall', 1: 'small', 2: 'medium', 3: 'large', 4: 'xlarge'},
        'min':     {0: 0.08,     1: 0.2,     2: 0.4,      3: 0.7,     4: 1.3},
        'max':     {0: 0.30,     1: 0.6,     2: 1.2,      3: 1.7,     4: 3.0}}
        #'min': {0: 0.08, 1: 0.2, 2: 0.35, 3: 1.0, 4: 2.0},
        #'max': {0: 0.25, 1: 0.5, 2: 1.2, 3: 3.0, 4: 5.0}}
    df_cats = pd.DataFrame.from_dict(cat_dict)
    df_sizes = pd.read_csv('./data/object_sizes_manual.csv')
    obj_names = suffix_duplicates(objects)
    sizes = {}
    for i,obj_name in enumerate(objects):
        obj_size_cat = df_sizes[df_sizes['object']==obj_name]['sizecat'].values[0]
        curr_cat = df_cats[df_cats['sizecat']==obj_size_cat]
        min_val = curr_cat['min'].values[0]
        max_val = curr_cat['max'].values[0]
        obj_size = random.triangular(min_val, max_val, (min_val + max_val) / 2)
        sizes[obj_names[i]] = [obj_size,obj_size_cat]
    return sizes


def retrieve_realistic_object_color(df_obj_cols,obj_name):
    filtered_df = df_obj_cols[df_obj_cols['object_name'] == obj_name]
    if filtered_df.empty:
        # if no color found, use random color of another object
        filtered_df = df_obj_cols
        #return (255,255,255)
    rows = filtered_df.to_dict(orient='records')
    weights = filtered_df['count'].tolist()
    sampled_row = random.choices(rows, weights=weights, k=1)[0]
    color = (sampled_row['R'],sampled_row['G'],sampled_row['B'])
    return color


def calculateCoords(
        objects, method='sampling', working_combos_precalculated=True,
        print_debug=False, progbar_prefix='', prev_text_len=0,
        return_alignment_diagnostics=False):
    '''Calculate the coordinates of a list of objects
    based on angles and distances between triplets
    objects: a list of the object names
    method: method of selecting triplets ('sampling' or 'averaging')
    dictionary: a dictionary of all object triplet data
    output: a dictionary of final object locations'''

    obj_names = suffix_duplicates(objects)

    if method == 'averaging':
        ## NOT RECOMMENDED: CODE OUTDATED AND UNTESTED
        pickleData = pickle.load(open("data/triplets_avg.pickle",'rb'))
        dictionary = {}
        for i, objA in enumerate(pickleData['objectA']):
            objB = pickleData['objectB'][i]
            objC = pickleData['objectC'][i]
            comboData = [pickleData['distanceAB'][i],pickleData['distanceAC'][i],
                pickleData['distanceAO'][i],pickleData['angleBAC'][i],
                pickleData['angleOAB'][i],pickleData['angleOAC'][i],pickleData['count'][i]]
            dictionary[f'{objA},{objB},{objC}'] = comboData
        print_debug and print("INFO: Loaded triplet data.")

    elif method == 'sampling':

        df = pd.read_csv('./data/triplets.csv')
        require_canonical_triplet_columns(df.columns, source='./data/triplets.csv')

        df = df.sample(frac=1).reset_index(drop=True)
        df = df.drop_duplicates(subset=['objectA','objectB','objectC'],keep='first')
        dictionary = {}
        for i, row in df.iterrows():
            objA = row['objectA']
            objB = row['objectB']
            objC = row['objectC']

            comboData = [row['distanceAB'],row['distanceAC'],row['distanceAO'],
                         row['angleBAC'],row['angleOAB'],row['angleOAC'],1]
            dictionary[f'{objA},{objB},{objC}'] = comboData

    else:
        print_debug and print(f'ERROR: Wrong method selected for calculateCoords: {method}.')
        return {}

    # generate every permutation of the given objects and try to find a set
    # of triplets that will contain enough data to calculate all object locations
    objMix = list(permutations(objects,len(objects)))
    random.shuffle(objMix)

    enoughData = True
    for i,objs in enumerate(objMix):
        print_debug and print("INFO: Attempt "+str(i+1)+"...")
        enoughData = True # set to false if any object isn't in final list
        # create list of necessary triplets based on input objects
        combos = [[objs[0],objs[1],x] for x in objs[2:]]
        good = []
        for combo in combos: # for each combo
            comboStr = ','.join(combo)
            if comboStr not in dictionary:
                print_debug and print("WARNING: Triplet not found in dictionary: " + comboStr)
                continue
            good.append(combo)

        for obj in objects: # if any of the objects aren't in the final list
            if obj not in [x for sublist in good for x in sublist]:
                print_debug and print("ERROR: Not enough data for "+obj+". Retrying...")
                enoughData = False
                break
        if enoughData:
            break

    if not enoughData:
        print_debug and print("ERROR: All tries failed. Exiting...")
        sys.exit(1)

    combos = good
    print_debug and print("INFO: Found good combo.")

    sizes = loadObjectSizes(objects)

    combos_n = suffix_duplicates(combos,nested=True)

    # ensure distance AB is the same for all triplets, so make it equal to mean distance AB of all triplets
    all_ab_dists = []
    for combo in combos:
        all_ab_dists.append(float(dictionary[','.join(combo)][0]))
    d_ab = np.mean(all_ab_dists) # universal distance AB for all triplets

    triplets = []
    for i, combo in enumerate(combos): # for each combo
        comboStr = ','.join(combo)
        data = dictionary[comboStr] # find triplet data
        print_debug and print("INFO: Setting up combo: "+ repr(combo))
        # set up variables
        objA,objB,objC = combos_n[i]

        #d_ab = float(data[0])   # distance between objA and objB
        d_ac = float(data[1])   # distance between objA and objC
        d_ao = float(data[2])   # distance between objA and camera
        aBAC = float(data[3]) # angle BAC
        aOAB = float(data[4]) # angle OAB
        aOAC = float(data[5]) # angle OAC

        # Establish canonical centroid locations for A, B, C, and camera.
        A, B, C, O = reconstruct_triplet_coordinates(
            d_ab, d_ac, d_ao, aBAC, aOAB, aOAC,
        )
        triplets.append([objA,objB,objC,A,B,C,O]) # objA,objB,objC,camera

    # Use the first triplet as the common frame. Every later triplet already
    # shares A and B, so a rotation around A--B is the only rigid alignment
    # that can improve camera agreement without moving either anchor.
    base_triplet = triplets[0]
    finalCoords = {} # initialize {obj: (x,y,z)} structure
    finalCoords[base_triplet[0]] = base_triplet[3] # add objA coords
    finalCoords[base_triplet[1]] = base_triplet[4] # add objB coords
    finalCoords[base_triplet[2]] = base_triplet[5] # add base objC coords
    finalCoords['CAMERA'] = base_triplet[6] # add camera coords
    alignment_diagnostics = []

    for triplet in triplets[1:]:
        aligned_c, _rotated_camera, diagnostic = align_triplet_about_axis(
            base_triplet[3], base_triplet[4], base_triplet[6],
            triplet[3], triplet[4], triplet[6], triplet[5],
            object_name=triplet[2],
            base_object_name=base_triplet[2],
            anchor_names=(base_triplet[0], base_triplet[1]),
        )
        finalCoords[triplet[2]] = aligned_c
        alignment_diagnostics.append(diagnostic)

    # Add these to pass color to blender - locations will be determined in blender
    finalCoords['WALL'] = tuple()
    finalCoords['FLOOR'] = tuple()

    df_obj_cols = pd.read_csv('./data/object_colors.csv')
    obj_colors = {}
    for obj in finalCoords.keys():
        obj_color = retrieve_realistic_object_color(df_obj_cols,obj.split('.')[0].lower())
        obj_colors[obj] = obj_color

    finalCoords = {obj:{
        'size':{
            'diameter':sizes[obj][0] if obj in sizes else 0,
            'category':sizes[obj][1] if obj in sizes else ''},
        'color':obj_colors[obj] if obj in obj_colors else (255,255,255),
        'coords':coords
        } for obj,coords in finalCoords.items()}

    # If any window-related items are present,
    # Make sure there is always a window and then randomly select blinds, curtain, or nothing
    window_objs = ['window','blinds','curtain']
    curr_window_objs = [o for o in finalCoords.keys() if o.split('.')[0] in window_objs]
    if len(curr_window_objs)>0:
        progbar_suffix = f'Windows, blinds, or curtain present: Randomizing a combination of these objects...'
        text_len = progress_bar.update(40, 100, progbar_prefix, progbar_suffix, length=10, prev_text_len=prev_text_len)
        if any('window' in o for o in curr_window_objs):
            for i in range(5):
                suff = f'.{i}' if i>0 else ''
                finalCoords.pop('blinds'+suff,None)
                finalCoords.pop('curtain'+suff,None)
        else:
            obj_to_rename = random.choice(curr_window_objs)
            objs_to_remove = [o for o in curr_window_objs if o!=obj_to_rename]
            finalCoords['window'] = finalCoords.pop(obj_to_rename)
            for obj in objs_to_remove:
                finalCoords.pop(obj)

        # At this point, we've ensured that the calculated coordinates for one of the window
        # objects are now the coordinates of the window itself. Now, we will add blinds or
        # curtain or neither and make them the same location and same size as the window.
        # We will also recalculate the color for each of the items so they are all different.
        finalCoords['window']['color'] = obj_colors['window'] if 'window' in obj_colors else (255,255,255)
        blinds_or_curtain = random.sample(['blinds','curtain'], random.randint(0,2))
        for item in blinds_or_curtain:
            finalCoords[item] = copy.deepcopy(finalCoords['window'])
            #finalCoords[item]['size']['diameter'] +=  finalCoords[item]['size']['diameter']*0.05
            finalCoords[item]['color'] = obj_colors[item] if item in obj_colors else (255,255,255)
    else:
        text_len = prev_text_len

    if return_alignment_diagnostics:
        return finalCoords, text_len, alignment_diagnostics
    return finalCoords, text_len
