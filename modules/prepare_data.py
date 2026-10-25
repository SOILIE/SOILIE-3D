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
from contextlib import contextmanager
import csv
import hashlib
import io
import os
from os.path import join
import shutil
import sys
import ast
import pickle
import json
import random
import re
import math
from math import sqrt
from pathlib import Path
import uuid
import unicodedata
import numpy as np
import pandas as pd

from modules.timer import *
from modules import progress_bar
from modules.object_identity import (
    ObjectIdentityError,
    decode_collector_object_label,
)
from modules.relation_manifest import (
    load_verified_relation_manifest,
    sha256_file,
)
from modules.working_combos import ordered_anchor_witnesses
from modules.working_combo_schema import MAX_TOTAL_OBJECT_COUNT, MIN_OBJECT_COUNT
from modules.triplet_schema import (
    TRIPLET_COLUMNS,
    validate_triplet_columns as _validate_triplet_columns,
)


ALIGNMENT_EPSILON = 1e-12
_WINDOWS_DEVICE_RE = re.compile(
    r'^(?:con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\..*)?$',
    re.IGNORECASE,
)
_WINDOWS_ILLEGAL_FILENAME_CHARACTERS = frozenset('<>:"/\\|?*')


class TripletGeometryError(ValueError):
    '''Stored relationship geometry cannot produce a valid rigid scene.'''


class TripletCoverageError(TripletGeometryError):
    '''The corpus lacks a complete ordered basis for the requested instances.'''


def _path_key(path):
    '''Return a platform-independent collision key for a resolved path.'''

    return Path(path).resolve(strict=False).as_posix().casefold()


def _require_portable_segment(value, *, field):
    '''Reject names whose filesystem identity differs across Windows/POSIX.'''

    if not value or value in {'.', '..'}:
        raise TripletGeometryError(f'{field} must be a non-empty path segment')
    if any(unicodedata.category(character).startswith('C') for character in value):
        raise TripletGeometryError(f'{field} cannot contain control characters')
    if value.endswith(('.', ' ')):
        raise TripletGeometryError(f'{field} cannot end in a dot or space')
    if any(character in _WINDOWS_ILLEGAL_FILENAME_CHARACTERS for character in value):
        raise TripletGeometryError(
            f'{field} contains a Windows-illegal filename character'
        )
    if _WINDOWS_DEVICE_RE.fullmatch(value):
        raise TripletGeometryError(f'{field} uses a reserved Windows device name')
    return value


def _paths_alias(first, second):
    '''Detect case aliases, symlink aliases, and existing hard-link aliases.'''

    first = Path(first)
    second = Path(second)
    if _path_key(first) == _path_key(second):
        return True
    if first.exists() and second.exists():
        try:
            return os.path.samefile(first, second)
        except OSError:
            pass
    return False


def _inside_root(path, root, *, field, must_exist=False):
    '''Resolve a path and require a portable location below one trusted root.'''

    root = Path(root).resolve(strict=True)
    path = Path(path).absolute()
    # Check lexical segments before asking Windows to resolve device aliases
    # such as NUL.csv, which may fail or acquire special semantics first.
    for offset, part in enumerate(path.parts):
        if offset == 0 and part == path.anchor:
            continue
        _require_portable_segment(part, field=f'{field} segment {offset}')
    try:
        resolved = path.resolve(strict=must_exist)
    except FileNotFoundError as exc:
        raise TripletGeometryError(f'{field} does not exist: {path}') from exc
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise TripletGeometryError(f'{field} must remain inside {root}') from exc
    if not relative.parts:
        raise TripletGeometryError(f'{field} must name a file below {root}')
    for offset, part in enumerate(relative.parts):
        _require_portable_segment(part, field=f'{field} segment {offset}')
    return path, resolved


