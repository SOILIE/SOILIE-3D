from copy import deepcopy
import json
import unittest

from serverless.benchmark.cost import conditional_bound, evidence, lambda_charge, monthly_lambda_budget, token_charge

RATES = {"computeUsdPerGbSecond":.0000166667, "storageUsdPerGbSecond":.000000034,
         "requestUsd":.0000002}
CARD = {"lambda":RATES, "gpt4":{"model":"gpt-4", "source":"https://developers.openai.com/api/docs/models/gpt-4", "inputUsdPerMillion":30, "outputUsdPerMillion":60},
        "gpt5nano":{"model":"gpt-5-nano", "source":"https://developers.openai.com/api/docs/models/gpt-5-nano", "inputUsdPerMillion":.05,"outputUsdPerMillion":.4}}


class CostTests(unittest.TestCase):
    def test_token_units(self):
        self.assertAlmostEqual(.15072, token_charge(4000,512,CARD["gpt4"]))

    def test_failed_and_skipped_invocations_still_cost_money(self):
        usage = {"billedDurationMs":1000,"memoryMb":1024,"ephemeralStorageMb":512}
        result = lambda_charge([dict(usage,newlyCompletedScenes=count) for count in (1,0,0)],RATES)
        self.assertEqual(1,result["completed"])
        self.assertAlmostEqual(3*(RATES["computeUsdPerGbSecond"]+RATES["requestUsd"]),result["usdPerCompletedScene"])

    def test_zero_completion_and_missing_usage_are_not_free(self):
        self.assertFalse(lambda_charge([],RATES)["available"])
        result = lambda_charge([{"billedDurationMs":900000,"memoryMb":10240,"ephemeralStorageMb":10240,"newlyCompletedScenes":0}],RATES)
        self.assertIsNone(result["usdPerCompletedScene"])
        self.assertGreater(result["usagePricedUsd"],.15)

    def test_usage_required_and_invalid_inputs_rejected(self):
        for value in (-1,True,float("nan"),float("inf")):
            with self.assertRaises(ValueError):
                token_charge(value,10,CARD["gpt4"])
        with self.assertRaises(KeyError):
            lambda_charge([{"durationMs":100}],RATES)

    def test_scenarios_never_turn_local_seconds_into_a_cloud_measurement(self):
        result = evidence([{"status":"complete","generationSeconds":20}, {"status":"failed","generationSeconds":10}],CARD)
        self.assertEqual(20,result["measuredLocal"]["secondsPerCompletedScene"])
        self.assertEqual(1,result["measuredLocal"]["failedAttemptsExcludedFromTiming"])
        self.assertFalse(result["lambda"]["moneyAvailable"])
        self.assertFalse(result["layoutgpt"]["moneyAvailable"])
        self.assertTrue(all(row["evidence"] == "conditional-bound" for row in result["conditionalBounds"]))

    def test_publication_rejects_account_payloads_in_price_inputs(self):
        for path, key in (((), "accountId"), ((), "freeTierUsage"),
                          (("lambda",), "functionArn"), (("gpt4",), "billingReceipt")):
            card = deepcopy(CARD)
            target = card
            for part in path:
                target = target[part]
            target[key] = "PRIVATE_SENTINEL"
            with self.assertRaisesRegex(ValueError, "public rate-card fields only") as failure:
                evidence([], card)
            self.assertNotIn("PRIVATE_SENTINEL", str(failure.exception))

    def test_public_scenarios_do_not_export_incidental_account_metadata(self):
        result = evidence([{"status":"complete", "generationSeconds":20,
                            "accountUsage":{"marker":"PRIVATE_SENTINEL"}}], CARD)
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(result))
        tier = result["freeTier"]
        self.assertFalse(tier["accountUsageIncluded"])
        self.assertEqual(400000, tier["allowanceAvailable"]["assumedRemainingFreeGbSeconds"])
        self.assertEqual(1000000, tier["allowanceAvailable"]["assumedRemainingFreeRequests"])
        self.assertEqual(0, tier["allowanceExhausted"]["assumedRemainingFreeGbSeconds"])
        self.assertEqual(0, tier["allowanceExhausted"]["assumedRemainingFreeRequests"])

    def test_bound_is_conditional_and_not_universal_llm_saving(self):
        legacy = conditional_bound(CARD["gpt4"],RATES)
        cheap = conditional_bound(CARD["gpt5nano"],RATES)
        self.assertEqual(.015,legacy["llmOutputChargeFloorUsd"])
        self.assertAlmostEqual(.005009891,legacy["soilieWorkerCostCeilingUsd"])
        self.assertTrue(legacy["cheaperUnderAssumptions"])
        self.assertGreater(legacy["minimumSavingPct"],66)
        self.assertFalse(cheap["cheaperUnderAssumptions"])
        self.assertIsNone(cheap["minimumSavingPct"])
        self.assertEqual(.0001,cheap["llmOutputChargeFloorUsd"])

    def test_bound_includes_failed_invocation_budget_and_no_universal_floor(self):
        row = conditional_bound(CARD["gpt4"],RATES,maximum_worker_seconds=60,maximum_invocations=2)
        self.assertAlmostEqual(2*.005009891,row["soilieWorkerCostCeilingUsd"])
        zero = conditional_bound(CARD["gpt4"],RATES,minimum_output_tokens=0)
        self.assertFalse(zero["cheaperUnderAssumptions"])
        with self.assertRaises(ValueError):
            conditional_bound(CARD["gpt4"],RATES,maximum_worker_seconds=901)

    def test_monthly_free_tier_covers_compute_but_not_extra_storage(self):
        covered = monthly_lambda_budget(600,30,10240,10240,RATES,400000,1000000)
        self.assertEqual(180000,covered["gbSeconds"])
        self.assertEqual(0,covered["computeUsd"])
        self.assertEqual(0,covered["requestUsd"])
        self.assertAlmostEqual(.005814,covered["extraTemporaryStorageUsd"])
        exhausted = monthly_lambda_budget(600,30,10240,10240,RATES)
        self.assertAlmostEqual(3.00594,exhausted["totalWorkerUsd"],places=5)

    def test_shared_account_consumption_and_failures_can_exhaust_allowance(self):
        partly = monthly_lambda_budget(600,30,10240,10240,RATES,100000,500)
        self.assertEqual(80000,partly["billableGbSeconds"])
        self.assertEqual(100,partly["billableRequests"])
        timed_out = monthly_lambda_budget(600,900,10240,10240,RATES,400000,1000000)
        self.assertGreater(timed_out["totalWorkerUsd"],80)


if __name__ == "__main__":
    unittest.main()
