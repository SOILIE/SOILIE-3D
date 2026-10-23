import matplotlib.pyplot as plt
import numpy as np
from skimage.morphology import disk
from skimage.morphology import closing, remove_small_objects
from skimage.measure import label
from scipy.spatial.distance import cdist
from scipy.optimize import linear_sum_assignment

def quaternion_to_rotation_matrix(quat):
    a, b, c, d = quat
    return np.array([
        [1 - 2*(c**2 + d**2), 2*(b*c - a*d), 2*(a*c + b*d)],
        [2*(b*c + a*d), 1 - 2*(b**2 + d**2), 2*(c*d - a*b)],
        [2*(b*d - a*c), 2*(a*b + c*d), 1 - 2*(b**2 + c**2)]
    ])


def quaternion_rotation_matrix(Q):
    """
    Convert a quaternion into a full three-dimensional rotation matrix.

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

def mapped_polygon_samples(xy, xyz):
    '''Drop unmapped raster-fill vertices without treating real zeros as empty.'''

    xy = np.asarray(xy, dtype=np.float64)
    xyz = np.asarray(xyz, dtype=np.float64)
    if xy.ndim != 2 or xy.shape[1:] != (2,):
        raise ValueError('xy polygon samples must be an Nx2 array')
    if xyz.shape != (len(xy), 3):
        raise ValueError('xyz polygon samples must be an Nx3 array aligned with xy')
    mapped = np.all(np.isfinite(xy), axis=1) & np.all(np.isfinite(xyz), axis=1)
    xy = xy[mapped]
    xyz = xyz[mapped]
    if not len(xy):
        raise ValueError('polygon contains no mapped source points')
    if not np.array_equal(xy[0], xy[-1]):
        xy = np.vstack((xy, xy[0]))
        xyz = np.vstack((xyz, xyz[0]))
    return xy, xyz

def project_3d_2d(points_3d, frame_pos, K):
    '''Project world points and retain camera depth for visibility filtering.'''

    points_3d = np.asarray(points_3d, dtype=np.float64)
    frame_pos = np.asarray(frame_pos, dtype=np.float64)
    K = np.asarray(K, dtype=np.float64)
    if points_3d.ndim != 2 or points_3d.shape[1:] != (3,):
        raise ValueError('points_3d must be an Nx3 array')
    if not np.all(np.isfinite(points_3d)):
        raise ValueError('points_3d must contain only finite coordinates')
    if frame_pos.shape != (7,) or not np.all(np.isfinite(frame_pos)):
        raise ValueError('frame_pos must be a finite seven-value camera pose')
    if K.shape != (3, 3) or not np.all(np.isfinite(K)):
        raise ValueError('K must be a finite 3x3 matrix')

    T_frame = get_extrinsic_matrix(frame_pos)

    points_homogeneous = np.hstack((points_3d, np.ones((points_3d.shape[0], 1))))
    T_relative = np.linalg.inv(T_frame)
    points_camera = np.dot(T_relative, points_homogeneous.T).T[:, :3]

    # print(points_camera.shape)

    camera_depth = points_camera[:, 2]
    uv = np.full((len(points_camera), 2), np.nan, dtype=np.float64)
    visible = np.isfinite(camera_depth) & (camera_depth > 0.0)
    if np.any(visible):
        uvw = np.dot(K, points_camera[visible].T).T
        uv[visible] = uvw[:, :2] / uvw[:, 2, np.newaxis]

    return uv, camera_depth, T_frame


def construct_frames(uv, camera_depth, point_cloud, lbls, *, pixel_origin):
    '''Rasterize the nearest positive-depth point at each source pixel.'''

    if type(pixel_origin) is not int or pixel_origin not in {0, 1}:
        raise ValueError('pixel_origin must be the integer 0 or 1')
    uv = np.asarray(uv, dtype=np.float64)
    camera_depth = np.asarray(camera_depth, dtype=np.float64)
    point_cloud = np.asarray(point_cloud, dtype=np.float64)
    lbls = np.asarray(lbls)
    if camera_depth.ndim != 1:
        raise ValueError('camera_depth must be one-dimensional')
    point_count = camera_depth.shape[0]
    if uv.shape != (point_count, 2):
        raise ValueError('uv must contain one 2D coordinate per camera depth')
    if point_cloud.shape != (point_count, 3):
        raise ValueError('point_cloud must contain one 3D point per camera depth')
    if lbls.shape != (point_count,):
        raise ValueError('lbls must contain one label per camera depth')
    if not np.all(np.isfinite(point_cloud)) or not np.all(np.isfinite(lbls)):
        raise ValueError('point_cloud and lbls must contain only finite values')
    im_frame = np.zeros((480, 640), dtype=np.uint8)
    # NaN is an unambiguous empty sentinel: zero is a valid image coordinate
    # and can also be a valid component of a world-space point.
    xyz_frame = np.full((480, 640, 3), np.nan, dtype=np.float64)
    xy_frame = np.full((480, 640, 2), np.nan, dtype=np.float64)
    z_buffer = np.full((480, 640), np.inf, dtype=np.float64)

    for uv_idx, (point, depth) in enumerate(zip(uv, camera_depth)):
        if depth <= 0.0 or not np.isfinite(depth) or not np.all(np.isfinite(point)):
            continue
        # Quantize once and retain that exact source-grid coordinate in the
        # annotation.  Otherwise raster truncation and collector-side rounding
        # can select adjacent depth pixels for the same polygon vertex.
        source_x, source_y = np.rint(point).astype(int)
        x = source_x - pixel_origin
        y = source_y - pixel_origin
        if 0 <= x < 640 and 0 <= y < 480 and depth < z_buffer[y, x]:
            # A scene point cloud can project many surfaces into one pixel.
            # A nearer point wins regardless of PLY record order, avoiding the
            # old last-row-wins labeling of occluded scene data.
            z_buffer[y, x] = depth
            im_frame[y, x] = int(lbls[uv_idx])  # Set pixel to white
            xyz_frame[y, x, :] = point_cloud[uv_idx]
            xy_frame[y, x, :] = (source_x, source_y)
    return im_frame, xyz_frame, xy_frame


def clean_label_mask(im_frame, oimg, prev_objs=None, prev_lbls=None):
    imgs = []
    imgs_lbls = []
    uniques = set(np.unique(im_frame)) - {0,10} # remove cases of 0 (no label) and 10 (background)

    for un in uniques:
        mask = np.zeros_like(im_frame).astype(bool)
        idx = np.where(im_frame == un)
        mask[idx] = True
        footprint = disk(6)
        mask = closing(mask, footprint)
        fmask = remove_small_objects(mask, 30)

        # plt.imshow(oimg)
        # plt.imshow(fmask, alpha=0.5)
        # plt.imshow(XA[1],alpha=0.3)
        # plt.show()
        label_image = label(fmask)

        lbl_uniqe = np.unique(label_image)
        for lblidx in lbl_uniqe[1:]:
            tmp_im = np.zeros_like(label_image)
            tmp_im[label_image == lblidx] = 1
            imgs.append(tmp_im)
            prev_suffixes = sorted([int(l.split('_')[1]) for l in imgs_lbls if l.startswith(str(un)) and '_' in l])
            if len(prev_suffixes)==0:
                suffix = '_1'
            else:
                suffix = '_'+str(int(prev_suffixes[-1]+1))
            imgs_lbls.append(str(un)+suffix)

    # Drop any objects that take up too few pixels in the frame - these appear to be a glitch
    pixel_counts = [np.count_nonzero(im) for im in imgs]
    pixel_percent = [pc/np.prod(imgs[i].shape) for i,pc in enumerate(pixel_counts)]
    imgs = [im for i, im in enumerate(imgs) if pixel_percent[i]>=0.002]
    imgs_lbls = [l for i, l in enumerate(imgs_lbls) if pixel_percent[i]>=0.002]

    # If this is not the first frame
    if prev_objs:
        imgs_lbls_og = imgs_lbls.copy() # Save initially identified imgs_lbls
        unique_labels = sorted(list(set([l.split('_')[0] for l in imgs_lbls+prev_lbls])))

        for lbl in unique_labels:
            # Find indices of current label in current and previous sets
            prev_lbl_idx = [i for i, l in enumerate(prev_lbls) if l.startswith(lbl)]
            curr_lbl_idx = [i for i, l in enumerate(imgs_lbls) if l.startswith(lbl)]
            if curr_lbl_idx==[] or prev_lbl_idx==[]:
                # if the label only existed in one frame but not the other (current or previous)
                continue # skip to the next label

            # Create input arrays for jaccard similarity scoring
            XA = np.take(np.array(imgs), curr_lbl_idx, axis=0)
            XB = np.take(np.array(prev_objs), prev_lbl_idx, axis=0)

            # Calculate similarity scores between objects in the current and previous frames
            jaccard_dist = cdist(XA.reshape(XA.shape[0], -1), XB.reshape(XB.shape[0], -1), metric='jaccard')

            # Because we are comparing sequential frames of a video, we can improve matching by
            # building in the assumption that objects tend to have a gradual progression from one
            # frame to the next rather than bouncing around. We do this by adding a spatial
            # penalty based on the Euclidean distance between object centroids.
            centroids_A = np.array([np.array(np.where(obj)).T.mean(axis=0) for obj in XA])
            centroids_B = np.array([np.array(np.where(obj)).T.mean(axis=0) for obj in XB])
            spatial_penalty = cdist(centroids_A, centroids_B, metric='euclidean') * 0.001

            # Combine Jaccard distance and spatial penalty
            similarity_matrix = jaccard_dist + spatial_penalty

            # Use the Hungarian algorithm to find the optimal assignment
            row_ind, col_ind = linear_sum_assignment(similarity_matrix)

            # Replace label only if a matching previous label of the same object type is found
            # Otherwise assign the same object label (prefix) and increment the number (suffix)
            for i, idx in enumerate(curr_lbl_idx):
                if i in row_ind:
                    img_lbl = prev_lbls[prev_lbl_idx[col_ind[np.where(row_ind==i)[0][0]]]]
                else:
                    img_lbl = imgs_lbls_og[curr_lbl_idx[i]].split('_')[0]
                imgs_lbls[idx] = img_lbl

            not_suffixed = [l for l in imgs_lbls if '_' not in l]
            for prefix in not_suffixed:
                suffixes = [int(l.split('_')[1]) for l in imgs_lbls if '_' in l and l.startswith(prefix)]
                max_suff = max(suffixes) if suffixes else 0
                img_lbl = prefix+'_'+str(max_suff+1)
                imgs_lbls[imgs_lbls.index(prefix)] = img_lbl

    return imgs, imgs_lbls
