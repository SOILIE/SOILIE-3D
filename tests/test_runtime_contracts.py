"""Count/seed/adapter boundaries shared by local and Lambda generation."""
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
import random
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from modules.prepare_data import TripletCoverageError, TripletGeometryError
from modules.working_combo_schema import WorkingCombinationError
from serverless.common import model_runtime as runtime
from serverless.common.object_counts import validate_selection

ROOT = Path(__file__).resolve().parents[1]


class CountingContractTests(unittest.TestCase):
    def test_every_quota_counts_both_window_types_as_extras(self):
        for n in range(3, 9):
            for extra in ([], ["window"], ["opaque_window"]):
                request = {"mode": "room_type", "objectCount": n, "allowDuplicates": True}
                objects = ["chair"] * n + extra
                before = objects.copy()
                with self.subTest(n=n, extra=extra):
                    result = validate_selection(request, objects)
                    self.assertEqual(n, result["countedObjectCount"])
                    self.assertEqual(len(extra), result["selectedWindowCount"])
                    self.assertEqual(n + len(extra), result["selectedTotalCount"])
                    self.assertEqual(before, objects)

    def test_explicit_windows_preserve_literal_order_and_duplicates(self):
        for objects in (["window"] * 3, ["opaque_window"] * 8,
                        ["curtain", "window", "curtain", "chair"]):
            request = {"mode": "objects", "objects": objects}
            result = validate_selection(request, objects)
            self.assertEqual(len(objects), result["requestedObjectCount"])
            self.assertEqual("explicit-list-length-v1", result["objectCountDefinition"])
            changed = [*objects[:-1], "table"]
            with self.assertRaises(ValueError):
                validate_selection(request, changed)

    def test_invalid_selection_never_shrinks_or_repairs_inventory(self):
        request = {"mode": "random", "objectCount": 3, "allowDuplicates": True}
        for selection in (["chair"] * 2, ["chair"] * 4,
                          ["chair"] * 3 + ["window", "opaque_window"],
                          ["Chair", "table", "lamp"], ["chair.1", "table", "lamp"]):
            before = selection.copy()
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                validate_selection(request, selection)
            self.assertEqual(before, selection)
        for value in (True, 3.0, "3", 2, 9, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_selection({**request, "objectCount": value}, ["chair"] * 3)
        with self.assertRaises(ValueError):
            validate_selection({**request, "allowDuplicates": False}, ["chair"] * 3)


class RuntimeAdapterTests(unittest.TestCase):
    def test_same_inventory_policy_and_scene_seed_are_explicit(self):
        selector = Mock(return_value=["chair", "table", "lamp", "window"])
        combos = SimpleNamespace(select_exact=selector, WorkingCombinationError=WorkingCombinationError)
        request = {"mode": "room_type", "roomType": "living_room", "objectCount": 3,
                   "seed": 41, "allowDuplicates": False, "sameObjectsAcrossScenes": True}
        with patch.object(runtime, "_load_original_modules", return_value=(None, combos, None)), \
             patch.object(runtime, "_model_working_directory", return_value=nullcontext()), \
             patch.object(runtime, "seed_model") as seed:
            self.assertEqual(["chair", "table", "lamp", "window"],
                             runtime.select_model_objects(Path("runtime"), request, 7))
            seed.assert_called_once_with(41)
            selector.assert_called_once_with(
                3, filepath=str(Path("runtime/data/working-combos-livingroom.csv")),
                triplet_filepath=str(Path("runtime/data/triplets.csv")), allow_duplicates=False)
            seed.reset_mock()
            runtime.select_model_objects(Path("runtime"), {**request, "sameObjectsAcrossScenes": False}, 7)
            seed.assert_called_once_with(41 + 7 * runtime.SCENE_SEED_STEP)

    def test_explicit_selection_is_a_copy_and_never_calls_random_selector(self):
        request = {"mode": "objects", "objects": ["window"] * 3}
        with patch.object(runtime, "_load_original_modules") as load:
            result = runtime.select_model_objects(ROOT, request, 9)
            load.assert_not_called()
        result.append("chair")
        self.assertEqual(["window"] * 3, request["objects"])

    def test_unfulfillable_request_fails_once_without_count_or_catalog_fallback(self):
        selector = Mock(side_effect=WorkingCombinationError("no supporting triplets"))
        combos = SimpleNamespace(select_exact=selector, WorkingCombinationError=WorkingCombinationError)
        request = {"mode": "random", "objectCount": 8, "seed": 17, "allowDuplicates": True}
        before = deepcopy(request)
        with patch.object(runtime, "_load_original_modules", return_value=(None, combos, None)), \
             patch.object(runtime, "_model_working_directory", return_value=nullcontext()), \
             patch.object(runtime, "seed_model"), self.assertRaises(runtime.ModelGenerationError):
            runtime.select_model_objects(ROOT, request, 0)
        selector.assert_called_once()
        self.assertEqual(before, request)

    def test_coordinate_errors_are_single_pass_and_keep_original_cause(self):
        for error in (TripletCoverageError("missing"), TripletGeometryError("impossible")):
            calculate = Mock(side_effect=error)
            prep = SimpleNamespace(calculateCoords=calculate, TripletCoverageError=TripletCoverageError,
                                   TripletGeometryError=TripletGeometryError)
            with patch.object(runtime, "_load_original_modules", return_value=(prep, None, None)), \
                 patch.object(runtime, "_model_working_directory", return_value=nullcontext()), \
                 patch.object(runtime, "seed_model"), self.assertRaises(runtime.ModelGenerationError) as caught:
                runtime.generate_model_inputs(ROOT, ["chair", "table", "lamp"], 8)
            self.assertIs(error, caught.exception.__cause__)
            calculate.assert_called_once()

    def test_both_existing_random_sources_follow_the_requested_seed(self):
        python_state, numpy_state = random.getstate(), np.random.get_state()
        self.addCleanup(random.setstate, python_state)
        self.addCleanup(np.random.set_state, numpy_state)
        for seed in (0, 91, 2**32 + 7):
            runtime.seed_model(seed)
            self.assertEqual(random.Random(seed).random(), random.random())
            self.assertEqual(np.random.RandomState(seed % 2**32).random_sample(), np.random.random())

    def test_working_directory_is_restored_when_core_raises(self):
        base = ROOT / ".codex/tests"
        base.mkdir(parents=True, exist_ok=True)
        original = Path.cwd()
        with tempfile.TemporaryDirectory(dir=base) as folder:
            with self.assertRaisesRegex(RuntimeError, "fixture"):
                with runtime._model_working_directory(Path(folder)):
                    self.assertEqual(Path(folder), Path.cwd())
                    raise RuntimeError("fixture")
        self.assertEqual(original, Path.cwd())
