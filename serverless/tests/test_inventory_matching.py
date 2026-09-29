"""Quality-independent pair feasibility; fixtures never read real responses."""
from collections import Counter
from copy import deepcopy
import unittest

from serverless.benchmark.inventory_matching import match, signature, substitutions


def row(name, labels, density=.2, room='living_room'):
    return {'scene': {'id': name, 'roomType': room,
                      'objects': [{'id': str(i), 'label': label} for i, label in enumerate(labels)]},
            'metrics': {'furnitureDensity': density}}


class InventoryMatchingTests(unittest.TestCase):
    def test_duplicate_instances_are_not_sets(self):
        self.assertEqual(1, substitutions(Counter(['chair', 'chair', 'sofa']),
                                         Counter(['chair', 'table', 'sofa'])))
        self.assertIsNone(substitutions(Counter(['chair']), Counter(['chair', 'chair'])))

    def test_names_do_not_merge_distinct_functional_categories(self):
        self.assertEqual(Counter({'bed': 2, 'nightstand': 1}),
                         signature(row('a', ['single_bed', 'double bed', 'night stand'])['scene']))
        for labels in (['floor lamp', 'ceiling light'], ['coffee table', 'dining table'],
                       ['chair', 'stool'], ['nightstand', 'side table']):
            self.assertEqual(2, len(signature(row('a', labels)['scene'])))

    def test_architecture_is_excluded_not_small_furniture(self):
        self.assertEqual(Counter({'pillow': 1}), signature(row('a', ['wall', 'pillow'])['scene']))

    def test_bedside_equivalence_is_explicit_sensitivity_only(self):
        a = row('a', ['bed', 'nightstand', 'lamp'], room='bedroom')
        b = row('b', ['bed', 'side table', 'floor lamp'], room='bedroom')
        self.assertEqual([], match([a], [b]))
        result = match([a], [b], category_aliases={'nightstand': 'side table'})
        self.assertEqual(1, result[0]['substitutions'])
        self.assertEqual('nightstand', a['scene']['objects'][1]['label'])

    def test_rooms_counts_anchors_and_density_are_required(self):
        a = row('a', ['sofa', 'table', 'chair'])
        for b in (row('b', ['sofa', 'table']), row('b', ['bed', 'table', 'chair']),
                  row('b', ['sofa', 'table', 'chair'], room='bedroom'),
                  row('b', ['sofa', 'table', 'chair'], density=.6)):
            self.assertEqual([], match([a], [b]))
        self.assertEqual(1, len(match([a], [row('b', ['sofa', 'table', 'chair'], .6)], density_caliper=None)))

    def test_maximum_cardinality_beats_greedy_retention(self):
        # b1 can use a1/a2; b2 only a1. Retaining a1-b1 would strand b2.
        a = [row('a1', ['sofa', 'table', 'chair']), row('a2', ['sofa', 'table', 'lamp'])]
        b = [row('b1', ['sofa', 'table', 'chair']), row('b2', ['sofa', 'chair', 'chair'])]
        result = match(a, b, [('a1', 'b1')])
        self.assertEqual({('a1', 'b2'), ('a2', 'b1')},
                         {(r['soilieScene'], r['baselineScene']) for r in result})

    def test_retention_precedes_extra_exactness(self):
        a = [row('a1', ['sofa', 'table', 'chair']), row('a2', ['sofa', 'table', 'lamp'])]
        b = [row('b1', ['sofa', 'table', 'chair'])]
        result = match(a, b, [('a2', 'b1')])
        self.assertEqual('a2', result[0]['soilieScene'])
        self.assertEqual(1, result[0]['substitutions'])
        self.assertEqual('a1', match(a, b, maximum_substitutions=0)[0]['soilieScene'])

    def test_order_quality_fields_and_callers_are_untouched(self):
        a = [row('a1', ['sofa', 'table']), row('a2', ['sofa', 'table'])]
        b = [row('b1', ['sofa', 'table']), row('b2', ['sofa', 'table'])]
        before = deepcopy((a, b))
        expected = match(a, b)
        self.assertEqual(before, (a, b))
        self.assertEqual(expected, match(a[::-1], b[::-1]))
        a[0]['metrics']['overlap'] = .99
        a[1]['preference'] = 'best'
        self.assertEqual(expected, match(a, b))

    def test_missing_density_and_duplicate_ids_fail_safely(self):
        a = row('a', ['sofa'])
        b = row('b', ['sofa'], None)
        self.assertEqual([], match([a], [b]))
        with self.assertRaises(ValueError):
            match([a, a], [b])
        with self.assertRaises(ValueError):
            match([a], [b], maximum_substitutions=2)


if __name__ == '__main__':
    unittest.main()
