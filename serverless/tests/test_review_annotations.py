from copy import deepcopy
import math
import unittest

from serverless.benchmark.import_layoutgpt import normalize
from serverless.benchmark.review_annotations import (
    ALIASES, LAYOUTGPT_FRONT, LEGACY_LAYOUTGPT_FRONT, functional_front,
    layoutgpt_front, presentation_label,
)
from serverless.benchmark.stimuli import diagram
from serverless.tests.test_benchmark_import import LayoutImportTests
from serverless.study.export_pilot import matching_description


class AnnotationTests(unittest.TestCase):
    def test_frozen_matching_thresholds_survive_presentation_only_rebuild(self):
        protocol = {'sampling': {'sourceSampling': [
            {'minimumSemanticSimilarity': 1/3, 'maximumFurnitureDensityDifference': 1},
            {'minimumSemanticSimilarity': 1/3, 'maximumFurnitureDensityDifference': 1}]}}
        description = matching_description(protocol)
        self.assertIn('33.3%', description)
        self.assertIn('at most 1;', description)
        self.assertNotIn('40.0%', description)

    def test_layoutgpt_front_is_source_positive_z_not_box_positive_x(self):
        for angle, expected in ((0, [0, 1]), (90, [1, 0]), (180, [0, -1]), (270, [-1, 0])):
            with self.subTest(angle=angle):
                for actual, target in zip(layoutgpt_front(angle), expected):
                    self.assertAlmostEqual(actual, target)
                scene = normalize(LayoutImportTests().layout(angle), 'bedroom', 0, 'fixture')
                self.assertEqual(scene['objects'][0]['frontConvention'], LAYOUTGPT_FRONT)
                self.assertEqual(functional_front(scene, scene['objects'][0]), layoutgpt_front(angle))

    def test_legacy_heading_conversion_matches_new_import_without_mutation(self):
        for angle in (0, 30, 90, 180, 270, -45):
            theta = math.radians(angle)
            item = {'id': 'bed', 'label': 'double_bed', 'frontDirection': [math.cos(theta), -math.sin(theta)],
                    'frontConvention': LEGACY_LAYOUTGPT_FRONT, 'corners': [[1, 2, 3]]}
            original = deepcopy(item)
            self.assertEqual(functional_front({'model': 'layoutgpt'}, item), layoutgpt_front(angle))
            self.assertEqual(item, original)

    def test_all_sources_show_the_same_functional_front_and_geometry(self):
        scene = normalize(LayoutImportTests().layout(30), 'bedroom', 0, 'fixture')
        expected = diagram(scene)
        for model, convention in (('soilie', 'V4 asset-corrected local +X'),
                                  ('infinigen_controlled', 'Infinigen canonical Subpart.Front local +X axis')):
            other = deepcopy(scene)
            other['model'] = model
            other['objects'][0]['frontConvention'] = convention
            self.assertEqual(diagram(other), expected)

    def test_display_aliases_are_idempotent_and_cover_factory_spelling_cues(self):
        for source, target in ALIASES.items():
            self.assertEqual(presentation_label(source.replace(' ', '_')), target)
            self.assertEqual(presentation_label(target), target)
        for source in ('bed', 'single bed', 'double_bed', 'kids-bed'):
            self.assertEqual(presentation_label(source), 'bed')
        self.assertEqual(presentation_label('simple_desk'), 'desk')
        self.assertNotEqual(presentation_label('stool'), presentation_label('chair'))

    def test_unasserted_front_does_not_invent_direction_unknown_direction_fails(self):
        for label in ('table', 'coffee_table', 'lamp', 'floor_lamp', 'rug', 'pillow', 'stool'):
            self.assertIsNone(functional_front({'model': 'soilie'}, {'label': label}))
        with self.assertRaises(ValueError):
            functional_front({'model': 'layoutgpt'}, {'id': 'bed', 'label': 'bed', 'frontDirection': [1, 0]})


if __name__ == '__main__':
    unittest.main()
