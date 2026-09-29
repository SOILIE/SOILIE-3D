# Inventory matching and size-reference checkpoint

Collection is paused at 343 saved judgments, with no in-flight calls. No prompt,
assignment, geometry, reference evidence or response in the frozen campaign was
changed. This is preparation for a study-design decision, not published results.

## What counts as a match?

An exact match has the same **multiset of neutral object categories**, including
duplicate instances. A one-substitution match has equal object counts but may
replace one object category on each side. Thus `[sofa, chair, chair]` versus
`[sofa, chair, lamp]` differs by one substitution. Categories already remove
single/double-bed and source-specific spelling cues. Floor lamps and ceiling
lights, stools and backed chairs, and coffee tables and dining tables stay
distinct. No geometry, objects or answers are removed to create a match.

Candidates also share room type and bed/sofa count. Density means the sum of
furniture footprint areas divided by room area. Its allowed absolute difference
is 0.25 for LayoutGPT and 1.0 for the expanded Infinigen condition. Removing the
density restriction entirely gives the same counts below: inventory is the
binding constraint, not density.

`inventory_matching.py` finds a maximum-cardinality, one-to-one assignment per
baseline and room type. Among equally large matchings it first retains existing
pairs, then minimizes substitutions and density differences, with deterministic
tie-breaking. It never reads preferences, explanations or quality measurements.
Exact-only and one-substitution maxima are separate solutions; their chosen
pairs need not be nested. A SOILIE room cannot be reused within one baseline/room
stratum, but may occur in the other baseline comparison.

## Existing-output feasibility

All 5,000 SOILIE bedrooms and 5,000 living rooms were searched, not just the 403
distinct SOILIE rooms currently illustrated in the review. Baselines include all
423 released LayoutGPT bedrooms, 121 controlled LayoutGPT living rooms, and
168/121 completed controlled Infinigen bedrooms/living rooms. The 53 earlier
unconstrained LayoutGPT living rooms are excluded as a different input condition.

| Baseline | Room type | Exact matches | At most one substitution | Existing pairs retained |
|---|---|---:|---:|---:|
| LayoutGPT | Bedroom | 1 | 3 | 1 |
| LayoutGPT | Living room | 5 | 69 | 29 |
| Infinigen, controlled | Bedroom | 0 | 0 | 0 |
| Infinigen, controlled | Living room | 0 | 0 | 0 |

These are category matches, not identical dimensions, geometry or assets. Most
Infinigen living rooms contain a rug, shelf, side table and TV stand, while those
categories are absent or rare in the SOILIE pool. A separate **unapproved
sensitivity** treating nightstands and side tables as interchangeable permits
seven one-substitution Infinigen bedroom pairs, but no exact Infinigen pairs and
no living-room pairs. It does not change the LayoutGPT counts. No broader
functional categories were silently substituted.

The stricter solution retains 30 existing pairs with 24 saved responses: eight
relative-size, six relationships, four room-function, three access and three
orientation judgments. The 16 non-size responses can be considered for retention
only if their complete input and instructions remain unchanged. The eight size
responses consumed the old catalog and cannot be relabelled as judgments using
the expanded catalog. All 343 originals remain preserved regardless of which
cohort is eventually selected. Re-pairing a room requires a new pairwise judgment.

## Reference preparation

`reference_volumes_candidate.json` adds 35 sourced product configurations to the
eight frozen examples, for **43 examples across 29 of the 33 displayed
categories**. It is opt-in and is not connected to the collector. The existing
catalog's checksum is enforced. Each addition records its source, units,
configuration, and exterior-envelope interpretation; packing dimensions, liquid
capacity and solid-material volume are not used as box volume.

Manufacturer specifications include IKEA furniture, Brother printers, BenQ
projectors, Sonos/JBL speakers, Panasonic phones, Targus bags, Tempur-Pedic
pillows, Nearly Natural plants and Crown beverage cans. Furniture examples are
still predominantly from IKEA and this is **not a representative distribution
or a set of allowable size limits**. Some broad categories have only one
configuration. Empty space, subtype variation and adjustable/deformable objects
still require judgment; a product-example range alone cannot establish a defect.

Across the 883 unique frozen source scenes, both baseline conditions and SOILIE
bedrooms now have examples for every within-room object-pair category. SOILIE
living rooms have examples for 2,309 of 2,613 pairs (88.4%). Books, caps, cereal
boxes and coffee mugs still need verified complete envelopes. They are explicitly
unresolved, not populated with invented precision. `require_coverage` rejects
a proposed cohort containing uncovered categories. Catalog coverage is not proof
of the accuracy or representativeness of the reference values.

## Intended relationship rule

Sofa alignment toward a TV stand is an intended relationship even without a
visible television. That alone is not grounds for invalidating or recollecting
an answer. A later prompt can make this explicit without claiming that a screen
is present or inventing screen-specific geometry. The earlier audit's proposal
to rerun every sofa/stand case is withdrawn under this clarified intent.

## Decision before any more paid reviews

The existing pool cannot support the requested large, tightly matched four-group
comparison. Preserve it as generated-inventory evidence; do not disguise it as
identical-furniture placement evidence. A substantially larger matched comparison
needs a new, outcome-blind shared-inventory generation schedule restricted to
classes supported by both methods. Validate feasibility and cost before starting
new generation. Alternatively, retain the broad original comparison and report
the stricter subset separately, with its small and uneven coverage visible.

No replacement cohort, changed prompt, new generation, full rerun or deployment
is authorized by this checkpoint. Finish the catalog for the chosen cohort and
freeze its evidence before collecting any judgments under revised inputs.

## Reproduction

Run from the backend repository. Output paths are exclusive-create; use a new
filename for each audit. Reports with individual scene IDs and input hashes are
kept in `.codex/review-pairing-20260929/`.

```powershell
python -m serverless.benchmark.inventory_matching `
  --base .codex/benchmark/soilie-platform-grid-final/evidence/measured-scenes.json `
  --scenes .codex/benchmark/soilie-platform-grid-final/review-functional-fronts-v3/source-scenes.json `
  --protocol .codex/benchmark/soilie-platform-grid-final/review-functional-use-v3-full/protocol.json `
  --supplements .codex/benchmark/layoutgpt-controlled/export.json .codex/benchmark/layoutgpt-controlled-supplement/export.json .codex/benchmark/infinigen-controlled-living-supplement/export.json `
  --expansion .codex/benchmark/infinigen-expanded-120 `
  --output .codex/review-pairing-20260929/full-pool-matching-recheck.json

python -m serverless.benchmark.reference_catalog `
  --scenes .codex/benchmark/soilie-platform-grid-final/review-functional-fronts-v3/source-scenes.json `
  --output .codex/review-pairing-20260929/catalog-coverage-recheck.json

python -m unittest serverless.tests.test_inventory_matching serverless.tests.test_reference_catalog serverless.tests.test_full_review
```
