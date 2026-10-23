'''Pure source-data geometry contracts used by the RGB-D collector.

The two supported corpora encode and pair frames differently.  Keeping those
choices in one explicit profile prevents a plausible-looking filename or pixel
value from silently selecting the wrong scientific interpretation.
'''

from dataclasses import dataclass
import math
import re

import numpy as np


SUN3D_CIRCULAR_UINT16_MM = 'sun3d_circular_uint16_mm'
PLAIN_UINT16_MM = 'plain_uint16_mm'
SUN3D_NEAREST_TIMESTAMP = 'sun3d_nearest_timestamp'
EXACT_FRAME_ID = 'exact_frame_id'
ONE_BASED_IMAGE_FRAME = 'one_based_image_frame'
JSON_POSITION = 'json_position'
WORLD_POINT_CLOUD_SCHEMA_VERSION = 1

_DEPTH_ENCODINGS = frozenset({SUN3D_CIRCULAR_UINT16_MM, PLAIN_UINT16_MM})
_PAIRING_STRATEGIES = frozenset({SUN3D_NEAREST_TIMESTAMP, EXACT_FRAME_ID})
_POSE_INDEX_STRATEGIES = frozenset({ONE_BASED_IMAGE_FRAME, JSON_POSITION})
_SUN3D_IMAGE_RE = re.compile(
    r'^(?P<frame>\d+)-(?P<timestamp>\d+)\.(?:jpg|jpeg)$', re.IGNORECASE,
)
_SUN3D_DEPTH_RE = re.compile(
    r'^(?P<frame>\d+)-(?P<timestamp>\d+)\.png$', re.IGNORECASE,
)
_WASHINGTON_IMAGE_RE = re.compile(r'^(?P<frame>\d+)-color\.png$', re.IGNORECASE)
_WASHINGTON_DEPTH_RE = re.compile(r'^(?P<frame>\d+)-depth\.png$', re.IGNORECASE)


class SourceGeometryError(ValueError):
    '''Source metadata cannot be interpreted without guessing.'''


@dataclass(frozen=True)
class SourceProfile:
    name: str
    depth_encoding: str
    pairing_strategy: str
    pose_index_strategy: str
    pixel_origin: int


@dataclass(frozen=True)
class FramePair:
    image_name: str
    depth_name: str
    image_frame_id: int
    depth_frame_id: int
    image_timestamp: int | None
    depth_timestamp: int | None
    timestamp_delta: int | None


SOURCE_PROFILES = {
    'princeton': SourceProfile(
        name='princeton',
        depth_encoding=SUN3D_CIRCULAR_UINT16_MM,
        pairing_strategy=SUN3D_NEAREST_TIMESTAMP,
        pose_index_strategy=ONE_BASED_IMAGE_FRAME,
        pixel_origin=1,
    ),
    'washington': SourceProfile(
        name='washington',
        depth_encoding=PLAIN_UINT16_MM,
        pairing_strategy=EXACT_FRAME_ID,
        pose_index_strategy=JSON_POSITION,
        pixel_origin=0,
    ),
}

# Exported aliases make the dataset contract explicit at call sites and in
# tests without duplicating any profile values.
PRINCETON_SUN3D_PROFILE = SOURCE_PROFILES['princeton']
WASHINGTON_RGBD_PROFILE = SOURCE_PROFILES['washington']


def _leaf_name(value, *, field):
    '''Return a platform-independent leaf filename or reject a path.'''

    text = str(value)
    leaf = re.split(r'[\\/]', text)[-1]
    if not text or leaf != text or leaf in {'.', '..'}:
        raise SourceGeometryError(f'{field} must be a single filename, not a path: {text!r}')
    return leaf


def pinned_extrinsics_filename(value):
    '''Validate the exact extrinsics filename declared by source JSON.'''

    if value is None:
        raise SourceGeometryError(
            'Source JSON must declare the exact extrinsics filename in "extrinsics"',
        )
    name = _leaf_name(value, field='extrinsics')
    if not name.lower().endswith('.txt'):
        raise SourceGeometryError(f'Extrinsics filename must end in .txt: {name!r}')
    return name


