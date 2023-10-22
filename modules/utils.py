import matplotlib.pyplot as plt
import numpy as np
from skimage.morphology import disk
from skimage.morphology import closing, remove_small_objects
from skimage.measure import label

def quaternion_to_rotation_matrix(quat):
    a, b, c, d = quat
    return np.array([
        [1 - 2*(c**2 + d**2), 2*(b*c - a*d), 2*(a*c + b*d)],
        [2*(b*c + a*d), 1 - 2*(b**2 + d**2), 2*(c*d - a*b)],
        [2*(b*d - a*c), 2*(a*b + c*d), 1 - 2*(b**2 + c**2)]
    ])


def quaternion_rotation_matrix(Q):
    """
    Covert a quaternion into a full three-dimensional rotation matrix.

    Input
    :param Q: A 4 element array representing the quaternion (q0,q1,q2,q3)

    Output
    :return: A 3x3 element matrix representing the full 3D rotation matrix.
             This rotation matrix converts a point in the local reference
             frame to a point in the global reference frame.
    """
    # Extract the values from Q
    q0 = Q[0]
    q1 = Q[1]
    q2 = Q[2]
    q3 = Q[3]

    # First row of the rotation matrix
    r00 = 2 * (q0 * q0 + q1 * q1) - 1
    r01 = 2 * (q1 * q2 - q0 * q3)
    r02 = 2 * (q1 * q3 + q0 * q2)

    # Second row of the rotation matrix
    r10 = 2 * (q1 * q2 + q0 * q3)
    r11 = 2 * (q0 * q0 + q2 * q2) - 1
    r12 = 2 * (q2 * q3 - q0 * q1)

    # Third row of the rotation matrix
    r20 = 2 * (q1 * q3 - q0 * q2)
    r21 = 2 * (q2 * q3 + q0 * q1)
    r22 = 2 * (q0 * q0 + q3 * q3) - 1

    # 3x3 rotation matrix
    rot_matrix = np.array([[r00, r01, r02],
                           [r10, r11, r12],
                           [r20, r21, r22]])

    return rot_matrix


def get_extrinsic_matrix(camera_poses):

    quat = camera_poses[:4]
    translation = camera_poses[4:]

    # Convert quaternion to rotation matrix
    # R = quaternion_rotation_matrix(quat)
    R = quaternion_to_rotation_matrix(quat)

    mx = np.eye(4)
    mx[:3, :3] = R
    mx[:3, 3] = translation

    return mx

def get_estimated_intrinsic_matrix(w, h):
    fx = 525.0  # Focal length in pixels (typically around 525 for 640x480 images)
    fy = 525.0  # Focal length in pixels (typically around 525 for 640x480 images)

    cx = float(w/2)  # Principal point (image center) in pixels (half of 640)
    cy = float(h/2)  # Principal point (image center) in pixels (half of 480)
    K = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]])

    return K


from scipy.spatial import ConvexHull
def convex_hull(coordinates):
    # Convert the list of coordinates to a NumPy array
    points = np.array(coordinates)

    # Compute the convex hull
    hull = ConvexHull(points)

    # Extract the vertices of the convex hull
    convex_hull_points = points[hull.vertices]


    return convex_hull_points

def interpolate_zeros(XY):
    from scipy.interpolate import interp1d

    x, y = XY[:,0], XY[:,1]

    xrange = np.arange(len(x))
    yrange = np.arange(len(y))

    idx = np.where(x != 0)
    f = interp1d(xrange[idx], x[idx])
    x = f(xrange)

    idx = np.where(y != 0)
    f = interp1d(yrange[idx], y[idx])
    y = f(yrange)

    return np.vstack((x,y)).T

