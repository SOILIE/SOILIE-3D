# Inventory-conditioned generation workers

Generate additional baseline rooms for tighter inventory matching without
changing the frozen SOILIE cohort or restarting AI judgments. Operational
files, credentials, logs, native outputs, and progress snapshots are **not**
committed. They live under the project's `.codex/arbutus-inventory/` directory.

## Conditions and selection

- The frozen plan targets 120 bedrooms and 120 living rooms per baseline.
  It retains 30 compatible LayoutGPT inputs and requests 210 new LayoutGPT
  rooms plus 240 Infinigen rooms. Retention does not automatically authorize
  reusing judgments collected under a different prompt or stimulus version.
- Selection uses inventories and a fixed hash order, never quality scores or
  reviewer preferences. Each target differs from its SOILIE inventory by at
  most one recorded category substitution; duplicates remain instances.
- LayoutGPT uses the original GPT-4, author training examples, native K=8
  bedroom/K=4 living-room retrieval, and held-out room dimensions. Its added
  instruction specifies the exact inventory. No SOILIE coordinates, sizes,
  orientations, or quality scores enter the prompt. Copilot is not substituted
  for GPT-4 when that original model is unavailable through Copilot.
- Infinigen is pinned to `fb7991e06580639202a4687937082cb63e931eb0`.
  The adapter supplies exact composition constraints and retains applicable
  native soft objectives and procedural factories. It uses `fast_solve.gin`
  and `singleroom.gin`, disables small-object population, and runs one room.
  **This is an inventory-conditioned configuration, not default Infinigen.**
  A requested nightstand is explicitly constrained beside a bed; a generic
  side table is not silently relabelled after generation.
- Native solver failures or incomplete inventories receive up to five fixed
  seed attempts (`baseSeed + attemptIndex * 100000`). All attempt receipts are
  retained. Acceptance checks successful geometry export and the requested
  inventory, not an overlap score or visual preference. Final density matching
  and stimulus validation still follow generation; 480 ready inputs do not
  guarantee 480 final matched review pairs.

## Execution and delivery

The cloud controller is a systemd service independent of SSH connections.
Local jobs use WSL. Task indices divisible by five belong to the local host;
the remainder initially belong to Arbutus. This partition prevents duplicate work.
Each task owns its folder, logs, attempt records, compact scene JSON, and native
Blender archive. Interrupted attempts fail closed rather than being overwritten.

Current resources: two local workers with two Blender threads each, and 28
Arbutus workers with one thread each on the requested 32-vCPU flavor. Leave
memory/CPU headroom for the desktop, SSH, compression, and transfer. Both
controllers enforce a free-disk reserve before starting another attempt.

When the cloud finishes its initial queue first, `serverless.arbutus.rebalance`
can hand off untouched local tasks. It briefly freezes only the local Python
scheduler, verifies every worker slot is occupied, excludes all started or
completed tasks, and writes explicit `delegated` receipts for the pending ones.
The in-flight Blender processes keep running. A hash-verified ready marker
gates the cloud worker's `--assignment` option. Inputs, seeds, completed scenes,
and the original campaign manifest are unchanged; actual host provenance is
recorded on each new result. Delegation is never counted as a completed room.

```powershell
wsl -d Ubuntu -- bash -lc 'cd /mnt/c/Users/mike/Documents/AppDev/SOILIE-3D && python3 -m serverless.arbutus.rebalance --root .codex/arbutus-inventory --output .codex/arbutus-inventory/cloud-tail.json'
```

Use a new assignment path, only while the local controller is running. Copy the
assignment and its `.ready.json` sibling to the idle cloud worker, then start
the existing worker command with `--assignment /mnt/soilie/cloud-tail.json`.
The campaign output lock prevents two cloud controllers from overlapping.
Resume that same assignment if interrupted; do not create replacement scenes.

The private VM has no floating IP. Reuse the existing shared security group and
MMP jump host. Pin its host key from the authenticated OpenStack console, not
an unauthenticated scan. Keep cloud/API credentials on the local machine;
the VM needs only its SSH public key and research runtime. Runtime bootstrap
can use authenticated encryption for a temporary S3 transfer; generation
outputs still follow **Arbutus -> local relay -> local project folder**.