def _project_relative_posix(path, project_root):
    '''Return one canonical project-relative POSIX provenance path.'''

    resolved = Path(path).resolve(strict=False)
    try:
        relative = resolved.relative_to(Path(project_root).resolve(strict=True))
    except ValueError as exc:
        raise TripletGeometryError(f'Path is outside the project: {resolved}') from exc
    for offset, part in enumerate(relative.parts):
        _require_portable_segment(part, field=f'Project path segment {offset}')
    return relative.as_posix()


def _safe_destinations(destinations, *, data_root, protected_paths):
    '''Validate destinations before creating directories or moving any file.'''

    checked = []
    for field, value in destinations:
        path, resolved = _inside_root(value, data_root, field=field)
        if path.is_symlink():
            raise TripletGeometryError(f'{field} must not be a symbolic link')
        if path.exists() and not path.is_file():
            raise TripletGeometryError(f'{field} must name a regular file')
        checked.append((field, path, resolved))

    for index, (field, path, _resolved) in enumerate(checked):
        for other_field, other_path, _other_resolved in checked[index + 1:]:
            if _paths_alias(path, other_path):
                raise TripletGeometryError(f'{field} and {other_field} must be separate files')
        for protected_field, protected_path in protected_paths:
            if _paths_alias(path, protected_path):
                raise TripletGeometryError(
                    f'{field} must not overwrite {protected_field}: {protected_path}',
                )
    return tuple(path for _field, path, _resolved in checked)


def _destination_lock_paths(destinations, *, project_root):
    '''Map a destination set to sorted per-path locks without deadlock cycles.'''

    lock_root = Path(project_root).resolve(strict=True) / '.codex' / 'triplet-output-locks'
    keys = sorted({_path_key(destination) for destination in destinations})
    return tuple(
        lock_root / f'{hashlib.sha256(key.encode("utf-8")).hexdigest()}.lock'
        for key in keys
    )


@contextmanager
def _destination_set_lock(destinations, *, project_root):
    '''Serialize writers whose related output sets share any destination.'''

    lock_paths = _destination_lock_paths(destinations, project_root=project_root)
    handles = []
    windows = os.name == 'nt'
    try:
        for lock_path in lock_paths:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            handle = os.fdopen(descriptor, 'r+b', buffering=0)
            try:
                if os.fstat(handle.fileno()).st_size == 0:
                    handle.write(b'\0')
                handle.seek(0)
                if windows:
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            except BaseException:
                handle.close()
                raise
            handles.append(handle)
        yield
    finally:
        for handle in reversed(handles):
            try:
                handle.seek(0)
                if windows:
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()


def _validate_triplet_row(row, *, source, line_number, decode_collector_labels):
    '''Validate one canonical row and return normalized model-ready values.'''

    if len(row) != len(TRIPLET_COLUMNS):
        raise TripletGeometryError(
            f'{source}:{line_number} has {len(row)} fields; '
            f'expected {len(TRIPLET_COLUMNS)}',
        )
    labels = []
    for offset, raw in enumerate(row[:3]):
        column = TRIPLET_COLUMNS[offset]
        if not isinstance(raw, str) or not raw or raw.strip() != raw:
            raise TripletGeometryError(
                f'{source}:{line_number} {column} must be a non-empty canonical name',
            )
        if decode_collector_labels:
            try:
                raw, _instance_id = decode_collector_object_label(raw)
            except ObjectIdentityError as exc:
                raise TripletGeometryError(
                    f'{source}:{line_number} {column} has an invalid collector identity: {exc}',
                ) from exc
        labels.append(raw)

    numbers = []
    for offset, raw in enumerate(row[3:], start=3):
        column = TRIPLET_COLUMNS[offset]
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise TripletGeometryError(
                f'{source}:{line_number} {column} must be numeric',
            ) from exc
        if not math.isfinite(value):
            raise TripletGeometryError(
                f'{source}:{line_number} {column} must be finite',
            )
        if column.startswith('distance') and value <= 0.0:
            raise TripletGeometryError(
                f'{source}:{line_number} {column} must be greater than zero',
            )
        if column.startswith('angle') and not 0.0 <= value <= 180.0:
            raise TripletGeometryError(
                f'{source}:{line_number} {column} must be between 0 and 180 degrees',
            )
        numbers.append(value)
    return labels + numbers


