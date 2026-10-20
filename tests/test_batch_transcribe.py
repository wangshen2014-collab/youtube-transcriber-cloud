import argparse
import base64
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
            for ext in ('txt', 'srt', 'json', 'zip'):
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
                store.publish(key, config, 'bad', {'audio': files['zip']})

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
            start=0.0, end=None, state_dir=self.root / 'state', model_dir=self.root / 'models',
            output_dir=self.root / 'out', pause_file=None, checkpoint_seconds=600)
        self.store = ResultStore(MemoryAPI())
        self.worked = []

    def worker(self, args, checkpoint, store, key, interval):
        self.worked.append(args.audio_file.name)
        # Before item two starts, item one must already have been published.
        if len(self.worked) == 2:
            self.assertIn(self.sources[0].name.split(' [')[0], store.read('README.md').decode())
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
        args = batch.job_args(self.options, self.sources[0])
        config = cli.settings(args); key = cli.task_key(config)
        self.store.checkpoint(key, {'settings': config, 'segments': [], 'resume_from': 3,
            'run_id': '20261020-010101-12345678', 'output_directory': '/foreign/machine/path'})
        args = batch.job_args(self.options, self.sources[0], key)
        path = batch.restore_checkpoint(self.store, key, config, args)
        restored = cli.read_json(path)
        self.assertTrue(restored['output_directory'].startswith(str(args.output_dir.resolve())))
        self.assertEqual(restored['resume_from'], 3)

    def test_input_directory_natural_order_and_no_recursive_sweep(self):
        for name in ('part10.wav', 'part2.wav', 'readme.txt'):
            (self.root / name).write_bytes(b'x')
        paths = batch.inputs([], self.root)
        self.assertLess([p.name for p in paths].index('part2.wav'), [p.name for p in paths].index('part10.wav'))
        self.assertNotIn('readme.txt', [p.name for p in paths])

    def test_worker_command_preserves_defaults_without_model_log_spam(self):
        args = batch.job_args(self.options, self.sources[0], 'a' * 24)
        process = mock.Mock(returncode=0)
        process.poll.side_effect = [None, 0, 0]
        with mock.patch('batch_transcribe.subprocess.Popen', return_value=process) as popen, \
             mock.patch('batch_transcribe.time.sleep'):
            code = batch.run_worker(args, args.state_dir / 'absent.json', self.store, 'a' * 24)
        command = popen.call_args.args[0]
        self.assertEqual(code, 0)
        for flag, value in [('--model', 'medium'), ('--threads', '6'), ('--beam-size', '3')]:
            self.assertEqual(command[command.index(flag) + 1], value)
        self.assertIn('--audio-file', command)
        self.assertNotIn('cloud_job.py', ' '.join(command))

    def test_verified_parts_merge_has_source_time_offsets_and_retains_parts(self):
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
            self.assertIsNotNone(self.store.read(f'jobs/{entry["key"]}/manifest.json'))

    def test_invalid_srt_is_rejected(self):
        for value in ('1\n00:61:00,000 --> 00:62:00,000\ntext',
                      '1\n00:00:02,000 --> 00:00:01,000\ntext', 'invalid'):
            with self.assertRaises(ValueError):
                batch.parse_srt(value)
