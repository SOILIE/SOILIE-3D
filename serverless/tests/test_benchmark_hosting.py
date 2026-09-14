import unittest

from serverless.benchmark.hosting import billed_seconds, container_budget


class HostingTests(unittest.TestCase):
    def test_minimum_is_per_job_not_per_batch(self):
        google = container_budget("cloudrun", 600, 30, 4, 16)
        self.assertEqual(60, google["billedSecondsEach"])
        self.assertEqual(144000, google["cpuSeconds"])
        self.assertAlmostEqual(3.744, google["totalWorkerUsd"])
        self.assertEqual(.3, billed_seconds(.3, 0, .1))
        self.assertEqual(60.1, billed_seconds(60.001, 60, .1))
        self.assertEqual(61, billed_seconds(60.001, 60, 1))

    def test_google_jobs_use_jobs_allowances_not_service_allowances(self):
        row = container_budget("cloudrun", 600, 30, 4, 16, allowance_available=True)
        self.assertEqual(0, row["cpuUsd"])
        self.assertAlmostEqual(.252, row["memoryUsd"])
        self.assertAlmostEqual(.252, row["totalWorkerUsd"])

    def test_cloudflare_plan_and_active_cpu(self):
        available = container_budget("cloudflare", 600, 30, 4, 12, 20, True)
        exhausted = container_budget("cloudflare", 600, 30, 4, 12, 20)
        self.assertAlmostEqual(6.305, available["totalWorkerUsd"])
        self.assertAlmostEqual(7.0052, exhausted["totalWorkerUsd"])
        idle = container_budget("cloudflare", 600, 30, 4, 12, 20, cpu_utilization=0)
        self.assertEqual(0, idle["cpuUsd"])
        self.assertEqual(exhausted["memoryUsd"], idle["memoryUsd"])
        self.assertEqual(exhausted["diskUsd"], idle["diskUsd"])
        self.assertEqual(5, container_budget("cloudflare", 0, 0, 4, 12, 20)["totalWorkerUsd"])

    def test_fargate_storage_is_included_not_a_monthly_free_tier(self):
        row = container_budget("fargate", 600, 30, 4, 10, 20)
        self.assertEqual(0, row["diskUsd"])
        self.assertAlmostEqual(2.063736, row["totalWorkerUsd"])
        covered = container_budget("fargate", 600, 30, 4, 10, 20, True)
        self.assertEqual(row["totalWorkerUsd"], covered["totalWorkerUsd"])
        extra = container_budget("fargate", 600, 30, 4, 10, 30)
        self.assertGreater(extra["diskUsd"], 0)

    def test_allocated_cpu_is_not_discounted_for_io(self):
        for provider in ("fargate", "cloudrun"):
            busy = container_budget(provider, 1, 30, 4, 10)
            idle = container_budget(provider, 1, 30, 4, 10, cpu_utilization=0)
            self.assertEqual(busy["totalWorkerUsd"], idle["totalWorkerUsd"])

    def test_invalid_inputs_fail(self):
        for bad in (-1, float("inf"), float("nan"), True):
            with self.assertRaises(ValueError):
                container_budget("cloudrun", 1, bad, 4, 16)
        with self.assertRaises(ValueError):
            container_budget("cloudflare", 1, 30, 4, 12, cpu_utilization=1.1)
        with self.assertRaises(ValueError):
            container_budget("cloudflare", True, 30, 4, 12)
        with self.assertRaises(ValueError):
            billed_seconds(30, 60, 0)
