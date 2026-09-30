"""Inventory-matched rerun instructions; earlier protocol text stays frozen."""
from serverless.study import functional_rubric_v3 as previous

VERSION = 'functional-use-v4'
STAND_RELATIONSHIP = (
    ' A sofa and a TV stand can establish an intended seating/media relationship even when no TV is shown. '
    'Assess whether the sofa front addresses the stand and whether their placement supports that relationship. '
    'Do not penalize the absent screen, invent its size or height, or claim a blocked screen sightline without a TV. '
    'Apply this rule equally to both rooms; when multiple seating groups exist, identify the relevant one '
    'rather than requiring every seat to face the stand.'
)


def prompt(profile):
    text = previous.prompt(profile)
    if profile == 'proportions':
        text = text.replace('They come from a small single-retailer convenience sample.',
            'They come from a small multi-manufacturer convenience sample, predominantly IKEA furniture. '
            'Every displayed category has at least one sourced envelope example, but category breadth '
            'and sparse sampling still limit interpretation.')
    if profile in ('orientation', 'relationships', 'room_function'):
        text += '\n\n' + STAND_RELATIONSHIP.strip()
    return text


def version(profile):
    return previous.VERSION if prompt(profile) == previous.prompt(profile) else VERSION
