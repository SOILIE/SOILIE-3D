import os
from os import listdir
from os.path import join
import hashlib
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

from modules.source_geometry import (
    EXACT_FRAME_ID,
    SourceGeometryError,
    WASHINGTON_RGBD_PROFILE,
    WASHINGTON_RGBD_SCENES_V2_CALIBRATION_METHOD,
    WASHINGTON_RGBD_SCENES_V2_CALIBRATION_SOURCE,
    WASHINGTON_RGBD_SCENES_V2_INTRINSICS,
    decode_depth,
    pair_rgb_depth_files,
    pinned_extrinsics_filename,
    reshape_c2w_rows,
    reshape_intrinsics,
    resolve_source_profile,
    validate_c2w,
)
from modules.utils import *


WASHINGTON_PREPROCESS_RECEIPT = 'preprocess-receipt.json'
WASHINGTON_PREPROCESS_RECEIPT_KIND = 'soilie3d.washington-preprocess'
WASHINGTON_PREPROCESS_RECEIPT_VERSION = 3


class WashingtonPreprocessError(ValueError):
    '''Washington source artifacts cannot be reused without a reviewed rebuild.'''


def _washington_source_geometry():
    '''Return a fresh JSON-safe copy of the reviewed native source contract.'''

    profile = WASHINGTON_RGBD_PROFILE
    return {
        'depthEncoding': profile.depth_encoding,
        'pairingStrategy': profile.pairing_strategy,
        'poseIndexStrategy': profile.pose_index_strategy,
        'pixelOrigin': profile.pixel_origin,
    }


def _washington_calibration():
    '''Return the reviewed calibration derived from first-party v2 artifacts.'''

    return {
        'model': 'pinhole',
        'intrinsics': [list(row) for row in WASHINGTON_RGBD_SCENES_V2_INTRINSICS],
        'pixelOrigin': WASHINGTON_RGBD_PROFILE.pixel_origin,
        'derivation': {
            'method': WASHINGTON_RGBD_SCENES_V2_CALIBRATION_METHOD,
            'source': WASHINGTON_RGBD_SCENES_V2_CALIBRATION_SOURCE,
        },
    }


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _strict_json(path):
    def reject_duplicate_keys(pairs):
        document = {}
        for key, value in pairs:
            if key in document:
                raise WashingtonPreprocessError(
                    f'Duplicate JSON key {key!r} in {path}',
                )
            document[key] = value
        return document

    try:
        with open(path, encoding='utf-8') as source:
            return json.load(source, object_pairs_hook=reject_duplicate_keys)
    except WashingtonPreprocessError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WashingtonPreprocessError(f'Cannot read JSON file {path}: {exc}') from exc


def _require_exact_keys(value, expected, *, field):
    if not isinstance(value, dict):
        raise WashingtonPreprocessError(f'{field} must be a JSON object')
    observed = set(value)
    expected = set(expected)
    missing = sorted(expected - observed)
    unknown = sorted(observed - expected)
    if missing or unknown:
        details = []
        if missing:
            details.append(f'missing {missing}')
        if unknown:
            details.append(f'unknown {unknown}')
        raise WashingtonPreprocessError(
            f'Invalid {field}: ' + '; '.join(details),
        )


def _leaf_name(value, *, field):
    if (
        not isinstance(value, str)
        or not value
        or value in {'.', '..'}
        or os.path.basename(value) != value
        or '/' in value
        or '\\' in value
    ):
        raise WashingtonPreprocessError(f'{field} must be one non-empty filename')
    return value


def _sha256_text(value, *, field):
    if not isinstance(value, str) or re.fullmatch(r'[0-9a-f]{64}', value) is None:
        raise WashingtonPreprocessError(f'{field} must be a lowercase SHA-256 digest')
    return value


def _pose_rows(path):
    try:
        rows = np.asarray(np.loadtxt(path), dtype=np.float64)
    except (OSError, TypeError, ValueError) as exc:
        raise WashingtonPreprocessError(f'Cannot read Washington poses {path}: {exc}') from exc
    rows = np.atleast_2d(rows)
    if rows.ndim != 2 or rows.shape[1] != 7 or not rows.size or not np.all(np.isfinite(rows)):
        raise WashingtonPreprocessError(
            'Washington pose annotations must contain finite seven-value rows',
        )
    return rows


