import csv
import random
import tempfile
import unittest
from pathlib import Path

from modules import working_combo_schema as schema
from modules import working_combos as combos
from modules.triplet_schema import TRIPLET_COLUMNS, LEGACY_TRIPLET_COLUMNS, validate_triplet_columns

ROOT = Path(__file__).resolve().parents[1] / '.codex/tests/catalogs/working'

class WorkingComboCoreTests(unittest.TestCase):
    def setUp(self):
        self.random_state = random.getstate()

    def tearDown(self):
        random.setstate(self.random_state)

    @classmethod
    def setUpClass(cls):
        ROOT.mkdir(parents=True, exist_ok=True)
        cls.temporary = tempfile.TemporaryDirectory(dir=ROOT)
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.catalog = cls.root / 'catalog.csv'
        cls.triplets = cls.root / 'triplets.csv'
        cls.rows = [tuple(f'item{i}' for i in range(n)) + extra
                    for n in range(3, 9) for extra in ((), ('window',))]
        with cls.catalog.open('w', newline='', encoding='utf-8') as f:
            w = csv.writer(f); w.writerow(schema.WORKING_COMBINATION_FIELDS)
            w.writerows(schema.padded_catalog_row(row) for row in cls.rows)
        with cls.triplets.open('w', newline='', encoding='utf-8') as f:
            w = csv.writer(f); w.writerow(TRIPLET_COLUMNS)
            for row in cls.rows:
                for c in row[2:]: w.writerow((row[0], row[1], c, 1, 1, 1, 1, 1, 1))

    def test_n_three_through_eight_with_optional_window(self):
        self.assertEqual(combos.available_counts(self.catalog), (3,4,5,6,7,8))
        self.assertEqual(list(schema.iter_working_combinations(self.catalog)), self.rows)
        for n in range(3, 9):
            for seed in range(8):
                random.seed(seed)
                result = combos.select_exact(n, filepath=self.catalog, triplet_filepath=self.triplets, allow_duplicates=False)
                self.assertEqual(sum(x not in ('window','opaque_window') for x in result), n)
                self.assertLessEqual(sum(x in ('window','opaque_window') for x in result), 1)

    def test_seed_reproducibility(self):
        def sample():
            random.seed(713)
            return [combos.select_exact(3, filepath=self.catalog, triplet_filepath=self.triplets, allow_duplicates=False) for _ in range(10)]
        self.assertEqual(sample(), sample())

    def test_duplicates_are_instances_and_policy_is_enforced(self):
        self.assertEqual(combos.ordered_anchor_witnesses(('chair','chair','table'), {('chair','chair','table')}), ((0,1,(2,)), (1,0,(2,))))
        self.assertEqual(schema.padded_catalog_row(('chair','chair','table'))[:3], ('chair','chair','table'))
        self.assertFalse(combos.ordered_anchor_witnesses(('chair','chair','table','lamp'), {('chair','chair','table')}))

    def test_bad_headers_rows_and_requests(self):
        for fields in (schema.LEGACY_SIX_COLUMN_FIELDS, schema.LEGACY_TOTAL_COUNT_FIELDS, ('obj0',)*9):
            with self.subTest(fields=fields), self.assertRaises(schema.WorkingCombinationError): schema.validate_working_combination_header(fields)
        for row in (('chair','table'), ('chair','table','lamp','window','opaque_window'), ('Chair','table','lamp'), ('chair','table','../lamp')):
            with self.subTest(row=row), self.assertRaises(schema.WorkingCombinationError): schema.padded_catalog_row(row)
        for count in (True, 2, 9, 3.0):
            with self.subTest(count=count), self.assertRaises(schema.WorkingCombinationError): combos.select_exact(count, filepath=self.catalog, triplet_filepath=self.triplets, allow_duplicates=False)
        with self.assertRaises(ValueError): validate_triplet_columns(LEGACY_TRIPLET_COLUMNS)
        with self.assertRaises(ValueError): validate_triplet_columns(TRIPLET_COLUMNS + ('extra',))

    def test_nontrailing_blank_and_extra_fields_rejected(self):
        path = self.root / 'invalid.csv'
        try:
            for row in (['chair','','lamp','table','','','','',''], ['chair','lamp','table','','','','','','','extra']):
                with path.open('w', newline='', encoding='utf-8') as f:
                    w=csv.writer(f); w.writerow(schema.WORKING_COMBINATION_FIELDS); w.writerow(row)
                with self.assertRaises(schema.WorkingCombinationError): list(schema.iter_working_combinations(path))
        finally: path.unlink(missing_ok=True)
