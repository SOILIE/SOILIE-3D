"""Strict provenance contract for one collector run's relation CSV files.

The prepared corpus must never be assembled by recursively accepting every
CSV under ``data``.  A collector run instead publishes one complete manifest
whose entries bind each relation file to its annotation frame and exact C2W
camera pose.  Consumers validate the entire manifest before using any row.
"""

from __future__ import annotations

import csv
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import unicodedata
import uuid

import numpy as np

from modules.object_identity import (
    ObjectIdentityError,
    decode_collector_object_label,
)
from modules.source_geometry import (
    SourceGeometryError,
    pair_rgb_depth_files,
    pinned_extrinsics_filename,
    pose_index,
    reshape_c2w_rows,
    resolve_source_profile,
    validate_c2w,
)
from modules.triplet_schema import (
    ANGLE_FIELDS,
    DISTANCE_FIELDS,
    TRIPLET_COLUMNS,
    validate_triplet_columns,
)


RELATION_MANIFEST_SCHEMA_VERSION = 1
RELATION_MANIFEST_KIND = "soilie-relation-inputs"
RELATION_MANIFEST_FILENAME = "relation-inputs.json"
INCOMPLETE = "incomplete"
COMPLETE = "complete"

_MANIFEST_KEYS = frozenset({
    "schemaVersion", "kind", "status", "collectionId", "frames",
})
_FRAME_KEYS = frozenset({
    "dataset", "scene", "annotation", "sourceGeometry", "jsonFrameIndex",
    "image", "depth", "pose", "relation",
})
_ANNOTATION_KEYS = frozenset({"path", "sha256"})
_SOURCE_GEOMETRY_KEYS = frozenset({
    "depthEncoding", "pairingStrategy", "poseIndexStrategy", "pixelOrigin",
})
_SOURCE_FILE_KEYS = frozenset({"name", "frameId", "timestamp"})
_POSE_KEYS = frozenset({
    "extrinsicsFile", "index", "cameraToWorld", "sha256",
})
_RELATION_KEYS = frozenset({"path", "sha256", "rowCount"})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_DEVICE_RE = re.compile(
    r"^(?:con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\..*)?$",
    re.IGNORECASE,
)
_WINDOWS_ILLEGAL_FILENAME_CHARACTERS = frozenset('<>:"/\\|?*')


class RelationManifestError(ValueError):
    """A relation-run manifest or one of its sealed inputs is invalid."""


@dataclass(frozen=True)
class VerifiedRelationFile:
    """One manifest entry paired with its verified local relation path."""

    record: dict
    path: Path


