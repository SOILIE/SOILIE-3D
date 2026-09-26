"""Researcher-approved dimension rubrics; original protocols remain immutable."""

VERSION = "functional-use-v1"
PROFILES = ("orientation", "proportions", "relationships", "access")
COMMON = (
    "Judge only the assigned dimension from the attached anonymous room pair. "
    "Prefer LEFT or RIGHT only for a clear, meaningful advantage on that dimension. "
    "Choose TIE when both arrangements are plausible or their important trade-offs are balanced; explain the balance. "
    "Do not invent a difference to force a winner. Confidence alone is not a tiebreaker. "
    "Inventories are fixed and may differ. Judge what is present; never penalize missing counterparts, "
    "reward broader inventories, or prefer a room merely for containing conventional object combinations. "
    "Do not use an unrelated quality as a general penalty. Count a visible obstruction only when it impairs "
    "the specific function or relationship being assessed, and identify that interference. "
    "Use the labelled plan, oblique and bird's-eye views. Front arrows are authoritative; do not redefine them "
    "from nearby pillows or infer fronts for unmarked or rotationally symmetric objects. "
    "The panels are fitted independently: screen size cannot establish absolute physical scale. "
    "Do not infer hidden mesh detail, method identity, an unshown person, or a television from a TV stand alone. "
    "Ordinary chairs tucked under desks or tables are not defects merely because they are tucked in. "
    "A coffee table between a sofa and TV is not an obstruction merely because it lies between them. "
    "Use only the supplied evidence, not external sources, other judgments, or numerical quality scores. "
    "If evidence cannot distinguish the rooms, choose tie and state the uncertainty. "
    "Separately mark which side has a visible problem on the assigned dimension: left, right, both, neither, or uncertain. "
    "Give confidence 1 (very uncertain) through 5 (very confident) and a brief evidence-based explanation. "
)
DIMENSIONS = {
    "orientation": (
        "Assess whether marked functional fronts face sensibly for each object's role and the relevant objects present. "
        "Bed arrows run head-to-foot, seating arrows point away from the backrest, desk arrows indicate the working edge, "
        "and storage arrows indicate its accessible face. Relevant functional relationships take priority over a generic "
        "face-the-centre rule. Bed heads need not touch a wall. Correct direction with insufficient approach distance "
        "is not itself a facing error; assess access separately. An accessible face turned into a wall can be a facing "
        "error: explain the direction problem rather than merely citing crowding. Do not penalize a normally tucked chair."
    ),
    "proportions": (
        "Assess believable relative oriented bounding-box volumes among the objects present, using the supplied table. "
        "Compare pairwise ratios within each room, not the magnitudes of independently normalized numbers across rooms. "
        "For volumes a and b the ratio is a/b; multiplying every volume in a room by the same positive constant changes "
        "nothing. A tiny normalization object naturally produces large numbers for furniture. Enclosing boxes contain "
        "empty space and are not solid material volumes. Allow ordinary furniture variation; a coffee-table box larger "
        "than a sofa box is not automatically implausible. Identify a specifically implausible pair and its ratio if "
        "one drives your preference. Do not impose a fixed category-size hierarchy. Shape, aspect ratio and absolute "
        "physical scale are outside this question. Do not compare whether the generators agree."
    ),
    "relationships": (
        "Assess useful distances, grouping and spatial relationships among the objects present. "
        "Separate seating, dining or working groups can be sensible; neither compactness nor separation is inherently "
        "better. Storage or a floor lamp need not be beside the main furniture without a functional reason. "
        "Count congestion only when it visibly disrupts an identified interaction, such as using a chair at its desk. "
        "Do not add a general collision penalty. A coffee table between a correctly aligned sofa and TV is normal "
        "unless it actually obstructs their use or viewing; merely lying between them is not a defect. "
        "A chair can be tucked under its table when stored and pulled or turned locally for use without moving "
        "other furniture. Assess the arrangement of present relationships, not how many conventional pairings exist."
    ),
    "access": (
        "Assess reachable, usable areas and circulation to all functional furniture, considering severity and the "
        "proportion affected rather than raw problem counts or inventory breadth. Chairs may be tucked in: check "
        "whether each can be reached and locally pulled or turned into a usable position at its table without moving "
        "other furniture. Do not assume chairs must remain in their stored position or can be carried elsewhere. "
        "A bed needs access along at least one complete edge other than its head. Foot-end-only access is acceptable; "
        "a complete accessible long side is preferable, all else equal. Sofas need approach access along their full "
        "front, and nightstands need front access. Tall objects must not block an actual TV's viewing path; a TV stand "
        "alone does not establish a TV. A normal coffee table in front of seating need not obstruct access when a "
        "usable approach strip remains. Decorative items are not independent destinations requiring person-sized "
        "approaches. An arrow marks facing, not necessarily the only entrance. Do not infer exact clearance dimensions "
        "or certify accessibility from independently fitted box views."
    ),
}


def prompt(profile):
    if profile not in DIMENSIONS:
        raise ValueError("Clarified rubric applies only to the four revised dimensions")
    return COMMON + "Assigned dimension: " + profile + ". " + DIMENSIONS[profile]