The relay runs up to four downloads concurrently. It verifies both archive and
scene SHA-256 hashes before writing `verified.json`. It reserves space for all
in-flight downloads and never deletes remote evidence. Local generation waits
at its disk reserve while the S3 uploader frees space, without counting the
wait as model execution or a failed generation.

The continuous archive worker forwards completed outputs from this local node
to `soilie3d-data/files/outputs/inventory-<date>-<plan-hash>/`. It uploads and
verifies each room before deleting its local archive and duplicate native
scene files. Geometry JSON, checkpoints, recovery receipts, and private logs
remain local. The public native bundle excludes execution logs and invocation
metadata; every excluded member is preserved in local `private-evidence/`.
It checks the full S3 SHA-256 (or downloads and hashes the object), not merely
an ETag or a user-supplied metadata field. A failed verification deletes nothing.
The Data-page index is merged conditionally, preserving unrelated entries.
This research prefix is outside the seven-day `generated/` lifecycle.

```powershell
python -m serverless.arbutus.archive --root .codex/arbutus-inventory --date 2026-09-29
```

Do not change the archive date when resuming the same campaign. The monitor
shows S3-verified room counts and waits for storage delivery as well as
generation. Native files are restored using the task's `s3-receipt.json` keys
and checksums. Do not remove the VM until all successful archives are verified
on S3 and failures are resolved.

## Monitor and resume (PowerShell, from the backend root)

```powershell
pwsh -NoProfile -File scripts/watch-room-generation.ps1
```

The monitor shows overall, Infinigen, and LayoutGPT bars, room-type counts,
active jobs, delivery counts, spending, and provisional ETA. It refreshes every
second and exits at full completion or Ctrl+C. Closing it does not stop workers.
The relay refreshes evidence every few seconds; stale heartbeats are flagged.

Resume a stopped relay only after checking its recorded PID is no longer alive:

```powershell
python -m serverless.arbutus.relay --root .codex/arbutus-inventory --transfers 4
```

Local worker command (does not require paid API credentials):

```powershell
wsl -d Ubuntu -- bash -lc 'cd /mnt/c/Users/mike/Documents/AppDev/SOILIE-3D && python3 -m serverless.arbutus.worker --plan .codex/arbutus-inventory/campaign-v1.json --output .codex/arbutus-inventory/local --repository .codex/infinigen --dependencies .codex/infinigen-env/lib/python3.10/site-packages --blender .codex/tools/blender-3.6.0-linux-x64/blender --host local --workers 2 --threads 2 --reserve-gib 12'
```

Inspect the remote service before starting another controller:

```powershell
ssh -F .codex/arbutus-inventory/ssh-config soilie-mike-dev 'systemctl status soilie-inventory-workers.service --no-pager'
```

Paid LayoutGPT calls require an explicitly authorized budget and available API
balance. Set `OPENAI_API_KEY` privately, then run:

```powershell
node serverless/benchmark/run_layoutgpt_controlled.mjs .codex/arbutus-inventory/layoutgpt
```

The single US$35 ledger covers both room types and conservatively reserves
unsettled charges. Three API requests can run concurrently; `--workers=1`
reduces concurrency when diagnosing intermittent provider errors. Completed or
uncertain requests never repeat. After replenishing credit, the explicit
`--retry-credit-exhausted` flag permits retrying a known HTTP 429 billing
rejection, retaining its receipt and reservation. Do not automatically retry
authorization, rate-limit, or ambiguous transport errors. A credential JSON
path may be supplied as the optional second argument instead of an environment
variable; it is never copied into outputs.

## Inventory-matched AI review

Generation completion is not pair eligibility. `review_cohort` verifies source
checksums, normalized category counts (duplicates included), room type, bed/sofa
anchors, functional fronts, and the recorded density limits before freezing a
pair list. One substitution means replacing at most one object category, with
equal total counts. Matching uses no quality scores or reviewer preferences.
The LayoutGPT and Infinigen density limits remain 0.25 and 1.0 respectively;
density is summed furniture-box footprint area divided by room area.

