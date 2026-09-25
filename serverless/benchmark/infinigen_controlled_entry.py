"""Run the pinned Infinigen release with a disclosed object-inventory task.

The native Indoors release does not expose an exact primary-object count.  This
entry point extends only its constraint graph: asset generation, placement,
annealing, collision handling and scene construction remain Infinigen's.  The
native profile is benchmarked separately and never passes through this module.
"""
from collections import OrderedDict
import os
from pathlib import Path
import runpy
import sys

# Blender's --python entrypoint does not reliably add this directory to sys.path.
# The inventory contract has no dependency on the backend or its environment.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from infinigen_task import controlled_role_counts

from infinigen.assets import elements, lighting, seating, shelves, tables
from infinigen.core.constraints import constraint_language as cl
from infinigen.core.tags import Semantics
from infinigen_examples import indoor_constraint_examples as constraints
from infinigen_examples.util import constraint_util as cu


_native_home_constraints = constraints.home_constraints


def controlled_home_constraints():
    """Add exact inventory constraints without changing native placement rules."""
    counts = controlled_role_counts('bedroom', int(os.environ.get('SOILIE_INFINIGEN_BEDROOM_COUNT', '6')))
    problem = _native_home_constraints()

    rooms = cl.scene()[{Semantics.Room, -Semantics.Object}]
    objects = cl.scene()[{Semantics.Object, -Semantics.Room}]
    furniture = objects[Semantics.Furniture].related_to(rooms, cu.on_floor)
    wall_furniture = furniture.related_to(rooms, cu.against_wall)
    storage = wall_furniture[Semantics.Storage]
    rugs = objects[elements.RugFactory].related_to(rooms, cu.on_floor)
    side_tables = furniture[Semantics.SideTable].related_to(furniture, cu.side_by_side)
    floor_lamps = (
        objects[Semantics.Lighting][lighting.FloorLampFactory]
        .related_to(rooms, cu.on_floor)
        .related_to(rooms, cu.against_wall)
    )

    bedrooms = rooms[Semantics.Bedroom].excludes(cu.room_types)
    beds = wall_furniture[Semantics.Bed][seating.BedFactory]
    desks = wall_furniture[shelves.SimpleDeskFactory]
    bedroom_condition = bedrooms.all(lambda room: (
        beds.related_to(room).count().equals(1)
        * storage.related_to(room).count().equals(counts['storage'])
        * side_tables.related_to(room)
            .related_to(beds.related_to(room), cu.leftright_leftright)
            .count().equals(1)
        * desks.related_to(room).count().equals(counts['desk'])
        * floor_lamps.related_to(room).count().equals(1)
        * rugs.related_to(room).count().equals(counts['rug'])
    ))

    living_rooms = rooms[Semantics.LivingRoom].excludes(cu.room_types)
    sofas = wall_furniture[seating.SofaFactory]
    tv_stands = wall_furniture[shelves.TVStandFactory]
    coffee_tables = furniture[tables.CoffeeTableFactory]
    living_condition = living_rooms.all(lambda room: (
        sofas.related_to(room).count().equals(1)
        * tv_stands.related_to(room).count().equals(1)
        * storage.related_to(room).count().equals(1)
        * side_tables.related_to(room)
            .related_to(sofas.related_to(room), cu.side_by_side)
            .count().equals(1)
        * coffee_tables.related_to(room).count().equals(1)
        * rugs.related_to(room).count().equals(1)
    ))

    problem.constraints = OrderedDict(problem.constraints)
    problem.score_terms = OrderedDict(problem.score_terms)
    problem.constraints["benchmark_controlled"] = bedroom_condition * living_condition
    problem.score_terms["benchmark_controlled"] = (
        bedrooms.sum(lambda room: (
            side_tables.related_to(room).distance(beds.related_to(room)).minimize(weight=3)
        ))
        + living_rooms.sum(lambda room: (
            sofas.related_to(room).distance(tv_stands.related_to(room)).hinge(2, 3).minimize(weight=3)
            + coffee_tables.related_to(room).distance(sofas.related_to(room)).hinge(0.45, 0.8).minimize(weight=3)
            + cl.focus_score(sofas.related_to(room), tv_stands.related_to(room)).maximize(weight=3)
        ))
    )
    return problem


constraints.home_constraints = controlled_home_constraints
runpy.run_module("infinigen_examples.generate_indoors", run_name="__main__")
