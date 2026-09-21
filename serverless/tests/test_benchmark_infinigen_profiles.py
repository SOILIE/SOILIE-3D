import unittest

from serverless.benchmark.run_infinigen import profile_command


class InfinigenProfileTests(unittest.TestCase):
    def test_default_profile_does_not_silently_enable_fast_solve(self):
        configs, overrides, description = profile_command("default", "bedroom", "Bedroom")
        self.assertEqual(configs, ["singleroom.gin"])
        self.assertNotIn("fast_solve.gin", configs)
        self.assertFalse(any("solve_small_enabled" in value for value in overrides))
        self.assertIn("Default", description)

    def test_matched_profile_uses_official_fast_config_and_skips_trinkets(self):
        configs, overrides, description = profile_command("matched-furniture-fast", "bedroom", "Bedroom")
        self.assertEqual(configs, ["fast_solve.gin", "singleroom.gin"])
        self.assertIn("compose_indoors.solve_small_enabled=False", overrides)
        self.assertTrue(any("restrict_child_primary" in value and "Bed" in value for value in overrides))
        self.assertIn("room-scale", description)


if __name__ == "__main__":
    unittest.main()
