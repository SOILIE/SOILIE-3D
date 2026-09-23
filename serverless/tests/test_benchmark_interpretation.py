import unittest

from serverless.benchmark.interpretation import discussion


class InterpretationTests(unittest.TestCase):
    def test_controlled_infinigen_is_named_without_progress_narration(self):
        comparison = {
            "comparisons": [{
                "baseline": "infinigen_controlled",
                "counts": {"soilie": 20, "infinigen_controlled": 20},
                "sharedStrata": ["bedroom|6|0.25"],
                "metrics": {
                    "meanWorstSolidOverlapPct": {"available": False},
                    "meanWorstEnvelopeOverlapPct": {
                        "available": True,
                        "means": {"soilie": 0.0, "infinigen_controlled": 4.0},
                    },
                    "meanOutsideFootprintPct": {"available": False},
                },
            }],
            "runs": [{"attempted": 10_000, "completed": 10_000}],
            "timing": {
                "soilie": {
                    "completedPerMinute": 3.0,
                    "completedLatencySeconds": {"median": 18.0, "p95": 29.0},
                }
            },
        }

        result = discussion(comparison, None)

        self.assertIn("Infinigen Indoors (controlled six-object task)", result)
        self.assertIn("10000 completed layouts from 10000 recorded generation attempts", result)
        self.assertIn("matched workload groups", result)
        self.assertNotIn("interim", result.lower())
        self.assertNotIn("not yet complete", result.lower())


if __name__ == "__main__":
    unittest.main()
