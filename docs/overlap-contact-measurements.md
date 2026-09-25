# Enclosing boxes and final surface contact

The placement pipeline separates object boxes before settling furniture onto
actual supporting surfaces. Final enclosing-box overlap is therefore not an
invariant of the completed model. A pillow resting on a mattress can lie inside
the box extending to the bed's headboard; an object below a desk can occupy
empty box volume. Reapplying box separation would undo legitimate contact.

The frozen four-condition cohort has 10,000 scenes. Its enclosing-box mean is
22.74610179% before separation, approximately 0% immediately after separation,
and 1.25587178% after final contact. 679 rooms have positive final box overlap.
Of their 875 recorded intersecting box pairs, 834 also have a recorded direct
support relationship. Neither the pairs nor their percentages are removed.

The recorded mesh diagnostics detect no intersections under their documented
tests. This is not a claim that every asset defines a closed solid: open mesh
surfaces cannot establish an enclosed volume or exclude containment inside an
undefined interior. The largest box-score scene and four high-overlap examples
without a direct support relationship were reconstructed with saved transforms
and mesh-bound parity checks, then their mesh diagnostics were recalculated:

- `soilie-bedroom-20410463`: bed with bag and three pillows, 80.16599691% box
  score. The smaller objects contact the mattress below the headboard.
- `soilie-bedroom-20970777`, `soilie-bedroom-20405478`,
  `soilie-bedroom-20979750`: small objects beneath a desk, with floor contact.
- `soilie-bedroom-22752416`: telephone within the bed's enclosing space.

All five recalculated diagnostics completed with no detected intersection.
No placement change, regeneration, timing substitution or reviewer rerun follows
from the box score alone. Scene and review hashes remain frozen. A newly detected
mesh crossing would require a separate repair and affected-stimulus review.

The surface-contact charts use all available room measurements, including
zeroes. SOILIE has 5,000 measured rooms per room type for floor penetration;
object-support gaps apply only to 964 bedrooms and 954 living rooms containing
that contact category. Coincident points do not mean missing observations.
LayoutGPT's box-only predictions cannot supply real surface contacts. Its
clearance metric can be computed after source-metadata scale recovery, which
is documented in `controlled-comparison-campaigns.md`.
