"""Dynamo contracts are stubbed: tests never use account credentials or AWS."""
import json
import unittest

import boto3
from botocore.stub import Stubber

from serverless.study.store import DynamoStudyStore


class DynamoStudyTests(unittest.TestCase):
    def setUp(self):
        self.client = boto3.client("dynamodb",region_name="ca-central-1",
                                   aws_access_key_id="test",aws_secret_access_key="test")
        self.stub = Stubber(self.client)
        self.stub.activate()
        self.store = DynamoStudyStore(self.client,"test-jobs")

    def tearDown(self):
        self.stub.assert_no_pending_responses()
        self.stub.deactivate()
        self.client.close()

    def test_conditional_create_cannot_replace_existing_session(self):
        original = {"sessionId":"s1","expiresAt":123,"respondentType":"ai_pilot"}
        proposed = dict(original,expiresAt=999)
        expected = {"TableName":"test-jobs","Item":{"pk":{"S":"ai-pilot#s1"},"sk":{"S":"meta"},
                    "document":{"S":json.dumps(proposed,separators=(",",":"))},"expiresAt":{"N":"999"},
                    "respondentType":{"S":"ai_pilot"}},"ConditionExpression":"attribute_not_exists(pk)"}
        self.stub.add_client_error("put_item",service_error_code="ConditionalCheckFailedException",expected_params=expected)
        self.stub.add_response("get_item",{"Item":{"document":{"S":json.dumps(original)}}},
                               {"TableName":"test-jobs","Key":{"pk":{"S":"ai-pilot#s1"},"sk":{"S":"meta"}},"ConsistentRead":True})
        self.assertEqual(original,self.store.create("s1",proposed))

    def test_response_query_reads_every_page_consistently(self):
        params = {"TableName":"test-jobs","KeyConditionExpression":"pk = :pk AND begins_with(sk, :prefix)",
                  "ExpressionAttributeValues":{":pk":{"S":"ai-pilot#s1"},":prefix":{"S":"response#"}},"ConsistentRead":True}
        cursor = {"pk":{"S":"ai-pilot#s1"},"sk":{"S":"response#first"}}
        self.stub.add_response("query",{"Items":[{"document":{"S":"{\"caseId\":\"first\"}"}}],"LastEvaluatedKey":cursor},params)
        self.stub.add_response("query",{"Items":[{"document":{"S":"{\"caseId\":\"second\"}"}}]},dict(params,ExclusiveStartKey=cursor))
        self.assertEqual([{"caseId":"first"},{"caseId":"second"}],self.store.responses("s1"))

    def test_service_errors_are_not_hidden_as_duplicate_writes(self):
        self.stub.add_client_error("put_item",service_error_code="ProvisionedThroughputExceededException")
        with self.assertRaises(self.client.exceptions.ProvisionedThroughputExceededException):
            self.store.create("s1",{"expiresAt":123})


if __name__ == "__main__":
    unittest.main()
