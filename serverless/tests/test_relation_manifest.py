from __future__ import annotations

from copy import deepcopy
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

import numpy as np

from modules.object_identity import encode_collector_object_label
from modules.prepare_data import (
    TripletGeometryError,
    averageTriplets,
    gatherTriplets,
)
from modules.relation_manifest import (
    COMPLETE,
    INCOMPLETE,
    RELATION_MANIFEST_KIND,
    RELATION_MANIFEST_SCHEMA_VERSION,
    RelationManifestError,
    begin_relation_run,
    build_relation_frame_record,
    camera_pose_sha256,
    complete_relation_run,
    load_verified_relation_manifest,
    relation_row_count,
    sha256_file,
    validate_relation_location,
)
from modules.source_geometry import (
    WASHINGTON_RGBD_SCENES_V2_NATIVE_UINT16_TENTH_MM,
)
from modules.triplet_schema import LEGACY_TRIPLET_COLUMNS, TRIPLET_COLUMNS


ROOT = Path(__file__).resolve().parents[2]
TEST_WORK_ROOT = ROOT / ".codex" / "tests"
WASHINGTON_SOURCE_GEOMETRY = {
    "depthEncoding": WASHINGTON_RGBD_SCENES_V2_NATIVE_UINT16_TENTH_MM,
    "pairingStrategy": "exact_frame_id",
    "poseIndexStrategy": "json_position",
    "pixelOrigin": 0,
}


