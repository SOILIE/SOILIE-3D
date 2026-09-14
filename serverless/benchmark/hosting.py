"""Generic hosting scenarios, not a portability test or a cloud billing report.

Provider rules differ enough that a RAM-only price comparison is misleading.
Keep minimum billed lifetimes, active versus allocated CPU, and monthly plan
fees explicit. No account credentials, configuration or usage are read here.
"""
from decimal import Decimal, ROUND_CEILING
import math


CHECKED_ON = "2026-09-14"
SOURCES = {
    "cloudflare": "https://developers.cloudflare.com/containers/platform/pricing/",
    "cloudflareLimits": "https://developers.cloudflare.com/containers/platform/limits/",
    "workersLimits": "https://developers.cloudflare.com/workers/platform/limits/",
    "cloudrun": "https://cloud.google.com/run/pricing",
    "cloudrunRuntime": "https://docs.cloud.google.com/run/docs/container-contract",
    "fargate": "https://aws.amazon.com/fargate/pricing/",
}
# Public on-demand tariffs verified at the sources above. GB follows each
# provider's documented unit; AWS's GB here is 1024**3 bytes (a GiB).
TARIFFS = {
    "cloudflare": {"cpuUsdPerSecond": .000020, "memoryUsdPerGiBSecond": .0000025,
                   "diskUsdPerGBSecond": .00000007, "monthlyPlanUsd": 5,
                   "includedCpuSeconds": 375*60, "includedMemoryGiBSeconds": 25*3600,
                   "includedDiskGBSeconds": 200*3600},
    "cloudrun": {"cpuUsdPerSecond": .000018, "memoryUsdPerGiBSecond": .000002,
                 "includedCpuSeconds": 240000, "includedMemoryGiBSeconds": 450000},
    "fargate": {"cpuUsdPerSecond": .000011244, "memoryUsdPerGiBSecond": .000001235,
                "diskUsdPerGBSecond": .0000000308, "includedDiskGB": 20},
}


