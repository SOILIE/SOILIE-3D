from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from modules.object_identity import (
    ObjectIdentityError,
    decode_collector_object_label,
    encode_collector_object_label,
)
from modules.prepare_data import (
    TripletGeometryError,
    _destination_lock_paths,
    averageTriplets,
    calculateCoords,
    gatherTriplets,
)
from modules.relation_manifest import (
    RelationManifestError,
    begin_relation_run,
    build_relation_frame_record,
    complete_relation_run,
    relation_row_count,
    sha256_file,
)
from modules.triplet_schema import TRIPLET_COLUMNS


ROOT = Path(__file__).resolve().parents[2]
TEST_WORK_ROOT = ROOT / ".codex" / "tests"


class TripletGatherIntegrityTests(unittest.TestCase):
    def setUp(self):
        TEST_WORK_ROOT.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=TEST_WORK_ROOT)
        self.repository = Path(self.temporary.name)
        self.data = self.repository / "data"
        self.annotation = self.repository / "json" / "princeton" / "index000.json"
        self.relation = self.data / "princeton" / "scene_one" / "1.csv"
        self.manifest = self.data / "relation-inputs.json"
        self.annotation.parent.mkdir(parents=True)
        self.relation.parent.mkdir(parents=True)
        self.annotation.write_text(
            json.dumps({
                "name": "scene_one",
                "frames": [{"polygon": [{"object": 0}]}],
                "fileList": ["0000001-100.jpg"],
                "extrinsics": "reviewed.txt",
            }, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def _relation_row(
        object_a="chair",
        object_b="desk",
        object_c="lamp",
        *,
        numeric=(1, 2, 3, 60, 70, 80),
    ):
        return (
            encode_collector_object_label(object_a, "001"),
            encode_collector_object_label(object_b, "002"),
            encode_collector_object_label(object_c, "003"),
            *numeric,
        )

    def _write_relation(self, rows=None):
        rows = list(rows if rows is not None else [self._relation_row()])
        with self.relation.open("w", newline="", encoding="utf-8") as destination:
            writer = csv.writer(destination)
            writer.writerow(TRIPLET_COLUMNS)
            writer.writerows(rows)

    def _publish(self, rows=None):
        self._write_relation(rows)
        frame = build_relation_frame_record(
            dataset="princeton",
            scene="scene_one",
            annotation_path="json/princeton/index000.json",
            annotation_sha256=sha256_file(self.annotation),
            source_geometry={
                "depthEncoding": "sun3d_circular_uint16_mm",
                "pairingStrategy": "sun3d_nearest_timestamp",
                "poseIndexStrategy": "one_based_image_frame",
                "pixelOrigin": 1,
            },
            json_frame_index=0,
            image={"name": "0000001-100.jpg", "frameId": 1, "timestamp": 100},
            depth={"name": "0000001-101.png", "frameId": 1, "timestamp": 101},
            extrinsics_file="reviewed.txt",
            pose_index=0,
            camera_to_world=np.hstack((np.eye(3), np.zeros((3, 1)))),
            relation_path="princeton/scene_one/1.csv",
            relation_sha256=sha256_file(self.relation),
            relation_row_count_value=relation_row_count(self.relation),
        )
        collection_id = begin_relation_run(self.manifest)
        complete_relation_run(
            self.manifest,
            collection_id=collection_id,
            frames=[frame],
            data_root=self.data,
        )

    def _gather(self, output=None, receipt=None):
        return gatherTriplets(
            self.manifest,
            output or self.data / "triplets.csv",
            receipt or self.data / "triplets.sources.json",
            data_root=self.data,
        )

    def test_object_identity_codec_is_canonical_and_delimiter_safe(self):
        encoded = encode_collector_object_label(" side_table, left:wing ", " rack_01:A ")
        self.assertEqual(
            ("side_table,_left:wing", "rack_01:A"),
            decode_collector_object_label(encoded),
        )
        self.assertEqual(encoded, encode_collector_object_label(*decode_collector_object_label(encoded)))
        spaced = encode_collector_object_label("bathroom", "bathroom left")
        joined = encode_collector_object_label("bathroom", "bath roomleft")
        self.assertNotEqual(spaced, joined)
        self.assertEqual(("bathroom", "bathroom left"), decode_collector_object_label(spaced))
        for invalid in (
            "chair_12",
            'soilie-object-v1: ["chair","12"]',
            'soilie-object-v1:["chair"]',
            'soilie-object-v1:["chair",null]',
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ObjectIdentityError):
                    decode_collector_object_label(invalid)

    def test_gather_decodes_instances_without_losing_class_punctuation(self):
        self._publish([
            self._relation_row("side_table, left:wing", "desk_unit", "lamp:task"),
        ])
        output = self.data / "prepared" / "triplets.csv"
        receipt = self.data / "prepared" / "triplets.sources.json"
        result = self._gather(output, receipt)

        with output.open(newline="", encoding="utf-8") as source:
            row = next(csv.DictReader(source))
        self.assertEqual("side_table,_left:wing", row["objectA"])
        self.assertEqual("desk_unit", row["objectB"])
        self.assertEqual("lamp:task", row["objectC"])
        self.assertEqual(1, result["sourceRows"])
        self.assertEqual(1, result["output"]["rowCount"])
        self.assertEqual(sha256_file(output), result["output"]["sha256"])
        self.assertEqual("data/relation-inputs.json", result["sourceManifest"]["path"])
        self.assertEqual("data/prepared/triplets.csv", result["output"]["path"])

    def test_relation_pipeline_rejects_legacy_delimiter_label(self):
        row = list(self._relation_row())
        row[0] = "side_table_12"
        with self.assertRaisesRegex(RelationManifestError, "collector object identity"):
            self._publish([row])

    def test_gather_rejects_input_collisions_case_aliases_and_escape(self):
        self._publish()
        cases = (
            (self.manifest, self.data / "receipt.json", "overwrite relation manifest"),
            (self.relation, self.data / "receipt.json", "overwrite relation input"),
            (
                self.data / "PRINCETON" / "SCENE_ONE" / "1.CSV",
                self.data / "receipt.json",
                "overwrite relation input",
            ),
            (
                self.repository / "outside.csv",
                self.data / "receipt.json",
                "must remain inside",
            ),
        )
        for output, receipt, message in cases:
            with self.subTest(output=output):
                with self.assertRaisesRegex(TripletGeometryError, message):
                    self._gather(output, receipt)

        for unsafe_name, message in (
            ("NUL.csv", "reserved Windows device"),
            ("triplets. ", "end in a dot or space"),
            ("triplets\n.csv", "control characters"),
            ("triplets*.csv", "Windows-illegal filename character"),
            ("triplets?.csv", "Windows-illegal filename character"),
            ('triplets".csv', "Windows-illegal filename character"),
            ("triplets<.csv", "Windows-illegal filename character"),
            ("triplets>.csv", "Windows-illegal filename character"),
            ("triplets|.csv", "Windows-illegal filename character"),
        ):
            with self.subTest(unsafe_name=unsafe_name):
                with self.assertRaisesRegex(TripletGeometryError, message):
                    self._gather(
                        self.data / unsafe_name,
                        self.data / "portable-receipt.json",
                    )

        annotation_alias = self.data / "annotation-alias.json"
        try:
            annotation_alias.symlink_to(self.annotation)
        except OSError:
            return
        with self.assertRaisesRegex(
            TripletGeometryError,
            "symbolic link|annotation input|must remain inside",
        ):
            self._gather(annotation_alias, self.data / "receipt.json")

    def test_verified_relation_snapshot_closes_verify_read_race(self):
        self._publish()
        output = self.data / "triplets.csv"
        receipt = self.data / "triplets.sources.json"
        output.write_bytes(b"old-output\n")
        receipt.write_bytes(b"old-receipt\n")

        from modules import prepare_data

        original_loader = prepare_data.load_verified_relation_manifest

        def mutate_after_verify(*args, **kwargs):
            loaded = original_loader(*args, **kwargs)
            self.relation.write_text("changed-after-verification\n", encoding="utf-8")
            return loaded

        with patch(
            "modules.prepare_data.load_verified_relation_manifest",
            side_effect=mutate_after_verify,
        ):
            with self.assertRaisesRegex(TripletGeometryError, "changed after verification"):
                self._gather(output, receipt)
        self.assertEqual(b"old-output\n", output.read_bytes())
        self.assertEqual(b"old-receipt\n", receipt.read_bytes())

    def test_destination_locks_are_order_independent_and_serialize_overlap(self):
        first = self.data / "triplets.csv"
        second = self.data / "triplets.sources.json"
        third = self.data / "triplets.other.json"
        forward = _destination_lock_paths(
            (first, second),
            project_root=self.repository,
        )
        reverse = _destination_lock_paths(
            (second, first),
            project_root=self.repository,
        )
        overlapping = _destination_lock_paths(
            (third, second),
            project_root=self.repository,
        )
        self.assertEqual(forward, reverse)
        self.assertEqual(1, len(set(forward) & set(overlapping)))
        self.assertTrue(all(".codex/triplet-output-locks" in path.as_posix() for path in forward))

    def test_gather_rolls_back_first_file_when_second_promotion_fails(self):
        self._publish()
        output = self.data / "triplets.csv"
        receipt = self.data / "triplets.sources.json"
        output.write_bytes(b"old-output\n")
        receipt.write_bytes(b"old-receipt\n")
        real_replace = os.replace

        def fail_receipt(source, destination):
            if Path(destination) == receipt:
                raise OSError("simulated receipt promotion failure")
            return real_replace(source, destination)

        with patch("modules.prepare_data.os.replace", side_effect=fail_receipt):
            with self.assertRaisesRegex(OSError, "simulated"):
                self._gather(output, receipt)
        self.assertEqual(b"old-output\n", output.read_bytes())
        self.assertEqual(b"old-receipt\n", receipt.read_bytes())

    def _write_gathered(self, rows):
        source = self.data / "prepared" / "triplets.csv"
        source.parent.mkdir(parents=True, exist_ok=True)
        with source.open("w", newline="", encoding="utf-8") as destination:
            writer = csv.writer(destination)
            writer.writerow(TRIPLET_COLUMNS)
            writer.writerows(rows)
        return source

    def test_average_uses_tuple_keys_and_validates_numeric_contract(self):
        source = self._write_gathered([
            ("table,side", "desk_unit", "lamp:task", 1, 2, 3, 60, 70, 80),
            ("table,side", "desk_unit", "lamp:task", 3, 4, 5, 80, 90, 100),
        ])
        output = self.data / "prepared" / "triplets_avg.csv"
        pickle_output = self.data / "prepared" / "triplets_avg.pickle"
        averageTriplets(source, output, pickle_output)
        with output.open(newline="", encoding="utf-8") as averaged:
            row = next(csv.DictReader(averaged))
        self.assertEqual("table,side", row["objectA"])
        self.assertEqual("2.0", row["distanceAB"])
        self.assertEqual("2", row["count"])

        for value, message in ((float("nan"), "finite"), (181, "between 0 and 180")):
            with self.subTest(value=value):
                bad = self._write_gathered([
                    ("a", "b", "c", 1, 2, 3, value, 70, 80),
                ])
                with self.assertRaisesRegex(TripletGeometryError, message):
                    averageTriplets(bad, output, pickle_output)

    def test_average_rejects_aliases_and_rolls_back_partial_promotion(self):
        source = self._write_gathered([
            ("a", "b", "c", 1, 2, 3, 60, 70, 80),
        ])
        output = self.data / "prepared" / "triplets_avg.csv"
        pickle_output = self.data / "prepared" / "triplets_avg.pickle"
        for csv_output, binary_output, message in (
            (source, pickle_output, "overwrite gathered triplet source"),
            (output, output, "separate files"),
            (
                self.data / "PREPARED" / "TRIPLETS.CSV",
                pickle_output,
                "overwrite gathered triplet source",
            ),
            (self.repository / "outside.csv", pickle_output, "must remain inside"),
        ):
            with self.subTest(csv_output=csv_output):
                with self.assertRaisesRegex(TripletGeometryError, message):
                    averageTriplets(source, csv_output, binary_output)

        output.write_bytes(b"old-csv\n")
        pickle_output.write_bytes(b"old-pickle\n")
        real_replace = os.replace

        def fail_pickle(source_candidate, destination):
            if Path(destination) == pickle_output:
                raise OSError("simulated pickle promotion failure")
            return real_replace(source_candidate, destination)

        with patch("modules.prepare_data.os.replace", side_effect=fail_pickle):
            with self.assertRaisesRegex(OSError, "simulated"):
                averageTriplets(source, output, pickle_output)
        self.assertEqual(b"old-csv\n", output.read_bytes())
        self.assertEqual(b"old-pickle\n", pickle_output.read_bytes())

    def test_calculate_coords_uses_tuple_keys_for_comma_classes(self):
        objects = ["anchor,left", "anchor_right", "lamp:task"]
        rows = []
        for object_a in objects:
            for object_b in objects:
                for object_c in objects:
                    if len({object_a, object_b, object_c}) == 3:
                        rows.append((object_a, object_b, object_c, 1, 1, 1, 90, 90, 60))
        triplets = pd.DataFrame.from_records(rows, columns=TRIPLET_COLUMNS)
        colors = pd.DataFrame({"object_name": [], "R": [], "G": [], "B": [], "count": []})

        def read_csv(path, *args, **kwargs):
            if str(path).endswith("object_colors.csv"):
                return colors.copy()
            return triplets.copy()

        sizes = {name: (1.0, "object") for name in objects}
        with (
            patch("modules.prepare_data.pd.read_csv", side_effect=read_csv),
            patch("modules.prepare_data.loadObjectSizes", return_value=sizes),
            patch("modules.prepare_data.retrieve_realistic_object_color", return_value=(1, 2, 3)),
        ):
            coordinates, _text_len = calculateCoords(objects)
        self.assertIn("anchor,left", coordinates)
        self.assertIn("anchor_right", coordinates)
        self.assertIn("lamp:task", coordinates)


if __name__ == "__main__":
    unittest.main()