def _metadata_profile(dataset_name, metadata):
    if not isinstance(metadata, dict):
        raise SourceGeometryError(
            f'Unknown dataset {dataset_name!r} requires a sourceGeometry object',
        )
    required = {
        'depthEncoding', 'pairingStrategy', 'poseIndexStrategy', 'pixelOrigin',
    }
    missing = sorted(required.difference(metadata))
    unknown = sorted(set(metadata).difference(required))
    if missing or unknown:
        details = []
        if missing:
            details.append(f'missing {missing}')
        if unknown:
            details.append(f'unknown {unknown}')
        raise SourceGeometryError(
            f'Invalid sourceGeometry metadata for {dataset_name!r}: ' + '; '.join(details),
        )
    profile = SourceProfile(
        name=str(dataset_name),
        depth_encoding=str(metadata['depthEncoding']),
        pairing_strategy=str(metadata['pairingStrategy']),
        pose_index_strategy=str(metadata['poseIndexStrategy']),
        pixel_origin=metadata['pixelOrigin'],
    )
    _validate_profile(profile)
    return profile


def _validate_profile(profile):
    if profile.depth_encoding not in _DEPTH_ENCODINGS:
        raise SourceGeometryError(
            f'Unsupported depth encoding {profile.depth_encoding!r}; '
            f'expected one of {sorted(_DEPTH_ENCODINGS)}',
        )
    if profile.pairing_strategy not in _PAIRING_STRATEGIES:
        raise SourceGeometryError(
            f'Unsupported frame-pairing strategy {profile.pairing_strategy!r}; '
            f'expected one of {sorted(_PAIRING_STRATEGIES)}',
        )
    if profile.pose_index_strategy not in _POSE_INDEX_STRATEGIES:
        raise SourceGeometryError(
            f'Unsupported pose-index strategy {profile.pose_index_strategy!r}; '
            f'expected one of {sorted(_POSE_INDEX_STRATEGIES)}',
        )
    if type(profile.pixel_origin) is not int or profile.pixel_origin not in {0, 1}:
        raise SourceGeometryError('pixelOrigin must be the integer 0 or 1')


def resolve_source_profile(dataset_name, metadata=None):
    '''Resolve a known profile, or require complete metadata for a new source.'''

    dataset_key = str(dataset_name).casefold()
    known = SOURCE_PROFILES.get(dataset_key)
    if known is None:
        return _metadata_profile(dataset_name, metadata)
    if metadata is not None:
        declared = _metadata_profile(dataset_name, metadata)
        expected = (
            known.depth_encoding,
            known.pairing_strategy,
            known.pose_index_strategy,
            known.pixel_origin,
        )
        actual = (
            declared.depth_encoding,
            declared.pairing_strategy,
            declared.pose_index_strategy,
            declared.pixel_origin,
        )
        if actual != expected:
            raise SourceGeometryError(
                f'sourceGeometry metadata conflicts with the {dataset_key!r} profile',
            )
    return known


def decode_depth(raw_depth, encoding):
    '''Decode a two-dimensional uint16 millimetre image into float64 metres.'''

    if encoding not in _DEPTH_ENCODINGS:
        raise SourceGeometryError(f'Unsupported depth encoding {encoding!r}')
    raw = np.asarray(raw_depth)
    if raw.ndim != 2 or not np.issubdtype(raw.dtype, np.integer):
        raise SourceGeometryError('Depth image must be a two-dimensional integer array')
    if raw.size and (int(raw.min()) < 0 or int(raw.max()) > np.iinfo(np.uint16).max):
        raise SourceGeometryError('Depth samples must fit unsigned 16-bit storage')
    encoded = raw.astype(np.uint16, copy=False)
    if encoding == SUN3D_CIRCULAR_UINT16_MM:
        # SUN3D stores depth with a circular 16-bit shift.  Bitwise OR is
        # essential here: Python's boolean ``or`` discards the restored high
        # bits for valid depths above 8192 mm.
        millimetres = np.bitwise_or(
            np.right_shift(encoded, np.uint16(3)),
            np.left_shift(encoded, np.uint16(13)),
        )
    else:
        millimetres = encoded
    return millimetres.astype(np.float64) / 1000.0