def _reject_duplicate_json_keys(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            raise RelationManifestError(f"Duplicate JSON provenance key: {key!r}")
        document[key] = value
    return document


def sha256_file(path: str | os.PathLike[str]) -> str:
    """Return the SHA-256 of a file without loading large inputs at once."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def camera_pose_sha256(camera_to_world) -> str:
    """Hash a C2W matrix using a platform-independent float64 encoding."""

    pose = validate_c2w(camera_to_world)
    canonical = np.asarray(pose, dtype="<f8", order="C")
    return hashlib.sha256(canonical.tobytes(order="C")).hexdigest()


def _exact_keys(value, expected, *, field):
    if not isinstance(value, dict):
        raise RelationManifestError(f"{field} must be a JSON object")
    observed = set(value)
    missing = sorted(expected - observed)
    unknown = sorted(observed - expected)
    if missing or unknown:
        details = []
        if missing:
            details.append(f"missing {missing}")
        if unknown:
            details.append(f"unknown {unknown}")
        raise RelationManifestError(f"Invalid {field}: " + "; ".join(details))


def _integer(value, *, field, minimum=0):
    if type(value) is not int or value < minimum:
        raise RelationManifestError(
            f"{field} must be an integer greater than or equal to {minimum}",
        )
    return value


def _finite_json_number(value):
    if type(value) not in {int, float}:
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _sha256(value, *, field):
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise RelationManifestError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _portable_segment(value: str, *, field: str) -> str:
    """Reject names that Windows and POSIX cannot identify consistently."""

    if not value or value in {".", ".."}:
        raise RelationManifestError(f"{field} must be a non-empty path segment")
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise RelationManifestError(f"{field} cannot contain control characters")
    if value.endswith((".", " ")):
        raise RelationManifestError(f"{field} cannot end in a dot or space")
    if any(character in _WINDOWS_ILLEGAL_FILENAME_CHARACTERS for character in value):
        raise RelationManifestError(
            f"{field} contains a Windows-illegal filename character",
        )
    # Windows reserves these aliases even when a normal-looking extension is
    # appended (for example, NUL.csv).  Rejecting them keeps one manifest from
    # resolving to different files on different collector platforms.
    if _WINDOWS_DEVICE_RE.fullmatch(value):
        raise RelationManifestError(f"{field} uses a reserved Windows device name")
    return value


def _leaf_name(value, *, field, suffix=None):
    if not isinstance(value, str) or not value:
        raise RelationManifestError(f"{field} must be a non-empty filename")
    if (
        value in {".", ".."}
        or "/" in value
        or "\\" in value
        or ":" in value
    ):
        raise RelationManifestError(f"{field} must be one portable leaf filename")
    _portable_segment(value, field=field)
    if suffix is not None and not value.casefold().endswith(suffix.casefold()):
        raise RelationManifestError(f"{field} must end in {suffix}")
    return value


def _relative_posix(value, *, field, leaf=False):
    """Validate one canonical path that has identical meaning on all OSes."""

    if not isinstance(value, str) or not value:
        raise RelationManifestError(f"{field} must be a non-empty POSIX path")
    # Backslashes, drive prefixes and UNC syntax must not acquire a different
    # meaning when the same manifest moves between POSIX and Windows.
    if "\\" in value or ":" in value or value.startswith("//"):
        raise RelationManifestError(f"{field} must be a portable relative POSIX path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
        or (leaf and len(path.parts) != 1)
    ):
        raise RelationManifestError(f"{field} must be a canonical relative POSIX path")
    for offset, part in enumerate(path.parts):
        _portable_segment(part, field=f"{field} segment {offset}")
    return path


def validate_relation_location(dataset: str, scene: str) -> tuple[str, str]:
    """Return canonical portable dataset/scene identities before any export."""

    dataset_path = _relative_posix(dataset, field="dataset", leaf=True)
    scene_path = _relative_posix(scene, field="scene")
    return dataset_path.as_posix(), scene_path.as_posix()


def _source_file(value, *, field):
    _exact_keys(value, _SOURCE_FILE_KEYS, field=field)
    _leaf_name(value["name"], field=f"{field}.name")
    _integer(value["frameId"], field=f"{field}.frameId")
    timestamp = value["timestamp"]
    if timestamp is not None:
        _integer(timestamp, field=f"{field}.timestamp")


def _profile_values(profile):
    return (
        profile.depth_encoding,
        profile.pairing_strategy,
        profile.pose_index_strategy,
        profile.pixel_origin,
    )


def _validate_frame_record(record, *, index):
    field = f"frames[{index}]"
    _exact_keys(record, _FRAME_KEYS, field=field)
    dataset = _relative_posix(record["dataset"], field=f"{field}.dataset", leaf=True)
    scene = _relative_posix(record["scene"], field=f"{field}.scene")
    _integer(record["jsonFrameIndex"], field=f"{field}.jsonFrameIndex")

    annotation = record["annotation"]
    _exact_keys(annotation, _ANNOTATION_KEYS, field=f"{field}.annotation")
    annotation_path = _relative_posix(
        annotation["path"], field=f"{field}.annotation.path",
    )
    expected_annotation_prefix = ("json", dataset.as_posix())
    if annotation_path.parts[:2] != expected_annotation_prefix:
        raise RelationManifestError(
            f"{field}.annotation.path must be below json/{dataset.as_posix()}",
        )
    if annotation_path.suffix.casefold() != ".json":
        raise RelationManifestError(f"{field}.annotation.path must end in .json")
    _sha256(annotation["sha256"], field=f"{field}.annotation.sha256")

    geometry = record["sourceGeometry"]
    _exact_keys(geometry, _SOURCE_GEOMETRY_KEYS, field=f"{field}.sourceGeometry")
    try:
        profile = resolve_source_profile(dataset.as_posix(), geometry)
    except SourceGeometryError as exc:
        raise RelationManifestError(f"{field}.sourceGeometry is invalid: {exc}") from exc

    _source_file(record["image"], field=f"{field}.image")
    _source_file(record["depth"], field=f"{field}.depth")
    try:
        (frame_pair,) = pair_rgb_depth_files(
            [record["image"]["name"]],
            [record["depth"]["name"]],
            profile.pairing_strategy,
        )
    except SourceGeometryError as exc:
        raise RelationManifestError(f"{field} image/depth pairing is invalid: {exc}") from exc
    declared_pair = (
        record["image"]["frameId"],
        record["depth"]["frameId"],
        record["image"]["timestamp"],
        record["depth"]["timestamp"],
    )
    parsed_pair = (
        frame_pair.image_frame_id,
        frame_pair.depth_frame_id,
        frame_pair.image_timestamp,
        frame_pair.depth_timestamp,
    )
    if declared_pair != parsed_pair:
        raise RelationManifestError(
            f"{field} declared frame IDs/timestamps do not match the source filenames",
        )

    pose = record["pose"]
    _exact_keys(pose, _POSE_KEYS, field=f"{field}.pose")
    _leaf_name(pose["extrinsicsFile"], field=f"{field}.pose.extrinsicsFile", suffix=".txt")
    _integer(pose["index"], field=f"{field}.pose.index")
    try:
        expected_pose_index = pose_index(
            frame_pair,
            record["jsonFrameIndex"],
            profile.pose_index_strategy,
        )
    except SourceGeometryError as exc:
        raise RelationManifestError(f"{field}.pose.index cannot be resolved: {exc}") from exc
    if pose["index"] != expected_pose_index:
        raise RelationManifestError(
            f"{field}.pose.index must be {expected_pose_index} for "
            f"{profile.pose_index_strategy!r}",
        )
    expected_pose_hash = _sha256(pose["sha256"], field=f"{field}.pose.sha256")
    encoded_pose = pose["cameraToWorld"]
    valid_encoded_pose = (
        isinstance(encoded_pose, list)
        and len(encoded_pose) == 3
        and all(isinstance(row, list) and len(row) == 4 for row in encoded_pose)
        and all(
            _finite_json_number(component)
            for row in encoded_pose
            for component in row
        )
    )
    if not valid_encoded_pose:
        raise RelationManifestError(
            f"{field}.pose.cameraToWorld must be a finite numeric 3x4 JSON array",
        )
    try:
        observed_pose_hash = camera_pose_sha256(encoded_pose)
    except (TypeError, ValueError) as exc:
        raise RelationManifestError(f"{field}.pose.cameraToWorld is not a valid C2W pose") from exc
    if observed_pose_hash != expected_pose_hash:
        raise RelationManifestError(f"{field}.pose SHA-256 does not match cameraToWorld")

    relation = record["relation"]
    _exact_keys(relation, _RELATION_KEYS, field=f"{field}.relation")
    relation_path = _relative_posix(relation["path"], field=f"{field}.relation.path")
    expected_relation_path = dataset / scene / f'{record["image"]["frameId"]}.csv'
    if relation_path != expected_relation_path:
        raise RelationManifestError(
            f"{field}.relation.path must equal {expected_relation_path.as_posix()!r}",
        )
    _sha256(relation["sha256"], field=f"{field}.relation.sha256")
    _integer(relation["rowCount"], field=f"{field}.relation.rowCount")
    return annotation_path, relation_path


def _resolve_inside(root: Path, relative: PurePosixPath, *, field: str) -> Path:
    root = root.resolve()
    candidate = root.joinpath(*relative.parts)
    try:
        candidate = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"{field} does not exist: {candidate}") from exc
    try:
        inside = os.path.commonpath((str(root), str(candidate))) == str(root)
    except ValueError:
        inside = False
    if not inside:
        raise RelationManifestError(f"{field} escapes its allowed root: {relative.as_posix()!r}")
    if not candidate.is_file():
        raise RelationManifestError(f"{field} is not a regular file: {candidate}")
    return candidate


def _load_annotation_json(path: Path) -> dict:
    try:
        document = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RelationManifestError(f"Cannot read annotation JSON {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise RelationManifestError(f"Annotation JSON {path} must contain one JSON object")
    return document


def _verify_annotation_semantics(
    annotation: dict,
    annotation_path: Path,
    record: dict,
    *,
    index: int,
    data_root: Path,
    extrinsics_cache: dict[Path, np.ndarray],
) -> None:
    """Bind sealed annotation bytes to the exact collected frame and pose."""

    field = f"frames[{index}]"
    if annotation.get("name") != record["scene"]:
        raise RelationManifestError(
            f"{field}.scene does not match annotation name in {annotation_path}",
        )
    # Existing reviewed annotations derive dataset identity from their
    # json/<dataset>/ path.  Newer annotations may additionally declare it.
    if "dataset" in annotation and annotation["dataset"] != record["dataset"]:
        raise RelationManifestError(
            f"{field}.dataset does not match annotation dataset in {annotation_path}",
        )

    frames = annotation.get("frames")
    json_index = record["jsonFrameIndex"]
    if not isinstance(frames, list) or json_index >= len(frames):
        raise RelationManifestError(
            f"{field}.jsonFrameIndex is outside annotation frames in {annotation_path}",
        )
    annotation_frame = frames[json_index]
    if (
        not isinstance(annotation_frame, dict)
        or not isinstance(annotation_frame.get("polygon"), list)
        or not annotation_frame["polygon"]
    ):
        raise RelationManifestError(
            f"{field}.jsonFrameIndex selects an empty annotation frame",
        )

    file_list = annotation.get("fileList")
    if not isinstance(file_list, list) or len(file_list) != len(frames):
        raise RelationManifestError(
            f"{field} annotation fileList must contain one image per frame",
        )
    if file_list[json_index] != record["image"]["name"]:
        raise RelationManifestError(
            f"{field}.image.name does not match annotation fileList[{json_index}]",
        )

    try:
        annotation_extrinsics = pinned_extrinsics_filename(annotation.get("extrinsics"))
        annotation_profile = resolve_source_profile(
            record["dataset"],
            annotation.get("sourceGeometry"),
        )
        record_profile = resolve_source_profile(record["dataset"], record["sourceGeometry"])
    except SourceGeometryError as exc:
        raise RelationManifestError(
            f"{field} annotation source geometry is invalid: {exc}",
        ) from exc
    if annotation_extrinsics != record["pose"]["extrinsicsFile"]:
        raise RelationManifestError(
            f"{field}.pose.extrinsicsFile does not match the annotation",
        )
    if _profile_values(annotation_profile) != _profile_values(record_profile):
        raise RelationManifestError(
            f"{field}.sourceGeometry does not match the annotation source profile",
        )

    # Raw source corpora are intentionally optional in a prepared checkout.
    # When the named file is present, however, the manifest must carry the
    # exact matrix at the source-specific pose index rather than merely a
    # self-consistent matrix/hash pair.
    source_relative = (
        PurePosixPath(record["dataset"])
        / PurePosixPath(record["scene"])
        / "extrinsics"
        / annotation_extrinsics
    )
    unresolved_extrinsics = data_root.joinpath(*source_relative.parts)
    if unresolved_extrinsics.exists():
        extrinsics_path = _resolve_inside(
            data_root,
            source_relative,
            field=f"{field}.pose.extrinsicsFile",
        )
        matrices = extrinsics_cache.get(extrinsics_path)
        if matrices is None:
            try:
                matrices = reshape_c2w_rows(np.loadtxt(extrinsics_path, ndmin=2))
            except (OSError, SourceGeometryError, ValueError) as exc:
                raise RelationManifestError(
                    f"Cannot read named extrinsics file {extrinsics_path}: {exc}",
                ) from exc
            extrinsics_cache[extrinsics_path] = matrices
        pose_index_value = record["pose"]["index"]
        if pose_index_value >= len(matrices):
            raise RelationManifestError(
                f"{field}.pose.index is outside named extrinsics file {extrinsics_path}",
            )
        try:
            expected_pose = validate_c2w(matrices[pose_index_value])
        except SourceGeometryError as exc:
            raise RelationManifestError(
                f"Named extrinsics file {extrinsics_path} contains an invalid C2W pose: {exc}",
            ) from exc
        recorded_pose = np.asarray(record["pose"]["cameraToWorld"], dtype=np.float64)
        if not np.array_equal(expected_pose, recorded_pose):
            raise RelationManifestError(
                f"{field}.pose.cameraToWorld does not match named extrinsics file",
            )


def _validate_relation_csv(path: Path, *, expected_rows: int) -> int:
    rows = 0
    with path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.reader(source)
        header = next(reader, ())
        try:
            validate_triplet_columns(header, source=str(path))
        except ValueError as exc:
            raise RelationManifestError(str(exc)) from exc
        for line_number, row in enumerate(reader, start=2):
            if len(row) != len(TRIPLET_COLUMNS):
                raise RelationManifestError(
                    f"{path}:{line_number} has {len(row)} fields; "
                    f"expected {len(TRIPLET_COLUMNS)}",
                )
            for offset, name in enumerate(row[:3]):
                if not name or name.strip() != name:
                    raise RelationManifestError(
                        f"{path}:{line_number} {TRIPLET_COLUMNS[offset]} must be a "
                        "non-empty canonical object name",
                    )
                try:
                    decode_collector_object_label(name)
                except ObjectIdentityError as exc:
                    raise RelationManifestError(
                        f"{path}:{line_number} {TRIPLET_COLUMNS[offset]} is not a "
                        f"canonical collector object identity: {exc}",
                    ) from exc
            for offset, raw in enumerate(row[3:], start=3):
                column = TRIPLET_COLUMNS[offset]
                try:
                    value = float(raw)
                except (TypeError, ValueError) as exc:
                    raise RelationManifestError(
                        f"{path}:{line_number} {column} must be numeric",
                    ) from exc
                if not math.isfinite(value):
                    raise RelationManifestError(
                        f"{path}:{line_number} {column} must be finite",
                    )
                if column in DISTANCE_FIELDS and value <= 0.0:
                    raise RelationManifestError(
                        f"{path}:{line_number} {column} must be greater than zero",
                    )
                if column in ANGLE_FIELDS and not 0.0 <= value <= 180.0:
                    raise RelationManifestError(
                        f"{path}:{line_number} {column} must be between 0 and 180 degrees",
                    )
            rows += 1
    if rows != expected_rows:
        raise RelationManifestError(
            f"{path} row count {rows} does not match manifest rowCount {expected_rows}",
        )
    return rows


def _validate_manifest_document(document, *, require_complete):
    _exact_keys(document, _MANIFEST_KEYS, field="relation manifest")
    if (
        type(document["schemaVersion"]) is not int
        or document["schemaVersion"] != RELATION_MANIFEST_SCHEMA_VERSION
    ):
        raise RelationManifestError(
            f"relation manifest schemaVersion must be {RELATION_MANIFEST_SCHEMA_VERSION}",
        )
    if document["kind"] != RELATION_MANIFEST_KIND:
        raise RelationManifestError(
            f"relation manifest kind must be {RELATION_MANIFEST_KIND!r}",
        )
    if document["status"] not in {INCOMPLETE, COMPLETE}:
        raise RelationManifestError("relation manifest status must be incomplete or complete")
    try:
        parsed_id = uuid.UUID(document["collectionId"])
    except (AttributeError, TypeError, ValueError) as exc:
        raise RelationManifestError("relation manifest collectionId must be a UUID") from exc
    if str(parsed_id) != document["collectionId"]:
        raise RelationManifestError("relation manifest collectionId must use canonical UUID text")
    if not isinstance(document["frames"], list):
        raise RelationManifestError("relation manifest frames must be a JSON array")
    if document["status"] == INCOMPLETE and document["frames"]:
        raise RelationManifestError("an incomplete relation manifest cannot contain frame records")
    if require_complete and document["status"] != COMPLETE:
        raise RelationManifestError(
            "relation manifest is incomplete; finish or restart source collection before gathering",
        )
    if document["status"] == COMPLETE and not document["frames"]:
        raise RelationManifestError("a complete relation manifest must contain at least one frame")

    parsed_paths = []
    identities = set()
    portable_relation_paths = set()
    for index, record in enumerate(document["frames"]):
        annotation_path, relation_path = _validate_frame_record(record, index=index)
        identity = (
            record["dataset"].casefold(),
            record["scene"].casefold(),
            record["jsonFrameIndex"],
        )
        if identity in identities:
            raise RelationManifestError(f"Duplicate relation input identity: {identity!r}")
        identities.add(identity)
        portable_key = relation_path.as_posix().casefold()
        if portable_key in portable_relation_paths:
            raise RelationManifestError(
                f"Duplicate portable relation path: {relation_path.as_posix()!r}",
            )
        portable_relation_paths.add(portable_key)
        parsed_paths.append((record, annotation_path, relation_path))
    return parsed_paths


def load_verified_relation_manifest(
    manifest_path: str | os.PathLike[str],
    *,
    data_root: str | os.PathLike[str] | None = None,
) -> tuple[dict, tuple[VerifiedRelationFile, ...]]:
    """Load and fully verify one complete relation-run manifest.

    The default layout expects the manifest directly under ``data`` and its
    annotation JSON files under the adjacent repository-level ``json`` folder.
    Every entry is validated before the tuple is returned to a corpus builder.
    """

    manifest_path = Path(manifest_path)
    try:
        document = json.loads(
            manifest_path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RelationManifestError(f"Cannot read relation manifest {manifest_path}: {exc}") from exc
    parsed_paths = _validate_manifest_document(document, require_complete=True)
    data_root = Path(data_root) if data_root is not None else manifest_path.parent
    repository_root = data_root.resolve().parent

    verified = []
    resolved_relation_paths = set()
    annotation_hashes = {}
    annotation_documents = {}
    extrinsics_cache = {}
    for index, (record, annotation_relative, relation_relative) in enumerate(parsed_paths):
        annotation_path = _resolve_inside(
            repository_root, annotation_relative,
            field=f"frames[{index}].annotation.path",
        )
        expected_annotation_hash = record["annotation"]["sha256"]
        observed_annotation_hash = annotation_hashes.get(annotation_path)
        if observed_annotation_hash is None:
            observed_annotation_hash = sha256_file(annotation_path)
            annotation_hashes[annotation_path] = observed_annotation_hash
        if observed_annotation_hash != expected_annotation_hash:
            raise RelationManifestError(
                f"Stale annotation input {annotation_path}: SHA-256 "
                f"{observed_annotation_hash} does not match manifest "
                f"{expected_annotation_hash}",
            )
        annotation_document = annotation_documents.get(annotation_path)
        if annotation_document is None:
            annotation_document = _load_annotation_json(annotation_path)
            annotation_documents[annotation_path] = annotation_document
        _verify_annotation_semantics(
            annotation_document,
            annotation_path,
            record,
            index=index,
            data_root=data_root.resolve(),
            extrinsics_cache=extrinsics_cache,
        )

        relation_path = _resolve_inside(
            data_root, relation_relative,
            field=f"frames[{index}].relation.path",
        )
        resolved_key = os.path.normcase(str(relation_path))
        if resolved_key in resolved_relation_paths:
            raise RelationManifestError(f"Duplicate resolved relation path: {relation_path}")
        resolved_relation_paths.add(resolved_key)
        observed_relation_hash = sha256_file(relation_path)
        expected_relation_hash = record["relation"]["sha256"]
        if observed_relation_hash != expected_relation_hash:
            raise RelationManifestError(
                f"Stale relation input {relation_path}: SHA-256 "
                f"{observed_relation_hash} does not match manifest "
                f"{expected_relation_hash}",
            )
        _validate_relation_csv(
            relation_path,
            expected_rows=record["relation"]["rowCount"],
        )
        verified.append(VerifiedRelationFile(record=record, path=relation_path))

    return document, tuple(verified)


def relation_row_count(path: str | os.PathLike[str]) -> int:
    """Validate a collector relation file and return its data-row count."""

    relation_path = Path(path)
    # First obtain the physical row count, then run the same strict semantic
    # validation used when a manifest is consumed.
    with relation_path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.reader(source)
        next(reader, None)
        count = sum(1 for _row in reader)
    return _validate_relation_csv(relation_path, expected_rows=count)


def build_relation_frame_record(
    *,
    dataset: str,
    scene: str,
    annotation_path: str,
    annotation_sha256: str,
    source_geometry: dict,
    json_frame_index: int,
    image: dict,
    depth: dict,
    extrinsics_file: str,
    pose_index: int,
    camera_to_world,
    relation_path: str,
    relation_sha256: str,
    relation_row_count_value: int,
) -> dict:
    """Build and validate one JSON-safe collector provenance record."""

    pose = validate_c2w(camera_to_world)
    record = {
        "dataset": dataset,
        "scene": scene,
        "annotation": {
            "path": annotation_path,
            "sha256": annotation_sha256,
        },
        "sourceGeometry": dict(source_geometry),
        "jsonFrameIndex": json_frame_index,
        "image": dict(image),
        "depth": dict(depth),
        "pose": {
            "extrinsicsFile": extrinsics_file,
            "index": pose_index,
            "cameraToWorld": pose.tolist(),
            "sha256": camera_pose_sha256(pose),
        },
        "relation": {
            "path": relation_path,
            "sha256": relation_sha256,
            "rowCount": relation_row_count_value,
        },
    }
    _validate_frame_record(record, index=0)
    return record


def _manifest_staging_root(path: Path) -> Path:
    path = path.resolve()
    return path.parent.parent / ".codex" / "relation-manifests"


@contextmanager
def _manifest_write_lock(path: Path):
    """Serialize marker replacement across processes on Windows and POSIX."""

    path = path.resolve()
    staging_root = _manifest_staging_root(path)
    staging_root.mkdir(parents=True, exist_ok=True)
    identity = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]
    lock_path = staging_root / f"{path.name}.{identity}.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, "r+b", buffering=0) as lock_file:
        if os.fstat(lock_file.fileno()).st_size == 0:
            lock_file.write(b"\0")
        lock_file.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _atomic_write_json(path: Path, document: dict, *, before_replace=None) -> None:
    """Publish JSON atomically using a unique repository-local .codex file."""

    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    staging_root = _manifest_staging_root(path)
    staging_root.mkdir(parents=True, exist_ok=True)
    temporary = staging_root / f"{path.name}.{uuid.uuid4()}.tmp"
    try:
        temporary.write_text(
            json.dumps(document, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        if before_replace is not None:
            before_replace()
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
        try:
            staging_root.rmdir()
            staging_root.parent.rmdir()
        except OSError:
            # Another run or agent may own files in the shared .codex folder.
            pass


def _require_live_incomplete_marker(path: Path, collection_id: str) -> None:
    """Confirm the publication still belongs to the run being completed."""

    try:
        marker = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except FileNotFoundError as exc:
        raise RelationManifestError(
            "Cannot complete relation run: the incomplete marker is missing",
        ) from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RelationManifestError(f"Cannot read live relation marker {path}: {exc}") from exc
    _validate_manifest_document(marker, require_complete=False)
    if marker["status"] != INCOMPLETE:
        raise RelationManifestError(
            "Cannot complete relation run: the live marker is already complete",
        )
    if marker["collectionId"] != collection_id:
        raise RelationManifestError(
            "Cannot complete relation run: collectionId does not match the live marker",
        )


def begin_relation_run(
    manifest_path: str | os.PathLike[str],
    *,
    collection_id: str | None = None,
) -> str:
    """Invalidate any prior default manifest and mark a new run incomplete."""

    collection_id = collection_id or str(uuid.uuid4())
    document = {
        "schemaVersion": RELATION_MANIFEST_SCHEMA_VERSION,
        "kind": RELATION_MANIFEST_KIND,
        "status": INCOMPLETE,
        "collectionId": collection_id,
        "frames": [],
    }
    _validate_manifest_document(document, require_complete=False)
    manifest_path = Path(manifest_path)
    with _manifest_write_lock(manifest_path):
        _atomic_write_json(manifest_path, document)
    return collection_id


def complete_relation_run(
    manifest_path: str | os.PathLike[str],
    *,
    collection_id: str,
    frames: list[dict],
    data_root: str | os.PathLike[str] | None = None,
) -> dict:
    """Verify all run inputs, then atomically publish the complete manifest."""

    document = {
        "schemaVersion": RELATION_MANIFEST_SCHEMA_VERSION,
        "kind": RELATION_MANIFEST_KIND,
        "status": COMPLETE,
        "collectionId": collection_id,
        "frames": frames,
    }
    _validate_manifest_document(document, require_complete=True)

    # Verify through a temporary complete document before replacing the
    # incomplete marker.  Failure therefore leaves gathering safely blocked.
    manifest_path = Path(manifest_path)
    staging_root = _manifest_staging_root(manifest_path)
    staging_root.mkdir(parents=True, exist_ok=True)
    with _manifest_write_lock(manifest_path):
        # Check once before potentially expensive input verification and again
        # immediately before replacement.  The second check prevents a stale
        # collector from publishing after a newer begin_relation_run marker.
        _require_live_incomplete_marker(manifest_path, collection_id)
        candidate = staging_root / f"complete.{uuid.uuid4()}.json"
        try:
            candidate.write_text(
                json.dumps(document, indent=2, allow_nan=False) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            load_verified_relation_manifest(
                candidate,
                data_root=data_root if data_root is not None else manifest_path.parent,
            )
            _atomic_write_json(
                manifest_path,
                document,
                before_replace=lambda: _require_live_incomplete_marker(
                    manifest_path,
                    collection_id,
                ),
            )
        finally:
            if candidate.exists():
                candidate.unlink()
    return document
