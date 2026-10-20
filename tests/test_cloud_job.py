import argparse
import contextlib
import io
import json
import os
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
        # A workflow carries its real source URL in the environment. Test cases
        # must explicitly isolate inputs rather than inherit that live request.
        environment = patch.dict(os.environ, {'AUDIO_URL': '', 'YOUTUBE_URL': '',
            'ASSET_NAME': '', 'RANGE_START': '0', 'RANGE_END': '', 'AUDIO_LANGUAGE': 'auto'})
        environment.start(); self.addCleanup(environment.stop)
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

    def test_release_link_dispatches_without_upload(self):
        self.args.release_url = 'https://github.com/test/repo/releases/tag/%E9%9F%B3%E9%A2%91'
        self.args.audio_file = None
        api = FakeAPI()
        cloud_job.submit(self.args, api)
        self.assertEqual(len(api.calls), 1)
        self.assertEqual(api.calls[0][2]['inputs']['audio_url'], self.args.release_url)
        self.assertEqual(api.calls[0][2]['inputs']['end'], '')
        self.assertIsNone(api.payload)

    def test_release_page_and_direct_attachment_url_parsing(self):
        self.assertEqual(cloud_job.release_reference('https://github.com/test/repo/releases/tag/%E9%9F%B3%E9%A2%91'),
                         ('test/repo', '音频', None))
        self.assertEqual(cloud_job.release_reference('https://github.com/test/repo/releases/download/%E9%9F%B3%E9%A2%91/name%20one.webm'),
                         ('test/repo', '音频', 'name one.webm'))
        with self.assertRaises(ValueError):
            cloud_job.release_reference('https://other.example/test/repo/releases/tag/audio')

    def test_release_page_downloads_single_audio_on_runner(self):
        from scripts.github_runner import prepare
        url = 'https://github.com/test/repo/releases/download/audio/title.webm'
        asset = {'name': 'title.webm', 'size': 3, 'browser_download_url': url, 'state': 'uploaded'}
        api = FakeAPI()
        with contextlib.chdir(self.temp.name), patch.dict(os.environ, {
            'AUDIO_URL': 'https://github.com/test/repo/releases/tag/audio',
            'YOUTUBE_URL': '', 'RANGE_START': '0', 'RANGE_END': '', 'AUDIO_LANGUAGE': 'auto'}), \
                patch.object(api, 'call', return_value={'draft': False, 'assets': [asset]}), \
                patch('scripts.github_runner.urlopen', return_value=io.BytesIO(b'abc')):
            prepare(api, '')
            spec = json.loads(Path('incoming/request.json').read_text(encoding='utf-8'))
            self.assertEqual(spec['source_type'], 'release_audio')
            self.assertEqual(spec['original_name'], 'title.webm')
            self.assertEqual(Path('incoming/input.webm').read_bytes(), b'abc')

    def test_ambiguous_release_needs_an_explicit_asset(self):
        from scripts.github_runner import resolve_release_audio
        assets = [{'name': 'one.webm'}, {'name': 'two.m4a'}]
        api = FakeAPI()
        with patch.object(api, 'call', return_value={'draft': False, 'assets': assets}):
            with self.assertRaisesRegex(ValueError, 'asset_name'):
                resolve_release_audio(api, 'https://github.com/test/repo/releases/tag/audio')

    def test_phone_form_prepares_link_without_a_release(self):
        from scripts.github_runner import prepare
        api = FakeAPI()
        with contextlib.chdir(self.temp.name), patch.dict(os.environ, {
            'YOUTUBE_URL': 'https://youtu.be/abcdefghijk', 'RANGE_START': '02:15',
            'RANGE_END': '', 'AUDIO_LANGUAGE': 'auto'}):
            prepare(api, '')
            spec = json.loads(Path('incoming/request.json').read_text(encoding='utf-8'))
            self.assertEqual(spec['source_type'], 'youtube')
            self.assertEqual(spec['start'], 135)
            self.assertIsNone(spec['end'])
            self.assertEqual(api.calls, [])

    def test_dispatch_failure_deletes_staging_release(self):
        api = FakeAPI(fail_dispatch=True)
        with self.assertRaises(RuntimeError):
            cloud_job.submit(self.args, api)
        self.assertTrue(any(c[0] == 'DELETE' and c[1] == 'releases/42' for c in api.calls))

    def test_tag_validation_prevents_arbitrary_release_cleanup(self):
        with self.assertRaises(ValueError):
            cloud_job.delete_transfer(FakeAPI(), {'id': 1, 'tag_name': 'v1.0', 'body': cloud_job.MARKER})
