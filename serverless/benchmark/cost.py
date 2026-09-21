"""Cost accounting for one completed layout from each compared method.

Desktop seconds are not Lambda billed seconds and the released LayoutGPT file
does not contain API receipts. The public comparison therefore combines a
reconstructed LayoutGPT token profile with an explicitly labelled SOILIE cloud
runtime-transfer scenario. Neither estimate is presented as a paid invoice.
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


def percentile(values, fraction):
    values = sorted(nonnegative(value) for value in values)
    if not values:
        return None
    return values[round((len(values) - 1) * fraction)]


def worker_scenario(seconds, aws, memory_mb=4096, storage_mb=10240):
    """Price one successful invocation using a transferred local runtime.

    The duration is an observed local placement duration, not a Lambda result.
    This helper prices the explicit counterfactual in which the same duration is
    achieved by the planned worker allocation.
    """
    seconds = nonnegative(seconds)
    memory = nonnegative(memory_mb) / 1024
    storage = max(0, nonnegative(storage_mb) / 1024 - .5)
    total = seconds * (memory * aws["computeUsdPerGbSecond"] +
                       storage * aws["storageUsdPerGbSecond"]) + aws["requestUsd"]
    return {"seconds": seconds, "usd": total, "memoryMb": memory_mb,
            "ephemeralStorageMb": storage_mb, "invocations": 1}


def validate_layoutgpt_profile(profile):
    allowed = {"schemaVersion", "evidence", "model", "configuration", "sourceRepository",
               "sourceCommit", "sourceFile", "releasedLayouts", "releasedLayoutsSha256",
               "tokenizer", "tokenAccounting", "sampling", "inputTokens", "outputTokens",
               "limitations"}
    if not isinstance(profile, dict) or set(profile) - allowed:
        raise ValueError("Unexpected LayoutGPT cost-profile fields")
    if profile.get("evidence") != "reconstructed-official-prompt-token-estimate":
        raise ValueError("LayoutGPT cost evidence must identify its reconstructed basis")
    if profile.get("model") != "gpt-4" or profile.get("releasedLayouts", 0) <= 0:
        raise ValueError("LayoutGPT cost profile must describe the released GPT-4 layouts")
    for name in ("inputTokens", "outputTokens"):
        values = profile.get(name)
        if not isinstance(values, dict) or set(values) != {"p10", "median", "p90", "configuredMaximum"}:
            raise ValueError("LayoutGPT token summaries require p10, median, p90 and configuredMaximum")
        ordered = [nonnegative(values[key]) for key in ("p10", "median", "p90", "configuredMaximum")]
        if ordered != sorted(ordered):
            raise ValueError("LayoutGPT token summaries must be ordered")


def per_room_comparison(attempts, rate_card, profile):
    """Compare equivalent layout stages with evidence attached to every price."""
    validate_layoutgpt_profile(profile)
    completed = [row for row in attempts if row["status"] == "complete"]
    seconds = [row["generationSeconds"] for row in completed]
    llm = rate_card["gpt4"]
    aws = rate_card["lambda"]
    layoutgpt = []
    for key, label in (("p10", "10th percentile"), ("median", "Median"), ("p90", "90th percentile")):
        input_tokens = profile["inputTokens"][key]
        output_tokens = profile["outputTokens"][key]
        layoutgpt.append({"id": key, "label": label, "inputTokens": input_tokens,
                          "outputTokens": output_tokens, "usd": token_charge(input_tokens, output_tokens, llm)})
    maximum = token_charge(profile["inputTokens"]["configuredMaximum"],
                           profile["outputTokens"]["configuredMaximum"], llm)
    soilie = []
    if seconds:
        for key, label, value in (
            ("median", "Median", percentile(seconds, .5)),
            ("mean", "Mean", sum(seconds) / len(seconds)),
            ("p95", "95th percentile", percentile(seconds, .95)),
        ):
            soilie.append(dict(worker_scenario(value, aws), id=key, label=label))
    layout_median = next(row["usd"] for row in layoutgpt if row["id"] == "median")
    soilie_median = next((row["usd"] for row in soilie if row["id"] == "median"), None)
    return {
        "scope": "One completed furniture-layout proposal. Image rendering, animation, storage, transfer and API orchestration are excluded for both methods.",
        "layoutgpt": {
            "rows": layoutgpt, "configuredMaximumUsd": maximum, "profile": profile,
            "basis": "Reconstructed token counts for the official GPT-4 bedroom configuration with eight in-context examples, priced at the current published GPT-4 tariff.",
            "receiptAvailable": False,
        },
        "soilie": {
            "rows": soilie, "completed": len(completed), "failedAttemptsExcluded": sum(row["status"] != "complete" for row in attempts),
            "basis": "Observed successful local placement durations priced as one 4 GB Lambda invocation with 10 GB temporary storage. This is a runtime-transfer scenario, not measured Lambda billing.",
            "receiptAvailable": False,
        },
        "medianCostRatio": layout_median / soilie_median if soilie_median else None,
        "medianSavingPct": (1 - soilie_median / layout_median) * 100 if soilie_median else None,
    }


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
    for key, source in (("gpt4", GPT4_URL),):
        tariff = card[key]
        fields(tariff, {"model", "inputUsdPerMillion", "outputUsdPerMillion",
                        "source", "verifiedOn", "verification"})
        if tariff["source"] != source:
            raise ValueError("Expected the public model price source")
        nonnegative(tariff["inputUsdPerMillion"])
        nonnegative(tariff["outputUsdPerMillion"])


def evidence(attempts, rate_card, layoutgpt_profile):
    validate_public_rate_card(rate_card)
    completed_rows = [row for row in attempts if row["status"] == "complete"]
    complete = len(completed_rows)
    seconds = sum(row["generationSeconds"] for row in completed_rows)
    aws = rate_card["lambda"]
    successful_mean = seconds / complete if complete else 0
    available = monthly_lambda_budget(600,successful_mean,4096,10240,aws,400000,1000000)
    exhausted = monthly_lambda_budget(600,successful_mean,4096,10240,aws)
    comparison = per_room_comparison(attempts, rate_card, layoutgpt_profile)
    return {"currency":"USD", "rateCard":rate_card,
            "hosting":hosting_evidence(available,exhausted,AWS_PRICING_URL),
            "question":"What is the estimated cost of one completed furniture layout from each evaluated method?",
            "finding":("Under the stated runtime-transfer and reconstructed-token assumptions, the median SOILIE layout is estimated at "
                       f"{comparison['medianCostRatio']:.0f} times less than the median official LayoutGPT GPT-4 call. This is a scoped estimate, not a cloud invoice or a claim about newer LLM substitutions." if comparison["medianCostRatio"] else
                       "The per-room cost comparison is unavailable until successful SOILIE timing exists."),
            "scope":comparison["scope"],
            "perCompletedRoom":comparison,
            "measuredLocal":{"completed":complete, "successfulGenerationSeconds":seconds,
                              "secondsPerCompletedScene":seconds/complete if complete else None,
                              "failedAttemptsExcludedFromTiming":sum(row["status"] != "complete" for row in attempts),
                             "moneyAvailable":False, "reason":"Local electricity and hardware costs were not metered. This is not zero-cost compute."},
            "layoutgpt":{"moneyAvailable":False, "reason":"Official parsed layout files omit API usage receipts and rejected requests; the published value is a reproducible prompt reconstruction."},
            "lambda":{"moneyAvailable":False, "reason":"No closed, version-matched Lambda billing ledger is part of this local placement benchmark."},
            "freeTier":{"evidence":"hypothetical-monthly-budget", "source":AWS_PRICING_URL,
                        "accountUsageIncluded":False,
                        "monthlyDurationAllowanceGbSeconds":400000,"monthlyRequestAllowance":1000000,
                        "assumptions":f"Illustrative workload: 600 completed layouts, one {successful_mean:.2f}-second invocation each, 4 GB memory and 10 GB temporary storage; no retries. Duration is the successful local mean transferred to Lambda, not measured renderer performance there.",
                        "allowanceAvailable":available,
                        "allowanceExhausted":exhausted,
                        "interpretation":"With the full account-wide allowance still available, this hypothetical workload has no compute or request charge, but extra temporary storage still has a small charge. This is a hosting subsidy, not proof of universally cheaper model computation.",
                        "exclusions":"S3 results, CloudWatch, API Gateway, queues, database operations, traffic, taxes and any extra invocation time are not included. LLM free quotas or credits can likewise remove API charges."},
            "exclusions":["Per-room estimates exclude credits; the separate monthly scenario considers the shared Lambda free tier", "Taxes and negotiated discounts are excluded", "No inference calls were purchased for this cost analysis", "A cheaper or newer LLM is not LayoutGPT until its layout quality is rerun and evaluated"]}


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
                        "source":GPT4_URL, "verifiedOn":datetime.now(UTC).date().isoformat(), "verification":"Official model documentation; standard text-token rates"}}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(document,indent=2),encoding="utf-8")
    print(json.dumps({"rateCard":str(args.output), "regionalRates":rates}))


if __name__ == "__main__":
    main()
