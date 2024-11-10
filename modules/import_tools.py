import os
from os import listdir
from os.path import join
import json
import shutil
import gc
import re
import ssl
ssl._create_default_https_context = ssl._create_unverified_context # for Mac compatibility

import urllib.request
from io import BytesIO
import matplotlib.pyplot as plt
import numpy as np
from plyfile import PlyData, PlyElement
#from scipy.spatial.transform import Rotation as R

from PIL import Image

from modules.utils import *

#filename = './extrinsics_test.txt'
def readValuesFromTxt(filename,local):
    if local:
        fid = open(filename,'rb')
        values = [list(map(lambda y: float(y), x.split(' '))) for x in \
		 ''.join(fid.read().decode('utf-8')).split('\n') if x!='']
        fid.close()
    else:
        fid = urllib.request.urlopen(filename)
        file_utf = fid.read().decode('utf-8')
        values = [list(map(lambda y: float(y), x.split(' '))) for x in \
		 ''.join(file_utf).split('\n') if x!='']
        fid.close()
    return values


#filename = '00000-depth.png'
def depthRead(filename,local):
    if local:
        depthfile = Image.open(filename,'r')
    else:
        depthfile = Image.open(BytesIO(urllib.request.urlopen(filename).read()))
    depthmap = np.array(depthfile)
    depthfile.close()
    # bitshift values from 16-bit to 13-bit
    bitshift = [[(d >> 3) or (d << 16-3) for d in row] for row in depthmap]
     # divide values by 1000 to go from millimeters to meters
    depthmap = np.array([[float(d)/1000 for d in row] for row in bitshift])
    gc.collect()
    return depthmap

#K=np.array([[525,0,320],[0,525,240],[0,0,1]])
def depth2XYZcamera(K,depth):
    [x,y] = np.meshgrid(range(0,depth.shape[1]),range(0,depth.shape[0]))
    XYZcamera = []

    # XYZcamera[0]: x-coords of corresponding point in 3d camera coordinate space for each pixel location
    x_coords = (x - K[0, 2]) * depth / K[0, 0]
    XYZcamera.append(x_coords) # units of x_coords is meters

    # XYZcamera[1]: y-coords of corresponding point in 3d camera coordinate space for each pixel location
    y_coords = (y - K[1, 2]) * depth / K[1, 1]
    XYZcamera.append(y_coords) # units of y_coords is meters

    # XYZcamera[2]: z-coords of corresponding point in 3d camera coordinate space for each pixel location
    XYZcamera.append(depth) # units of z_coords is meters

    # XYZcamera[3]: valid points for each pixel location
    valid = [[int(bool(n)) for n in row] for row in depth]
    XYZcamera.append(valid)

    return XYZcamera


def getCameraCoords(XYZcamera):
    XYZ = []
    for j in range(0,len(XYZcamera[0][0])):
        for i in range(0,len(XYZcamera[0])):
            if XYZcamera[3][i][j]:
                data = (XYZcamera[0][i][j],
                        XYZcamera[1][i][j],
                        XYZcamera[2][i][j])
                XYZ.append(data)
    XYZ = np.array(XYZ)
    return XYZ


def camera2XYZworld(XYZcamera,extrinsics):
    XYZ = []
    for j in range(0,len(XYZcamera[0][0])):
        for i in range(0,len(XYZcamera[0])):
            if XYZcamera[3][i][j]:
                data = (XYZcamera[0][i][j],
                        XYZcamera[1][i][j],
                        XYZcamera[2][i][j])
                XYZ.append(data)
    XYZ = np.array(XYZ)
    XYZworld = transformPointCloud(XYZ,extrinsics)
    vals = (XYZworld,XYZcamera[3])
    return vals


def transformPointCloud(XYZ,Rt):
    rotation = [(float(r[0]),float(r[1]),float(r[2])) for r in Rt]
    translation = np.array(Rt)[:3,3]
    return np.matmul(rotation,np.transpose(XYZ)) + np.repeat(np.reshape(translation,(3,1)),XYZ.shape[0],axis=1)