The current selection contains 120 pairs in each baseline-by-room-type group.
Extra LayoutGPT proposals share the original US$35 cap through immutable parent
ledger hashes; the cap is not reset per supplementary batch. All proposals,
including unmatched ones, remain auditable.

Prepare a new directory, never overwrite a frozen protocol:

```powershell
python -m serverless.arbutus.review_cohort --root .codex/arbutus-inventory --prepare-source .codex/arbutus-inventory/review-audit-final.json --output .codex/arbutus-inventory/review-source-v4-audited
python -m serverless.cloud_benchmark.full_review --prepare --source .codex/arbutus-inventory/review-source-v4-audited --root .codex/arbutus-inventory/review-functional-use-v4-audited --catalog .codex/arbutus-inventory/review-source-v4-audited/catalog.json
node serverless/cloud_benchmark/render_staged_pilot.mjs .codex/arbutus-inventory/review-functional-use-v4-audited <repository-with-playwright>
python -m serverless.cloud_benchmark.audit_full_diagrams --root .codex/arbutus-inventory/review-functional-use-v4-audited
```

All 240 native Infinigen floors are re-read with
`serverless/benchmark/reextract_infinigen_floor.py` in Blender. The footprint
projects every tagged visible floor triangle, including sloped faces and
thresholds; it does not discard faces that miss an exact horizontal tolerance.
Furniture transforms, floor elevation datum and generation timings are unchanged.
The checked native blend/state hashes live in `floor-corrections/`. The pairing
audit is refreshed against those footprints before freezing the source set.

The collector additionally checks every packet and pixel-identical reversal.
`retain_identical_reviews` can copy an earlier response only when its model,
effort, full prompt, system instructions, schema, evidence and image are identical.
Its receipt identifies the original response; changed prompts or pairings need
new judgments. Reference examples cover all displayed categories but are not
population norms. Frozen v4 instructions allow a sofa to face a TV stand without
inventing an unseen TV. Historical prompts and responses are never rewritten.

Two opposite-side judgments per pair in five dimensions yield 4,800 assignments.
Use five isolated workers with the existing Codex login, not an API key:

```powershell
python -m serverless.cloud_benchmark.run_full_review --root .codex/arbutus-inventory/review-functional-use-v4-audited --workers 5 --codex <codex-executable> --authorize-review
pwsh -NoProfile -File scripts/watch-ai-reviews.ps1
```

The six progress bars include overall progress and each dimension. The monitor
does not control the workers. Completed answers are not retried; interrupted
attempts stop collection for inspection. No agreement or preference target
determines retention. Independent judgments of the same pair are not independent
room samples, and AI preferences do not establish human validity.

`comparison_archive` consolidates verified rooms under
`s3://soilie3d-data/files/outputs/website-comparisons/`, keeping existing URLs.
Use its `--attach-only --review <review-root>` option after all audits to attach
prompts, matching, sources and packets without copying rooms again. The manifest
separates published quantitative rooms from inventory-conditioned review inputs.
Private execution files and unfinished preference results are never published.

## Validation and cleanup

```powershell
python -m unittest serverless.tests.test_arbutus_inventory serverless.tests.test_arbutus_archive serverless.tests.test_inventory_matching serverless.tests.test_layoutgpt_controlled
node --test serverless/tests/layoutgpt-budget.test.mjs
pwsh -NoProfile -File scripts/watch-room-generation.ps1 -Once
```

Keep `.codex/arbutus-inventory/campaign-v1.json`, request plans, paid responses,
ledgers, and generation receipts unchanged. Save resumable controller/VM IDs
in that private directory. Only delete rebuildable transfer bundles after
verifying delivery. Never remove native scene evidence merely because it was
generated successfully; verify the retained archive first. Terminate only
task-owned workers, and delete only the journalled temporary VM after delivery.
