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

The official bedroom files contain no original API timers or token receipts.
Only our living-room proposals have measured request-to-response times and
returned token usage. Neither JSON loading time nor living-room latency is a
valid replacement for missing bedroom inference time.

## Cost distributions

The compiler emits `cost-measurements.json`: 5,000 SOILIE generation-stage
durations, 121 LayoutGPT token records and 38 isolated Infinigen construction
durations (20 bedrooms, 18 living rooms). It contains only public numerical
inputs, anonymous observation identities and fixed public tariffs. No account
identity, private usage allowances, provider request IDs or billing totals are
published. The comparison artifact pins the ledger's checksum.

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
