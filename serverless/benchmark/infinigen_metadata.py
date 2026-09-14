"""Interpret official solver metadata without inferring rooms from box positions."""
import re


def has_tag(record, tag):
    return f"Semantics({tag})" in record["tags"]


def ancestor_rooms(records, identifier):
    """Follow original object/support relations; never cross room-neighbour edges."""
    pending, visited, rooms = [identifier], set(), set()
    while pending:
        key = pending.pop()
        if key in visited:
            continue
        visited.add(key)
        if key not in records:
            raise ValueError(f"Unknown relation target {key}")
        record = records[key]
        if has_tag(record, "room"):
            rooms.add(key)
            continue
        pending.extend(relation["target_name"] for relation in record["relations"])
    return rooms


def generated_instances(records, room_type):
    instances, rooms = [], set()
    for identifier, record in records.items():
        if record.get("generator") is None:
            if has_tag(record, "object"):
                raise ValueError(f"Furniture instance {identifier} has no generator metadata")
            continue
        if has_tag(record, "cutter") or has_tag(record, "room"):
            continue
        parents = ancestor_rooms(records, identifier)
        if len(parents) != 1:
            raise ValueError(f"Generated instance {identifier} has ambiguous or absent room membership")
        rooms.update(parents)
        instances.append((identifier, record))
    if len(rooms) != 1 or not instances:
        raise ValueError("Expected exactly one populated room from the single-room task")
    room_id = rooms.pop()
    expected = {"bedroom": "bedroom", "living_room": "living-room"}[room_type]
    if not has_tag(records[room_id], expected):
        raise ValueError("Generated room metadata differs from the requested room type")
    return room_id, instances


def asset_label(record):
    factories = [match.group(1) for tag in record["tags"]
                 if (match := re.fullmatch(r"FromGenerator\((\w+)Factory\)", tag))]
    if len(factories) != 1:
        raise ValueError("Expected one explicit generator class per semantic instance")
    return re.sub(r"(?<!^)(?=[A-Z])", "_", factories[0]).lower()


def vertically_supported(record):
    """Wall and ceiling mounts need a different diagnostic than a floor gap."""
    if has_tag(record, "wall-decoration") or has_tag(record, "ceiling-light"):
        return False
    parents = [tag for relation in record["relations"]
               for tag in relation.get("relation", {}).get("parent_tags", [])]
    return "Subpart(support)" in parents or not any(tag in parents for tag in ("Subpart(wall)", "Subpart(ceiling)"))
