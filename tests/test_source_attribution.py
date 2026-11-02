import argparse
import json
from pathlib import Path
import tempfile
import unittest

import batch_transcribe as batch
import transcribe_cli as cli
from desktop import source_attribution as credits


class AttributionTests(unittest.TestCase):
    def test_channel_is_not_assumed_to_be_author_or_opinion_owner(self):
        info = credits.attribution(metadata={'channel': '频道甲', 'uploader': '上传者乙'})
        self.assertEqual(info['channel'], '频道甲')
        self.assertNotIn('author', info)
        self.assertNotIn('opinion_owner', info)
        fallback = credits.attribution(metadata={'uploader': '上传者乙'})
        self.assertEqual(fallback['channel'], '上传者乙')

    def test_long_download_title_keeps_channel_and_split_sequence(self):
        from desktop.audio_export import safe_name
        title = credits.credited_title('很长的节目标题' * 40 + ' 第001段', {'channel': '作者频道'})
        name = safe_name(title)
        self.assertTrue(name.startswith('【频道 作者频道】'))
        self.assertTrue(name.endswith(' 第001段'))
        self.assertLessEqual(len(name), 100)

    def test_names_from_prompt_and_filename_keep_their_provenance(self):
        parser = argparse.ArgumentParser(); credits.add_arguments(parser)
        options = parser.parse_args(['--author', '主持人甲', '--interviewee', '嘉宾乙、嘉宾丙',
                                     '--opinion-owner', '嘉宾乙'])
        info = credits.attribution(options, source=Path('【频道 科技频道】节目 第001段.m4a'))
        self.assertEqual(info['channel_source'], '音频文件名')
        self.assertEqual(info['author_source'], '用户提供')
        self.assertEqual(info['interviewee'], '嘉宾乙、嘉宾丙')
        restored = credits.attribution(source=Path('【作者 主持人甲】【频道 科技频道】节目.m4a'))
        self.assertEqual(restored['author'], '主持人甲')
        self.assertEqual(restored['channel'], '科技频道')

    def test_annotation_is_idempotent_and_preserves_subtitle_times_and_words(self):
        info = {'author': '作者甲', 'author_source': '用户提供'}
        original = [{'start': 12.5, 'end': 15.0, 'text': '实际原话。'},
                    {'start': 15.0, 'end': 17.5, 'text': '下一句。'}]
        annotated = credits.annotate_segments(original, info)
        self.assertEqual(annotated, credits.annotate_segments(annotated, info))
        self.assertEqual([(s['start'], s['end']) for s in original], [(s['start'], s['end']) for s in annotated])
        self.assertTrue(annotated[0]['text'].endswith('实际原话。'))
        self.assertEqual(annotated[1]['text'], '下一句。')
        self.assertEqual(original[0]['text'], '实际原话。')
        self.assertEqual(credits.annotate_text(credits.annotate_text('正文', info), info), credits.annotate_text('正文', info))
        windows_text = credits.annotate_text('正文', info).replace('\n', '\r\n')
        self.assertEqual(credits.strip_header(windows_text), '正文')

    def batch_case(self):
        from test_batch_transcribe import BatchTests
        case = BatchTests(methodName='runTest'); case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def test_direct_engine_writes_credit_header_without_biasing_recognition(self):
        from test_transcriber import TranscriptionTests
        case = TranscriptionTests(methodName='runTest'); case.setUp()
        self.addCleanup(case.doCleanups)
        case.args.author = '作者甲'
        case.args.interviewee = '受访者乙'
        self.assertEqual(cli.run(case.args), 0)
        for suffix in ('txt', 'srt'):
            text = next(case.args.output_dir.rglob('*.' + suffix)).read_text(encoding='utf-8')
            self.assertIn('作者甲', text); self.assertIn('受访者乙', text)
            self.assertEqual(text.count(credits.BEGIN), 1)
        recognition = next(call for call in case.calls if call[0] == 'transcribe')
        self.assertNotIn('initial_prompt', recognition[2])

    def test_explicit_credits_change_dedup_identity_and_survive_worker_transport(self):
        case = self.batch_case()
        plain = cli.settings(batch.job_args(case.options, case.sources[0]))
        case.options.author = '作者甲'
        case.options.interviewee = '受访者乙'
        case.options.opinion_owner = '受访者乙'
        case.options.source_metadata = {'title': '节目名', 'channel': '频道甲', 'source_url': 'https://youtu.be/abcdefghijk'}
        args = batch.job_args(case.options, case.sources[0])
        attributed = cli.settings(args)
        self.assertNotEqual(cli.task_key(plain), cli.task_key(attributed))
        parser = argparse.ArgumentParser(); credits.add_arguments(parser)
        transported = parser.parse_args(credits.worker_arguments(args))
        self.assertEqual(credits.attribution(args), credits.attribution(transported))
        case.options.files = case.sources[:1]
        self.assertEqual(batch.run_batch(case.options, case.store, case.worker), 0)
        ready = next(case.options.output_dir.rglob('ready'))
        txt = next(ready.glob('*.txt')); srt = next(ready.glob('*.srt'))
        self.assertIn('【作者 作者甲】【频道 频道甲】节目名', txt.stem)
        for path in (txt, srt):
            content = path.read_text(encoding='utf-8')
            self.assertIn('受访者乙', content)
            self.assertIn('观点归属', content)
            self.assertEqual(content.count(credits.BEGIN), 1)
        segments = batch.parse_srt(srt.read_text(encoding='utf-8'))
        self.assertEqual((segments[0]['start'], segments[0]['end']), (0, 1))

    def test_split_merge_has_one_credit_header_and_correct_offsets(self):
        case = self.batch_case()
        names = ['【频道 频道甲】节目 第001段 [00-00-00 至 00-00-01].wav',
                 '【频道 频道甲】节目 第002段 [00-00-01 至 00-00-02].wav']
        renamed = []
        for old, name in zip(case.sources, names):
            new = old.with_name(name); old.rename(new); renamed.append(new)
        case.sources = renamed; case.options.files = renamed
        case.options.interviewee = '受访者乙'
        self.assertEqual(batch.run_batch(case.options, case.store, case.worker), 0)
        merged_root = case.options.output_dir / 'merged'
        for suffix in ('txt', 'srt'):
            text = next(merged_root.rglob('*.' + suffix)).read_text(encoding='utf-8')
            self.assertEqual(text.count(credits.BEGIN), 1)
            self.assertIn('频道甲', text); self.assertIn('受访者乙', text)
        srt = next(merged_root.rglob('*.srt')).read_text(encoding='utf-8')
        self.assertEqual([(s['start'], s['end']) for s in batch.parse_srt(srt)], [(0, 1), (1, 2)])