def _parse_unique(names, pattern, *, kind):
    records = []
    seen_names = set()
    seen_keys = set()
    for value in names:
        name = _leaf_name(value, field=f'{kind} filename')
        folded = name.casefold()
        if folded in seen_names:
            raise SourceGeometryError(f'Duplicate {kind} filename: {name!r}')
        seen_names.add(folded)
        match = pattern.fullmatch(name)
        if match is None:
            raise SourceGeometryError(
                f'{kind.capitalize()} filename does not match the selected source profile: {name!r}',
            )
        frame_id = int(match.group('frame'))
        timestamp = (
            int(match.group('timestamp'))
            if 'timestamp' in match.groupdict()
            else None
        )
        key = (frame_id, timestamp)
        if key in seen_keys:
            raise SourceGeometryError(
                f'Duplicate {kind} frame key {key!r}: {name!r}',
            )
        seen_keys.add(key)
        records.append((name, frame_id, timestamp))
    if not records:
        raise SourceGeometryError(f'No {kind} files were supplied')
    return records


def pair_rgb_depth_files(image_names, depth_names, strategy):
    '''Pair source images and depths deterministically in annotation order.'''

    if strategy == SUN3D_NEAREST_TIMESTAMP:
        images = _parse_unique(image_names, _SUN3D_IMAGE_RE, kind='image')
        depths = _parse_unique(depth_names, _SUN3D_DEPTH_RE, kind='depth')
        # The reference SUN3D loader chooses the depth whose timestamp has the
        # smallest absolute delta for each RGB image.  Sorting first gives the
        # otherwise implicit MATLAB ``min`` first-match rule a deterministic
        # tie break (earlier timestamp, then frame ID and filename).
        ordered_depths = sorted(depths, key=lambda item: (item[2], item[1], item[0]))
        pairs = []
        for image_name, image_frame, image_timestamp in images:
            depth_name, depth_frame, depth_timestamp = min(
                ordered_depths,
                key=lambda item: (
                    abs(item[2] - image_timestamp), item[2], item[1], item[0],
                ),
            )
            pairs.append(FramePair(
                image_name=image_name,
                depth_name=depth_name,
                image_frame_id=image_frame,
                depth_frame_id=depth_frame,
                image_timestamp=image_timestamp,
                depth_timestamp=depth_timestamp,
                timestamp_delta=abs(depth_timestamp - image_timestamp),
            ))
        return tuple(pairs)

    if strategy == EXACT_FRAME_ID:
        images = _parse_unique(image_names, _WASHINGTON_IMAGE_RE, kind='image')
        depths = _parse_unique(depth_names, _WASHINGTON_DEPTH_RE, kind='depth')
        image_frames = {frame_id for _name, frame_id, _timestamp in images}
        depth_frames = {frame_id for _name, frame_id, _timestamp in depths}
        if image_frames != depth_frames:
            missing = sorted(image_frames - depth_frames)
            extra = sorted(depth_frames - image_frames)
            details = []
            if missing:
                details.append(f'missing depth frame IDs {missing}')
            if extra:
                details.append(f'unmatched depth frame IDs {extra}')
            raise SourceGeometryError(
                'Washington RGB-D image/depth frame-ID sets differ: ' + '; '.join(details),
            )
        depth_by_frame = {frame_id: (name, timestamp) for name, frame_id, timestamp in depths}
        pairs = []
        for image_name, image_frame, _image_timestamp in images:
            if image_frame not in depth_by_frame:
                raise SourceGeometryError(
                    f'No depth frame matches image frame {image_frame}: {image_name!r}',
                )
            depth_name, _depth_timestamp = depth_by_frame[image_frame]
            pairs.append(FramePair(
                image_name=image_name,
                depth_name=depth_name,
                image_frame_id=image_frame,
                depth_frame_id=image_frame,
                image_timestamp=None,
                depth_timestamp=None,
                timestamp_delta=None,
            ))
        return tuple(pairs)

    raise SourceGeometryError(f'Unsupported frame-pairing strategy {strategy!r}')