def _read_triplet_bytes(payload, *, source, expected_rows=None,
                        decode_collector_labels=False):
    '''Parse and fully validate the exact byte snapshot used by a consumer.'''

    try:
        text = payload.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise TripletGeometryError(f'{source} must be UTF-8 CSV') from exc
    reader = csv.reader(io.StringIO(text, newline=''))
    header = next(reader, ())
    require_canonical_triplet_columns(header, source=source)
    rows = [
        _validate_triplet_row(
            row,
            source=source,
            line_number=line_number,
            decode_collector_labels=decode_collector_labels,
        )
        for line_number, row in enumerate(reader, start=2)
    ]
    if expected_rows is not None and len(rows) != expected_rows:
        raise TripletGeometryError(
            f'{source} row count {len(rows)} does not match expected {expected_rows}',
        )
    return pd.DataFrame.from_records(rows, columns=TRIPLET_COLUMNS)


def _promote_candidates(pairs, *, staging_root):
    '''Promote a related file set and restore every prior byte on failure.'''

    backups = {}
    promoted = []
    try:
        for _candidate, destination in pairs:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                backup = staging_root / f'backup.{uuid.uuid4()}'
                shutil.copyfile(destination, backup)
                backups[destination] = backup
        try:
            for candidate, destination in pairs:
                os.replace(candidate, destination)
                promoted.append(destination)
        except BaseException as promotion_error:
            rollback_errors = []
            for destination in reversed(promoted):
                try:
                    backup = backups.get(destination)
                    if backup is None:
                        destination.unlink(missing_ok=True)
                    else:
                        os.replace(backup, destination)
                except OSError as exc:
                    rollback_errors.append(f'{destination}: {exc}')
            if rollback_errors:
                raise TripletGeometryError(
                    'Triplet output promotion failed and rollback was incomplete: '
                    + '; '.join(rollback_errors),
                ) from promotion_error
            raise
    finally:
        for backup in backups.values():
            backup.unlink(missing_ok=True)


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


