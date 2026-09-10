# /// script
# requires-python = ">=3.10"
# dependencies = ["PyYAML==6.0.2"]
# ///
"""Model lifecycle tests, with tiny pinned fixtures and no model/network required."""
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import local_model as local
from retrieval import retrieve


class Response(io.BytesIO):
    def geturl(self):
        return 'https://cdn.example.test/model'


class ModelTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.payload = b'known-model'
        self.files = {'model.onnx': (len(self.payload), hashlib.sha256(self.payload).hexdigest())}
        patcher = patch.object(local, 'FILES', self.files)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_status_and_missing_model_never_download(self):
        with patch.object(local, 'urlopen', side_effect=AssertionError('unexpected network')):
            self.assertFalse(local.model_status(self.root)['ready'])
            with self.assertRaisesRegex(ValueError, 'download-model'):
                local.LocalModel(self.root).profile()
            vault = self.root/'vault'
            vault.mkdir()
            (vault/'SQLite.md').write_text('# SQLite\nLocal notes', encoding='utf-8')
            result = retrieve(vault, 'SQLite', provider=local.LocalModel(self.root), cache_root=self.root/'cache')
            self.assertEqual(result['results'][0]['matched_by'], ['fts'])
            self.assertIn('semantic_unavailable', [d['code'] for d in result['diagnostics']])

    def test_verified_download_is_idempotent(self):
        with patch.object(local, 'urlopen', return_value=Response(self.payload)) as request:
            self.assertTrue(local.download_model(self.root)['ready'])
            self.assertTrue(local.download_model(self.root)['ready'])
            self.assertEqual(request.call_count, 1)

    def test_corrupt_download_retries_and_never_replaces_existing_file(self):
        target = local.model_directory(self.root)/'model.onnx'
        target.parent.mkdir(parents=True)
        target.write_bytes(b'previous-file')
        with patch.object(local, 'urlopen', side_effect=lambda *a, **kw: Response(b'bad')), patch.object(local.time, 'sleep'):
            with self.assertRaisesRegex(ValueError, 'installation failed'):
                local.download_model(self.root)
        self.assertEqual(target.read_bytes(), b'previous-file')
        self.assertEqual(list(target.parent.glob('.download-*')), [])

    def test_verified_offline_import_and_corruption_detection(self):
        source = self.root/'offline'
        source.mkdir()
        (source/'model.onnx').write_bytes(self.payload)
        with patch.object(local, 'urlopen', side_effect=AssertionError('unexpected network')):
            self.assertTrue(local.download_model(self.root, source)['ready'])
            target = local.model_directory(self.root)/'model.onnx'
            target.write_bytes(b'x'*len(self.payload))
            self.assertFalse(local.model_status(self.root)['ready'])
            self.assertTrue(local.download_model(self.root, source)['ready'])

    def test_failed_download_can_resume_verified_files(self):
        other = b'second-file'
        self.files['second.bin'] = (len(other), hashlib.sha256(other).hexdigest())
        with patch.object(local, 'urlopen', side_effect=[Response(self.payload), OSError(), OSError(), OSError()]), patch.object(local.time, 'sleep'):
            with self.assertRaises(ValueError):
                local.download_model(self.root)
        with patch.object(local, 'urlopen', return_value=Response(other)) as request:
            self.assertTrue(local.download_model(self.root)['ready'])
            self.assertEqual(request.call_count, 1)

    def test_symlink_is_not_a_verified_model(self):
        source = self.root/'source'
        source.write_bytes(self.payload)
        target = local.model_directory(self.root)/'model.onnx'
        target.parent.mkdir(parents=True)
        target.symlink_to(source)
        self.assertFalse(local.model_status(self.root)['ready'])


if __name__ == '__main__':
    unittest.main()
