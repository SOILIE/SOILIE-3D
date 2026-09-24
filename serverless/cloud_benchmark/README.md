# Temporary platform benchmark

Private, finite Lambda execution of the tracked SOILIE placement pipeline.
The website and its production renderer are not changed by these tools.

## Design

| Room preset | Local | AWS Lambda |
| --- | ---: | ---: |
| Bedroom | 2,500 retained, support-corrected | 2,500 new |
| Living room | 2,500 new or already completed | 2,500 new |

`design.py` freezes requests before examining results. Each new-generation
condition requests 625 scenes at each object count, 3–6, with duplicates enabled.
Living-room shards 00–07 are local; 08–11 split at request 125; 12–19 are cloud.
Independent fixed bedroom seeds start at 120260924. Failures remain evidence,
not completed scenes. None of these runs renders images or uses web room sizing.

Local bedrooms keep their original generation durations and separate repair
durations. New local runs have six one-thread worker slots. Cloud timings identify
the Lambda memory configuration, immutable image and source checksums. Placement
duration excludes geometry-measurement overhead; billed duration includes startup,
measurement and result storage. Do not label either as an uncontended local run,
combine platforms into one latency distribution, or substitute old serial timings.

## Execution

1. Keep model/observer source unchanged during the campaign. Place `STOP` in the
   superseded balanced campaign so its current six shards drain without starting
   later shards. The `local` module fills released slots with disjoint work and
   stops repairs at the retained 2,500 bedrooms.
2. Run `deploy.ps1` with the authorized AWS profile. The isolated image inherits
   only dependencies/assets from an immutable renderer image, copies model code
   directly from the checkout, and checks every runtime asset during the build.
3. Run `pilot` against four local scenes per room type, covering counts 3–6.
   All eight cases must have identical requests, selections and geometry within
   0.00001 m. Lambda denies `PR_SET_PDEATHSIG`; the cloud adapter instead uses an
   owned process group and the service watchdog. It changes no model function.
4. Run `campaign` with its frozen plan, parity receipt and budget. It starts with
   16 active calls, then allows up to the requested concurrency as the budget
   permits. Every active call reserves 915 seconds of compute, not its expected
   average. The default US$25 ceiling includes a US$1 setup allowance; unused
   AWS free tier is not assumed. Reservations are not incurred spending.
5. Ambiguous network outcomes stop new dispatch. Reconcile S3/receipts first;
   never blindly resubmit paid work. Downloads are checked against S3 checksums.
   `recover_response` can reconstruct a missing client receipt from a unique S3
   artifact and its CloudWatch billing report without invoking Lambda again.
   Keep failed-call costs even when a repaired scene is later regenerated.
6. After completion, `cleanup` verifies all 5,000 downloaded results, archives all
   private bucket objects and CloudWatch logs, and removes only the temporary
   Lambda stack, private bucket and ECR repository. A failed validation prevents
   deletion. Preserve the local evidence for the later cross-model analysis.

All generated inputs, receipts, artifacts and logs belong under the repository's
`.codex/` directory. Keep account/resource identifiers out of website summaries.
The temporary bucket is deliberately separate from the published research data.