def gatherTriplets(
        manifest_path='./data/relation-inputs.json',
        output_path='./data/triplets.csv',
        receipt_path='./data/triplets.sources.json',
        *, data_root=None):
    '''Gather only the exact, provenance-sealed relations from one collector run.'''

    print("Gathering all triplets into single file...")
    timer = startTimer()
    manifest_argument = Path(manifest_path)
    data_root = Path(data_root) if data_root is not None else manifest_argument.parent
    data_root = data_root.resolve(strict=True)
    manifest_path, manifest_resolved = _inside_root(
        manifest_argument,
        data_root,
        field='Relation manifest',
        must_exist=True,
    )
    manifest_snapshot = manifest_path.read_bytes()
    manifest, verified_files = load_verified_relation_manifest(
        manifest_path,
        data_root=data_root,
    )
    if manifest_path.read_bytes() != manifest_snapshot:
        raise TripletGeometryError('Relation manifest changed while it was being verified')

    repository_root = data_root.parent
    protected_paths = [('relation manifest', manifest_resolved)]
    annotation_paths = []
    for index, record in enumerate(manifest['frames']):
        annotation = repository_root.joinpath(*record['annotation']['path'].split('/'))
        _argument, annotation_resolved = _inside_root(
            annotation,
            repository_root,
            field=f'frames[{index}].annotation.path',
            must_exist=True,
        )
        annotation_paths.append((annotation_resolved, record['annotation']['sha256']))
        protected_paths.append((f'annotation input {index}', annotation_resolved))
    for index, verified in enumerate(verified_files):
        protected_paths.append((f'relation input {index}', verified.path))

    output_path, receipt_path = _safe_destinations(
        (
            ('Triplet output', output_path),
            ('Triplet provenance receipt', receipt_path),
        ),
        data_root=data_root,
        protected_paths=protected_paths,
    )

    frames = []
    relation_snapshots = []
    for verified in verified_files:
        payload = verified.path.read_bytes()
        observed_digest = hashlib.sha256(payload).hexdigest()
        expected_digest = verified.record['relation']['sha256']
        if observed_digest != expected_digest:
            raise TripletGeometryError(
                f'Relation input changed after verification: {verified.path}',
            )
        frame = _read_triplet_bytes(
            payload,
            source=str(verified.path),
            expected_rows=verified.record['relation']['rowCount'],
            decode_collector_labels=True,
        )
        relation_snapshots.append((verified.path, payload))
        frames.append(frame)

    master_df = pd.concat(frames, ignore_index=True)
    if master_df.empty:
        raise TripletGeometryError(
            'The complete relation manifest contains no triplet data rows'
        )
    master_df = master_df.sort_values(
        by=list(TRIPLET_COLUMNS),
        kind='mergesort',
        ignore_index=True,
    )
    source_rows = int(sum(
        item.record['relation']['rowCount'] for item in verified_files
    ))
    if source_rows != len(master_df):
        raise TripletGeometryError(
            f'Manifest source row count {source_rows} does not match gathered '
            f'row count {len(master_df)}',
        )

    project_root = data_root.parent
    staging_root = project_root / '.codex' / 'relation-gather'
    staging_root.mkdir(parents=True, exist_ok=True)
    identifier = str(uuid.uuid4())
    candidate_output = staging_root / f'triplets.{identifier}.csv'
    candidate_receipt = staging_root / f'triplets.{identifier}.sources.json'
    try:
        master_df.to_csv(candidate_output, index=False, lineterminator='\n')
        candidate_payload = candidate_output.read_bytes()
        candidate_frame = _read_triplet_bytes(
            candidate_payload,
            source=str(candidate_output),
            expected_rows=len(master_df),
        )
        if not candidate_frame.equals(master_df):
            raise TripletGeometryError(
                'Gathered candidate rows do not match the verified source snapshots',
            )
        if len(candidate_frame) != source_rows:
            raise TripletGeometryError(
                'Gathered candidate row count does not match sealed sourceRows',
            )
        output_digest = hashlib.sha256(candidate_payload).hexdigest()
        receipt = {
            'schemaVersion': 1,
            'kind': 'soilie-triplet-source-receipt',
            'collectionId': manifest['collectionId'],
            'sourceManifest': {
                'path': _project_relative_posix(manifest_path, project_root),
                'sha256': hashlib.sha256(manifest_snapshot).hexdigest(),
            },
            'frameCount': len(verified_files),
            'sourceRows': source_rows,
            'output': {
                'path': _project_relative_posix(output_path, project_root),
                'rowCount': int(len(master_df)),
                'sha256': output_digest,
            },
            # Copy the sealed records so a later collector run cannot erase the
            # scene/frame/image/depth/pose identity of this gathered corpus.
            'frames': manifest['frames'],
        }
        candidate_receipt.write_text(
            json.dumps(receipt, indent=2, allow_nan=False) + '\n',
            encoding='utf-8',
            newline='\n',
        )
        try:
            observed_receipt = json.loads(candidate_receipt.read_text(encoding='utf-8'))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise TripletGeometryError('Cannot validate triplet provenance candidate') from exc
        if observed_receipt != receipt:
            raise TripletGeometryError('Triplet provenance candidate failed read-back validation')
        receipt_snapshot = candidate_receipt.read_bytes()

        # Serialize the full final check and both replacements. Per-destination
        # locks are acquired in stable order, so overlapping output sets cannot
        # interleave or deadlock across collector processes.
        with _destination_set_lock(
                (output_path, receipt_path),
                project_root=project_root):
            # The output is built from immutable byte snapshots. Rechecking
            # every seal under the writer lock prevents a receipt from being
            # published after its named inputs have changed.
            if manifest_path.read_bytes() != manifest_snapshot:
                raise TripletGeometryError('Relation manifest changed before promotion')
            for relation_path, payload in relation_snapshots:
                if relation_path.read_bytes() != payload:
                    raise TripletGeometryError(
                        f'Relation input changed before promotion: {relation_path}',
                    )
            for annotation_path, expected_digest in annotation_paths:
                if sha256_file(annotation_path) != expected_digest:
                    raise TripletGeometryError(
                        f'Annotation input changed before promotion: {annotation_path}',
                    )
            promoted_candidate_payload = candidate_output.read_bytes()
            if (
                    promoted_candidate_payload != candidate_payload
                    or hashlib.sha256(promoted_candidate_payload).hexdigest() != output_digest):
                raise TripletGeometryError('Gathered CSV candidate changed before promotion')
            if candidate_receipt.read_bytes() != receipt_snapshot:
                raise TripletGeometryError(
                    'Triplet provenance candidate changed before promotion',
                )
            _promote_candidates(
                (
                    (candidate_output, output_path),
                    (candidate_receipt, receipt_path),
                ),
                staging_root=staging_root,
            )
    finally:
        for candidate in (candidate_output, candidate_receipt):
            if candidate.exists():
                candidate.unlink()
        try:
            staging_root.rmdir()
        except OSError:
            pass

    sys.stdout.write("Done! (%s)\n"%str(endTimerPretty(timer))); sys.stdout.flush()
    return receipt


