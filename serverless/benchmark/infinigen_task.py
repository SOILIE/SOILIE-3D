"""Shared stdlib-only configuration and validation for Infinigen workers.

Kept separate from local campaign orchestration so Blender/Python 3.10 and
the Lambda handler import the same contract without importing newer runtimes.
"""
from collections import Counter
import json
from serverless.benchmark.infinigen_metadata import asset_label, generated_instances, has_tag

CONTROLLED_PROFILES = {'controlled-six-fast', 'controlled-count-fast', 'controlled-inventory-fast'}
BEDROOM_COUNT_CYCLE = (3, 4, 5, 6)
# Keep the old count cycle and default contract frozen: historical campaign
# plans import them and must not acquire a new inventory by resuming old IDs.
LEGACY_INVENTORY_VERSION = 'infinigen-controlled-v1'
INVENTORY_VERSION = 'infinigen-controlled-v2'
INVENTORY_COUNT_CYCLE = (3, 4, 5, 6, 7, 8)
COMMIT = 'fb7991e06580639202a4687937082cb63e931eb0'
PROFILES = CONTROLLED_PROFILES | {'default', 'tutorial-fast', 'matched-furniture-fast'}


def controlled_role_counts(room_type, object_count=6, inventory_version=LEGACY_INVENTORY_VERSION):
    """Nested bedroom inventories retain the bedside relation at every size.

    These are input constraints, not post-generation deletions. Six objects
    reproduces the existing inventory. The explicit v2 contract extends both
    room types to eight, adding a second side table and then a second storage
    instance. Those are native solver objects, never copies of old geometry.
    """
    if inventory_version not in (LEGACY_INVENTORY_VERSION, INVENTORY_VERSION):
        raise ValueError('Unknown controlled inventory version')
    allowed = INVENTORY_COUNT_CYCLE if inventory_version == INVENTORY_VERSION else BEDROOM_COUNT_CYCLE
    if type(object_count) is not int or object_count not in allowed:
        raise ValueError(f'Controlled object count must be an integer in {allowed}')
    if room_type == 'bedroom':
        return {'bed': 1, 'storage': int(object_count >= 4) + int(object_count >= 8),
                'side_table': 1 + int(object_count >= 7),
                'desk': int(object_count >= 5), 'floor_lamp': 1,
                'rug': int(object_count >= 6)}
    if room_type == 'living_room':
        if inventory_version == INVENTORY_VERSION:
            return {'sofa': 1, 'tv_stand': 1,
                    'storage': int(object_count >= 5) + int(object_count >= 8),
                    'side_table': int(object_count >= 4) + int(object_count >= 7),
                    'coffee_table': 1, 'rug': int(object_count >= 6)}
        if object_count == 6:
            return dict.fromkeys(('sofa', 'tv_stand', 'storage', 'side_table', 'coffee_table', 'rug'), 1)
    raise ValueError('Living rooms retain the six-object inventory')


def controlled_roles(records, room_type):
    """Return the role assigned to every generated instance, if exact.

    Infinigen's solver can serialize a scene after its reduced-iteration
    ``fast_solve`` pass without satisfying every count constraint.  A process
    exit code therefore cannot certify the controlled benchmark input by
    itself. Read the solver's own semantic metadata and require the disclosed
    role multiplicities before a checkpoint counts as complete.
    """
    _room_id, instances = generated_instances(records, room_type)
    roles = []
    for _identifier, record in instances:
        label = asset_label(record)
        if room_type == "bedroom":
            if has_tag(record, "bed"):
                role = "bed"
            elif has_tag(record, "storage"):
                role = "storage"
            elif has_tag(record, "side-table"):
                role = "side_table"
            elif label == "simple_desk":
                role = "desk"
            elif label == "floor_lamp":
                role = "floor_lamp"
            elif label == "rug":
                role = "rug"
            else:
                role = None
        else:
            if label == "sofa":
                role = "sofa"
            elif label == "t_v_stand":
                role = "tv_stand"
            elif has_tag(record, "storage"):
                role = "storage"
            elif has_tag(record, "side-table"):
                role = "side_table"
            elif label == "coffee_table":
                role = "coffee_table"
            elif label == "rug":
                role = "rug"
            else:
                role = None
        roles.append(role)
    return roles


def validate_controlled_output(work, room_type, object_count=6,
                               inventory_version=LEGACY_INVENTORY_VERSION):
    state = json.loads((work/"solve_state.json").read_text())
    records = state.get("objs")
    if not isinstance(records, dict):
        raise ValueError("Controlled output has no solver object records")
    roles = controlled_roles(records, room_type)
    expected = Counter({role: count for role, count in controlled_role_counts(
        room_type, object_count, inventory_version).items() if count})
    if len(roles) != object_count or Counter(roles) != expected:
        raise ValueError(f"Expected controlled role multiplicities {dict(expected)}; observed {roles}")
    return roles


def profile_command(profile, room_type, parent):
    """Return official configs and disclosed overrides for one run profile."""
    configs = ["singleroom.gin"]
    overrides = ["compose_indoors.terrain_enabled=False",
                 f"restrict_solving.restrict_parent_rooms=['{parent}']"]
    description = "Default single-room solver and full procedural population"
    if profile in CONTROLLED_PROFILES | {"tutorial-fast", "matched-furniture-fast"}:
        configs.insert(0, "fast_solve.gin")
        overrides.append("restrict_solving.solve_max_rooms=1")
        description = "Official fast_solve single-room profile; reduced solver iterations"
    if profile == "matched-furniture-fast":
        # The comparison targets room-scale furniture. Disabling the small
        # population stage also avoids conflating layout placement with costly
        # stable-pose construction for decorative shelf trinkets. Do not add a
        # primary-category allow-list here: Infinigen's greedy domain
        # intersection can validly yield an empty room when a selected room's
        # constraint graph has no surviving primary choice.
        overrides.append("compose_indoors.solve_small_enabled=False")
        description = ("Official fast_solve single-room profile with its full room-scale furniture "
                       "domain; small decorative-object solving disabled")
    elif profile in CONTROLLED_PROFILES:
        overrides.extend([
            "restrict_solving.consgraph_filters=['benchmark_controlled']",
            "compose_indoors.solve_small_enabled=False",
        ])
        description = ("Controlled six-object task using Infinigen's pinned solver, assets and "
                       "scene construction with a disclosed benchmark constraint graph")
        if profile == 'controlled-count-fast':
            description = ("Controlled 3–6-object bedroom task using Infinigen's pinned solver, assets "
                           "and scene construction; exact inventory recorded per run")
        elif profile == 'controlled-inventory-fast':
            description = ("Controlled 3–8-object bedroom/living-room task using Infinigen's pinned "
                           "solver, assets and scene construction; versioned role multiplicities "
                           "recorded per run")
    return configs, overrides, description
