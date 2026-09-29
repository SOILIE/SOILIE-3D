"""Exact category/count inputs for the pinned Infinigen solver.

Only input constraints change. Native procedural factories, collision solver,
annealing and applicable native soft objectives are retained. Nightstands are
explicit bedside tables, constrained beside a bed, not renamed generic tables.
"""
from collections import OrderedDict
from functools import reduce
import json
import operator
import os
import runpy

from infinigen.assets import seating, shelves, tables
from infinigen.core.constraints import constraint_language as cl
from infinigen.core.tags import Semantics
from infinigen_examples import indoor_constraint_examples as native
from infinigen_examples.util import constraint_util as cu

original=native.home_constraints
task=json.loads(os.environ['SOILIE_INVENTORY_TASK'])


def inventory_constraints():
    problem=original()
    rooms=cl.scene()[{Semantics.Room,-Semantics.Object}]
    objs=cl.scene()[{Semantics.Object,-Semantics.Room}]
    floor=objs[Semantics.Furniture].related_to(rooms,cu.on_floor)
    wall=floor.related_to(rooms,cu.against_wall)
    beds=wall[seating.BedFactory]
    categories={
        'bed':beds, 'sofa':floor[seating.SofaFactory],
        'chair':floor[seating.ChairFactory], 'desk':wall[shelves.SimpleDeskFactory],
        # Restrict storage to a cabinet. The source Storage semantic also
        # includes open shelves, a different category in the neutral rubric.
        'storage':wall[shelves.SingleCabinetFactory],
        'nightstand':floor[tables.SideTableFactory].related_to(beds,cu.leftright_leftright),
        'coffee table':floor[tables.CoffeeTableFactory],
        'dining table':floor[tables.TableDiningFactory],
        'tv stand':wall[shelves.TVStandFactory],
    }
    inventory=task['inventory']
    if not inventory or set(inventory)-set(categories):
        raise ValueError('Unsupported inventory input')
    selected=rooms[Semantics.Bedroom if task['roomType']=='bedroom' else Semantics.LivingRoom].excludes(cu.room_types)
    def in_room(room):
        return reduce(operator.mul,[categories[name].related_to(room).count().equals(count)
                                   for name,count in sorted(inventory.items())])
    # Restrict generation to the declared categories. Other native domains
    # are not added through optional room-composition hard constraints.
    problem.constraints=OrderedDict(benchmark_inventory=selected.all(in_room))
    # These are the release's expressions and weights, not new hand-tuned
    # objectives. Disabled population stages cannot introduce decorative items.
    keys={'storage','sidetable','desk','bedroom','sofa','sofa_positioning','tv','livingroom','dining_chairs'}
    problem.score_terms=OrderedDict(('benchmark_inventory_'+key,value)
        for key,value in problem.score_terms.items() if key in keys)
    return problem


native.home_constraints=inventory_constraints
runpy.run_module('infinigen_examples.generate_indoors',run_name='__main__')