def _read_averaged_candidate(payload, *, source, expected_rows):
    '''Validate the complete averaged CSV, including its positive count.'''

    try:
        text = payload.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise TripletGeometryError(f'{source} must be UTF-8 CSV') from exc
    reader = csv.reader(io.StringIO(text, newline=''))
    header = next(reader, ())
    expected_header = tuple(TRIPLET_COLUMNS) + ('count',)
    if tuple(header) != expected_header:
        raise TripletGeometryError(
            f'{source} has an invalid averaged triplet header: {header!r}',
        )
    rows = []
    for line_number, row in enumerate(reader, start=2):
        if len(row) != len(expected_header):
            raise TripletGeometryError(
                f'{source}:{line_number} has {len(row)} fields; '
                f'expected {len(expected_header)}',
            )
        validated = _validate_triplet_row(
            row[:-1],
            source=source,
            line_number=line_number,
            decode_collector_labels=False,
        )
        try:
            count = int(row[-1])
        except (TypeError, ValueError) as exc:
            raise TripletGeometryError(
                f'{source}:{line_number} count must be a positive integer',
            ) from exc
        if count <= 0 or str(count) != row[-1]:
            raise TripletGeometryError(
                f'{source}:{line_number} count must be a positive integer',
            )
        rows.append(validated + [count])
    if len(rows) != expected_rows:
        raise TripletGeometryError(
            f'{source} row count {len(rows)} does not match expected {expected_rows}',
        )
    return pd.DataFrame.from_records(rows, columns=expected_header)