def _classify_annotation_files(annot_files):
    classified = {}
    for raw_path in annot_files:
        path = os.fspath(raw_path)
        if not os.path.isfile(path):
            raise WashingtonPreprocessError(f'Annotation input does not exist: {path}')
        lowered = os.path.basename(path).casefold()
        matches = []
        if 'label' in lowered:
            matches.append('labels')
        if 'pose' in lowered:
            matches.append('poses')
        if lowered.endswith('.ply'):
            matches.append('pointCloud')
        if len(matches) != 1:
            raise WashingtonPreprocessError(
                f'Cannot classify Washington annotation input {path!r}; expected one '
                'label file, one pose file, and one .ply point cloud',
            )
        kind = matches[0]
        if kind in classified:
            raise WashingtonPreprocessError(
                f'Duplicate Washington {kind} annotation inputs: '
                f'{classified[kind]!r} and {path!r}',
            )
        classified[kind] = path
    missing = sorted({'labels', 'poses', 'pointCloud'} - set(classified))
    if missing:
        raise WashingtonPreprocessError(
            f'Missing Washington annotation inputs: {missing}',
        )
    return classified


def _washington_frame_pairs(main_folder):
    image_dir = join(main_folder, 'image')
    depth_dir = join(main_folder, 'depth')
    if not os.path.isdir(image_dir) or not os.path.isdir(depth_dir):
        raise WashingtonPreprocessError(
            'Washington preprocessing requires image and depth directories',
        )
    image_names = sorted(
        name for name in os.listdir(image_dir)
        if os.path.isfile(join(image_dir, name))
    )
    depth_names = sorted(
        name for name in os.listdir(depth_dir)
        if os.path.isfile(join(depth_dir, name))
    )
    try:
        pairs = pair_rgb_depth_files(image_names, depth_names, EXACT_FRAME_ID)
    except SourceGeometryError as exc:
        raise WashingtonPreprocessError(str(exc)) from exc
    if not pairs:
        raise WashingtonPreprocessError('Washington scene has no paired RGB-D frames')
    return pairs


def _sample_indices(frame_count, nFrame_samples):
    if type(nFrame_samples) is not int or nFrame_samples < 1:
        raise WashingtonPreprocessError('nFrame_samples must be a positive integer')
    if nFrame_samples > frame_count:
        raise WashingtonPreprocessError(
            f'nFrame_samples ({nFrame_samples}) exceeds available paired frames '
            f'({frame_count})',
        )
    step = frame_count // nFrame_samples
    return list(range(0, frame_count, step))[:nFrame_samples]


def _sample_identity(main_folder, pairs, indices):
    return [
        {
            'poseIndex': index,
            'frameId': pairs[index].image_frame_id,
            'image': pairs[index].image_name,
            'imageSha256': _sha256_file(
                join(main_folder, 'image', pairs[index].image_name),
            ),
            'depth': pairs[index].depth_name,
            'depthSha256': _sha256_file(
                join(main_folder, 'depth', pairs[index].depth_name),
            ),
        }
        for index in indices
    ]


def _build_preprocess_receipt(
    *,
    main_folder,
    fname,
    json_path,
    annotation_files,
    requested_frame_samples,
    source_frame_count,
    source_pose_count,
    samples,
    extrinsics_filename,
):
    return {
        'schemaVersion': WASHINGTON_PREPROCESS_RECEIPT_VERSION,
        'kind': WASHINGTON_PREPROCESS_RECEIPT_KIND,
        'scene': fname,
        'sourceGeometry': _washington_source_geometry(),
        'calibration': _washington_calibration(),
        'requestedFrameSamples': requested_frame_samples,
        'sourceFrameCount': source_frame_count,
        'sourcePoseCount': source_pose_count,
        'samples': samples,
        'annotations': {
            kind: {
                'name': os.path.basename(path),
                'sha256': _sha256_file(path),
            }
            for kind, path in sorted(annotation_files.items())
        },
        'outputs': {
            'json': {
                'name': os.path.basename(json_path),
                'sha256': _sha256_file(json_path),
            },
            'intrinsics': {
                'name': 'intrinsics.txt',
                'sha256': _sha256_file(join(main_folder, 'intrinsics.txt')),
            },
            'extrinsics': {
                'name': extrinsics_filename,
                'sha256': _sha256_file(
                    join(main_folder, 'extrinsics', extrinsics_filename),
                ),
            },
        },
    }


