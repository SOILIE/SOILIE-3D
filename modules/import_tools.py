import os
from os import listdir
from os.path import join
import json
import shutil
import gc
import re

import urllib.request
from io import BytesIO
import matplotlib.pyplot as plt
import numpy as np
from plyfile import PlyData, PlyElement
import open3d as o3d
from scipy.spatial.transform import Rotation as R

from PIL import Image

from modules.utils import *

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


def getExtrinsics(extrinsicsC2W,frame):
    extrinsics = [[extrinsicsC2W[0][0][frame],extrinsicsC2W[0][1][frame], \
                   extrinsicsC2W[0][2][frame],extrinsicsC2W[0][3][frame]],\
                  [extrinsicsC2W[1][0][frame],extrinsicsC2W[1][1][frame], \
                   extrinsicsC2W[1][2][frame],extrinsicsC2W[1][3][frame]],\
                  [extrinsicsC2W[2][0][frame],extrinsicsC2W[2][1][frame], \
                   extrinsicsC2W[2][2][frame],extrinsicsC2W[2][3][frame]]]
    return extrinsics


def depthRead(filename,local):
    if local:
        depth = Image.open(filename,'r')
    else:
        depth = Image.open(BytesIO(urllib.request.urlopen(filename).read()))
    depthmap = np.array(depth)
    depth.close()
    bitshift = [[(d >> 3) or (d << 16-3) for d in row] for row in depthmap]
    depthmap = [[float(d)/1000 for d in row] for row in bitshift]
    gc.collect()
    #operation = lambda d: (d >> 3) or (d << 16-3)
    #bitshift = np.vectorize(operation,otypes=[np.float])
    #depthmap = bitshift(depthmap)
    #operation = lambda d: float(d)/1000.0
    #div = np.vectorize(operation,otypes=[np.float])
    #depthmap = div(depthmap)
    #gc.collect()
    return depthmap


def depth2XYZcamera(K,depth):
    [x,y] = np.meshgrid(range(1,641),range(1,481))
    XYZcamera = []
    #operation = lambda op,n1,n2: eval(str(n1)+str(op)+str(n2))
    #op_apply = np.vectorize(operation)
    l = np.multiply([[d - K[0][2] for d in row] for row in x], \
                    [[d / K[0][0] for d in row] for row in depth])
    #x = None
    #l = np.multiply(op_apply('-',x,K[0][2]), \
    #                op_apply('/',depth,K[0][0]))
    XYZcamera.append(l)
    l = np.multiply([[d - K[1][2] for d in row] for row in y], \
                    [[d / K[1][1] for d in row] for row in depth])
    #l = np.multiply(op_apply('-',y,K[1][2]), \
    #                op_apply('/',depth,K[1][1]))
    XYZcamera.append(l)
    #l = y = x = None
    #gc.collect()
    XYZcamera.append(depth)
    #operation = lambda(n): int(bool(n))
    #op_apply = np.vectorize(operation)
    #depth = op_apply(depth)
    depth = [[int(bool(n)) for n in row] for row in depth]
    #operation = None
    #op_apply = None
    XYZcamera.append(depth)
    #depth = None
    #XYZcamera = np.swapaxes(XYZcamera,0,2)
    #XYZcamera = np.swapaxes(XYZcamera,0,1)
    #gc.collect()
    return XYZcamera


def camera2XYZworld(XYZcamera,extrinsics):
    XYZ = []
    #print "XYZcamera:",len(XYZcamera),len(XYZcamera[0]),len(XYZcamera[0][0])
    for j in range(0,len(XYZcamera[0][0])):
        for i in range(0,len(XYZcamera[0])):
            if XYZcamera[3][i][j]:
                data = (XYZcamera[0][i][j],\
                        XYZcamera[1][i][j],\
                        XYZcamera[2][i][j])
                XYZ.append(data)
    #print "XYZ:",len(XYZ),len(XYZ[0])
    XYZworld = transformPointCloud(XYZ,extrinsics)
    vals = (XYZworld,XYZcamera[3])
    return vals


def transformPointCloud(XYZ,Rt):
    Rt = [(float(t[0]),float(t[1]),float(t[2])) for t in Rt]
    return np.matmul(Rt,np.transpose(XYZ))

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

    pcd = o3d.io.read_point_cloud(pcloudfile)
    point_cloud = np.asarray(pcd.points)
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
    for i, frame_pos in enumerate(camera_poses):

        oimage = Image.open(join(main_folder, 'image', flst[i]))
        # Map 3D points to 2D.
        uv, uvw, extrinsic = project_3d_2d(point_cloud, frame_pos, T_global_origin, K)
        extrinsics.append(extrinsic[:3, :])

        # Construct mask, xy and xyz frames.
        im_frame, xyz_frame, xy_frame, test_frame = construct_frames(uv, point_cloud, lbls, oimage)

        imgs, imgs_lbls = clean_mask(im_frame, oimage)

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

            cur_obj_name = object_categories[int(imgs_lbls[imidx])]

            matching_dicts = {}
            for cur_objDict in mainObjects:
                if cur_obj_name in cur_objDict['name']:
                    matching_dicts = cur_objDict

            if not matching_dicts:
                mainObjects.append({'name': f'{cur_obj_name}: 1'})
            else:
                mainObjects.append({'name': f'{cur_obj_name}: {int(matching_dicts["name"][-1])+1}'})

            object_polygon = {}
            object_polygon["x"] = xs.tolist()
            object_polygon["y"] = ys.tolist()
            object_polygon["XYZ"] = XYZ.tolist()
            object_polygon["object"] = len(mainObjects)-1 #int(imgs_lbls[imidx])
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

    data["date"] = ""
    data["name"] = fname
    data["frames"] = frame_polygons_arr
    data['objects'] = mainObjects
    data['extrinsics'] = "extrinsics.txt"
    data['conflictList'] = []
    data['fileList'] = flst.tolist()

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
            annot_files = [join(pcDir, s) for s in sorted(os.listdir(pcDir)) if subDir[-2:] in s]
            project_annotations_3D_to_2D(join(dataDir, subDir), subDir, jsonDir, annot_files, nFrame_samples)
            print(f"JSON file saved in '{jsonDir}'")
        else:
            print(f"JSON file already exists in '{jsonDir}'")
from sys import platform