def reshape_c2w_rows(values):
    '''Convert text rows into a finite stack of 3x4 camera-to-world matrices.'''

    try:
        rows = np.asarray(values, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise SourceGeometryError('Extrinsics must contain numeric rows') from exc
    # Princeton stores three four-value rows per pose.  The Washington
    # preprocessor stores one flattened twelve-value row per pose.  Those are
    # the only two layouts emitted by the supported source pipelines.
    valid_layout = (
        rows.ndim == 2
        and (
            (rows.shape[1] == 4 and rows.shape[0] % 3 == 0)
            or rows.shape[1] == 12
        )
    )
    if not valid_layout:
        raise SourceGeometryError(
            'Extrinsics must use 3x4 rows or one flattened 12-value row per C2W pose',
        )
    matrices = rows.reshape((-1, 3, 4))
    if not matrices.size or not np.all(np.isfinite(matrices)):
        raise SourceGeometryError('Extrinsics must contain finite 3x4 C2W matrices')
    return matrices


def reshape_intrinsics(values):
    '''Read the row-major 3x3 intrinsic matrix used by both source profiles.'''

    try:
        intrinsics = np.asarray(values, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise SourceGeometryError('Camera intrinsics must contain numeric rows') from exc
    if intrinsics.shape != (3, 3) or not np.all(np.isfinite(intrinsics)):
        raise SourceGeometryError('Camera intrinsics must be one finite 3x3 matrix')
    if intrinsics[0, 0] == 0.0 or intrinsics[1, 1] == 0.0:
        raise SourceGeometryError('Camera intrinsic focal lengths must be non-zero')
    if not np.allclose(intrinsics[2], [0.0, 0.0, 1.0], rtol=0.0, atol=1e-12):
        raise SourceGeometryError(
            'Camera intrinsics must use a row-major pinhole matrix with final row [0, 0, 1]',
        )
    return intrinsics


def validate_c2w(matrix):
    '''Return one finite float64 3x4 camera-to-world matrix.'''

    try:
        c2w = np.asarray(matrix, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise SourceGeometryError('Camera pose must contain numeric values') from exc
    if c2w.shape != (3, 4) or not np.all(np.isfinite(c2w)):
        raise SourceGeometryError('Camera pose must be one finite 3x4 C2W matrix')
    rotation = c2w[:, :3]
    if (
        not np.allclose(rotation.T @ rotation, np.eye(3), rtol=0.0, atol=1e-3)
        or not math.isclose(float(np.linalg.det(rotation)), 1.0, rel_tol=0.0, abs_tol=1e-3)
    ):
        raise SourceGeometryError(
            'Camera pose rotation must be a right-handed orthonormal C2W basis',
        )
    return c2w


def pose_index(frame_pair, json_index, strategy):
    '''Resolve the source-specific camera-pose index for an annotated frame.'''

    if type(json_index) is not int or json_index < 0:
        raise SourceGeometryError('JSON frame index must be a non-negative integer')
    if strategy == ONE_BASED_IMAGE_FRAME:
        if frame_pair.image_frame_id < 1:
            raise SourceGeometryError('SUN3D image frame IDs must be one-based')
        return frame_pair.image_frame_id - 1
    if strategy == JSON_POSITION:
        return json_index
    raise SourceGeometryError(f'Unsupported pose-index strategy {strategy!r}')


def select_c2w_pose(matrices, frame_pair, json_index, strategy):
    '''Select and validate the C2W matrix corresponding to a paired frame.'''

    stack = np.asarray(matrices, dtype=np.float64)
    if stack.ndim != 3 or stack.shape[1:] != (3, 4) or not np.all(np.isfinite(stack)):
        raise SourceGeometryError('Camera poses must be a finite Nx3x4 C2W stack')
    index = pose_index(frame_pair, json_index, strategy)
    if index >= len(stack):
        raise SourceGeometryError(
            f'Camera pose index {index} is outside the {len(stack)}-pose stack',
        )
    return validate_c2w(stack[index])


def camera_origin_world(c2w):
    '''Return the camera centre in world coordinates for a C2W pose.'''

    origin = validate_c2w(c2w)[:, 3]
    if not all(math.isfinite(float(value)) for value in origin):
        raise SourceGeometryError('Camera origin must be finite')
    return origin.copy()
