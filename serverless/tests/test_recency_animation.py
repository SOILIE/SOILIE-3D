from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageSequence

from modules.png2gif import animate_output, animation_frames, gif_frame_durations
from modules.recency import (
    light_belongs_to_object,
    light_energy,
    luminous_object_type,
    stable_remesh_depths,
    stable_visibility,
    transition_remesh_depths,
    transition_visibility,
)


class RecencyScheduleTests(unittest.TestCase):
    def test_focal_states_keep_only_current_and_five_predecessors(self) -> None:
        self.assertEqual(
            (1.0, 0.0, 0.0, 0.05, 0.10, 0.20, 0.35, 0.50),
            stable_visibility(8, 0),
        )
        self.assertEqual(
            (0.05, 0.10, 0.20, 0.35, 0.50, 1.0, 0.0, 0.0),
            stable_visibility(8, 5),
        )
        self.assertEqual(
            (0.0, 0.05, 0.10, 0.20, 0.35, 0.50, 1.0, 0.0),
            stable_visibility(8, 6),
        )
        self.assertEqual(
            (0.0, 0.0, 0.05, 0.10, 0.20, 0.35, 0.50, 1.0),
            stable_visibility(8, 7),
        )

        for focus_index in range(8):
            state = stable_visibility(8, focus_index)
            self.assertEqual(6, sum(alpha > 0 for alpha in state))
            self.assertEqual(1.0, state[focus_index])

        # Small scenes contain the current object and every distinct predecessor
        # exactly once; cyclic indexing never duplicates an object.
        self.assertEqual((1.0, 0.35, 0.50), stable_visibility(3, 0))

    def test_transition_endpoints_and_loop_are_exact(self) -> None:
        for focus_index in range(8):
            self.assertEqual(
                stable_visibility(8, focus_index),
                transition_visibility(8, focus_index, 0.0),
            )
            self.assertEqual(
                stable_visibility(8, (focus_index + 1) % 8),
                transition_visibility(8, focus_index, 1.0),
            )

        expected = (0.50, 0.0, 0.0, 0.0625, 0.125, 0.2375, 0.3875, 0.625)
        for expected_alpha, observed_alpha in zip(expected, transition_visibility(8, 7, 0.75)):
            self.assertAlmostEqual(expected_alpha, observed_alpha)

    def test_transition_never_exposes_a_seventh_object(self) -> None:
        for object_count in range(1, 13):
            expected_maximum = min(object_count, 6)
            for focus_index in range(object_count):
                for step in range(101):
                    progress = step / 100
                    state = transition_visibility(object_count, focus_index, progress)
                    self.assertLessEqual(
                        sum(alpha > 0 for alpha in state),
                        expected_maximum,
                        (object_count, focus_index, progress, state),
                    )

    def test_opacities_and_detail_levels_are_bounded_and_deterministic(self) -> None:
        self.assertEqual(
            (None, None, 3, 4, 5, 6, 7, None),
            stable_remesh_depths(8, 7),
        )
        self.assertEqual(
            (None, None, None, 3, 4, 5, 6, 7),
            stable_remesh_depths(8, 0),
        )
        for focus_index in range(8):
            for progress in (0.0, 0.25, 0.5, 0.75, 1.0):
                state = transition_visibility(8, focus_index, progress)
                self.assertTrue(all(math.isfinite(alpha) and 0.0 <= alpha <= 1.0 for alpha in state))
                depths = transition_remesh_depths(8, focus_index, progress)
                self.assertTrue(all(
                    depth is None or 3 <= depth <= 7
                    for depth in depths
                ))
                self.assertTrue(all(
                    depth is None
                    for depth, alpha in zip(depths, state)
                    if alpha == 0.0
                ))

    def test_light_energy_follows_visibility_and_reaches_zero(self) -> None:
        self.assertEqual(0.0, light_energy(1250, 0.0))
        self.assertEqual(625.0, light_energy(1250, 0.5))
        self.assertEqual(1250.0, light_energy(1250, 1.0))
        for arguments in ((-1, 0.5), (1, -0.1), (1, 1.1), (math.inf, 0.5)):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                light_energy(*arguments)

    def test_lamps_and_windows_own_only_their_exact_lights(self) -> None:
        self.assertEqual('lamp', luminous_object_type('Lamp_0001'))
        self.assertEqual('window', luminous_object_type('window_0001'))
        self.assertEqual('window', luminous_object_type('opaque_window_0001'))
        self.assertIsNone(luminous_object_type('wind_chime_0001'))
        self.assertIsNone(luminous_object_type('chair_0001'))
        self.assertTrue(light_belongs_to_object('window_0001', 'window_0001.Light'))
        self.assertTrue(light_belongs_to_object('window_0001', 'window_0001.Light.001'))
        self.assertFalse(light_belongs_to_object('window_0001', 'window_00010.Light'))
        self.assertFalse(light_belongs_to_object('window_0001', 'window_0001_shade.Light'))

    def test_invalid_schedule_inputs_are_actionable(self) -> None:
        invalid_arguments = (
            (0, 0, 0.0),
            (3, -1, 0.0),
            (3, 3, 0.0),
            (3, 0, float('nan')),
            (3, 0, 1.01),
        )
        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                transition_visibility(*arguments)


