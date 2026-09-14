"""Persistence adapters share immutable create/response semantics."""
from contextlib import closing
import json
import sqlite3
from botocore.exceptions import ClientError


class DynamoStudyStore:
    def __init__(self, client, table):
        self.client, self.table = client, table

    def _get(self, session_id, sk):
        row = self.client.get_item(TableName=self.table, Key={"pk":{"S":"ai-pilot#"+session_id},"sk":{"S":sk}}, ConsistentRead=True).get("Item")
        return json.loads(row["document"]["S"]) if row else None

    def get(self, session_id):
        return self._get(session_id,"meta")

    def _put(self, session_id, sk, document, expiry):
        try:
            self.client.put_item(TableName=self.table, Item={"pk":{"S":"ai-pilot#"+session_id},"sk":{"S":sk},
                "document":{"S":json.dumps(document,separators=(",",":"))},"expiresAt":{"N":str(expiry)},
                "respondentType":{"S":"ai_pilot"}}, ConditionExpression="attribute_not_exists(pk)")
        except ClientError as error:
            if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
        return self._get(session_id,sk)

    def create(self, session_id, document):
        return self._put(session_id,"meta",document,document["expiresAt"])

    def save_response(self, session_id, document):
        session = self.get(session_id)
        return self._put(session_id,"response#"+document["caseId"],document,session["expiresAt"])

    def responses(self, session_id):
        result, cursor = [], None
        while True:
            request = dict(TableName=self.table, KeyConditionExpression="pk = :pk AND begins_with(sk, :prefix)",
                ExpressionAttributeValues={":pk":{"S":"ai-pilot#"+session_id},":prefix":{"S":"response#"}}, ConsistentRead=True)
            if cursor:
                request["ExclusiveStartKey"] = cursor
            response = self.client.query(**request)
            result.extend(json.loads(row["document"]["S"]) for row in response.get("Items",[]))
            cursor = response.get("LastEvaluatedKey")
            if not cursor:
                return result


class SQLiteStudyStore:
    """Local E2E adapter, not a second study protocol or browser-only storage."""
    def __init__(self, path):
        self.path = str(path)
        with closing(self.connect()) as db:
            db.execute("CREATE TABLE IF NOT EXISTS records (session TEXT, key TEXT, document TEXT, PRIMARY KEY(session,key))")
            db.commit()

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def get(self, session_id):
        with closing(self.connect()) as db:
            row = db.execute("SELECT document FROM records WHERE session=? AND key='meta'", (session_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def create(self, session_id, document):
        with closing(self.connect()) as db:
            db.execute("INSERT OR IGNORE INTO records VALUES (?, 'meta', ?)", (session_id,json.dumps(document)))
            db.commit()

    def save_response(self, session_id, document):
        with closing(self.connect()) as db:
            db.execute("INSERT OR IGNORE INTO records VALUES (?, ?, ?)", (session_id,document["caseId"],json.dumps(document)))
            row = db.execute("SELECT document FROM records WHERE session=? AND key=?", (session_id,document["caseId"])).fetchone()
            db.commit()
        return json.loads(row[0])

    def responses(self, session_id):
        with closing(self.connect()) as db:
            rows = db.execute("SELECT document FROM records WHERE session=? AND key != 'meta' ORDER BY key", (session_id,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def sessions(self):
        with closing(self.connect()) as db:
            rows = db.execute("SELECT document FROM records WHERE key='meta' ORDER BY session").fetchall()
        return [json.loads(row[0]) for row in rows]