def _validate_receipt_file_record(record, *, field, expected_name, expected_path):
    _require_exact_keys(record, {'name', 'sha256'}, field=field)
    name = _leaf_name(record['name'], field=f'{field}.name')
    if name != expected_name:
        raise WashingtonPreprocessError(
            f'{field}.name is {name!r}; expected {expected_name!r}',
        )
    expected_hash = _sha256_text(record['sha256'], field=f'{field}.sha256')
    if not os.path.isfile(expected_path):
        raise WashingtonPreprocessError(f'{field} is missing: {expected_path}')
    observed_hash = _sha256_file(expected_path)
    if observed_hash != expected_hash:
        raise WashingtonPreprocessError(
            f'{field} is stale: SHA-256 {observed_hash} does not match receipt '
            f'{expected_hash}',
        )


def _validate_preprocess_receipt(
    *,
    main_folder,
    fname,
    jsonDir,
    pcDir,
    nFrame_samples,
):
    receipt_path = join(main_folder, WASHINGTON_PREPROCESS_RECEIPT)
    receipt = _strict_json(receipt_path)
    if not isinstance(receipt, dict):
        raise WashingtonPreprocessError(
            'Washington preprocess receipt must be a JSON object',
        )
    receipt_version = receipt.get('schemaVersion')
    if (
        type(receipt_version) is not int
        or receipt_version != WASHINGTON_PREPROCESS_RECEIPT_VERSION
    ):
        raise WashingtonPreprocessError(
            f'Unsupported Washington preprocess receipt schemaVersion '
            f'{receipt_version!r}; rebuild with the reviewed native geometry '
            'and calibration contract',
        )
    _require_exact_keys(
        receipt,
        {
            'schemaVersion', 'kind', 'scene', 'sourceGeometry',
            'calibration',
            'requestedFrameSamples', 'sourceFrameCount', 'sourcePoseCount',
            'samples', 'annotations', 'outputs',
        },
        field='Washington preprocess receipt',
    )
    if receipt['kind'] != WASHINGTON_PREPROCESS_RECEIPT_KIND:
        raise WashingtonPreprocessError('Washington preprocess receipt kind is invalid')
    if receipt['scene'] != fname:
        raise WashingtonPreprocessError(
            f'Washington preprocess receipt scene {receipt["scene"]!r} does not '
            f'match {fname!r}',
        )
    try:
        resolve_source_profile('washington', receipt['sourceGeometry'])
    except SourceGeometryError as exc:
        raise WashingtonPreprocessError(
            f'Washington preprocess receipt sourceGeometry is invalid: {exc}',
        ) from exc
    if receipt['sourceGeometry'] != _washington_source_geometry():
        raise WashingtonPreprocessError(
            'Washington preprocess receipt sourceGeometry is not the reviewed '
            'native RGB-D Scenes v2 contract',
        )
    if receipt['calibration'] != _washington_calibration():
        raise WashingtonPreprocessError(
            'Washington preprocess receipt calibration does not match the '
            'reviewed native RGB-D Scenes v2 artifact derivation',
        )

    for field in ('requestedFrameSamples', 'sourceFrameCount', 'sourcePoseCount'):
        value = receipt[field]
        if type(value) is not int or value < 1:
            raise WashingtonPreprocessError(f'{field} must be a positive integer')
    if (
            nFrame_samples is not None
            and receipt['requestedFrameSamples'] != nFrame_samples):
        raise WashingtonPreprocessError(
            f'requestedFrameSamples is {receipt["requestedFrameSamples"]}; '
            f'current request is {nFrame_samples}',
        )

    pairs = _washington_frame_pairs(main_folder)
    if len(pairs) != receipt['sourceFrameCount']:
        raise WashingtonPreprocessError(
            f'Paired frame count {len(pairs)} does not match receipt '
            f'{receipt["sourceFrameCount"]}',
        )
    expected_indices = _sample_indices(
        len(pairs),
        receipt['requestedFrameSamples'],
    )
    expected_samples = _sample_identity(main_folder, pairs, expected_indices)
    if receipt['samples'] != expected_samples:
        raise WashingtonPreprocessError(
            'Washington sampled RGB-D frame identity or content does not match receipt',
        )

    annotations = receipt['annotations']
    _require_exact_keys(
        annotations,
        {'labels', 'poses', 'pointCloud'},
        field='Washington preprocess receipt annotations',
    )
    annotation_paths = {}
    for kind, record in annotations.items():
        if not isinstance(record, dict):
            raise WashingtonPreprocessError(
                f'Washington preprocess receipt annotations.{kind} must be an object',
            )
        name = _leaf_name(record.get('name'), field=f'annotations.{kind}.name')
        path = join(pcDir, name)
        _validate_receipt_file_record(
            record,
            field=f'annotations.{kind}',
            expected_name=name,
            expected_path=path,
        )
        annotation_paths[kind] = path
    if _classify_annotation_files(annotation_paths.values()) != annotation_paths:
        raise WashingtonPreprocessError('Washington annotation receipt classifications differ')

    camera_poses = _pose_rows(annotation_paths['poses'])
    if camera_poses.shape[0] != receipt['sourcePoseCount']:
        raise WashingtonPreprocessError(
            f'Pose count {camera_poses.shape[0]} does not match receipt '
            f'{receipt["sourcePoseCount"]}',
        )
    if camera_poses.shape[0] != len(pairs):
        raise WashingtonPreprocessError(
            f'Pose count {camera_poses.shape[0]} does not match paired frame count '
            f'{len(pairs)}',
        )

    outputs = receipt['outputs']
    _require_exact_keys(
        outputs,
        {'json', 'intrinsics', 'extrinsics'},
        field='Washington preprocess receipt outputs',
    )
    json_path = join(jsonDir, f'{fname}.json')
    _validate_receipt_file_record(
        outputs['json'],
        field='outputs.json',
        expected_name=f'{fname}.json',
        expected_path=json_path,
    )
    _validate_receipt_file_record(
        outputs['intrinsics'],
        field='outputs.intrinsics',
        expected_name='intrinsics.txt',
        expected_path=join(main_folder, 'intrinsics.txt'),
    )

    data = _strict_json(json_path)
    if data.get('name') != fname:
        raise WashingtonPreprocessError('Preprocessed JSON scene name does not match receipt')
    if data.get('sourceGeometry') != receipt['sourceGeometry']:
        raise WashingtonPreprocessError(
            'Preprocessed JSON sourceGeometry does not match the receipt',
        )
    if not isinstance(data.get('frames'), list) or len(data['frames']) != len(expected_samples):
        raise WashingtonPreprocessError(
            'Preprocessed JSON frame count does not match sampled frame count',
        )
    expected_file_list = [sample['image'] for sample in expected_samples]
    if data.get('fileList') != expected_file_list:
        raise WashingtonPreprocessError(
            'Preprocessed JSON fileList does not match sampled RGB frames',
        )
    try:
        extrinsics_name = pinned_extrinsics_filename(data.get('extrinsics'))
    except SourceGeometryError as exc:
        raise WashingtonPreprocessError(str(exc)) from exc
    extrinsics_path = join(main_folder, 'extrinsics', extrinsics_name)
    _validate_receipt_file_record(
        outputs['extrinsics'],
        field='outputs.extrinsics',
        expected_name=extrinsics_name,
        expected_path=extrinsics_path,
    )

    try:
        intrinsics = reshape_intrinsics(np.loadtxt(join(main_folder, 'intrinsics.txt')))
        # ``np.loadtxt`` otherwise collapses a one-row, twelve-column file to
        # one dimension.  Keep the row axis so a valid one-frame rebuild passes
        # the same C2W validation as a multi-frame bundle.
        extrinsics = reshape_c2w_rows(np.loadtxt(extrinsics_path, ndmin=2))
    except (OSError, ValueError, SourceGeometryError) as exc:
        raise WashingtonPreprocessError(
            f'Preprocessed calibration artifacts are invalid: {exc}',
        ) from exc
    expected_intrinsics = np.asarray(
        WASHINGTON_RGBD_SCENES_V2_INTRINSICS,
        dtype=np.float64,
    )
    if not np.array_equal(intrinsics, expected_intrinsics):
        raise WashingtonPreprocessError(
            'Pinned intrinsics do not match the reviewed artifact-derived '
            'native Washington RGB-D Scenes calibration',
        )
    if extrinsics.shape[0] != len(expected_samples):
        raise WashingtonPreprocessError(
            'Pinned extrinsics pose count does not match sampled frame count',
        )
    expected_extrinsics = []
    for index in expected_indices:
        expected_pose = validate_c2w(get_extrinsic_matrix(camera_poses[index])[:3, :])
        expected_extrinsics.append(expected_pose)
    expected_extrinsics = np.asarray(expected_extrinsics, dtype=np.float64)
    for pose in extrinsics:
        validate_c2w(pose)
    if not np.allclose(extrinsics, expected_extrinsics, rtol=0.0, atol=1e-12):
        raise WashingtonPreprocessError(
            'Pinned extrinsics content does not match sampled source poses',
        )
    return receipt