class GifAnimationTests(unittest.TestCase):
    @staticmethod
    def _write_png(path: Path, color: tuple[int, int, int]) -> None:
        with Image.new('RGB', (2, 2), color) as frame:
            frame.save(path)

    def test_animation_frames_close_the_loop(self) -> None:
        images = [
            Image.new('RGB', (1, 1), color)
            for color in ((255, 0, 0), (0, 255, 0), (0, 0, 255))
        ]
        frames = animation_frames(images, num_interpolated_frames=1)
        try:
            self.assertEqual(6, len(frames))
            self.assertEqual((255, 0, 0), frames[0].getpixel((0, 0)))
            self.assertEqual((127, 127, 0), frames[1].getpixel((0, 0)))
            self.assertEqual((0, 255, 0), frames[2].getpixel((0, 0)))
            self.assertEqual((0, 127, 127), frames[3].getpixel((0, 0)))
            self.assertEqual((0, 0, 255), frames[4].getpixel((0, 0)))
            self.assertEqual((127, 0, 127), frames[5].getpixel((0, 0)))
        finally:
            for frame in frames:
                frame.close()
            for source in images:
                source.close()

    def test_animation_frames_reject_mismatched_sources_without_owning_them(self) -> None:
        images = [Image.new('RGB', (1, 1)), Image.new('RGB', (2, 1))]
        try:
            with self.assertRaisesRegex(ValueError, 'same mode and dimensions'):
                animation_frames(images)
            # The caller retains ownership of supplied images on failure.
            self.assertEqual((0, 0, 0), images[0].getpixel((0, 0)))
        finally:
            for source in images:
                source.close()

    def test_saved_gif_has_equal_cadence_through_the_loop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = [
                root / 'scene_0001.png',
                root / 'scene_0002.png',
                root / 'scene_0003.png',
            ]
            for path, color in zip(sources, ((255, 0, 0), (0, 255, 0), (0, 0, 255))):
                self._write_png(path, color)
            self._write_png(root / '01_birds_eye_view.png', (255, 0, 255))

            animate_output('scene.png', str(root), duration_per_frame=200, num_interpolated_frames=1)

            with Image.open(root / 'scene.gif') as animation:
                frames = [frame.convert('RGB').copy() for frame in ImageSequence.Iterator(animation)]
                durations = [
                    int(frame.info.get('duration', animation.info.get('duration', 0)))
                    for frame in ImageSequence.Iterator(animation)
                ]
                self.assertEqual(0, animation.info['loop'])
            try:
                self.assertEqual(6, len(frames))
                self.assertEqual([100] * 6, durations)
                self.assertEqual((127, 0, 127), frames[-1].getpixel((0, 0)))
            finally:
                for frame in frames:
                    frame.close()

            # All source handles have been released before animate_output returns.
            for source in sources:
                source.unlink()

    def test_default_timing_preserves_every_source_interval_exactly(self) -> None:
        expected_interval = gif_frame_durations(400, 5)
        self.assertEqual(6, len(expected_interval))
        self.assertEqual(400, sum(expected_interval))
        self.assertLessEqual(max(expected_interval) - min(expected_interval), 10)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, color in enumerate(((255, 0, 0), (0, 255, 0), (0, 0, 255)), 1):
                self._write_png(root / f'scene_{index:04}.png', color)
            animate_output('scene.png', str(root))

            with Image.open(root / 'scene.gif') as animation:
                durations = [
                    int(frame.info.get('duration', animation.info.get('duration', 0)))
                    for frame in ImageSequence.Iterator(animation)
                ]

            self.assertEqual(18, len(durations))
            self.assertEqual(1200, sum(durations))
            for start in range(0, len(durations), 6):
                self.assertEqual(400, sum(durations[start:start + 6]))

    def test_gif_timing_rejects_unrepresentable_intervals(self) -> None:
        for arguments in ((405, 5), (50, 5), (0, 0), (400, -1)):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                gif_frame_durations(*arguments)

    def test_zero_and_one_source_behaviors_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, 'No animation PNG frames'):
                animate_output('empty.png', str(root))

            source = root / 'only_0001.png'
            self._write_png(source, (12, 34, 56))
            animate_output('only.png', str(root))
            with Image.open(root / 'only.gif') as animation:
                self.assertEqual(1, sum(1 for _frame in ImageSequence.Iterator(animation)))
                self.assertEqual((12, 34, 56), animation.convert('RGB').getpixel((0, 0)))
                self.assertEqual(400, animation.info['duration'])
            source.unlink()


if __name__ == '__main__':
    unittest.main()
