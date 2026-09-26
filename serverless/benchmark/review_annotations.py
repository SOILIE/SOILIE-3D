"""Presentation-only names and functional fronts; never alter scene geometry.

The coordinate adapters are explicit, not inferred from the box's longest edge
or from which direction would make a layout look better. See FRONT_CONVENTIONS.md.
"""
import math


PRESENTATION_VERSION = 'functional-fronts-neutral-labels-v3'
LAYOUTGPT_FRONT = '3D-FRONT local +Z mapped to benchmark XY'
LEGACY_LAYOUTGPT_FRONT = 'released local +X orientation heading'

# Display vocabulary is deliberately independent of the frozen matching policy.
# Aliases remove spelling/factory cues, but keep functionally different classes
# (a stool is not a backed chair; a ceiling light is not a floor lamp) distinct.
ALIASES = {
    'single bed': 'bed', 'double bed': 'bed', 'kids bed': 'bed',
    'night stand': 'nightstand', 'corner side table': 'side table',
    'round end table': 'side table', 'sidetable desk': 'side table',
    'simple desk': 'desk', 'dressing table': 'vanity',
    'wardrobe': 'storage', 'closet': 'storage', 'cupboard': 'storage',
    'cabinet': 'storage', 'children cabinet': 'storage', 'dresser': 'storage',
    'wine cabinet': 'storage', 'single cabinet': 'storage', 'kitchen cabinet': 'storage',
    'bookshelf': 'shelf', 'cell shelf': 'shelf', 'large shelf': 'shelf',
    'simple bookcase': 'shelf', 't v stand': 'tv stand',
    'dining chair': 'chair', 'dressing chair': 'chair', 'office chair': 'chair',
    'sofa chair': 'chair', 'armchair': 'chair', 'lounge chair': 'chair',
    'multi seat sofa': 'sofa', 'l shaped sofa': 'sofa', 'loveseat sofa': 'sofa',
    'ceiling lamp': 'ceiling light', 'pendant lamp': 'ceiling light',
    'large plant container': 'plant', 'bin': 'waste bin', 'trash can': 'waste bin',
}

# Only categories whose functional front is established by source conventions.
# Small accessories and freely usable tables have no asserted functional front.
FRONT_MEANINGS = {
    'bed': 'head to foot', 'desk': 'toward the working/user edge',
    'vanity': 'toward the working/user edge', 'chair': 'away from the seat back',
    'sofa': 'away from the backrest', 'storage': 'toward the accessible face',
    'shelf': 'toward the accessible face', 'tv stand': 'toward the accessible face',
    'laptop': 'toward the keyboard/user edge', 'tv': 'out from the screen',
}


def presentation_label(label):
    name = ' '.join(label.lower().replace('_', ' ').replace('-', ' ').split())
    return ALIASES.get(name, name)


def unit_xy(values):
    if len(values) != 2 or not all(math.isfinite(float(v)) for v in values):
        raise ValueError('Front direction must be a finite XY vector')
    x, y = map(float, values)
    length = math.hypot(x, y)
    if length <= 1e-9:
        raise ValueError('Front direction has no horizontal component')
    return [x / length, y / length]


def layoutgpt_front(orientation_degrees):
    """ATISS row-vector +Z @ R, then source X/Z -> benchmark X/Y."""
    theta = math.radians(float(orientation_degrees))
    return unit_xy([math.sin(theta), math.cos(theta)])


def functional_front(scene, item):
    """Read a verified semantic front, or None where this task asserts none.

    Frozen legacy outputs remain immutable. Their +X heading can be converted
    exactly to +Z-in-XZ without moving a vertex or changing a source transform.
    Unknown conventions fail closed instead of silently assuming an axis.
    """
    if presentation_label(item['label']) not in FRONT_MEANINGS:
        return None
    front = item.get('frontDirection')
    convention = item.get('frontConvention')
    if front is None:
        raise ValueError(f"Missing functional-front evidence for {item.get('id')}")
    x, y = unit_xy(front)
    if convention == LEGACY_LAYOUTGPT_FRONT and scene['model'] == 'layoutgpt':
        return [-y, x]
    if convention == LAYOUTGPT_FRONT and scene['model'] == 'layoutgpt':
        return [x, y]
    if convention == 'V4 asset-corrected local +X' and scene['model'] == 'soilie':
        return [x, y]
    if convention == 'Infinigen canonical Subpart.Front local +X axis' and scene['model'] in ('infinigen', 'infinigen_controlled'):
        return [x, y]
    # Explicit convention for synthetic geometry tests, not accepted in packets.
    if convention == 'fixture functional front' and scene.get('fixture') is True:
        return [x, y]
    raise ValueError(f"Unverified front convention for {scene.get('id')}/{item.get('id')}: {convention}")