def averageTriplets(
        source_path='./data/triplets.csv',
        output_csv='./data/triplets_avg.csv',
        output_pickle='./data/triplets_avg.pickle'):
    '''Average the already gathered canonical corpus without rediscovering CSVs.'''

    # average triplet values =======================###
    sys.stdout.write("Averaging triplets..."); sys.stdout.flush()
    timer = startTimer()
    source_argument = Path(source_path)
    lexical_parents = (source_argument.absolute().parent,) + tuple(
        source_argument.absolute().parents
    )
    data_root = next(
        (parent for parent in lexical_parents if parent.name.casefold() == 'data'),
        None,
    )
    if data_root is None:
        raise TripletGeometryError('Gathered triplet source must be below a project data directory')
    data_root = data_root.resolve(strict=True)
    source_path, source_resolved = _inside_root(
        source_argument,
        data_root,
        field='Gathered triplet source',
        must_exist=True,
    )
    output_csv, output_pickle = _safe_destinations(
        (
            ('Averaged triplet CSV', output_csv),
            ('Averaged triplet pickle', output_pickle),
        ),
        data_root=data_root,
        protected_paths=(('gathered triplet source', source_resolved),),
    )
    source_snapshot = source_path.read_bytes()
    df_trip = _read_triplet_bytes(
        source_snapshot,
        source=str(source_path),
    )
    if df_trip.empty:
        raise TripletGeometryError('Cannot average an empty gathered triplet corpus')
    averages = {}
    for _index, row in df_trip.iterrows():
        objA = row['objectA']
        objB = row['objectB']
        objC = row['objectC']
        triplet = (objA, objB, objC)
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
        objA, objB, objC = triplet
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
    project_root = data_root.parent
    staging_root = project_root / '.codex' / 'triplet-average'
    staging_root.mkdir(parents=True, exist_ok=True)
    identifier = str(uuid.uuid4())
    candidate_csv = staging_root / f'triplets-avg.{identifier}.csv'
    candidate_pickle = staging_root / f'triplets-avg.{identifier}.pickle'
    try:
        df_triplets.to_csv(candidate_csv, index=False, lineterminator='\n')
        with candidate_pickle.open('wb') as handle:
            pickle.dump(triplets, handle, protocol=pickle.HIGHEST_PROTOCOL)
        candidate_csv_snapshot = candidate_csv.read_bytes()
        candidate_frame = _read_averaged_candidate(
            candidate_csv_snapshot,
            source=str(candidate_csv),
            expected_rows=len(df_triplets),
        )
        if not candidate_frame.equals(df_triplets):
            raise TripletGeometryError(
                'Averaged triplet CSV rows do not match the computed averages',
            )
        try:
            candidate_pickle_snapshot = candidate_pickle.read_bytes()
            observed_pickle = pickle.loads(candidate_pickle_snapshot)
        except (OSError, pickle.PickleError, EOFError) as exc:
            raise TripletGeometryError('Cannot validate averaged triplet pickle') from exc
        if observed_pickle != triplets:
            raise TripletGeometryError('Averaged triplet pickle failed read-back validation')
        with _destination_set_lock(
                (output_csv, output_pickle),
                project_root=project_root):
            if source_path.read_bytes() != source_snapshot:
                raise TripletGeometryError('Gathered triplet source changed before promotion')
            if candidate_csv.read_bytes() != candidate_csv_snapshot:
                raise TripletGeometryError('Averaged triplet CSV changed before promotion')
            if candidate_pickle.read_bytes() != candidate_pickle_snapshot:
                raise TripletGeometryError('Averaged triplet pickle changed before promotion')
            _promote_candidates(
                (
                    (candidate_csv, output_csv),
                    (candidate_pickle, output_pickle),
                ),
                staging_root=staging_root,
            )
    finally:
        for candidate in (candidate_csv, candidate_pickle):
            candidate.unlink(missing_ok=True)
        try:
            staging_root.rmdir()
        except OSError:
            pass

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

def suffix_duplicates(items):
    '''Return stable instance IDs while preserving the first label verbatim.'''

    result = []
    item_count = {}
    for item in items:
        if item not in item_count:
            item_count[item] = 0
            result.append(item)
        else:
            item_count[item] += 1
            result.append(f"{item}.{item_count[item]}")

    return result


