import argparse
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import cloud_job


class FakeAPI:
    def __init__(self, fail_dispatch=False):
        self.calls = []; self.payload = None; self.fail_dispatch = fail_dispatch

    def call(self, method, path, body=None, upload=None):
        self.calls.append((method, path, body))
        if path == 'releases' and method == 'POST':
            return {**body, 'id': 42, 'upload_url': 'https://uploads.github.com/repos/test/repo/releases/42/assets{?name}'}
        if upload:
            self.payload = Path(upload).read_bytes()
        if 'dispatches' in path and self.fail_dispatch:
            raise RuntimeError('test failure')


class CloudJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.audio = Path(self.temp.name) / '中文标题.webm'
        self.audio.write_bytes(b'test audio')
        self.args = argparse.Namespace(audio_file=self.audio, start='0', end=None, language='auto')

    def test_default_full_audio_and_archive(self):
        api = FakeAPI()
        tag = cloud_job.submit(self.args, api)
        cloud_job.validate_tag(tag)
        with zipfile.ZipFile(io.BytesIO(api.payload)) as archive:
            manifest = json.loads(archive.read('request.json'))
            self.assertEqual(manifest['start'], 0)
            self.assertIsNone(manifest['end'])
            self.assertEqual(manifest['original_name'], '中文标题.webm')
            self.assertEqual(archive.read('input.webm'), b'test audio')
        self.assertFalse(any(c[0] == 'DELETE' for c in api.calls))

    def test_invalid_range_never_uploads(self):
        self.args.start = '03:00'; self.args.end = '02:00'
        api = FakeAPI()
        with self.assertRaises(ValueError):
            cloud_job.submit(self.args, api)
        self.assertEqual(api.calls, [])

    def test_youtube_link_only_dispatches_without_upload(self):
        self.args.url = 'https://youtu.be/abcdefghijk?si=share'
        self.args.audio_file = None
        self.args.start = '00:30'; self.args.end = '03:30'
        api = FakeAPI()
        cloud_job.submit(self.args, api)
        self.assertEqual(len(api.calls), 1)
        inputs = api.calls[0][2]['inputs']
        self.assertEqual(inputs['youtube_url'], 'https://www.youtube.com/watch?v=abcdefghijk')
        self.assertEqual(inputs['start'], '30')
        self.assertEqual(inputs['end'], '210')
        self.assertIsNone(api.payload)

    def test_dispatch_failure_deletes_staging_release(self):
        api = FakeAPI(fail_dispatch=True)
        with self.assertRaises(RuntimeError):
            cloud_job.submit(self.args, api)
        self.assertTrue(any(c[0] == 'DELETE' and c[1] == 'releases/42' for c in api.calls))

    def test_tag_validation_prevents_arbitrary_release_cleanup(self):
        with self.assertRaises(ValueError):
            cloud_job.delete_transfer(FakeAPI(), {'id': 1, 'tag_name': 'v1.0', 'body': cloud_job.MARKER})