def _incomplete_preprocess_error(fname, detail):
    return WashingtonPreprocessError(
        f'Washington scene {fname!r}: incomplete preprocessing artifacts; '
        f'explicit rebuild required with rebuild=True ({detail})',
    )

#filename = './extrinsics_test.txt'
def readValuesFromTxt(filename,local):
    if local:
        with open(filename, 'rb') as fid:
            file_utf = fid.read().decode('utf-8')
    else:
        with urllib.request.urlopen(filename) as fid:
            file_utf = fid.read().decode('utf-8')
    return [list(map(float, line.split())) for line in file_utf.splitlines() if line.strip()]


#filename = '00000-depth.png'
def depthRead(filename, local, depth_encoding):
    '''Read a source depth image using its explicitly selected encoding.'''

    if local:
        depthfile = Image.open(filename,'r')
    else:
        with urllib.request.urlopen(filename) as response:
            depthfile = Image.open(BytesIO(response.read()))
    try:
        depthmap = decode_depth(np.array(depthfile), depth_encoding)
    finally:
        depthfile.close()
    gc.collect()
    return depthmap

def depth2XYZcamera(K, depth, *, pixel_origin):
    '''Project depth pixels using the coordinate origin declared by the source.'''

    if type(pixel_origin) is not int or pixel_origin not in {0, 1}:
        raise ValueError('pixel_origin must be the integer 0 or 1')
    K = np.asarray(K, dtype=np.float64)
    depth = np.asarray(depth, dtype=np.float64)
    if K.shape != (3, 3) or not np.all(np.isfinite(K)):
        raise ValueError('Camera intrinsics must be one finite 3x3 matrix')
    if depth.ndim != 2 or not np.all(np.isfinite(depth)):
        raise ValueError('Depth must be one finite two-dimensional image')
    if K[0, 0] == 0 or K[1, 1] == 0:
        raise ValueError('Camera focal lengths must be non-zero')

    x_values = np.arange(pixel_origin, depth.shape[1] + pixel_origin)
    y_values = np.arange(pixel_origin, depth.shape[0] + pixel_origin)
    [x,y] = np.meshgrid(x_values, y_values)
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


