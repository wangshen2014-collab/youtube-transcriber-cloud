import argparse
import base64
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from urllib.parse import unquote

import batch_transcribe as batch
import transcribe_cli as cli
from github_results import ResultStore, FORMAT


class MemoryAPI:
    repo = 'owner/repo'

    def __init__(self):
        self.blobs, self.trees, self.commits = {}, {}, {}
        self.tip = None
        self.calls = []
        self.conflict = False

    def call(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == 'GET' and path == 'git/ref/heads/transcripts':
            if not self.tip:
                raise RuntimeError('GitHub API HTTP 404')
            return {'object': {'sha': self.tip}}
        if method == 'GET' and path.startswith('contents/'):
            name = unquote(path[len('contents/'):].split('?')[0])
            files = self.trees[self.commits[self.tip]] if self.tip else {}
            if name not in files:
                raise RuntimeError('GitHub API HTTP 404')
            return {'encoding': 'base64', 'content': self.blobs[files[name]]}
        if method == 'GET' and path.startswith('git/commits/'):
            return {'tree': {'sha': self.commits[path.split('/')[-1]]}}
        if method == 'GET' and path.startswith('git/trees/'):
            ref = path.split('/')[-1].split('?')[0]
            files = self.trees[self.commits.get(ref, ref)]
            return {'tree': [{'path': p, 'type': 'blob', 'sha': sha} for p, sha in files.items()], 'truncated': False}
        if method == 'POST' and path == 'git/blobs':
            key = f'b{len(self.blobs)}'; self.blobs[key] = body['content']
            return {'sha': key}
        if method == 'POST' and path == 'git/trees':
            files = dict(self.trees.get(body.get('base_tree'), {}))
            for entry in body['tree']:
                if entry['sha'] is None:
                    del files[entry['path']]
                else:
                    files[entry['path']] = entry['sha']
            key = f't{len(self.trees)}'; self.trees[key] = files
            return {'sha': key}
        if method == 'POST' and path == 'git/commits':
            key = f'c{len(self.commits)}'; self.commits[key] = body['tree']
            return {'sha': key}
        if (method, path) in [('POST', 'git/refs'), ('PATCH', 'git/refs/heads/transcripts')]:
            if self.conflict:
                self.conflict = False
                raise RuntimeError('GitHub API HTTP 422')
            self.tip = body['sha']
            return {}
        raise AssertionError((method, path))


class PublisherTests(unittest.TestCase):
    def test_atomic_allowlisted_results_and_no_actions_or_main_writes(self):
        api = MemoryAPI(); store = ResultStore(api); store.ensure()
        with tempfile.TemporaryDirectory() as folder:
            files = {}
            for ext in ('txt', 'srt', 'json'):
                files[ext] = Path(folder) / ('中文.' + ext)
                files[ext].write_bytes(b'test')
            key = 'a' * 24; config = {'test': True}
            store.checkpoint(key, {'settings': config})
            record = store.publish(key, config, '中文音频', files)
            self.assertEqual(store.completed(key, config), record)
            self.assertIsNone(store.checkpoint(key))
            self.assertIn('中文音频', store.read('README.md').decode())
            self.assertIn('%E4%B8%AD', record['files']['txt'])
            self.assertTrue(all('actions/' not in p for _, p, _ in api.calls))
            self.assertTrue(all('heads/main' not in p for _, p, _ in api.calls))
            for method, path, body in api.calls:
                if method == 'PATCH':
                    self.assertFalse(body['force'])
            with self.assertRaises(ValueError):
                store.publish(key, config, 'bad', {'audio': files['txt']})
            with self.assertRaises(ValueError):
                store.publish(key, config, 'bad', {**files, 'zip': files['txt']})

    def test_retries_conflict_preserving_existing_files(self):
        api = MemoryAPI(); store = ResultStore(api); store.ensure()
        store.update({'keep.txt': b'keep'}, 'first')
        api.conflict = True
        with mock.patch('github_results.time.sleep'):
            store.update({'new.txt': b'new'}, 'second')
        self.assertEqual(store.read('keep.txt'), b'keep')
        self.assertEqual(store.read('new.txt'), b'new')
        self.assertEqual(store.read('FORMAT.json'), store.encode(FORMAT))

    def test_refuses_unmarked_results_branch(self):
        api = MemoryAPI(); store = ResultStore(api)
        store.update({'README.md': b'users own branch'}, 'own')
        with self.assertRaises(ValueError):
            store.ensure()


class BatchTests(unittest.TestCase):
    def setUp(self):
        batch.STOP = False
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sources = [self.root / '中文1.wav', self.root / '中文2.wav']
        for n, source in enumerate(self.sources):
            source.write_bytes(b'audio-' + str(n).encode())
        self.options = argparse.Namespace(files=self.sources, input_dir=None, language='auto',
            quality='standard', threads=6,
            start=0.0, end=None, state_dir=self.root / 'state', model_dir=self.root / 'models',
            output_dir=self.root / 'out', pause_file=None, checkpoint_seconds=600)
        self.store = ResultStore(MemoryAPI())
        self.worked = []

    def worker(self, args, checkpoint, store, key, interval):
        self.worked.append(args.audio_file.name)
        # Before item two starts, item one must already have been published.
        if len(self.worked) == 2:
            self.assertIn(self.sources[0].stem.split(' [')[0], store.read('README.md').decode())
        config = cli.settings(args)
        folder = args.output_dir / '20261020-010101-12345678'; folder.mkdir(parents=True)
        for ext, value in [('txt', '测试字幕'), ('srt', '1\n00:00:00,000 --> 00:00:01,000\n测试字幕\n')]:
            (folder / (config['source_id'] + '.' + ext)).write_text(value, encoding='utf-8')
        cli.atomic_json(folder / 'run-report.json', {'status': 'completed', 'settings': config,
            'source': {'type': 'audio_file'}, 'selected_start': 0, 'selected_end': 1})
        return 0

    def test_sequential_save_then_cross_task_dedup(self):
        self.assertEqual(batch.run_batch(self.options, self.store, self.worker), 0)
        self.assertEqual(self.worked, [p.name for p in self.sources])
        self.options.state_dir = self.root / 'new-state'
        self.options.output_dir = self.root / 'new-output'
        self.assertEqual(batch.run_batch(self.options, self.store, self.worker), 0)
        self.assertEqual(len(self.worked), 2)

    def test_publish_failure_retry_does_not_repeat_inference(self):
        self.options.files = self.sources[:1]
        with mock.patch.object(self.store, 'publish', side_effect=RuntimeError('offline')), \
             mock.patch('batch_transcribe.time.sleep'):
            self.assertEqual(batch.run_batch(self.options, self.store, self.worker), 1)
        ledger = cli.read_json(self.options.state_dir / 'batch-status.json')
        self.assertEqual(ledger['jobs'][0]['status'], 'publish_pending')
        self.assertEqual(batch.run_batch(self.options, self.store, self.worker), 0)
        self.assertEqual(len(self.worked), 1)

    def test_failed_item_continues_to_next(self):
        def fail_first(args, *rest):
            if args.audio_file == self.sources[0]:
                return 1
            self.assertEqual(len(self.worked), 0)
            return self.worker(args, *rest)
        self.assertEqual(batch.run_batch(self.options, self.store, fail_first), 1)
        ledger = cli.read_json(self.options.state_dir / 'batch-status.json')
        self.assertEqual([j['status'] for j in ledger['jobs']], ['failed', 'completed'])

    def test_paused_item_leaves_remaining_queue(self):
        self.assertEqual(batch.run_batch(self.options, self.store, lambda *a: 75), 75)
        ledger = cli.read_json(self.options.state_dir / 'batch-status.json')
        self.assertEqual([j['status'] for j in ledger['jobs']], ['paused', 'queued'])

    def test_checkpoint_restore_rewrites_foreign_path(self):
        self.store.ensure()
        source = self.sources[0]
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        config = {
            'language': 'auto', 'model': 'medium', 'threads': 6, 'beam_size': 3,
            'device': 'cpu', 'compute_type': 'int8', 'vad_filter': False,
            'condition_on_previous_text': True, 'start': 0.0, 'end': None,
            'source_type': 'audio_file', 'audio_sha256': digest, 'source_id': 'audio-' + digest[:24],
        }
        key = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:24]
        self.store.checkpoint(key, {'settings': config, 'segments': [], 'resume_from': 3,
            'run_id': '20261020-010101-12345678', 'output_directory': '/foreign/machine/path'})

        def check_resume(args, path, store, actual_key, interval):
            self.assertEqual(cli.settings(args), config)
            self.assertEqual(actual_key, key)
            restored = cli.read_json(path)
            self.assertTrue(restored['output_directory'].startswith(str(args.output_dir.resolve())))
            self.assertEqual(restored['resume_from'], 3)
            return 75

        self.assertEqual(batch.run_batch(self.options, self.store, check_resume), 75)

    def test_same_audio_at_different_quality_runs_and_deduplicates_separately(self):
        self.options.files = self.sources[:1]
        keys = set()
        for quality, model, beam_size in (
            ('standard', 'medium', 3), ('medium-high', 'large-v3', 3), ('high', 'large-v3', 5)
        ):
            self.options.quality = quality
            self.assertEqual(batch.run_batch(self.options, self.store, self.worker), 0)
            key = cli.read_json(self.options.state_dir / 'batch-status.json')['jobs'][0]['key']
            keys.add(key)
            record = json.loads(self.store.read(f'jobs/{key}/manifest.json'))
            self.assertEqual((record['settings']['model'], record['settings']['beam_size']), (model, beam_size))
            self.assertNotIn('quality', record['settings'])
        self.assertEqual(len(keys), 3)
        self.assertEqual(batch.run_batch(self.options, self.store, self.worker), 0)
        self.assertEqual(len(self.worked), 3)

    def test_main_passes_quality_and_threads_and_defaults(self):
        for extra, expected in (([], ('standard', 6)), (['--quality', 'high', '--threads', '2'], ('high', 2))):
            with self.subTest(extra=extra), mock.patch.object(batch, 'auth_token', return_value='unused'), \
                 mock.patch.object(batch, 'run_batch', return_value=0) as run:
                self.assertEqual(batch.main(['--state-dir', str(self.options.state_dir), str(self.sources[0]), *extra]), 0)
                options = run.call_args.args[0]
                self.assertEqual((options.quality, options.threads), expected)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as failure:
            batch.main(['--threads', '0', str(self.sources[0])])
        self.assertEqual(failure.exception.code, 2)

    def test_input_directory_natural_order_and_no_recursive_sweep(self):
        for name in ('part10.wav', 'part2.wav', 'readme.txt'):
            (self.root / name).write_bytes(b'x')
        paths = batch.inputs([], self.root)
        self.assertLess([p.name for p in paths].index('part2.wav'), [p.name for p in paths].index('part10.wav'))
        self.assertNotIn('readme.txt', [p.name for p in paths])

    def test_worker_command_uses_resolved_quality_and_threads(self):
        self.options.quality = 'high'
        self.options.threads = 2
        args = batch.job_args(self.options, self.sources[0], 'a' * 24)
        process = mock.Mock(returncode=0)
        process.poll.side_effect = [None, 0, 0]
        with mock.patch('batch_transcribe.subprocess.Popen', return_value=process) as popen, \
             mock.patch('batch_transcribe.time.sleep'):
            code = batch.run_worker(args, args.state_dir / 'absent.json', self.store, 'a' * 24)
        command = popen.call_args.args[0]
        self.assertEqual(code, 0)
        for flag, value in [('--model', 'large-v3'), ('--threads', '2'), ('--beam-size', '5')]:
            self.assertEqual(command[command.index(flag) + 1], value)
        self.assertIn('--audio-file', command)
        self.assertNotIn('cloud_job.py', ' '.join(command))

    def test_verified_parts_merge_retires_parts_and_deduplicates_reruns(self):
        renamed = [self.root / '同一视频 第001段 [00-00-00 至 00-00-01].wav',
                   self.root / '同一视频 第002段 [00-00-01 至 00-00-02].wav']
        for old, new in zip(self.sources, renamed):
            old.rename(new)
        self.sources = renamed; self.options.files = renamed
        self.assertEqual(batch.run_batch(self.options, self.store, self.worker), 0)
        ledger = cli.read_json(self.options.state_dir / 'batch-status.json')
        self.assertEqual(ledger['merges'][0]['status'], 'completed')
        merged = next((self.options.output_dir / 'merged').rglob('*.srt')).read_text(encoding='utf-8')
        self.assertIn('00:00:01,000 --> 00:00:02,000', merged)
        for entry in ledger['jobs']:
            self.assertIsNone(self.store.read(f'jobs/{entry["key"]}/manifest.json'))
            self.assertIn(entry["key"], json.loads(self.store.read("retired-parts.json")))
        self.assertFalse(list(self.options.output_dir.rglob("*.zip")))
        self.assertEqual(batch.run_batch(self.options, self.store, self.worker), 0)
        self.assertEqual(len(self.worked), 2)

    def test_invalid_srt_is_rejected(self):
        for value in ('1\n00:61:00,000 --> 00:62:00,000\ntext',
                      '1\n00:00:02,000 --> 00:00:01,000\ntext', 'invalid'):
            with self.assertRaises(ValueError):
                batch.parse_srt(value)

    def test_merge_publish_failure_preserves_parts_then_retry_cleans_without_inference(self):
        renamed = [self.root / '同一视频 第001段 [00-00-00 至 00-00-01].wav',
                   self.root / '同一视频 第002段 [00-00-01 至 00-00-02].wav']
        for old, new in zip(self.sources, renamed):
            old.rename(new)
        self.sources = renamed; self.options.files = renamed
        publish = self.store.publish
        def fail_merge(key, config, title, files):
            if config.get('source_type') == 'verified-parts-merge':
                raise RuntimeError('merge upload failed')
            return publish(key, config, title, files)
        with mock.patch.object(self.store, 'publish', side_effect=fail_merge):
            self.assertEqual(batch.run_batch(self.options, self.store, self.worker), 1)
        ledger = cli.read_json(self.options.state_dir / 'batch-status.json')
        for entry in ledger['jobs']:
            self.assertIsNotNone(self.store.read(f'jobs/{entry["key"]}/manifest.json'))
        self.assertEqual(batch.run_batch(self.options, self.store, self.worker), 0)
        self.assertEqual(len(self.worked), 2)
        for entry in ledger['jobs']:
            self.assertIsNone(self.store.read(f'jobs/{entry["key"]}/manifest.json'))
