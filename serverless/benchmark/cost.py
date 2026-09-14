"""Cost accounting keeps observed usage, tariffs and hypothetical inputs separate.

Desktop seconds are not Lambda billed seconds. Parsed LayoutGPT boxes are not
the complete few-shot prompt or token-usage receipt. Neither is silently priced
as if it were the missing billing evidence.
"""
from __future__ import annotations

import argparse
from datetime import datetime, UTC
import hashlib
import json
import math
from pathlib import Path
from urllib.request import urlopen

from serverless.benchmark.hosting import evidence as hosting_evidence

AWS_URL = "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AWSLambda/current/ca-central-1/index.json"
GPT4_URL = "https://developers.openai.com/api/docs/models/gpt-4"
NANO_URL = "https://developers.openai.com/api/docs/models/gpt-5-nano"
AWS_PRICING_URL = "https://aws.amazon.com/lambda/pricing/"


def nonnegative(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
        raise ValueError("Usage and price inputs must be finite non-negative numbers")
    return value


def token_charge(input_tokens, output_tokens, rates):
    return (nonnegative(input_tokens)*rates["inputUsdPerMillion"] +
            nonnegative(output_tokens)*rates["outputUsdPerMillion"])/1_000_000


def lambda_charge(receipts, rates):
    """Every invocation, including failed retries, contributes its billed usage.

    Callers must supply a closed, complete invocation ledger. A success reports
    how many newly completed scenes it produced; a skipped retry contributes
    cost but zero additional scenes. No free tier or discounts are assumed.
    """
    if not receipts:
        return {"available": False, "reason": "No complete billed-invocation ledger was supplied."}
    total, completed = 0.0, 0
    for row in receipts:
        seconds = nonnegative(row["billedDurationMs"])/1000
        memory = nonnegative(row["memoryMb"])/1024
        storage = max(0, nonnegative(row["ephemeralStorageMb"])/1024-.5)
        count = row["newlyCompletedScenes"]
        if type(count) is not int or count < 0:
            raise ValueError("Newly completed scenes must be a non-negative integer")
        total += seconds*(memory*rates["computeUsdPerGbSecond"] + storage*rates["storageUsdPerGbSecond"])+rates["requestUsd"]
        completed += count
    return {"available": True, "invocations": len(receipts), "completed": completed,
            "usagePricedUsd": total, "usdPerCompletedScene": total/completed if completed else None,
            "basis": "Billed invocation usage priced at public on-demand rates, before credits, discounts or taxes"}


def conditional_bound(llm, aws, minimum_output_tokens=250, maximum_worker_seconds=30,
                      maximum_invocations=1, memory_mb=10240, storage_mb=10240):
    """An LLM-favouring lower bound against an explicitly assumed worker ceiling.

    The token minimum is an assumption, not inferred from parsed layouts. The
    worker ceiling includes all billable attempt time per newly completed scene,
    not just a successful attempt. It is NOT measured Lambda performance.
    """
    if type(maximum_invocations) is not int or maximum_invocations < 1:
        raise ValueError("At least one billable worker invocation must be budgeted")
    lower = token_charge(0, minimum_output_tokens, llm)
    seconds = nonnegative(maximum_worker_seconds)
    if seconds > 900*maximum_invocations:
        raise ValueError("The assumed worker duration exceeds the invocation budget")
    memory, storage = nonnegative(memory_mb)/1024, max(0,nonnegative(storage_mb)/1024-.5)
    per_second = memory*aws["computeUsdPerGbSecond"]+storage*aws["storageUsdPerGbSecond"]
    upper = seconds*per_second + maximum_invocations*aws["requestUsd"]
    return {"evidence":"conditional-bound", "model":llm["model"], "minimumOutputTokens":minimum_output_tokens,
            "inputChargeAssumedUsd":0, "llmOutputChargeFloorUsd":lower,
            "maximumWorkerSecondsPerCompletedScene":seconds, "maximumInvocationsPerCompletedScene":maximum_invocations,
            "lambdaMemoryMb":memory_mb,"lambdaEphemeralStorageMb":storage_mb,
            "soilieWorkerCostCeilingUsd":upper, "cheaperUnderAssumptions":upper < lower,
            "minimumSavingPct":(1-upper/lower)*100 if upper < lower else None,
            "breakEvenWorkerSeconds":max(0,lower-maximum_invocations*aws["requestUsd"])/per_second,
            "source":llm["source"],
            "scope":"Marginal paid layout-generation charges only, excluding free allowances, discounts, rendering, serving and storage on both sides",
            "assumptions":["The LLM emits at least the stated number of billable output tokens for each completed scene",
                           "LLM input is treated as free and no additional reasoning, retries or tool charges are counted",
                           "All SOILIE invocation time, including cold starts and failed attempts, fits within the stated per-completion time and request budgets",
                           "The SOILIE runtime ceiling and LLM output minimum are hypothetical, not benchmark measurements",
                           "The compared methods' scene quality must be assessed separately"]}


def monthly_lambda_budget(invocations, seconds_each, memory_mb, storage_mb, aws,
                          remaining_free_gb_seconds=0, remaining_free_requests=0):
    """Operator's marginal monthly charge under explicitly supplied allowances.

    The account-wide remaining allowance must not be guessed from this website's
    own usage. Extra ephemeral storage is not covered by the duration allowance.
    This is a hypothetical workload calculation, not an AWS invoice reader.
    """
    if type(invocations) is not int or invocations < 0:
        raise ValueError("Invocation count must be a non-negative integer")
    seconds, memory = nonnegative(seconds_each), nonnegative(memory_mb)/1024
    storage = max(0,nonnegative(storage_mb)/1024-.5)
    free_compute, free_requests = nonnegative(remaining_free_gb_seconds), nonnegative(remaining_free_requests)
    gb_seconds = invocations*seconds*memory
    charged_compute = max(0,gb_seconds-free_compute)
    charged_requests = max(0,invocations-free_requests)
    compute_cost = charged_compute*aws["computeUsdPerGbSecond"]
    request_cost = charged_requests*aws["requestUsd"]
    storage_cost = invocations*seconds*storage*aws["storageUsdPerGbSecond"]
    return {"invocations":invocations,"billedSecondsEach":seconds,"allocatedMemoryMb":memory_mb,
            "ephemeralStorageMb":storage_mb,"gbSeconds":gb_seconds,
            "assumedRemainingFreeGbSeconds":free_compute,"assumedRemainingFreeRequests":free_requests,
            "billableGbSeconds":charged_compute,"billableRequests":charged_requests,
            "computeUsd":compute_cost,"requestUsd":request_cost,"extraTemporaryStorageUsd":storage_cost,
            "totalWorkerUsd":compute_cost+request_cost+storage_cost}


def validate_public_rate_card(card):
    """Fail closed if an account/usage payload is mixed into public prices.

    Publication accepts only the documented anonymous price snapshot. Account
    configuration, billing receipts and remaining free-tier usage are not inputs
    to the website scenarios, even when available for private operations work.
    Do not include rejected keys or values in the error message.
    """
    def fields(value, allowed):
        if not isinstance(value, dict) or set(value) - allowed:
            raise ValueError("Public cost evidence accepts public rate-card fields only")

    fields(card, {"checkedAt", "currency", "lambda", "gpt4", "gpt5nano"})
    aws = card["lambda"]
    fields(aws, {"computeUsdPerGbSecond", "storageUsdPerGbSecond", "requestUsd",
                 "region", "architecture", "source", "offerPublishedAt", "sha256", "skus"})
    for key in ("computeUsdPerGbSecond", "storageUsdPerGbSecond", "requestUsd"):
        nonnegative(aws[key])
    if aws.get("source", AWS_URL) != AWS_URL:
        raise ValueError("Expected the public regional AWS price source")
    if "skus" in aws:
        fields(aws["skus"], {"computeUsdPerGbSecond", "storageUsdPerGbSecond", "requestUsd"})
        for identifiers in aws["skus"].values():
            fields(identifiers, {"sku", "rateCode"})
    for key, source in (("gpt4", GPT4_URL), ("gpt5nano", NANO_URL)):
        tariff = card[key]
        fields(tariff, {"model", "inputUsdPerMillion", "outputUsdPerMillion",
                        "source", "verifiedOn", "verification"})
        if tariff["source"] != source:
            raise ValueError("Expected the public model price source")
        nonnegative(tariff["inputUsdPerMillion"])
        nonnegative(tariff["outputUsdPerMillion"])


def evidence(attempts, rate_card):
    validate_public_rate_card(rate_card)
    completed_rows = [row for row in attempts if row["status"] == "complete"]
    complete = len(completed_rows)
    seconds = sum(row["generationSeconds"] for row in completed_rows)
    aws = rate_card["lambda"]
    available = monthly_lambda_budget(600,30,10240,10240,aws,400000,1000000)
    exhausted = monthly_lambda_budget(600,30,10240,10240,aws)
    return {"currency":"USD", "rateCard":rate_card,
            "hosting":hosting_evidence(available,exhausted,AWS_PRICING_URL),
            "question":"Can lower compute cost compensate for slower layout generation?",
            "finding":"SOILIE can have lower layout-generation charges under explicit runtime and token-budget assumptions. The conditional bounds below show when that follows from the prices; they do not establish measured savings or an advantage over every LLM.",
            "scope":"Layout generation only; image rendering, animation, training, data preparation, storage, transfer and API orchestration are separate costs.",
            "measuredLocal":{"completed":complete, "successfulGenerationSeconds":seconds,
                              "secondsPerCompletedScene":seconds/complete if complete else None,
                              "failedAttemptsExcludedFromTiming":sum(row["status"] != "complete" for row in attempts),
                             "moneyAvailable":False, "reason":"Local electricity and hardware costs were not metered. This is not zero-cost compute."},
            "layoutgpt":{"moneyAvailable":False, "reason":"Official parsed layout files omit full few-shot messages, usage receipts, rejected requests and billable retries."},
            "lambda":{"moneyAvailable":False, "reason":"No closed, version-matched Lambda billing ledger is part of this local placement benchmark."},
            "conditionalBounds":[conditional_bound(rate_card[key],aws) for key in ("gpt4","gpt5nano")],
            "freeTier":{"evidence":"hypothetical-monthly-budget", "source":AWS_PRICING_URL,
                        "accountUsageIncluded":False,
                        "monthlyDurationAllowanceGbSeconds":400000,"monthlyRequestAllowance":1000000,
                        "assumptions":"Illustrative workload: 600 completed layouts, one 30-second invocation each, 10 GB memory and 10 GB temporary storage; no retries. These are generic scenario inputs, not observed deployment settings or measured renderer performance.",
                        "allowanceAvailable":available,
                        "allowanceExhausted":exhausted,
                        "interpretation":"With the full account-wide allowance still available, this hypothetical workload has no compute or request charge, but extra temporary storage still has a small charge. This is a hosting subsidy, not proof of universally cheaper model computation.",
                        "exclusions":"S3 results, CloudWatch, API Gateway, queues, database operations, traffic, taxes and any extra invocation time are not included. LLM free quotas or credits can likewise remove API charges."},
            "exclusions":["Per-scene conditional bounds exclude credits; the separate monthly scenario considers the shared free tier", "Taxes and negotiated discounts are excluded", "No inference calls were purchased for this cost analysis", "The legacy GPT-4 rate is not a price claim about all current LLMs"]}


def main():
    parser = argparse.ArgumentParser(description="Snapshot publicly available rate evidence; does not invoke any model or AWS workload")
    parser.add_argument("--output",type=Path,required=True)
    args = parser.parse_args()
    raw = urlopen(AWS_URL,timeout=60).read()
    offer = json.loads(raw)
    usage_names = {"CAN1-Lambda-GB-Second":"computeUsdPerGbSecond", "CAN1-Lambda-Storage-GB-Second":"storageUsdPerGbSecond", "CAN1-Request":"requestUsd"}
    rates, skus = {}, {}
    for sku,product in offer["products"].items():
        name = usage_names.get(product["attributes"].get("usagetype"))
        if name:
            tiers = [dimension for term in offer["terms"]["OnDemand"][sku].values()
                     for dimension in term["priceDimensions"].values() if dimension["beginRange"] == "0"]
            if len(tiers) != 1 or name in rates:
                raise ValueError("Ambiguous public on-demand rate")
            rates[name] = float(tiers[0]["pricePerUnit"]["USD"])
            skus[name] = {"sku":sku, "rateCode":tiers[0]["rateCode"]}
    if set(rates) != set(usage_names.values()):
        raise ValueError("Incomplete regional AWS rate card")
    document = {"checkedAt":datetime.now(UTC).isoformat(), "currency":"USD",
                "lambda":dict(rates,region="ca-central-1",architecture="x86_64",source=AWS_URL,
                              offerPublishedAt=offer["publicationDate"],sha256=hashlib.sha256(raw).hexdigest(),skus=skus),
                "gpt4":{"model":"gpt-4", "inputUsdPerMillion":30.0, "outputUsdPerMillion":60.0,
                        "source":GPT4_URL, "verifiedOn":"2026-09-13", "verification":"Official model documentation; standard text-token rates"},
                "gpt5nano":{"model":"gpt-5-nano", "inputUsdPerMillion":.05, "outputUsdPerMillion":.4,
                            "source":NANO_URL,"verifiedOn":"2026-09-14", "verification":"Official model documentation; standard text-token rates; price sensitivity only, not a room-generation evaluation"}}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(document,indent=2),encoding="utf-8")
    print(json.dumps({"rateCard":str(args.output), "regionalRates":rates}))


if __name__ == "__main__":
    main()