def camera2XYZworld(XYZcamera,extrinsics):
    XYZ = []
    for j in range(0,len(XYZcamera[0][0])):
        for i in range(0,len(XYZcamera[0])):
            if XYZcamera[3][i][j]:
                data = (XYZcamera[0][i][j],
                        XYZcamera[1][i][j],
                        XYZcamera[2][i][j])
                XYZ.append(data)
    XYZ = np.asarray(XYZ, dtype=np.float64).reshape((-1, 3))
    XYZworld = transformPointCloud(XYZ,extrinsics)
    vals = (XYZworld,XYZcamera[3])
    return vals


def transformPointCloud(XYZ,Rt):
    c2w = validate_c2w(Rt)
    XYZ = np.asarray(XYZ, dtype=np.float64)
    if XYZ.ndim != 2 or XYZ.shape[1:] != (3,) or not np.all(np.isfinite(XYZ)):
        raise ValueError('Point cloud must be a finite Nx3 array')
    rotation = c2w[:, :3]
    translation = c2w[:, 3]
    return np.matmul(rotation,np.transpose(XYZ)) + np.repeat(np.reshape(translation,(3,1)),XYZ.shape[0],axis=1)

#main_folder = './data/washington/scene_01'
#fname='scene_01'
#jsonDir='./json/washington'
def project_annotations_3D_to_2D(
    main_folder,
    fname,
    jsonDir,
    annot_files,
    nFrame_samples=30,
):
    '''Build one native Washington annotation bundle with reviewed calibration.

    The fixed matrix is the reviewed artifact-consistent winner of symmetric
    candidate comparisons across official v2 RGB, depth, pose and PLY data.
    Keeping that result here prevents an older generic-camera helper or a
    converted-release calibration from entering a native-source rebuild.
    '''

    fname = _leaf_name(fname, field='Washington scene name')
    # Load the pinned native-v2 calibration before creating any outputs.
    calibration = _washington_calibration()
    K = reshape_intrinsics(calibration['intrinsics'])
    annotation_files = _classify_annotation_files(annot_files)
    lblfile = annotation_files['labels']
    posfile = annotation_files['poses']
    pcloudfile = annotation_files['pointCloud']

    pairs = _washington_frame_pairs(main_folder)
    camera_poses = _pose_rows(posfile)
    if camera_poses.shape[0] != len(pairs):
        raise WashingtonPreprocessError(
            f'Washington pose count {camera_poses.shape[0]} does not match paired '
            f'RGB-D frame count {len(pairs)}',
        )
    index = _sample_indices(len(pairs), nFrame_samples)
    samples = _sample_identity(main_folder, pairs, index)

    pcd = PlyData.read(pcloudfile)
    pcd_data = pcd.elements[0].data
    point_cloud = np.asarray([pcd_data['x'],pcd_data['y'],pcd_data['z']],dtype='float64').T
    lbls = np.asarray(np.loadtxt(lblfile), dtype=np.float64)[1:]
    if (
        point_cloud.ndim != 2
        or point_cloud.shape[1:] != (3,)
        or not np.all(np.isfinite(point_cloud))
        or not np.all(np.isfinite(lbls))
        or point_cloud.shape[0] != lbls.shape[0]
    ):
        raise WashingtonPreprocessError(
            'Washington point-cloud coordinates and labels must be finite and '
            'contain the same number of points',
        )

    os.makedirs(main_folder, exist_ok=True)
    os.makedirs(jsonDir, exist_ok=True)
    os.makedirs(join(main_folder, 'extrinsics'), exist_ok=True)
    np.savetxt(join(main_folder, 'intrinsics.txt'), K)

    print('points in the cloud: ', point_cloud.shape)
    print('points label: ', lbls.shape)
    print('frames pos: ', camera_poses.shape)

    object_categories = {1: 'bowl', 2: 'cap', 3: 'cereal_box', 4: 'coffee_mug', 5: 'coffee_table',
               6: 'office_chair', 7: 'soda_can', 8: 'sofa', 9: 'table', 10: 'background'}

    flst = [pair.image_name for pair in pairs]
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
    camera_poses = camera_poses[index]
    flst = np.array(flst)[index]

    extrinsics = []
    frames = [] # list to store object boolean arrays for each frame
    all_imgs_lbls = [] # list to store unique object IDs for each frame
    for i, frame_pos in enumerate(camera_poses):
        print(f'frame {i+1}/{len(camera_poses)}',end='\r',flush=True)
        oimage = Image.open(join(main_folder, 'image', flst[i]))
        try:
            image_size = oimage.size
            if image_size != (640, 480):
                raise WashingtonPreprocessError(
                    f'Washington RGB frame {flst[i]!r} is {image_size}; '
                    'expected 640x480',
                )
            # Map 3D points to 2D.
            uv, camera_depth, extrinsic = project_3d_2d(point_cloud, frame_pos, K)
            extrinsics.append(validate_c2w(extrinsic[:3, :]))
            # Construct mask, xy and xyz frames.
            im_frame, xyz_frame, xy_frame = construct_frames(
                uv,
                camera_depth,
                point_cloud,
                lbls,
                pixel_origin=WASHINGTON_RGBD_PROFILE.pixel_origin,
            )

            prev_objs = frames[i - 1] if i>0 else None
            prev_lbls = all_imgs_lbls[i - 1] if i>0 else None
            imgs, imgs_lbls = clean_label_mask(
                im_frame, oimage, prev_objs, prev_lbls,
            )
        finally:
            oimage.close()
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
            XYZ = xyz_frame[ys_idx, xs_idx, :]
            XY, XYZ = mapped_polygon_samples(XY, XYZ)
            xs, ys = XY[:, 0], XY[:, 1]

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
    data['sourceGeometry'] = _washington_source_geometry()
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

    extrinsics_filename = 'extrinsics.txt'
    extrinsics = np.asarray(extrinsics)
    extrinsics = extrinsics.reshape(extrinsics.shape[0], -1)
    np.savetxt(join(main_folder, 'extrinsics', extrinsics_filename), extrinsics)

    json_path = join(jsonDir, f'{fname}.json')
    with open(json_path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, indent=4, allow_nan=False)
        f.write('\n')

    return _build_preprocess_receipt(
        main_folder=main_folder,
        fname=fname,
        json_path=json_path,
        annotation_files=annotation_files,
        requested_frame_samples=nFrame_samples,
        source_frame_count=len(pairs),
        source_pose_count=len(pairs),
        samples=samples,
        extrinsics_filename=extrinsics_filename,
    )


