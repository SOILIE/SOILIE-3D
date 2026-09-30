from copy import deepcopy
import hashlib
import math
from pathlib import Path
import unittest

from serverless.benchmark.functional_evidence import reference_catalog as frozen_catalog
from serverless.benchmark.reference_catalog import candidate, categories, coverage, ratios, require_coverage, validate


class ReferenceCatalogTests(unittest.TestCase):
    def test_candidate_does_not_replace_frozen_reference(self):
        catalog = candidate()
        self.assertEqual('draft-not-used-in-collection', catalog['status'])
        self.assertEqual('catalog-volume-examples-v1', frozen_catalog()['version'])
        self.assertEqual(8, len(frozen_catalog()['products']))
        self.assertEqual(46, len(catalog['products']))
        path = Path(__file__).parents[1] / 'study/reference_volumes.json'
        self.assertEqual(catalog['baseSha256'], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_source_units_and_envelope_not_capacity(self):
        products = {p['id']: p for p in candidate()['products']}
        self.assertAlmostEqual(66 * 66 * 122.22 / 1e9, products['crown-standard-12oz']['envelopeM3'])
        self.assertAlmostEqual(3 * 2 * .018, products['stoense-rug']['envelopeM3'])
        self.assertAlmostEqual(math.prod([78.375, 41.375, 36.25]) * .0254**3,
                               products['tarva-twin']['envelopeM3'])

    def test_pair_ratios_are_scale_invariant_and_reciprocal(self):
        catalog = candidate()
        original = ratios(catalog, 'bed', 'nightstand')
        scaled = deepcopy(catalog)
        for p in scaled['products']:
            p['envelopeM3'] *= 1000
        for a, b in zip(original, ratios(scaled, 'bed', 'nightstand')):
            self.assertAlmostEqual(a['ratio'], b['ratio'])
        reverse = {(r['second'], r['first']):r['ratio'] for r in ratios(catalog, 'nightstand', 'bed')}
        for r in original:
            self.assertAlmostEqual(1, r['ratio'] * reverse[r['first'], r['second']])
        self.assertEqual([], ratios(catalog, 'not supplied', 'bed'))

    def test_missing_coverage_is_not_imputed(self):
        scene = {'model':'fixture', 'roomType':'bedroom', 'objects':[
            {'id':'1','label':'double_bed'}, {'id':'2','label':'nightstand'}, {'id':'3','label':'cap'}]}
        result = coverage([scene], candidate())['fixture:bedroom']
        self.assertEqual(3, result['objectPairs'])
        self.assertEqual(1, result['coveredObjectPairs'])
        self.assertEqual({'cap':1}, result['missingCategories'])
        with self.assertRaisesRegex(ValueError, 'cap'):
            require_coverage([scene], candidate())
        scene['objects'].pop()
        require_coverage([scene], candidate())

    def test_invalid_dimensions_sources_duplicates_and_units_fail(self):
        product = candidate()['products'][0]
        for change in ({'dimensions':[1,2]}, {'dimensions':[1,2,0]}, {'dimensions':[1,2,float('nan')]},
                       {'dimensions':[True,2,3]}, {'unit':'oz'}, {'source':'unsourced'}, {'basis':''}):
            with self.assertRaises(ValueError):
                validate([dict(product, **change)])
        with self.assertRaises(ValueError):
            validate([product, product])
        self.assertFalse(set(candidate()['unresolvedCategories']) & categories(candidate()))


if __name__ == '__main__':
    unittest.main()