class RelationManifestTests(unittest.TestCase):
    def setUp(self):
        TEST_WORK_ROOT.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=TEST_WORK_ROOT)
        self.repository = Path(self.temporary.name)
        self.data = self.repository / "data"
        self.annotation = self.repository / "json" / "princeton" / "index000.json"
        self.relation = (
            self.data / "princeton" / "hotel_umd" / "maryland_hotel3" / "1.csv"
        )
        self.manifest = self.data / "relation-inputs.json"
        self.annotation.parent.mkdir(parents=True)
        self.relation.parent.mkdir(parents=True)
        self.write_annotation()
        self.write_relation(self.relation)

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def write_relation(path: Path, *, header=TRIPLET_COLUMNS, row=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as destination:
            writer = csv.writer(destination)
            writer.writerow(header)
            if row is not False:
                writer.writerow(row or (
                    encode_collector_object_label("chair", "1"),
                    encode_collector_object_label("desk", "1"),
                    encode_collector_object_label("lamp", "1"),
                    1, 2, 3, 60, 70, 80,
                ))

    def write_annotation(self, document=None):
        document = document or {
            "name": "hotel_umd/maryland_hotel3",
            "frames": [
                {"polygon": [{"object": 0}]},
                {"polygon": [{"object": 0}]},
                {"polygon": [{"object": 0}]},
            ],
            "fileList": [
                "0000001-100.jpg",
                "0000002-100.jpg",
                "0000003-100.jpg",
            ],
            "extrinsics": "reviewed.txt",
        }
        self.annotation.write_text(
            json.dumps(document, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        return document

    def frame_record(self, *, relation=None, frame_id=1, json_index=0):
        relation = relation or self.relation
        return build_relation_frame_record(
            dataset="princeton",
            scene="hotel_umd/maryland_hotel3",
            annotation_path="json/princeton/index000.json",
            annotation_sha256=sha256_file(self.annotation),
            source_geometry={
                "depthEncoding": "sun3d_circular_uint16_mm",
                "pairingStrategy": "sun3d_nearest_timestamp",
                "poseIndexStrategy": "one_based_image_frame",
                "pixelOrigin": 1,
            },
            json_frame_index=json_index,
            image={"name": f"{frame_id:07d}-100.jpg", "frameId": frame_id, "timestamp": 100},
            depth={"name": f"{frame_id:07d}-101.png", "frameId": frame_id, "timestamp": 101},
            extrinsics_file="reviewed.txt",
            pose_index=frame_id - 1,
            camera_to_world=np.hstack((np.eye(3), np.zeros((3, 1)))),
            relation_path=relation.relative_to(self.data).as_posix(),
            relation_sha256=sha256_file(relation),
            relation_row_count_value=relation_row_count(relation),
        )

    def publish(self, frames=None):
        collection_id = begin_relation_run(self.manifest)
        return complete_relation_run(
            self.manifest,
            collection_id=collection_id,
            frames=list(frames or [self.frame_record()]),
            data_root=self.data,
        )

    def test_incomplete_marker_blocks_gather_until_every_input_is_verified(self):
        collection_id = begin_relation_run(self.manifest)
        marker = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertEqual(INCOMPLETE, marker["status"])
        self.assertEqual([], marker["frames"])
        with self.assertRaisesRegex(RelationManifestError, "incomplete"):
            load_verified_relation_manifest(self.manifest)

        document = complete_relation_run(
            self.manifest,
            collection_id=collection_id,
            frames=[self.frame_record()],
            data_root=self.data,
        )
        self.assertEqual(COMPLETE, document["status"])
        loaded, files = load_verified_relation_manifest(self.manifest)
        self.assertEqual(RELATION_MANIFEST_SCHEMA_VERSION, loaded["schemaVersion"])
        self.assertEqual(RELATION_MANIFEST_KIND, loaded["kind"])
        self.assertEqual((self.relation.resolve(),), tuple(item.path for item in files))

    def test_complete_requires_the_current_incomplete_marker(self):
        frame = self.frame_record()
        missing_id = str(uuid.uuid4())
        with self.assertRaisesRegex(RelationManifestError, "marker is missing"):
            complete_relation_run(
                self.manifest,
                collection_id=missing_id,
                frames=[frame],
                data_root=self.data,
            )

        completed = self.publish()
        with self.assertRaisesRegex(RelationManifestError, "already complete"):
            complete_relation_run(
                self.manifest,
                collection_id=completed["collectionId"],
                frames=[frame],
                data_root=self.data,
            )

        stale_id = begin_relation_run(self.manifest)
        live_id = begin_relation_run(self.manifest)
        self.assertNotEqual(stale_id, live_id)
        with self.assertRaisesRegex(RelationManifestError, "does not match the live marker"):
            complete_relation_run(
                self.manifest,
                collection_id=stale_id,
                frames=[frame],
                data_root=self.data,
            )
        marker = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertEqual(INCOMPLETE, marker["status"])
        self.assertEqual(live_id, marker["collectionId"])

        run_id = begin_relation_run(self.manifest)
        replacement_id = str(uuid.uuid4())
        original_loader = load_verified_relation_manifest

        def replace_marker_after_candidate_verification(*args, **kwargs):
            result = original_loader(*args, **kwargs)
            replacement = {
                "schemaVersion": RELATION_MANIFEST_SCHEMA_VERSION,
                "kind": RELATION_MANIFEST_KIND,
                "status": INCOMPLETE,
                "collectionId": replacement_id,
                "frames": [],
            }
            self.manifest.write_text(json.dumps(replacement), encoding="utf-8")
            return result

        with patch(
            "modules.relation_manifest.load_verified_relation_manifest",
            side_effect=replace_marker_after_candidate_verification,
        ):
            with self.assertRaisesRegex(RelationManifestError, "does not match the live marker"):
                complete_relation_run(
                    self.manifest,
                    collection_id=run_id,
                    frames=[frame],
                    data_root=self.data,
                )
        marker = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertEqual(replacement_id, marker["collectionId"])

    def test_unlisted_stale_and_non_relation_csv_files_are_ignored(self):
        decoys = (
            self.data / "object_colors.csv",
            self.data / "triplets.csv",
            self.data / "working-combos-bedroom.csv",
            self.relation.with_name("2.csv"),
            self.relation.parent / "centroids.csv",
        )
        for decoy in decoys:
            decoy.parent.mkdir(parents=True, exist_ok=True)
            decoy.write_text("not,a,relation\n", encoding="utf-8")

        collection_id = begin_relation_run(self.manifest)
        complete_relation_run(
            self.manifest,
            collection_id=collection_id,
            frames=[self.frame_record()],
            data_root=self.data,
        )
        _document, files = load_verified_relation_manifest(self.manifest)
        self.assertEqual([self.relation.resolve()], [item.path for item in files])

    def test_stale_or_missing_relation_leaves_run_incomplete(self):
        record = self.frame_record()
        collection_id = begin_relation_run(self.manifest)
        self.relation.write_text("changed\n", encoding="utf-8")
        with self.assertRaisesRegex(RelationManifestError, "Stale relation input"):
            complete_relation_run(
                self.manifest,
                collection_id=collection_id,
                frames=[record],
                data_root=self.data,
            )
        self.assertEqual(
            INCOMPLETE,
            json.loads(self.manifest.read_text(encoding="utf-8"))["status"],
        )

        self.write_relation(self.relation)
        record = self.frame_record()
        collection_id = begin_relation_run(self.manifest)
        self.relation.unlink()
        with self.assertRaisesRegex(FileNotFoundError, "does not exist"):
            complete_relation_run(
                self.manifest,
                collection_id=collection_id,
                frames=[record],
                data_root=self.data,
            )

    def test_duplicate_identity_and_portable_path_are_rejected(self):
        first = self.frame_record()
        second_relation = self.relation.with_name("2.csv")
        self.write_relation(second_relation)
        duplicate_identity = self.frame_record(
            relation=second_relation,
            frame_id=2,
            json_index=0,
        )
        collection_id = begin_relation_run(self.manifest)
        with self.assertRaisesRegex(RelationManifestError, "Duplicate relation input identity"):
            complete_relation_run(
                self.manifest,
                collection_id=collection_id,
                frames=[first, duplicate_identity],
                data_root=self.data,
            )

        duplicate_path = deepcopy(first)
        duplicate_path["jsonFrameIndex"] = 1
        collection_id = begin_relation_run(self.manifest)
        with self.assertRaisesRegex(RelationManifestError, "Duplicate portable relation path"):
            complete_relation_run(
                self.manifest,
                collection_id=collection_id,
                frames=[first, duplicate_path],
                data_root=self.data,
            )

    def test_portable_paths_reject_posix_and_windows_escape_forms(self):
        self.assertEqual(
            ("princeton", "hotel_umd/maryland_hotel3"),
            validate_relation_location("princeton", "hotel_umd/maryland_hotel3"),
        )
        for scene in ("../outside", r"hotel_umd\maryland_hotel3", "C:/outside"):
            with self.subTest(scene=scene):
                with self.assertRaises(RelationManifestError):
                    validate_relation_location("princeton", scene)

        for scene in ("bad\x00name", "trailing.", "trailing ", "NUL", "room/COM1.csv"):
            with self.subTest(scene=scene):
                with self.assertRaisesRegex(
                    RelationManifestError,
                    "control|dot or space|reserved Windows",
                ):
                    validate_relation_location("princeton", scene)

        for character in '*?<>|"':
            scene = f"room/bad{character}name"
            with self.subTest(scene=scene):
                with self.assertRaisesRegex(
                    RelationManifestError,
                    "Windows-illegal filename character",
                ):
                    validate_relation_location("princeton", scene)

        reserved_leaf = self.frame_record()
        reserved_leaf["pose"]["extrinsicsFile"] = "NUL.txt"
        collection_id = begin_relation_run(self.manifest)
        with self.assertRaisesRegex(RelationManifestError, "reserved Windows"):
            complete_relation_run(
                self.manifest,
                collection_id=collection_id,
                frames=[reserved_leaf],
                data_root=self.data,
            )

        record = self.frame_record()
        unsafe = (
            "../outside.csv",
            "princeton/../outside.csv",
            "princeton\\scene\\1.csv",
            r"C:\outside.csv",
            r"\\server\share\outside.csv",
            "/absolute/outside.csv",
        )
        for value in unsafe:
            with self.subTest(value=value):
                bad = deepcopy(record)
                bad["relation"]["path"] = value
                collection_id = begin_relation_run(self.manifest)
                with self.assertRaisesRegex(RelationManifestError, "relative POSIX|canonical"):
                    complete_relation_run(
                        self.manifest,
                        collection_id=collection_id,
                        frames=[bad],
                        data_root=self.data,
                    )

    def test_annotation_hash_and_pose_hash_are_sealed(self):
        record = self.frame_record()
        self.assertEqual(
            camera_pose_sha256(record["pose"]["cameraToWorld"]),
            record["pose"]["sha256"],
        )
        collection_id = begin_relation_run(self.manifest)
        self.annotation.write_text('{"reviewed":false}\n', encoding="utf-8")
        with self.assertRaisesRegex(RelationManifestError, "Stale annotation input"):
            complete_relation_run(
                self.manifest,
                collection_id=collection_id,
                frames=[record],
                data_root=self.data,
            )

        self.annotation.write_text('{"reviewed":true}\n', encoding="utf-8")
        bad_pose = self.frame_record()
        bad_pose["pose"]["cameraToWorld"][0][3] = 1.0
        collection_id = begin_relation_run(self.manifest)
        with self.assertRaisesRegex(RelationManifestError, "pose SHA-256"):
            complete_relation_run(
                self.manifest,
                collection_id=collection_id,
                frames=[bad_pose],
                data_root=self.data,
            )

    def test_pose_index_profile_and_filename_metadata_are_cross_checked(self):
        cases = []

        wrong_pose_index = self.frame_record()
        wrong_pose_index["pose"]["index"] = 7
        cases.append((wrong_pose_index, "pose.index must be 0"))

        wrong_timestamp = self.frame_record()
        wrong_timestamp["image"]["timestamp"] = 999
        cases.append((wrong_timestamp, "frame IDs/timestamps"))

        wrong_known_profile = self.frame_record()
        wrong_known_profile["sourceGeometry"]["pixelOrigin"] = 0
        cases.append((wrong_known_profile, "conflicts with the 'princeton' profile"))

        for record, message in cases:
            with self.subTest(message=message):
                collection_id = begin_relation_run(self.manifest)
                with self.assertRaisesRegex(RelationManifestError, message):
                    complete_relation_run(
                        self.manifest,
                        collection_id=collection_id,
                        frames=[record],
                        data_root=self.data,
                    )

    def test_exact_frame_pairing_requires_equal_parsed_ids_and_null_timestamps(self):
        annotation = self.repository / "json" / "washington" / "scene_01.json"
        relation = self.data / "washington" / "scene_01" / "5.csv"
        annotation.parent.mkdir(parents=True)
        self.write_relation(relation)
        annotation_document = {
            "name": "scene_01",
            "frames": [{"polygon": [{"object": 0}]}],
            "fileList": ["00005-color.png"],
            "extrinsics": "extrinsics.txt",
            "sourceGeometry": WASHINGTON_SOURCE_GEOMETRY,
        }
        annotation.write_text(
            json.dumps(annotation_document) + "\n",
            encoding="utf-8",
        )
        record = build_relation_frame_record(
            dataset="washington",
            scene="scene_01",
            annotation_path="json/washington/scene_01.json",
            annotation_sha256=sha256_file(annotation),
            source_geometry=WASHINGTON_SOURCE_GEOMETRY,
            json_frame_index=0,
            image={"name": "00005-color.png", "frameId": 5, "timestamp": None},
            depth={"name": "00005-depth.png", "frameId": 5, "timestamp": None},
            extrinsics_file="extrinsics.txt",
            pose_index=0,
            camera_to_world=np.hstack((np.eye(3), np.zeros((3, 1)))),
            relation_path="washington/scene_01/5.csv",
            relation_sha256=sha256_file(relation),
            relation_row_count_value=relation_row_count(relation),
        )

        bad_id = deepcopy(record)
        bad_id["depth"]["name"] = "00006-depth.png"
        bad_id["depth"]["frameId"] = 6
        collection_id = begin_relation_run(self.manifest)
        with self.assertRaisesRegex(RelationManifestError, "frame-ID sets differ"):
            complete_relation_run(
                self.manifest,
                collection_id=collection_id,
                frames=[bad_id],
                data_root=self.data,
            )

        bad_timestamp = deepcopy(record)
        bad_timestamp["image"]["timestamp"] = 5
        collection_id = begin_relation_run(self.manifest)
        with self.assertRaisesRegex(RelationManifestError, "frame IDs/timestamps"):
            complete_relation_run(
                self.manifest,
                collection_id=collection_id,
                frames=[bad_timestamp],
                data_root=self.data,
            )

        for geometry, message in (
            (None, "explicitly declare sourceGeometry"),
            ({**WASHINGTON_SOURCE_GEOMETRY, "depthEncoding": "plain_uint16_mm"},
             "conflicts with the 'washington' profile"),
        ):
            with self.subTest(geometry=geometry):
                changed_annotation = deepcopy(annotation_document)
                if geometry is None:
                    del changed_annotation["sourceGeometry"]
                else:
                    changed_annotation["sourceGeometry"] = geometry
                annotation.write_text(
                    json.dumps(changed_annotation) + "\n",
                    encoding="utf-8",
                )
                changed_record = deepcopy(record)
                changed_record["annotation"]["sha256"] = sha256_file(annotation)
                collection_id = begin_relation_run(self.manifest)
                with self.assertRaisesRegex(RelationManifestError, message):
                    complete_relation_run(
                        self.manifest,
                        collection_id=collection_id,
                        frames=[changed_record],
                        data_root=self.data,
                    )

    def test_annotation_semantics_bind_scene_frame_file_and_source_contract(self):
        base = self.write_annotation()
        cases = []

        wrong_scene = deepcopy(base)
        wrong_scene["name"] = "other/scene"
        cases.append((wrong_scene, "scene does not match annotation name"))

        wrong_dataset = deepcopy(base)
        wrong_dataset["dataset"] = "washington"
        cases.append((wrong_dataset, "dataset does not match annotation dataset"))

        missing_frame = deepcopy(base)
        missing_frame["frames"] = []
        missing_frame["fileList"] = []
        cases.append((missing_frame, "outside annotation frames"))

        empty_frame = deepcopy(base)
        empty_frame["frames"][0] = {"polygon": []}
        cases.append((empty_frame, "selects an empty annotation frame"))

        missing_file_list = deepcopy(base)
        del missing_file_list["fileList"]
        cases.append((missing_file_list, "fileList must contain one image per frame"))

        wrong_file = deepcopy(base)
        wrong_file["fileList"][0] = "0000006-100.jpg"
        cases.append((wrong_file, "does not match annotation fileList"))

        wrong_extrinsics = deepcopy(base)
        wrong_extrinsics["extrinsics"] = "other.txt"
        cases.append((wrong_extrinsics, "does not match the annotation"))

        wrong_profile = deepcopy(base)
        wrong_profile["sourceGeometry"] = {
            "depthEncoding": "plain_uint16_mm",
            "pairingStrategy": "exact_frame_id",
            "poseIndexStrategy": "json_position",
            "pixelOrigin": 0,
        }
        cases.append((wrong_profile, "annotation source geometry is invalid"))

        for annotation_document, message in cases:
            with self.subTest(message=message):
                self.write_annotation(annotation_document)
                record = self.frame_record()
                collection_id = begin_relation_run(self.manifest)
                with self.assertRaisesRegex(RelationManifestError, message):
                    complete_relation_run(
                        self.manifest,
                        collection_id=collection_id,
                        frames=[record],
                        data_root=self.data,
                    )

        self.write_annotation(base)

    def test_accessible_named_extrinsics_must_contain_the_recorded_pose(self):
        extrinsics = (
            self.data
            / "princeton"
            / "hotel_umd"
            / "maryland_hotel3"
            / "extrinsics"
            / "reviewed.txt"
        )
        extrinsics.parent.mkdir(parents=True)
        source_pose = np.hstack((np.eye(3), np.zeros((3, 1))))
        np.savetxt(extrinsics, source_pose)

        self.publish()

        recorded_pose = source_pose.copy()
        recorded_pose[0, 3] = 2.0
        record = self.frame_record()
        record["pose"]["cameraToWorld"] = recorded_pose.tolist()
        record["pose"]["sha256"] = camera_pose_sha256(recorded_pose)
        collection_id = begin_relation_run(self.manifest)
        with self.assertRaisesRegex(RelationManifestError, "does not match named extrinsics"):
            complete_relation_run(
                self.manifest,
                collection_id=collection_id,
                frames=[record],
                data_root=self.data,
            )

    def test_relation_header_rows_and_numeric_ranges_are_strict(self):
        names = tuple(
            encode_collector_object_label(value, "1")
            for value in ("a", "b", "c")
        )
        cases = (
            (LEGACY_TRIPLET_COLUMNS, None, "legacy mislabeled"),
            (TRIPLET_COLUMNS, (*names, 1, 1, 1, "nan", 1, 1), "finite"),
            (TRIPLET_COLUMNS, (*names, 1, 1, 1, 181, 1, 1), "between 0 and 180"),
            (TRIPLET_COLUMNS, (*names, 0, 1, 1, 1, 1, 1), "greater than zero"),
            (TRIPLET_COLUMNS, ("a", *names[1:], 1, 1, 1, 1, 1, 1), "canonical collector"),
        )
        for header, row, message in cases:
            with self.subTest(message=message):
                self.write_relation(self.relation)
                record = self.frame_record()
                self.write_relation(self.relation, header=header, row=row)
                record["relation"]["sha256"] = sha256_file(self.relation)
                record["relation"]["rowCount"] = 1
                collection_id = begin_relation_run(self.manifest)
                with self.assertRaisesRegex(RelationManifestError, message):
                    complete_relation_run(
                        self.manifest,
                        collection_id=collection_id,
                        frames=[record],
                        data_root=self.data,
                    )

    def test_unknown_keys_row_count_and_symlink_escape_are_rejected(self):
        record = self.frame_record()
        unknown = deepcopy(record)
        unknown["relation"]["extra"] = True
        collection_id = begin_relation_run(self.manifest)
        with self.assertRaisesRegex(RelationManifestError, "unknown"):
            complete_relation_run(
                self.manifest,
                collection_id=collection_id,
                frames=[unknown],
                data_root=self.data,
            )

        wrong_count = deepcopy(record)
        wrong_count["relation"]["rowCount"] += 1
        collection_id = begin_relation_run(self.manifest)
        with self.assertRaisesRegex(RelationManifestError, "row count"):
            complete_relation_run(
                self.manifest,
                collection_id=collection_id,
                frames=[wrong_count],
                data_root=self.data,
            )

        outside = self.repository / "outside.csv"
        self.write_relation(outside)
        self.relation.unlink()
        try:
            self.relation.symlink_to(outside)
        except OSError:
            self.skipTest("This platform does not permit an unprivileged symlink fixture")
        escaped = deepcopy(record)
        escaped["relation"]["sha256"] = sha256_file(outside)
        collection_id = begin_relation_run(self.manifest)
        with self.assertRaisesRegex(RelationManifestError, "escapes"):
            complete_relation_run(
                self.manifest,
                collection_id=collection_id,
                frames=[escaped],
                data_root=self.data,
            )

    def test_gather_uses_only_manifest_rows_and_writes_a_self_contained_receipt(self):
        for decoy in (
            self.data / "object_colors.csv",
            self.data / "triplets.csv",
            self.data / "working-combos-refined.csv",
            self.relation.parent / "centroids.csv",
            self.relation.with_name("99.csv"),
        ):
            decoy.parent.mkdir(parents=True, exist_ok=True)
            decoy.write_text("not,a,relation\n", encoding="utf-8")
        self.publish()

        output = self.data / "candidate" / "triplets.csv"
        receipt_path = self.data / "candidate" / "triplets.sources.json"
        with patch(
            "modules.prepare_data.os.walk",
            side_effect=AssertionError("gather must not discover CSV files"),
        ):
            receipt = gatherTriplets(
                self.manifest,
                output,
                receipt_path,
                data_root=self.data,
            )

        with output.open(newline="", encoding="utf-8") as source:
            rows = list(csv.DictReader(source))
        self.assertEqual(1, len(rows))
        self.assertEqual(
            ("chair", "desk", "lamp"),
            tuple(rows[0][name] for name in ("objectA", "objectB", "objectC")),
        )
        self.assertEqual(sha256_file(output), receipt["output"]["sha256"])
        self.assertEqual(sha256_file(self.manifest), receipt["sourceManifest"]["sha256"])
        self.assertEqual(1, receipt["frameCount"])
        self.assertEqual(1, receipt["sourceRows"])
        self.assertEqual(self.frame_record()["pose"], receipt["frames"][0]["pose"])
        self.assertEqual(
            receipt,
            json.loads(receipt_path.read_text(encoding="utf-8")),
        )

        averaged = self.data / "candidate" / "triplets_avg.csv"
        averaged_pickle = self.data / "candidate" / "triplets_avg.pickle"
        with patch(
            "modules.prepare_data.os.walk",
            side_effect=AssertionError("average must read only the gathered corpus"),
        ):
            averageTriplets(output, averaged, averaged_pickle)
        with averaged.open(newline="", encoding="utf-8") as source:
            averaged_rows = list(csv.DictReader(source))
        self.assertEqual("1", averaged_rows[0]["count"])
        self.assertTrue(averaged_pickle.is_file())

    def test_stale_manifest_input_does_not_replace_existing_outputs(self):
        self.publish()
        output = self.data / "triplets-candidate.csv"
        receipt = self.data / "triplets-candidate.sources.json"
        output.write_bytes(b"existing-output\n")
        receipt.write_bytes(b"existing-receipt\n")
        self.relation.write_text("changed\n", encoding="utf-8")

        with self.assertRaisesRegex(RelationManifestError, "Stale relation input"):
            gatherTriplets(
                self.manifest,
                output,
                receipt,
                data_root=self.data,
            )

        self.assertEqual(b"existing-output\n", output.read_bytes())
        self.assertEqual(b"existing-receipt\n", receipt.read_bytes())

    def test_all_empty_manifest_is_rejected_without_replacing_output(self):
        self.write_relation(self.relation, row=False)
        self.publish()
        output = self.data / "empty-candidate.csv"
        output.write_bytes(b"existing-output\n")

        with self.assertRaisesRegex(TripletGeometryError, "no triplet data rows"):
            gatherTriplets(
                self.manifest,
                output,
                self.data / "empty-candidate.sources.json",
                data_root=self.data,
            )

        self.assertEqual(b"existing-output\n", output.read_bytes())


if __name__ == "__main__":
    unittest.main()