def _json_scene_index(jsonDir):
    if not os.path.isdir(jsonDir):
        return {}
    scenes = {}
    for filename in sorted(name for name in listdir(jsonDir) if name.endswith('.json')):
        path = join(jsonDir, filename)
        document = _strict_json(path)
        scene = document.get('name') if isinstance(document, dict) else None
        if not isinstance(scene, str) or not scene:
            raise WashingtonPreprocessError(
                f'Washington source JSON {path} has no non-empty scene name',
            )
        if scene in scenes:
            raise WashingtonPreprocessError(
                f'Duplicate Washington JSON scene name {scene!r}: '
                f'{scenes[scene]} and {path}',
            )
        scenes[scene] = path
    return scenes


def _organize_washington_frames(main_folder):
    image_dir = join(main_folder, 'image')
    depth_dir = join(main_folder, 'depth')
    extrinsics_dir = join(main_folder, 'extrinsics')
    os.makedirs(image_dir, exist_ok=True)
    os.makedirs(depth_dir, exist_ok=True)
    os.makedirs(extrinsics_dir, exist_ok=True)

    for filename in sorted(os.listdir(main_folder)):
        source = join(main_folder, filename)
        if not os.path.isfile(source) or not filename.casefold().endswith('.png'):
            continue
        lowered = filename.casefold()
        if lowered.endswith('-color.png'):
            destination = join(image_dir, filename)
        elif lowered.endswith('-depth.png'):
            destination = join(depth_dir, filename)
        else:
            raise WashingtonPreprocessError(
                f'Unrecognized Washington PNG filename {source!r}',
            )
        if os.path.exists(destination):
            raise WashingtonPreprocessError(
                f'Washington frame exists both before and after organization: '
                f'{source!r} and {destination!r}',
            )
        shutil.move(source, destination)