def nonnegative(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("Hosting inputs must be finite non-negative numbers")
    return value


def billed_seconds(lifetime_seconds, minimum_seconds, quantum_seconds):
    """Round each instance, not the batch; Decimal avoids 0.3 / 0.1 drift."""
    lifetime = nonnegative(lifetime_seconds)
    minimum = nonnegative(minimum_seconds)
    quantum = Decimal(str(nonnegative(quantum_seconds)))
    if not quantum:
        raise ValueError("Billing quantum must be positive")
    return float((Decimal(str(max(lifetime, minimum)))/quantum).to_integral_value(rounding=ROUND_CEILING)*quantum)


def container_budget(provider, jobs, lifetime_seconds, cpu, memory_gib, disk_gb=0,
                     allowance_available=False, cpu_utilization=1):
    if provider not in TARIFFS or type(jobs) is not int or jobs < 0:
        raise ValueError("Expected a supported provider and non-negative job count")
    cpu, memory_gib, disk_gb = map(nonnegative, (cpu, memory_gib, disk_gb))
    if not cpu or not memory_gib or nonnegative(cpu_utilization) > 1:
        raise ValueError("Positive capacity and a CPU utilization from zero to one are required")
    if type(allowance_available) is not bool:
        raise ValueError("Allowance availability must be explicit")
    tariff = TARIFFS[provider]
    minimum, quantum = (0, .01) if provider == "cloudflare" else (60, .1 if provider == "cloudrun" else 1)
    seconds = billed_seconds(lifetime_seconds, minimum, quantum)
    elapsed = jobs*seconds
    # Cloudflare meters consumed CPU. Google and Fargate meter allocation even
    # while waiting for I/O. RAM/disk stay allocated throughout awake lifetime.
    cpu_seconds = elapsed*cpu*(cpu_utilization if provider == "cloudflare" else 1)
    memory_seconds = elapsed*memory_gib
    disk_seconds = elapsed*max(0, disk_gb-tariff.get("includedDiskGB", 0))
    cpu_free = tariff.get("includedCpuSeconds", 0) if allowance_available else 0
    memory_free = tariff.get("includedMemoryGiBSeconds", 0) if allowance_available else 0
    disk_free = tariff.get("includedDiskGBSeconds", 0) if allowance_available else 0
    components = {
        "cpuUsd": max(0, cpu_seconds-cpu_free)*tariff["cpuUsdPerSecond"],
        "memoryUsd": max(0, memory_seconds-memory_free)*tariff["memoryUsdPerGiBSecond"],
        "diskUsd": max(0, disk_seconds-disk_free)*tariff.get("diskUsdPerGBSecond", 0),
        "planUsd": tariff.get("monthlyPlanUsd", 0),
    }
    return {"jobs": jobs, "billedSecondsEach": seconds, "cpuSeconds": cpu_seconds,
            "memoryGiBSeconds": memory_seconds, "diskGBSeconds": disk_seconds,
            "assumedCpuUtilization": cpu_utilization if provider == "cloudflare" else None,
            "allowanceAvailable": allowance_available, **components,
            "totalWorkerUsd": sum(components.values())}


def evidence(lambda_available, lambda_exhausted, lambda_source):
    """Only fixed anonymous assumptions enter the public export.

    These capacities are candidate configurations, not equivalent hardware.
    Cloud Run receives RAM headroom because scratch writes consume RAM there.
    None is claimed to finish a scene in 30 seconds without a deployment test.
    """
    rows = [{"id": "lambda", "label": "AWS Lambda", "region": "Canada Central",
             "configuration": "10 GiB RAM; 10 GiB temporary storage; CPU scales with RAM",
             "billing": "1 ms duration rounding; no provisioned concurrency assumed",
             "idle": "No invocation compute charge between jobs",
             "feasibility": "Native Blender in a Linux image, subject to the 15-minute execution and image/memory limits.",
             "available": lambda_available, "exhausted": lambda_exhausted,
             "sources": [lambda_source, "https://docs.aws.amazon.com/lambda/latest/dg/gettingstarted-limits.html"]}]
    configurations = [
        ("cloudrun", "Google Cloud Run Jobs", "Iowa (us-central1)", 4, 16, 0,
         "4 vCPU; 16 GiB RAM shared by the process and writable scratch files",
         "60 s minimum per task; 100 ms rounding", "No running-task charge between jobs",
         "Native Linux containers. Scratch writes consume RAM; this is not 16 GiB RAM plus a separate disk allowance.",
         [SOURCES["cloudrun"], SOURCES["cloudrunRuntime"]]),
        ("fargate", "AWS Fargate", "US East (N. Virginia)", 4, 10, 20,
         "4 vCPU; 10 GiB RAM; 20 GiB included temporary storage",
         "60 s minimum per task; 1 s rounding, starting at image download", "No task compute charge after termination",
         "Native Linux containers; suitable for jobs exceeding a function timeout. Networking can add charges.",
         [SOURCES["fargate"]]),
        ("cloudflare", "Cloudflare Containers", "Global tariff", 4, 12, 20,
         "standard-4: 4 vCPU; 12 GiB RAM; 20 GB disk; all four CPUs assumed busy",
         "10 ms rounding; CPU consumed, RAM and disk provisioned", "$5/month plan remains; resources billed until sleep",
         "Container candidate within the published limits. Ordinary Workers are the API layer, not a drop-in native Blender host.",
         [SOURCES["cloudflare"], SOURCES["cloudflareLimits"], SOURCES["workersLimits"]]),
    ]
    for provider, label, region, cpu, memory, disk, config, billing, idle, feasibility, sources in configurations:
        rows.append({"id": provider, "label": label, "region": region, "configuration": config,
                     "billing": billing, "idle": idle, "feasibility": feasibility, "sources": sources,
                     "available": container_budget(provider, 600, 30, cpu, memory, disk, True),
                     "exhausted": container_budget(provider, 600, 30, cpu, memory, disk, False)})
    return {"evidence": "hypothetical-hosting-scenarios", "checkedOn": CHECKED_ON,
            "accountUsageIncluded": False, "portabilityBenchmarked": False,
            "question": "What could low-volume hosting cost on other platforms?",
            "assumptions": "600 completed layouts per month, one isolated job per layout, no retries, and a hypothetical 30-second total billable lifetime before provider minimums. Startup, data transfer within the job and shutdown must fit that assumption. All containers terminate or sleep immediately afterward.",
            "interpretation": "These are price calculations for different candidate capacities, not a speed-normalized provider ranking. Free allowances can dominate small workloads; monthly plan fees and one-minute minimums can outweigh lower per-second rates.",
            "allowances": "The first scenario leaves each provider's standard monthly allowance fully available; the second leaves none. Cloudflare's entire required $5 plan is included in both; if that plan is already paid for another purpose, subtract $5 to calculate incremental cost. Fargate assumes no recurring free compute allowance. Promotional credits and negotiated discounts are excluded.",
            "exclusions": "Worker resources and the required Cloudflare plan only. Registry/build storage, persistent results, logs, API/queue/database operations, Workers and Durable Object overages, IPv4/NAT, bandwidth and taxes are additional. No alternative platform has been deployment-benchmarked here.",
            "otherCompute": "A rented VM or existing local machine is another option. Divide its billed runtime plus idle time, disk and operating costs by completed scenes; for local hardware include electricity and equipment cost. Without measured utilization and a selected tariff, neither is honestly priced as zero. Batching can amortize startup and job minimums, but is a different workload from this one-job-per-scene example.",
            "rates": TARIFFS, "rows": rows}
