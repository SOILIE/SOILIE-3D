"""Receipt coverage cannot be inferred from the size of a matched review set."""
from copy import deepcopy
import unittest

from serverless.cloud_benchmark.complete_coverage import api_records, update_contacts


class CompleteCoverageTests(unittest.TestCase):
    def fixture(self):
        usage={'prompt_tokens':100,'completion_tokens':20}
        attempt={'id':'one','status':'complete','geometryStatus':'complete','unparsedLines':0,
                 'usage':usage,'wallSeconds':3,'requestedObjects':4}
        scene={'id':'layoutgpt-one','roomType':'bedroom',
               'provenance':{'requestSha256':'request','responseSha256':'response'}}
        export={'complete':False,'reservedUncertainUsd':0,'attempts':[attempt],
                'variant':'shared-inventory-gpt4-v1','rows':[{'scene':scene}]}
        ledger={'entries':{'one':{'status':'complete','usage':usage,'wallSeconds':3,
                                  'requestSha256':'request','responseSha256':'response'}}}
        return export,ledger

    def test_intentionally_unrun_plan_entries_are_not_failed_api_calls(self):
        export,ledger=self.fixture()
        rows=api_records(export,ledger)
        self.assertEqual(1,len(rows))
        self.assertEqual('shared-inventory-gpt4-v1',rows[0]['condition'])
        self.assertNotIn('entries',rows[0])

    def test_missing_or_uncertain_calls_block_publication(self):
        export,ledger=self.fixture()
        ledger['entries']['unaccounted']={'status':'uncertain'}
        with self.assertRaises(ValueError):api_records(export,ledger)
        export,ledger=self.fixture(); export['reservedUncertainUsd']=.1
        with self.assertRaises(ValueError):api_records(export,ledger)

    def test_duplicate_or_changed_receipts_are_rejected(self):
        export,ledger=self.fixture(); export['attempts'].append(deepcopy(export['attempts'][0]))
        with self.assertRaises(ValueError):api_records(export,ledger)
        export,ledger=self.fixture(); ledger['entries']['one']['responseSha256']='changed'
        with self.assertRaises(ValueError):api_records(export,ledger)

    def test_contact_observation_cannot_move_furniture_or_change_ids(self):
        scene={'objects':[{'id':'chair','corners':[[1,2,3]]}]}
        record={'furnitureModified':False,'objects':[{'id':'chair','support':{'gapM':0}}]}
        updated=update_contacts(deepcopy(scene),record)
        self.assertEqual(scene['objects'][0]['corners'],updated['objects'][0]['corners'])
        record['objects'][0]['id']='other'
        with self.assertRaises(ValueError):update_contacts(deepcopy(scene),record)
        record['furnitureModified']=True
        with self.assertRaises(ValueError):update_contacts(deepcopy(scene),record)