def _scene_annotation_files(pcDir, scene):
    if not os.path.isdir(pcDir):
        raise WashingtonPreprocessError(
            f'Washington annotation directory is missing: {pcDir}',
        )
    scene_token = scene.rsplit('_', 1)[-1]
    token_pattern = re.compile(rf'(?<!\d){re.escape(scene_token)}(?!\d)')
    paths = [
        join(pcDir, filename)
        for filename in sorted(os.listdir(pcDir))
        if os.path.isfile(join(pcDir, filename))
        and token_pattern.search(filename)
        and not filename.casefold().endswith('.png')
    ]
    # Classification supplies the precise duplicate/missing-file diagnostics.
    return list(_classify_annotation_files(paths).values())


def pre_process_input_files(
    dataDir='./data/washington',
    pcDir='./data/washington/pc',
    jsonDir='./json/washington',
    nFrame_samples=30,
    *,
    rebuild=False,
):
    '''Validate or explicitly rebuild Washington preprocessing artifacts.

    A JSON file by itself is never treated as proof that calibration and poses
    exist.  Reuse requires a strict receipt whose hashes and frame/pose identity
    still validate.  Any missing or stale bundle requires an explicit rebuild
    using the pinned calibration derived from first-party v2 artifacts.
    '''

    if type(rebuild) is not bool:
        raise WashingtonPreprocessError('rebuild must be a boolean')
    if nFrame_samples is not None and (
            type(nFrame_samples) is not int or nFrame_samples < 1):
        raise WashingtonPreprocessError(
            'nFrame_samples must be a positive integer or None when reusing receipts',
        )
    if rebuild and nFrame_samples is None:
        raise WashingtonPreprocessError(
            'nFrame_samples must be a positive integer when rebuilding',
        )
    json_scenes = _json_scene_index(jsonDir)
    data_scenes = (
        {
            name for name in os.listdir(dataDir)
            if name != 'pc' and os.path.isdir(join(dataDir, name))
        }
        if os.path.isdir(dataDir)
        else set()
    )
    # A checked-in JSON with no raw scene directory is itself an incomplete
    # bundle, not a reason to silently skip source validation.
    scenes = sorted(data_scenes | set(json_scenes))
    if not scenes:
        raise WashingtonPreprocessError(
            'Washington preprocessing found no source scenes or prepared scene JSON files',
        )

    # Validate every reusable bundle before doing any filesystem organization.
    # That keeps a default collection run read-only when artifacts are stale.
    validated = []
    if not rebuild:
        for scene in scenes:
            canonical_json = os.path.normcase(os.path.abspath(join(jsonDir, f'{scene}.json')))
            existing_json = json_scenes.get(scene)
            if (
                existing_json is not None
                and os.path.normcase(os.path.abspath(existing_json)) != canonical_json
            ):
                raise _incomplete_preprocess_error(
                    scene,
                    f'scene JSON is not at canonical path {canonical_json}',
                )
            try:
                receipt = _validate_preprocess_receipt(
                    main_folder=join(dataDir, scene),
                    fname=scene,
                    jsonDir=jsonDir,
                    pcDir=pcDir,
                    nFrame_samples=nFrame_samples,
                )
            except WashingtonPreprocessError as exc:
                raise _incomplete_preprocess_error(scene, str(exc)) from exc
            validated.append(receipt)
            print(f"Verified preprocessing receipt for {scene} in '{dataDir}'")
        return tuple(validated)

    missing_raw_scenes = sorted(set(json_scenes) - data_scenes)
    if missing_raw_scenes:
        raise WashingtonPreprocessError(
            'Washington preprocessing cannot rebuild JSON scenes whose raw scene '
            f'directories are missing: {missing_raw_scenes}',
        )
    for scene, existing_json in json_scenes.items():
        canonical_json = os.path.normcase(os.path.abspath(join(jsonDir, f'{scene}.json')))
        if os.path.normcase(os.path.abspath(existing_json)) != canonical_json:
            raise WashingtonPreprocessError(
                f'Washington scene {scene!r} already uses noncanonical JSON '
                f'{existing_json}; move or remove it before rebuilding',
            )
    os.makedirs(jsonDir, exist_ok=True)
    for scene in scenes:
        main_folder = join(dataDir, scene)
        print(f"Rebuilding preprocessing for {scene} from '{dataDir}'")
        _organize_washington_frames(main_folder)
        annot_files = _scene_annotation_files(pcDir, scene)
        receipt_path = join(main_folder, WASHINGTON_PREPROCESS_RECEIPT)
        if os.path.exists(receipt_path):
            # Invalidate the old proof before overwriting any artifact.  A
            # failed rebuild therefore cannot leave a receipt that looks live.
            os.unlink(receipt_path)
        receipt = project_annotations_3D_to_2D(
            main_folder,
            scene,
            jsonDir,
            annot_files,
            nFrame_samples,
        )
        # The receipt is intentionally the final write in a successful build.
        with open(receipt_path, 'w', encoding='utf-8', newline='\n') as output:
            json.dump(receipt, output, indent=2, allow_nan=False)
            output.write('\n')
        try:
            validated_receipt = _validate_preprocess_receipt(
                main_folder=main_folder,
                fname=scene,
                jsonDir=jsonDir,
                pcDir=pcDir,
                nFrame_samples=nFrame_samples,
            )
        except WashingtonPreprocessError:
            os.unlink(receipt_path)
            raise
        validated.append(validated_receipt)
        print(f"JSON and preprocessing receipt saved for {scene} in '{jsonDir}'")
    return tuple(validated)
