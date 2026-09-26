# Infinigen cloud timing pilot

Measures four conditions: bedroom/living room × room-scale/controlled inventory.
The first case in each condition gates the remaining cases, up to 20 per
condition. Seeds are fixed before generation. This is a timing cohort, not a
replacement for the geometry or AI-review cohorts.

`build.ps1` packages the pinned initial Indoors source, Blender 3.6.0 and the
validated Linux dependencies from project `.codex/` directories. It rejects a
modified upstream checkout or oversized image and smoke-tests imports. Run
`python -m serverless.infinigen_cloud.campaign --output .codex/benchmark/<run>
--name soilie-infinigen-timing-<unique-id> --rates <public-rate-card.json>` only
with authorization for the compute budget.

Each worker uses x86-64 AWS Lambda, 6 GiB RAM, 10 GiB ephemeral storage, four
Blender threads and a 900-second function timeout. Construction is stopped at
760 seconds, leaving time to transfer artifacts. The local benchmark's
`fast_solve` and disabled small-decoration solving are retained. Controlled
bedrooms cycle 3–6 objects; controlled living rooms contain six. No alternative
solver or retry replaces a failed scene.

The measured generation stage starts before Blender startup and ends after
scene serialization. Validation, compression and S3 upload are excluded from
that timer but remain in billed invocation duration. Report these separately.
Do not pool cloud and desktop times or interpolate fictitious observations to
smooth an empirical distribution.

A $10 compute ceiling reserves a full timeout plus startup allowance before
every invocation. Ambiguous responses keep their reservation. The controller
refuses to reuse a campaign ledger and deletes its own temporary stack and ECR
repository in `finally`; cleanup errors are recorded for follow-up. The
handler terminates its complete child-process group and clears its `/tmp`
workspace. Generated scenes and result records remain in the authorized S3
output prefix. Private ledgers and logs must not be copied into website files.

For a corrected packaging pilot, supply `--previous <closed campaign.json>`.
Its costs remain inside the same $10 ceiling; initialization failures retain
their full worst-case reservation. Do not reset the budget by starting a new
folder. `publication.measured_rows` accepts only a complete 80-room cohort and
strips private receipt fields before the website compiler can consume it.

Calls use asynchronous delivery with automatic function-error retries disabled.
The controller polls each durable S3 result; a conditional S3 claim prevents a
duplicate delivery from running Blender twice. CloudWatch billing reports are
saved privately before resource cleanup. A lost client response is not a reason
to regenerate a scene: `receipts.py` can reconcile a closed pilot against its S3
results and saved CloudWatch evidence. Continue using `--previous <verified
pilot.json> --reuse-completed`; the same fixed schedule skips verified cases and
retains all prior spending in the cap.