def replace_zeroRow_previousRow(XYZ):
    XYZ = XYZ[~(XYZ == 0).all(axis=1)]
    XYZ = np.vstack([XYZ, XYZ[0]])

    # zero_rows = (XYZ == 0).all(axis=1)
    # index_array = np.arange(len(XYZ))
    # replace_indices = index_array[zero_rows]
    # if len(replace_indices) > 0:
    #     for ridx in replace_indices:
    #         if ridx > 0:
    #             XYZ[ridx] = XYZ[ridx - 1]
    #         else:
    #             XYZ[ridx] = XYZ[-1]



    return XYZ

def project_3d_2d(points_3d, frame_pos, T_global_origin, K):
    '''
    Vectorized function to project 3d points into 2D frame giving the camera characteristics.
    :param points_3d:
    :param frame_pos:
    :param K:
    :return:
    '''

    T_frame = get_extrinsic_matrix(frame_pos)

    points_homogeneous = np.hstack((points_3d, np.ones((points_3d.shape[0], 1))))
    T_relative = np.linalg.inv(T_frame)
    points_camera = np.dot(T_relative, points_homogeneous.T).T[:, :3]

    # print(points_camera.shape)

    uvw = np.dot(K, points_camera.T).T
    uv = (uvw[:, :2] / uvw[:, 2].reshape(-1, 1))

    # exit()
    ######################
    # T_relative = np.linalg.inv(T_global_origin) @ T_frame
    #
    # print(points_3d.shape)
    # # # Get the 3D points as a Nx4 array
    # points_homogeneous = np.hstack((points_3d, np.ones((points_3d.shape[0], 1))))
    # # Transform 3D points to camera coordinates
    # points_camera = np.dot(T_relative, points_homogeneous.T).T[:, :3]
    #
    # print(points_camera.shape)
    #
    # uvw = np.dot(K, points_camera.T).T
    # uv = (uvw[:, :2] / uvw[:, 2].reshape(-1, 1))
    #
    # print(uv.shape)

    ##############################
    # Project all global coordinates to 2D image coordinates
    # uvw = np.dot(K, global_points.T)
    # uv = (uvw[:2] / uvw[2]).T

    return uv, uvw, T_frame


def construct_frames(uv, point_cloud, lbls, oimage):
    frame_coords_idx = []
    im_frame = np.zeros((480, 640), dtype=np.uint8)
    xyz_frame = np.zeros((480, 640, 3), dtype=float)
    xy_frame = np.zeros((480, 640, 2), dtype=float)
    test_frame = np.zeros((480, 640, 2), dtype=float)

    for uv_idx, point in enumerate(uv):
        x, y = map(int, point)
        if 0 <= x < 640 and 0 <= y < 480:
            im_frame[y, x] = int(lbls[uv_idx])  # Set pixel to white
            xyz_frame[y, x, :] = point_cloud[uv_idx]
            xy_frame[y, x, :] = uv[uv_idx]
            frame_coords_idx.append(uv_idx)
            test_frame = point_cloud[uv_idx, :2] #

    # plt.imshow(oimage)
    # plt.imshow(im_frame, alpha=0.3)
    # plt.show()
    # exit()
    return im_frame, xyz_frame, xy_frame, test_frame


def clean_mask(im_frame, oimg):
    imgs = []
    imgs_lbls = []
    uniques = np.unique(im_frame)

    for un in uniques[1:-1]:
        mask = np.zeros_like(im_frame).astype(bool)
        idx = np.where(im_frame == un)

        mask[idx] = True

        footprint = disk(6)
        mask = closing(mask, footprint)

        fmask = remove_small_objects(mask, 30)

        # plt.imshow(oimg)
        # plt.imshow(fmask, alpha=0.5)
        # plt.show()
        # exit()
        label_image = label(fmask)

        lbl_uniqe = np.unique(label_image)
        for lblidx in lbl_uniqe[1:]:
            tmp_im = np.zeros_like(label_image)
            tmp_im[label_image == lblidx] = 1
            imgs.append(tmp_im)
            imgs_lbls.append(un)

    return imgs, imgs_lbls
