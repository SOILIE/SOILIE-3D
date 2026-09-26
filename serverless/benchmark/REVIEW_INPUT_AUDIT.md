# Review-input audit and corrected preparation

The recorded AI inputs contained two presentation confounds. This is an input
audit, not a measurement of the direction or magnitude of their effect on votes.

| Recorded comparison | Pairs | Image references with size-specific instruction | References with single/double-bed labels |
| --- | ---: | ---: | ---: |
| SOILIE / LayoutGPT | 240 | 480 / 480 | 119 |
| SOILIE / controlled Infinigen | 240 | 480 / 480 | 0 |

All 883 unique referenced SVGs were checked against their content-addressed
filenames. The original paired PNG supplied to an orientation reviewer was also
inspected: its full-height image contains the size instruction. This was not an
extra caption added only by the website example viewer. The common matching
taxonomy was not applied to display labels; raw labels also determined colours.

The image instruction conflicts with four of the five assigned questions.
Source-specific labels can also affect the size question. Therefore all ten
reviewer slots should be evaluated afresh, not just the non-size reviewers.
Existing responses cannot establish outcomes for the corrected presentation.
They remain immutable audit evidence; do not relabel or reuse them as new votes.
Geometry measurements, generation timings and recorded usage are unaffected.

## Corrected inputs

- Keep exactly the 480 existing pairs and their geometry; do not select by votes.
- Put judging instructions in the assigned prompt only, never in shared images.
- Use one shared category vocabulary and colour rule on both sides. For example,
  single bed, double bed and bed all display as bed. Number duplicate objects by
  position rather than source-specific instance IDs. Raw model labels stay in
  source evidence, not reviewer images.
- Supply the relative-volume table only to proportions reviewers. All other
  dimensions receive the same neutral plan, oblique and bird's-eye views without
  that table. Keep source-defined fronts and independently fitted panels.
- Use new image hashes and a new study version. Persist the dimension-specific
  image selection through assignment, reversed-side repeats, export and examples.
- Keep collection disabled until explicitly authorized. Preparation starts no
  reviewer agents, sessions, paid API calls or new scene generations.

The shared labels are broad roles, not detailed asset descriptions. They remove
naming cues but do not guarantee that a generator is impossible to recognize
from its geometry. Fresh reviews must keep the same room-stratified sample,
balanced sides and independent contexts, without targeting a desired winner.

`python -m serverless.benchmark.review_input_audit --benchmarks <website/benchmarks>`
rechecks the frozen evidence. Adding repeatable `--scene-source <export.json>`
arguments and `--output <new .codex directory>` prepares inactive protocols and
two image variants. It verifies exact scene digests and refuses to overwrite a
finished preparation. The prepared manifest records zero reviewers started.

Validation checked 1,920 image references (two views-of-evidence variants for
each side of 480 pairs): rendered polygon coordinates match the original images,
no size instruction remains, and only the proportions variant contains volume
data. Regression tests cover alias-equivalent labels/colours, unchanged source
geometry, per-dimension assignment and reload behavior.
