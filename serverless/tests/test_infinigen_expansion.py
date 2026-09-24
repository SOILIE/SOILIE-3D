import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from serverless.benchmark.expand_infinigen import compress_blend


class InfinigenExpansionTests(unittest.TestCase):
    def test_lossless_compression_is_recoverable_and_resumable(self):
        scratch = Path(__file__).parents[2] / '.codex/tests'
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temporary:
            root = Path(temporary)
            source = root / 'scene.blend'
            content = b'Blender evidence bytes' * 1000
            source.write_bytes(content)
            result = compress_blend(source, root)
            self.assertFalse(source.exists())
            self.assertEqual(hashlib.sha256(content).hexdigest(), result['originalSha256'])
            with gzip.open(source.with_suffix('.blend.gz'), 'rb') as stream:
                self.assertEqual(content, stream.read())
            self.assertEqual(result, compress_blend(source, root))
            self.assertLess(result['archiveBytes'], result['originalBytes'])
            with self.assertRaises(ValueError): compress_blend(root / 'other.blend', root)
            with self.assertRaises(ValueError): compress_blend(root.parent / 'scene.blend', root)


if __name__ == '__main__': unittest.main()
