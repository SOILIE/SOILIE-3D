# Comparison coverage and cost evidence

## LayoutGPT output stages

The evaluated LayoutGPT data are 423 official GPT-4 bedroom layouts and 121
recorded GPT-4 living-room proposals. Both describe categories, translations,
dimensions and rotations. `run_layoutgpt_controlled.mjs` makes an API call;
`import_layoutgpt_controlled.py` parses the returned text with the original
parser. Neither invokes mesh retrieval.

The [authors' mesh-export procedure](https://github.com/UCSB-AI/LayoutGPT#blender-visualization)
is a separate ATISS `render_from_files.py --export_scene` operation using
3D-FUTURE assets and its furniture index. It can produce OBJ/material files.
Those realized meshes are not present in this evaluation. This is an unexecuted
stage, not an inherent inability of LayoutGPT to produce furnished scenes.
Measuring contacts after retrieval requires the actual retrieved assets and
transforms, not substituting SOILIE assets or treating boxes as mesh surfaces.

### Realization prerequisites and verified access blocker

The pinned LayoutGPT checkout is available locally under
`.codex/layoutgpt-realization/source` at commit
`fc31954962553e5b65bf267a904a6930d50b1f5e`. The authors' public preprocessed
archive is reachable. Its ZIP directory contains 88,293 entries, including
both furniture indexes, but **no OBJ furniture meshes**. The extracted indexes
are retained in `.codex/layoutgpt-realization/metadata/`:

| File | SHA256 |
| --- | --- |
| `threed_future_model_bedroom.pkl` | `9dd2659bdb5d8a825215e00afdeae836a346a59e58cae3492df34f77bbd5027a` |
| `threed_future_model_livingroom.pkl` | `cdb429ca4e192d49da09411a36937556329ce015be111741b84019e388a7707f` |

The source's `get_textured_objects` calls
`ThreedFutureDataset.get_closest_furniture_to_box`, selecting a same-category
asset by squared half-size distance. It uses the asset's dataset scale, centers
its bounds, then applies the predicted rotation and translation. It does not
stretch the mesh to exactly fill the predicted box. The pickles do not cache
the computed `size` property; selection itself needs asset bounds/meshes.
Use these upstream functions and preserve retrieved dimensions, object
instances and transforms. Do not replace missing assets with SOILIE models,
change placement, ground floating meshes, or silently remove objects.

The access audit checked both official download routes with HTTP and Chromium:
`https://tianchi.aliyun.com/dataset/98063` redirects to the general dataset
listing; `https://tianchi.aliyun.com/specials/promotion/alibaba-3d-future`
renders the provider's 404 page. Neither exposes a mesh download in that
unauthenticated session. The authors' [research-use agreement](https://terms.aliyun.com/legal-agreement/terms/suit_bu1_ali_cloud/suit_bu1_ali_cloud202004171628_60052.html)
and [dataset-maintainer contact](https://github.com/3D-FRONT-FUTURE/3D-FUTURE-ToolBox#contact)
remain reachable. This is a missing authorized asset source, not a technical
claim that mesh realization is impossible. A licensed local archive or valid
provider download link is needed; third-party mirrors do not establish access
rights. Do not publish raw licensed furniture in the public S3 archive.

Once assets are accessible, realize all 423 released bedrooms and 121 recorded
living rooms without additional GPT calls. Keep proposed-box metrics and frozen
AI review inputs intact, and record mesh realization as a downstream stage with
source/index/asset hashes. Run `mesh_contact.measure_contacts` and
`solid_overlap.measure` on the realized objects without corrective movement.
Verify a bedroom and a living room against upstream OBJ export before batch
processing; attach results to matching scene IDs, and distinguish changed
retrieved bounds from the original proposed bounds. No realized LayoutGPT mesh
measurements have been produced yet.

The official bedroom files contain no original API timers or token receipts.
A separate timing pilot supplies 20 fresh GPT-4 bedroom calls, using the
original K=8 prompt, a 512-token output limit and no added count instruction.
Targets are a seeded sample of distinct rectangular test rooms, selected before
generation (seed 20260925). All 20 parsed successfully with no omitted lines.
Median request-to-response latency is 5.6409 s; p95 is 8.1275 s. Their returned
token counts price to $1.78797 total at the public rates. Network/provider
queueing is included; retrieval, parsing, mesh realization and rendering are
excluded. The API client runs locally; the GPT model runs at its provider.
These observations do not replace or augment the 423 released bedrooms in
geometry comparisons or change the frozen AI review stimuli.

## Cost distributions

The compiler emits `cost-measurements.json`: 5,000 SOILIE generation-stage
durations, 141 LayoutGPT token records (20 bedrooms, 121 living rooms) and 38 isolated Infinigen construction
durations (20 bedrooms, 18 living rooms). It contains only public numerical
inputs, anonymous observation identities and fixed public tariffs. No account
identity, private usage allowances, provider request IDs or billing totals are
published. The comparison artifact pins the ledger's checksum.

To reproduce the timing pilot preparation, use `layoutgpt_controlled` with
`--bedroom-pilot` and the pinned upstream `run_layoutgpt_3d.py`. This requires
the authors' processed metadata, not licensed furniture meshes. The runner
validates a separate $6.15 maximum reservation for exactly 20 calls and never
retries ambiguous outcomes. Import with `import_layoutgpt_controlled`, then
pass the export to `publication_views --bedroom-timing`. Private prompts,
provider response IDs and operational receipts remain outside public exports.

- SOILIE: measured cloud generation seconds at a 4 GiB x86-64 AWS Lambda tariff,
  10 GiB ephemeral storage (9.5 GiB chargeable), plus one request. This is not
  complete billed invocation duration. Benchmark measurement and other
  invocation overhead are excluded; downstream correction has a separate timer.
- LayoutGPT: each call's actual returned input/output token counts at the
  published GPT-4 rates, $30/$60 per million tokens. A token-priced API charge
  is not an invoice after credits, taxes or discounts. Retrieval, parsing,
  mesh realization and rendering are outside that charge.
- Infinigen: explicitly hypothetical transfer of isolated desktop duration to
  the same worker tariff. The local task used four Blender threads; equivalent
  performance on a 4 GiB Lambda worker is **not** established. Memory adequacy,
  container compatibility and cloud execution were not tested. This scenario
  is an illustration, not evidence of achievable cloud cost or a cost ranking.

Every price is calculated per observation before summarizing. The median,
quartiles, full range and p95 show variability across rooms at fixed prices.
They are not confidence intervals, measurement-error bars or uncertainty about
the tariff. The Infinigen assumption adds unquantified systematic uncertainty;
the spread of its desktop runtimes does not estimate that uncertainty.

Private operational records do contain billed durations for all 5,000 cloud
benchmark invocations. Those include benchmark observation and startup work,
and therefore are not a clean price for a production layout alone. They must
not be silently relabeled as generation-only cost or published as account data.

## Other missing entries

Controlled-inventory Infinigen generation shared CPUs across workers. Its
observed elapsed times cannot establish isolated latency or allocated resource
cost per room. The site explains this adjacent to timing/cost plots. The 40
room-scale scenes all enter geometry comparisons; only 38 isolated timings
enter speed and hypothetical cost distributions.

GRAINS contributes its authors' 1,027-second / 10,000-bedroom timing reference.
No evaluated GRAINS scenes, per-room latency records or matching resource costs
are available. It cannot supply distributions, mesh-contact results or AI votes.

## Mesh contact versus enclosing-box overlap

See [the contact audit](overlap-contact-measurements.md). Nonzero enclosing-box
intersection after surface settlement is not by itself a model defect. Preserve
the raw box scores and present them separately from mesh-intersection detection.
Never move a supported object off its support just to force that proxy to zero.