#main_folder = './data/washington/scene_01'
#fname='scene_01'
#jsonDir='./json/washington'
def project_annotations_3D_to_2D(main_folder, fname ,jsonDir, annot_files, nFrame_samples=30):

    for an in annot_files:
        if 'label' in an: lblfile = an
        elif 'pose' in an: posfile = an
        elif 'ply' in an: pcloudfile = an
        else:
            exit('Error in annotation file names')

    pcd = PlyData.read(pcloudfile)
    pcd_data = pcd.elements[0].data
    point_cloud = np.asarray([pcd_data['x'],pcd_data['y'],pcd_data['z']],dtype='float64').T
    camera_poses = np.loadtxt(posfile)
    lbls = np.loadtxt(lblfile)[1:]

    # Construct the transformation matrix for the global origin using First frame's camera pose
    T_global_origin = get_extrinsic_matrix(camera_poses[0])

    # Construct the estimated intrinsic matrix
    K = get_estimated_intrinsic_matrix(640, 480)
    np.savetxt(join(main_folder, 'intrinsics.txt'), K)

    print('points in the cloud: ', point_cloud.shape)
    print('points label: ', lbls.shape)
    print('frames pos: ', camera_poses.shape)

    object_categories = {1: 'bowl', 2: 'cap', 3: 'cereal_box', 4: 'coffee_mug', 5: 'coffee_table',
               6: 'office_chair', 7: 'soda_can', 8: 'sofa', 9: 'table', 10: 'background'}

    flst = sorted(os.listdir(join(main_folder, 'image')))
    # flst = [f'{get_fname(i)}{f}' for i, f in enumerate(flst)]

    data = {}
    frame_polygons_arr = []
    mainObjects = []

    ## Sample 20 random frames within the scene
    # nFrame_samples = 20
    # index = np.random.choice(camera_poses.shape[0], nFrame_samples, replace=False)
    # camera_poses = camera_poses[index]
    # flst = np.array(flst)[index]

    ## Sample every n frames within the scene in sequence, skipping the same # of frames between each included frame
    index = list(range(0,camera_poses.shape[0],int(camera_poses.shape[0]/nFrame_samples)))[:nFrame_samples]
    camera_poses = camera_poses[index]
    flst = np.array(flst)[index]

    extrinsics = []
    frames = [] # list to store object boolean arrays for each frame
    all_imgs_lbls = [] # list to store unique object IDs for each frame
    for i, frame_pos in enumerate(camera_poses):
        print(f'frame {i+1}/{len(camera_poses)}',end='\r',flush=True)
        oimage = Image.open(join(main_folder, 'image', flst[i]))
        # Map 3D points to 2D.
        uv, uvw, extrinsic = project_3d_2d(point_cloud, frame_pos, T_global_origin, K)
        extrinsics.append(extrinsic[:3, :])
        # Construct mask, xy and xyz frames.
        im_frame, xyz_frame, xy_frame, test_frame = construct_frames(uv, point_cloud, lbls, oimage)

        prev_objs = frames[i - 1] if i>0 else None
        prev_lbls = all_imgs_lbls[i - 1] if i>0 else None
        imgs, imgs_lbls = clean_label_mask(im_frame, oimage, prev_objs, prev_lbls)
        frames.append(imgs)

        ## Some issues initially encountered with all_imgs_lbls (useful for viewing the progression of the labels
        ## through the frames - before final cleanup).
        # 1. Remove objects that appear for a single frame - we can assume these are a glitch (RESOLVED)
        # 2. Remove objects that take up too few pixels in a frame - we can assume these are a glitch (RESOLVED)
        # 3. For objects that are matched multiple times to an object in the previous frame,
        #    match only most similar, and use og label for other one (e.g. 9th frame from bottom) (RESOLVED)
        # 4. Objects that pop out of frame and back in get labeled as a new object.
        #    In these cases, re-evaluate if its object category matches any previous
        #    by finding the closest frame of each version of the object and comparing to
        #    only those boolean arrays (e.g. 8_3 should actually be 8_1). (UNRESOLVED: This can happen in theory,
        #    but does not occur in the washington dataset, so did not implement this).
        # 5. For objects that have duplicates somewhere in a scene but in the current and previous frame they are the
        #    only object of its type, they will default to being numbered as 1 even if they were actually 2 in the
        #    previous frame. This means that even for non-dupe objects within a frame, we
        #    still need to compare to objects of the same type in the previous frame to make sure they aren't
        #    a continuation of an object from the previous frame that is numbered higher than 1.
        #    This required doing away with the concept of "dupes", as we need to run the algorithm on all cases
        #    regardless. We are still comparing between only objects of the same type, so if there is only 1
        #    object, the match becomes trivial but we still propagate the label to the next frame. (DONE)
        all_imgs_lbls.append(imgs_lbls)

        object_polygon_arr = []
        for imidx, im in enumerate(imgs):
            # plt.imshow(im)
            # plt.show()
            coords = np.where(im == 1)
            coords = [[coords[1][i],coords[0][i]] for i, c in enumerate(coords[0])]

            result = convex_hull(coords)
            result = np.vstack((result, result[0]))

            xs_idx, ys_idx = zip(*result)
            XY = xy_frame[ys_idx, xs_idx, :]
            if 0 in XY:
                # XY = interpolate_zeros(XY)
                XY = replace_zeroRow_previousRow(XY)
            xs, ys = XY[:, 0], XY[:, 1]

            XYZ = xyz_frame[ys_idx, xs_idx, :]
            if 0 in XYZ:
                XYZ = replace_zeroRow_previousRow(XYZ)

            #cur_obj_name = object_categories[int(imgs_lbls[imidx])]
            obj_idx = int(imgs_lbls[imidx].split('_')[0])
            cur_obj_name = object_categories[obj_idx]
            cur_obj_dict = {'name': imgs_lbls[imidx].replace(f'{obj_idx}_',f'{cur_obj_name}: ')}

            if cur_obj_dict not in mainObjects:
                mainObjects.append(cur_obj_dict)

            object_polygon = {}
            object_polygon["x"] = xs.tolist()
            object_polygon["y"] = ys.tolist()
            object_polygon["XYZ"] = XYZ.tolist()
            object_polygon["object"] = mainObjects.index(cur_obj_dict)
            object_polygon_arr.append(object_polygon)

        frame_polygons = {}
        frame_polygons['polygon'] = object_polygon_arr
        frame_polygons_arr.append(frame_polygons)

        # plt.imshow(oimage)
        # for f in object_polygon_arr:
        #     print(f)
        #     x = f['x']
        #     y = f['y']
        #     plt.plot(x, y)
        # plt.show()
        # exit()

    for i, imgs_lbls in enumerate(all_imgs_lbls):
        prev_lbls = all_imgs_lbls[i-1]
        imgs_lbls
        next_lbls = all_imgs_lbls[i+1] if i+1<len(all_imgs_lbls) else {}

        # Remove objects that appear for a single frame - we can assume these are a glitch
        single_frame_objs = list(set(imgs_lbls) - (set(prev_lbls) or set(next_lbls)))
        for del_count, o in enumerate(single_frame_objs):
            j = imgs_lbls.index(o) - del_count # index shifts each time we delete, so we subtract del_count
            del frame_polygons_arr[i]['polygon'][j]

    data["date"] = ""
    data["name"] = fname
    data["frames"] = frame_polygons_arr
    data['objects'] = mainObjects
    data['extrinsics'] = "extrinsics.txt"
    data['conflictList'] = []
    data['fileList'] = flst.tolist()

    # Final cleanup of "glitch" objects that were included in the list but ended up getting dropped
    # from all frames during processing. When we drop an object from data['objects'], we must also shift
    # all object IDs above it down by one in data['frames'][f]['polygon']['object'] for all frames f
    objs_present = set(sorted([y for x in [sorted([o['object'] for o in data['frames'][f]['polygon']]) for f in range(len(camera_poses))] for y in x]))
    objs_not_present = sorted(list(set(range(len(data['objects'])))-objs_present),reverse=True)
    for idx in objs_not_present:
        for f in range(len(camera_poses)):
            for id_o in range(len(data['frames'][f]['polygon'])):
                o = data['frames'][f]['polygon'][id_o]
                if o['object']>idx:
                    o['object'] = o['object']-1
        del data['objects'][idx]

    extrinsics = np.asarray(extrinsics)
    extrinsics = extrinsics.reshape(extrinsics.shape[0], -1)
    np.savetxt(join(main_folder,'extrinsics/extrinsics.txt'), extrinsics)

    with open(join(jsonDir, f'{fname}.json'), 'w') as f:
        json.dump(data, f,  indent=4)


