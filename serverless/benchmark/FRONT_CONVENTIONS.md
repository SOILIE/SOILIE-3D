# Functional-front annotations for the visual review

These annotations describe final placements. They do not rotate, repair, rescale,
or reselect scenes. Original scene digests remain unchanged; new stimulus hashes
and the `functional-fronts-neutral-labels-v3` policy identify the presentation.

| Source | Canonical horizontal front | Conversion to benchmark XY |
| --- | --- | --- |
| SOILIE-3D | +X after per-asset correction in `assets/asset_rotations.csv` | Final world transform applied to +X |
| LayoutGPT / 3D-FRONT | +Z in its Y-up coordinate system | `[sin(theta), cos(theta)]`, theta is the released orientation |
| Infinigen Indoors | +X, tagged `Subpart.Front` | Final asset-root world transform applied to +X |

## Source evidence

- SOILIE `modules/render.py:transform_objects` applies the asset correction then
  bakes it into the mesh. Wall and pair rules rotate this corrected frame.
- LayoutGPT commit `fc31954962553e5b65bf267a904a6930d50b1f5e`,
  `ATISS/scene_synthesis/utils.py:get_textured_objects`, applies the emitted yaw
  with `vertices.dot(R)`. The row-vector +Z transforms to `(sin(theta),0,cos(theta))`.
  The importer maps source X/Z to benchmark X/Y, while box yaw remains `-theta`.
  The upstream [3D-FRONT toolbox](https://github.com/3D-FRONT-FUTURE/3D-FRONT-ToolBox/tree/236c5bd5b66d5ab2ef1e9f70dd3da26a08dad50c)
  `Generator/interface.py:quaternion_to_dir` defaults to `[0,0,1]`;
  `Generator/model.py` uses that direction for furniture, not local +X.
- Infinigen commit `fb7991e06580639202a4687937082cb63e931eb0`,
  `core/tagging.py:CANONICAL_TAG_MEANINGS` assigns Front to maximum local X.
  Bed pillows are placed toward low X and foot bedding toward high X in
  `assets/seating/bed.py`; the chair factory rotates its negative-Y-facing seat
  by +90 degrees; `assets/shelves/simple_desk.py` likewise rotates the working
  depth axis by +90 degrees. These checks establish functional, not arbitrary
  longest-box-axis, directions.

## Review policy

Arrows mean head-to-foot for beds, away from the backrest for seating, toward the
working edge for desks, and toward the accessible face for storage/shelves.
Tables, lamps, rugs and small accessories without an established functional
front receive no arrow and are excluded from facing judgments. An unknown
direction for a marked category aborts packet preparation.

Names and colours share one display vocabulary, separate from the frozen pair
matching rules. Bed subtypes become `bed`, factory names such as `simple_desk`
become `desk`, and spelling variants normalize. Real role differences such as
stools versus backed chairs remain visible. Duplicate instance numbers depend on
position, not source asset IDs. Instructions are in the assigned prompt only;
relative volume numbers appear only for the proportions task.

The same cameras, fit rule, typography, colours and legends apply to both sides.
Panels are independently fitted: absolute canvas size cannot compare physical
size between methods. Matching is not exact inventory identity; judgments concern
objects present, not omitted expected furniture. Geometry itself may reveal a
generator's tendencies, so this is method-name blinding, not guaranteed anonymity.

Validation covers quarter turns and oblique yaw, legacy-to-corrected conversion,
identical cross-source renderings of identical annotated geometry, unknown-axis
rejection, label equivalence, unchanged source digests and immutable image hashes.
Prior votes are never transferred to corrected inputs.
