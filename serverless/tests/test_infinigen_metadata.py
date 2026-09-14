from copy import deepcopy
import unittest

from serverless.benchmark.infinigen_metadata import ancestor_rooms, asset_label, generated_instances, vertically_supported


def records():
    return {"room-a":{"obj":"Bedroom.meshed","tags":["Semantics(room)","Semantics(bedroom)"],"generator":None,
                      "relations":[{"target_name":"room-b","relation":{"relation_type":"RoomNeighbour"}}]},
            "room-b":{"tags":["Semantics(room)","Semantics(living-room)"],"generator":None,"relations":[]},
            "table":{"obj":"Table.spawn_asset","tags":["FromGenerator(SideTableFactory)","Semantics(object)"],"generator":"factory",
                     "relations":[{"target_name":"room-a","relation":{"parent_tags":["Subpart(support)"]}}]},
            "lamp":{"obj":"Lamp.spawn_asset","tags":["FromGenerator(LampFactory)","Semantics(object)"],"generator":"factory",
                    "relations":[{"target_name":"table","relation":{"parent_tags":["Subpart(support)"]}}]}}


class InfinigenMetadataTests(unittest.TestCase):
    def test_supported_objects_follow_original_room_without_crossing_neighbours(self):
        data = records()
        self.assertEqual({"room-a"},ancestor_rooms(data,"lamp"))
        room, instances = generated_instances(data,"bedroom")
        self.assertEqual("room-a",room)
        self.assertEqual(["table","lamp"],[key for key,_ in instances])
        self.assertEqual("side_table",asset_label(data["table"]))
        self.assertTrue(vertically_supported(data["lamp"]))

    def test_wall_mounts_are_not_misreported_as_floating(self):
        painting = deepcopy(records()["lamp"])
        painting["relations"][0]["relation"]["parent_tags"] = ["Subpart(wall)"]
        self.assertFalse(vertically_supported(painting))

    def test_ambiguous_membership_or_missing_instances_rejected(self):
        data = records()
        data["lamp"]["relations"].append({"target_name":"room-b","relation":{}})
        with self.assertRaises(ValueError):
            generated_instances(data,"bedroom")
        data = records()
        data["lamp"]["generator"] = None
        with self.assertRaises(ValueError):
            generated_instances(data,"bedroom")
        with self.assertRaises(ValueError):
            generated_instances(records(),"living_room")

    def test_unknown_asset_semantics_not_relabelled_as_generic_furniture(self):
        with self.assertRaises(ValueError):
            asset_label({"tags":[]})


if __name__ == "__main__":
    unittest.main()