def ensure_window_assemblies(coordinates, sizes):
    '''Retain requested coverings and add only the window mesh they require.

    A blind or curtain is rendered as a covering over a window.  The historical
    implementation randomly deleted requested instances and sometimes replaced
    one label with another.  Instance suffixes now identify deterministic
    assemblies: ``curtain.1`` pairs with ``window.1`` while an unsuffixed
    covering pairs with ``window``.  Companion windows are additive; no
    requested instance is removed or renamed.
    '''

    covering_names = [
        name for name in tuple(coordinates)
        if name.split('.', 1)[0] in {'blinds', 'curtain'}
    ]
    for covering_name in covering_names:
        _base, separator, suffix = covering_name.partition('.')
        window_name = f'window.{suffix}' if separator else 'window'
        if window_name not in coordinates:
            coordinates[window_name] = copy.deepcopy(coordinates[covering_name])
            sizes[window_name] = copy.deepcopy(sizes[covering_name])
        else:
            # Coverings in one assembly share the window's geometric anchor and
            # sampled size but retain their own identity and colour.
            coordinates[covering_name] = copy.deepcopy(coordinates[window_name])
            sizes[covering_name] = copy.deepcopy(sizes[window_name])
    return coordinates, sizes


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

    object_count = len(objects)
    if not MIN_OBJECT_COUNT <= object_count <= MAX_TOTAL_OBJECT_COUNT:
        raise TripletCoverageError(
            f'Coordinate construction requires {MIN_OBJECT_COUNT} to '
            f'{MAX_TOTAL_OBJECT_COUNT} total object instances; received {object_count}'
        )

    instance_names = suffix_duplicates(objects)

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
            dictionary[(objA, objB, objC)] = comboData
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
            dictionary[(objA, objB, objC)] = comboData

    else:
        print_debug and print(f'ERROR: Wrong method selected for calculateCoords: {method}.')
        return {}

    # Search ordered anchor *instances*, not n! label permutations.  A complete
    # witness must have one A/B/C row for every non-anchor position, including
    # another occurrence of either anchor label.  Sampling uniformly from this
    # finite witness set retains the model's random basis choice without letting
    # repeated labels masquerade as complete instance coverage.
    triplet_keys = frozenset(dictionary)
    witnesses = ordered_anchor_witnesses(objects, triplet_keys)
    if not witnesses:
        raise TripletCoverageError(
            'No ordered instance anchor pair has triplet data for every '
            'requested partner instance'
        )
    anchor_a, anchor_b, partner_indices = random.choice(witnesses)
    if len(partner_indices) > 1:
        # The first partner supplies the retained camera/reference frame.  Pick
        # that *instance* independently of input order, then keep every other
        # partner in its stable order for deterministic downstream alignment.
        base_partner = random.choice(partner_indices)
        partner_indices = (base_partner,) + tuple(
            index for index in partner_indices if index != base_partner
        )
    combos = [
        [objects[anchor_a], objects[anchor_b], objects[partner]]
        for partner in partner_indices
    ]
    combo_instance_names = [
        [instance_names[anchor_a], instance_names[anchor_b], instance_names[partner]]
        for partner in partner_indices
    ]
    print_debug and print("INFO: Found complete ordered instance-anchor witness.")

    sizes = loadObjectSizes(objects)

    # ensure distance AB is the same for all triplets, so make it equal to mean distance AB of all triplets
    all_ab_dists = []
    for combo in combos:
        all_ab_dists.append(float(dictionary[tuple(combo)][0]))
    d_ab = np.mean(all_ab_dists) # universal distance AB for all triplets

    triplets = []
    for i, combo in enumerate(combos): # for each combo
        combo_key = tuple(combo)
        data = dictionary[combo_key] # find triplet data
        print_debug and print("INFO: Setting up combo: "+ repr(combo))
        # set up variables
        objA,objB,objC = combo_instance_names[i]

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

    finalCoords, sizes = ensure_window_assemblies(finalCoords, sizes)

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

    # Assembly normalization is deterministic and does not represent another
    # sampling pass, so it should not mutate the caller's progress-line state.
    text_len = prev_text_len

    if return_alignment_diagnostics:
        return finalCoords, text_len, alignment_diagnostics
    return finalCoords, text_len
