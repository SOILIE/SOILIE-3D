"""Synthetic operational tests. No API calls or fabricated evaluation votes."""
from copy import deepcopy
import unittest
from unittest.mock import Mock, patch

from serverless.arbutus.comparison_archive import copy_verified, folder
from serverless.benchmark.functional_evidence import volume_examples
from serverless.benchmark.reference_catalog import candidate
from serverless.study import functional_rubric_v3
from serverless.study import functional_rubric_v4
from serverless.cloud_benchmark.retain_identical_reviews import fingerprint


class InventoryReviewTests(unittest.TestCase):
    def test_reference_selection_preserves_historical_catalog(self):
        objects=[{'id':'1','category':'book'},{'id':'2','category':'bed'}]
        self.assertEqual([],volume_examples(objects)[0]['catalogRatios'])
        compiled=candidate()
        values=volume_examples(objects,compiled)[0]['catalogRatios']
        self.assertTrue(values)
        self.assertTrue(all(r['ratio']>0 for r in values))
        scaled=deepcopy(compiled)
        for p in scaled['products']: p['envelopeM3']*=1000
        self.assertEqual(values,volume_examples(objects,scaled)[0]['catalogRatios'])

    def test_archive_only_uses_scoped_public_prefixes(self):
        client=Mock()
        for source,destination in [('secret/credentials.json','files/outputs/website-comparisons/a'),
                                   ('files/outputs/a','files/other/a')]:
            with self.assertRaises(ValueError):
                copy_verified(client,{'key':source},destination)
        client.head_object.assert_not_called()
        with self.assertRaises(ValueError):
            folder({'model':'soilie','roomType':'bedroom','id':'../escape'})

    def test_archive_existing_destination_requires_checksum_verification(self):
        client=Mock()
        artifact={'key':'files/outputs/old/room','sha256':'a'*64,'bytes':12}
        destination='files/outputs/website-comparisons/room'
        with patch('serverless.arbutus.comparison_archive.verify_remote',side_effect=ValueError('changed')):
            with self.assertRaisesRegex(ValueError,'changed'):
                copy_verified(client,artifact,destination)
        client.copy_object.assert_not_called()

    def test_frozen_v3_prompt_is_not_rewritten(self):
        self.assertIn('single-retailer',functional_rubric_v3.prompt('proportions'))

    def test_stand_relationship_does_not_invent_a_screen(self):
        for profile in ('orientation','relationships','room_function'):
            text=functional_rubric_v4.prompt(profile)
            self.assertIn('even when no TV is shown',text)
            self.assertIn('Do not penalize the absent screen',text)
            self.assertIn('without a TV',text)
            self.assertEqual('functional-use-v4',functional_rubric_v4.version(profile))
        self.assertEqual(functional_rubric_v3.prompt('access'),functional_rubric_v4.prompt('access'))
        self.assertNotIn('single-retailer',functional_rubric_v4.prompt('proportions'))

    def test_retention_requires_every_delivered_input_to_match(self):
        protocol={'reviewers':[{'reviewerId':'old','model':'gpt-5.6-sol',
                  'reasoningEffort':'xhigh','promptHash':'prompt','systemPromptHash':'system'}],
                  'outputSchema':{'type':'object'}}
        row={'reviewerId':'old','profile':'access','deliveryPromptHash':'delivery','evidenceSha256':'evidence'}
        packet={'imageSha256':'image'}
        original=fingerprint(protocol,row,packet)
        for field in ('model','reasoningEffort','promptHash','systemPromptHash'):
            changed=deepcopy(protocol)
            changed['reviewers'][0][field]='changed'
            self.assertNotEqual(original,fingerprint(changed,row,packet))
        for field in ('profile','deliveryPromptHash','evidenceSha256'):
            self.assertNotEqual(original,fingerprint(protocol,{**row,field:'changed'},packet))
        self.assertNotEqual(original,fingerprint(protocol,row,{'imageSha256':'swapped'}))
        self.assertNotEqual(original,fingerprint({**protocol,'outputSchema':{}},row,packet))
        # Identity and the answer itself cannot influence eligibility.
        for answer in ('left','right','tie'):
            self.assertEqual(original,fingerprint(protocol,{**row,'judgement':answer},packet))
        renamed=deepcopy(protocol)
        renamed['reviewers'][0]['reviewerId']='new'
        self.assertEqual(original,fingerprint(renamed,{**row,'reviewerId':'new'},packet))


if __name__=='__main__': unittest.main()
