"""Shared, dependency-free inventory contract for controlled Infinigen tasks."""

CONTROLLED_PROFILES = {'controlled-six-fast', 'controlled-count-fast'}
BEDROOM_COUNT_CYCLE = (3, 4, 5, 6)


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
