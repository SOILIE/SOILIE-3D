"""Shared stdlib-only configuration and validation for Infinigen workers.

Kept separate from local campaign orchestration so Blender/Python 3.10 and
the Lambda handler import the same contract without importing newer runtimes.
"""
import json
from serverless.benchmark.infinigen_metadata import asset_label, generated_instances, has_tag

CONTROLLED_PROFILES = {'controlled-six-fast', 'controlled-count-fast'}
BEDROOM_COUNT_CYCLE = (3, 4, 5, 6)
COMMIT = 'fb7991e06580639202a4687937082cb63e931eb0'
PROFILES = CONTROLLED_PROFILES | {'default', 'tutorial-fast', 'matched-furniture-fast'}


def controlled_role_counts(room_type, object_count=6):
    """Nested bedroom inventories retain the bedside relation at every size.

    These are input constraints, not post-generation deletions. Six objects
    reproduces the existing inventory. Living rooms keep their six-role task.
    """
    if type(object_count) is not int or object_count not in BEDROOM_COUNT_CYCLE:
        raise ValueError('Controlled object count must be an integer from 3 to 6')
    if room_type == 'bedroom':
        return {'bed': 1, 'storage': int(object_count >= 4), 'side_table': 1,
                'desk': int(object_count >= 5), 'floor_lamp': 1,
                'rug': int(object_count >= 6)}
    if room_type == 'living_room' and object_count == 6:
        return dict.fromkeys(('sofa', 'tv_stand', 'storage', 'side_table', 'coffee_table', 'rug'), 1)
    raise ValueError('Living rooms retain the six-object inventory')


def controlled_roles(records, room_type):
    """Return the role assigned to every generated instance, if exact.

    Infinigen's solver can serialize a scene after its reduced-iteration
    ``fast_solve`` pass without satisfying every count constraint.  A process
    exit code therefore cannot certify the controlled benchmark input by
    itself.  Read the solver's own semantic metadata and require the six
    disclosed roles before a checkpoint counts as complete.
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


def validate_controlled_output(work, room_type, object_count=6):
    state = json.loads((work/"solve_state.json").read_text())
    records = state.get("objs")
    if not isinstance(records, dict):
        raise ValueError("Controlled output has no solver object records")
    roles = controlled_roles(records, room_type)
    expected = {role for role, count in controlled_role_counts(room_type, object_count).items() if count}
    if len(roles) != object_count or set(roles) != expected:
        raise ValueError(f"Expected one instance of each controlled role; observed {roles}")
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
    return configs, overrides, description
