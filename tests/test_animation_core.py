import unittest
from PIL import Image
from modules import recency, png2gif

class AnimationCoreTests(unittest.TestCase):
    def test_cyclic_visibility_and_handoff(self):
        self.assertEqual(recency.stable_visibility(6, 0), (1.0,0.0,0.1,0.2,0.35,0.5))
        for focus in range(6):
            self.assertEqual(recency.transition_visibility(6, focus, 0), recency.stable_visibility(6, focus))
            self.assertEqual(recency.transition_visibility(6, focus, 1), recency.stable_visibility(6, (focus+1)%6))
            for progress in (0, .25, .5, .75, 1):
                self.assertLessEqual(sum(a > 0 for a in recency.transition_visibility(6,focus,progress)), 5)
        for invalid in (-.1, 1.1, float('nan'), True):
            with self.assertRaises(ValueError): recency.transition_visibility(6,0,invalid)

    def test_exact_gif_timing_and_seam(self):
        self.assertEqual(png2gif.gif_frame_durations(100, 2), (30,40,30))
        self.assertEqual(sum(png2gif.gif_frame_durations(400, 5)), 400)
        red = Image.new('RGB',(1,1),(255,0,0)); blue = Image.new('RGB',(1,1),(0,0,255))
        frames = png2gif.animation_frames([red,blue], 1)
        try:
            self.assertEqual([f.getpixel((0,0)) for f in frames], [(255,0,0),(127,0,127),(0,0,255),(127,0,127)])
        finally:
            for frame in frames: frame.close()
            red.close(); blue.close()
        for duration, n in ((9,0),(15,0),(20,2),(100,-1)):
            with self.assertRaises(ValueError): png2gif.gif_frame_durations(duration,n)
