import unittest
from modules.object_identity import encode_collector_object_label, decode_collector_object_label, ObjectIdentityError

class ObjectIdentityCoreTests(unittest.TestCase):
    def test_unicode_delimiters_and_whitespace_round_trip(self):
        encoded = encode_collector_object_label('  café chair  ', ' a,b:_ 1 ')
        self.assertEqual(encoded, 'soilie-object-v1:["café_chair","a,b:_ 1"]')
        self.assertEqual(decode_collector_object_label(encoded), ('café_chair','a,b:_ 1'))
        self.assertEqual(encode_collector_object_label('cafe\u0301', 'x'), encode_collector_object_label('café','x'))
        self.assertNotEqual(encode_collector_object_label('a_b','c'), encode_collector_object_label('a','b_c'))

    def test_rejects_noncanonical_and_malformed_encodings(self):
        for value in ('chair_1', 'soilie-object-v1:{', 'soilie-object-v1:["a", "b"]', 'soilie-object-v1:[" a","b"]', 'soilie-object-v1:["a",1]', 'soilie-object-v1:["a","b","c"]', None):
            with self.subTest(value=value), self.assertRaises(ObjectIdentityError): decode_collector_object_label(value)
        for identity in (('', 'id'), ('chair','  '), (None,'id')):
            with self.subTest(identity=identity), self.assertRaises(ObjectIdentityError): encode_collector_object_label(*identity)
