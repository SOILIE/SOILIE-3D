import json
from pathlib import Path
import tempfile
import unittest

from serverless.benchmark.import_infinigen import require_complete_sample
from serverless.benchmark.run_infinigen import (checkpoint_rows, controlled_roles,
                                                profile_command,
                                                revalidate_controlled_checkpoints, validate_controlled_output)
from serverless.benchmark.infinigen_task import controlled_role_counts


class InfinigenProfileTests(unittest.TestCase):
    @staticmethod
    def controlled_records(room_type, specifications):
        room_tag = "bedroom" if room_type == "bedroom" else "living-room"
        records = {
            "room": {"generator": None, "tags": ["Semantics(room)", f"Semantics({room_tag})"],
                     "relations": []},
        }
        for index, (factory, semantics) in enumerate(specifications):
            records[f"object-{index}"] = {
                "generator": factory,
                "tags": ["Semantics(object)", f"FromGenerator({factory}Factory)"]
                        + [f"Semantics({tag})" for tag in semantics],
                "relations": [{"target_name": "room"}],
            }
        return records

    def test_default_profile_does_not_silently_enable_fast_solve(self):
        configs, overrides, description = profile_command("default", "bedroom", "Bedroom")
        self.assertEqual(configs, ["singleroom.gin"])
        self.assertNotIn("fast_solve.gin", configs)
        self.assertFalse(any("solve_small_enabled" in value for value in overrides))
        self.assertIn("Default", description)

    def test_matched_profile_uses_official_fast_config_and_skips_trinkets_without_emptying_primary_domain(self):
        configs, overrides, description = profile_command("matched-furniture-fast", "bedroom", "Bedroom")
        self.assertEqual(configs, ["fast_solve.gin", "singleroom.gin"])
        self.assertIn("compose_indoors.solve_small_enabled=False", overrides)
        self.assertFalse(any("restrict_child_primary" in value for value in overrides))
        self.assertIn("room-scale", description)

    def test_controlled_profile_uses_disclosed_six_object_constraint(self):
        configs, overrides, description = profile_command("controlled-six-fast", "bedroom", "Bedroom")
        self.assertEqual(configs, ["fast_solve.gin", "singleroom.gin"])
        self.assertIn("restrict_solving.consgraph_filters=['benchmark_controlled']", overrides)
        self.assertIn("compose_indoors.solve_small_enabled=False", overrides)
        self.assertFalse(any("restrict_child_primary" in value for value in overrides))
        self.assertIn("six-object", description)

    def test_controlled_roles_require_the_declared_composition(self):
        bedroom = self.controlled_records("bedroom", [
            ("Bed", ["bed"]), ("SingleCabinet", ["storage"]),
            ("SideTable", ["side-table"]), ("SimpleDesk", []),
            ("FloorLamp", []), ("Rug", []),
        ])
        living = self.controlled_records("living_room", [
            ("Sofa", []), ("TVStand", []), ("SingleCabinet", ["storage"]),
            ("SideTable", ["side-table"]), ("CoffeeTable", []), ("Rug", []),
        ])
        self.assertEqual(set(controlled_roles(bedroom, "bedroom")),
                         {"bed", "storage", "side_table", "desk", "floor_lamp", "rug"})
        self.assertEqual(set(controlled_roles(living, "living_room")),
                         {"sofa", "tv_stand", "storage", "side_table", "coffee_table", "rug"})

    def test_variable_bedroom_inventory_is_exact_and_nested(self):
        specifications = [("Bed", ["bed"]), ("SideTable", ["side-table"]), ("FloorLamp", []),
                          ("SingleCabinet", ["storage"]), ("SimpleDesk", []), ("Rug", [])]
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            for count in range(3, 7):
                expected = controlled_role_counts('bedroom', count)
                self.assertEqual(count, sum(expected.values()))
                self.assertEqual(1, expected['bed'])
                self.assertEqual(1, expected['side_table'])
                records = self.controlled_records('bedroom', specifications[:count])
                (work / 'solve_state.json').write_text(json.dumps({'objs': records}))
                self.assertEqual(count, len(validate_controlled_output(work, 'bedroom', count)))
                if count < 6:
                    with self.assertRaises(ValueError): validate_controlled_output(work, 'bedroom')
            for value in (2, 7, 3.0, True):
                with self.assertRaises(ValueError): controlled_role_counts('bedroom', value)
            with self.assertRaises(ValueError): controlled_role_counts('living_room', 3)
        configs, overrides, description = profile_command('controlled-count-fast', 'bedroom', 'Bedroom')
        self.assertEqual(['fast_solve.gin', 'singleroom.gin'], configs)
        self.assertIn("restrict_solving.consgraph_filters=['benchmark_controlled']", overrides)
        self.assertIn('3–6-object', description)

    def test_resume_reclassifies_an_incomplete_controlled_scene(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            work = root/"scene-bedroom-000"
            work.mkdir()
            records = self.controlled_records("bedroom", [
                ("SingleCabinet", ["storage"]), ("SimpleDesk", []),
                ("FloorLamp", []), ("Rug", []),
            ])
            (work/"solve_state.json").write_text(json.dumps({"objs": records}))
            checkpoint = root/"attempt-bedroom-000.json"
            checkpoint.write_text(json.dumps({
                "id": "bad", "roomType": "bedroom", "status": "complete",
            }))
            rows = revalidate_controlled_checkpoints(
                checkpoint_rows(root, "bedroom"), root, "bedroom"
            )
            self.assertEqual(rows[0]["status"], "failed")
            self.assertEqual(rows[0]["errorCode"], "CONTROLLED_COMPOSITION_MISMATCH")
            self.assertEqual(json.loads(checkpoint.read_text())["status"], "failed")

    def test_checkpoint_rows_preserve_attempt_order_and_success_count(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for index, status in ((0, "complete"), (1, "failed"), (2, "complete")):
                (root/f"attempt-bedroom-{index:03d}.json").write_text(json.dumps({
                    "id": f"scene-{index}", "roomType": "bedroom", "status": status,
                }))
            rows = checkpoint_rows(root, "bedroom")
            self.assertEqual(["scene-0", "scene-1", "scene-2"], [row["id"] for row in rows])
            self.assertEqual(2, sum(row["status"] == "complete" for row in rows))

    def test_checkpoint_rows_reject_cross_room_record(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/"attempt-bedroom-000.json").write_text(json.dumps({
                "id": "wrong", "roomType": "living_room", "status": "complete",
            }))
            with self.assertRaises(RuntimeError):
                checkpoint_rows(root, "bedroom")

    def test_checkpoint_rows_reject_gaps(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/"attempt-bedroom-001.json").write_text(json.dumps({
                "id": "late", "roomType": "bedroom", "status": "complete",
            }))
            with self.assertRaises(RuntimeError):
                checkpoint_rows(root, "bedroom")

    def test_publication_export_requires_every_requested_scene(self):
        scenes = ([{"roomType": "bedroom"}] * 2
                  + [{"roomType": "living_room"}] * 2)
        require_complete_sample(scenes, [], {"targetPerRoom": 2})
        with self.assertRaises(RuntimeError):
            require_complete_sample(scenes[:-1], [], {"targetPerRoom": 2})
        with self.assertRaises(RuntimeError):
            require_complete_sample(scenes, [{"id": "bad"}], {"targetPerRoom": 2})

    def test_single_room_supplement_requires_only_its_declared_room(self):
        config = {'targetPerRoom': 1, 'roomTypes': ['living_room']}
        require_complete_sample([{'roomType': 'living_room'}], [], config)
        for scenes in ([], [{'roomType': 'bedroom'}],
                       [{'roomType': 'living_room'}, {'roomType': 'bedroom'}]):
            with self.assertRaises(RuntimeError):
                require_complete_sample(scenes, [], config)


if __name__ == "__main__":
    unittest.main()