def pre_process_input_files(dataDir='./data/washington',pcDir='./data/washington/pc',jsonDir='./json/washington',nFrame_samples=30):

    all_json_names = []
    for jFile in [f for f in listdir(jsonDir) if f.endswith('.json')]:
        with open(join(jsonDir,jFile),'rb') as jData:
            data = json.load(jData)
            all_json_names.append(data['name'])

    dirlst = sorted([f for f in os.listdir(dataDir) if f!='pc' and '.' not in f])

    for subDir in dirlst:
        print(f"Pre-processing {subDir} from '{dataDir}'")
        main_folder = join(dataDir,subDir)

        if not os.path.exists(join(main_folder,'image')):
            os.mkdir(join(main_folder,'image'))
        if not os.path.exists(join(main_folder,'depth')):
            os.mkdir(join(main_folder,'depth'))
        if not os.path.exists(join(main_folder,'extrinsics')):
            os.mkdir(join(main_folder,'extrinsics'))

        flst = [f for f in sorted(os.listdir(main_folder)) if 'png' in f]

        for f in flst:
            if 'color' in f:
                shutil.move(join(main_folder, f), join(main_folder, 'image', f))
            elif 'depth' in f:
                shutil.move(join(main_folder, f), join(main_folder, 'depth', f))

        if subDir not in all_json_names:
            annot_files = [join(pcDir, s) for s in sorted(os.listdir(pcDir)) if subDir[-2:] in s and not s.endswith('.png')]
            project_annotations_3D_to_2D(join(dataDir, subDir), subDir, jsonDir, annot_files, nFrame_samples)
            print(f"JSON file saved in '{jsonDir}'")
        else:
            print(f"JSON file already exists in '{jsonDir}'")
