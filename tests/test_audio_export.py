import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import time
import unittest
import wave
from unittest import mock

spec = importlib.util.spec_from_file_location('audio_export', Path(__file__).resolve().parents[1] / 'desktop' / 'audio_export.py')
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


class AudioExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.wav'
        with wave.open(str(self.source), 'wb') as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(16000)
            audio.writeframes(struct.pack('<h', 100) * (16000 * 3))

    def test_full_export_named_title_not_id_and_no_overwrite(self):
        first = exporter.export_audio(self.source, self.root / '输出', '中文视频标题', 'abcdefghijk')
        second = exporter.export_audio(self.source, self.root / '输出', '中文视频标题', 'abcdefghijk')
        self.assertEqual(first.name, '中文视频标题.wav')
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_bytes(), self.source.read_bytes())

    def test_real_partial_export_duration_and_name(self):
        import av
        path = exporter.export_audio(self.source, self.root / '输出', '中文视频标题', 'abcdefghijk',
                                     start=1, end=2, partial=True)
        self.assertEqual(path.name, '中文视频标题 [00-00-01 至 00-00-02].m4a')
        with av.open(str(path)) as audio:
            samples = sum(frame.samples for frame in audio.decode(audio=0))
        self.assertAlmostEqual(samples / 48000, 1, delta=0.06)

    def test_pause_cleans_incomplete_export(self):
        with self.assertRaises(exporter.ExportPaused):
            exporter.export_audio(self.source, self.root / '输出', '测试', 'abcdefghijk', should_pause=lambda: True)
        self.assertEqual(list((self.root / '输出').iterdir()), [])
        self.assertTrue(self.source.exists())

    def test_ttl_only_removes_unchanged_tracked_audio(self):
        folder = self.root / '输出'
        expired = exporter.export_audio(self.source, folder, '到期音频', 'abcdefghijk')
        modified = exporter.export_audio(self.source, folder, '用户修改', 'abcdefghijk')
        registry = self.root / 'registry.json'
        exporter.track_export(expired, registry); exporter.track_export(modified, registry)
        modified.write_bytes(b'modified by user')
        unrelated = folder / '字幕.txt'; unrelated.write_text('保留', encoding='utf-8')
        self.assertEqual(exporter.cleanup_exports(registry, now=time.time() + 8 * 86400), 1)
        self.assertFalse(expired.exists()); self.assertTrue(modified.exists()); self.assertTrue(unrelated.exists())

    def test_upload_small_file_keeps_original(self):
        paths = exporter.export_upload_audio(self.source, self.root / '输出', '中文标题', 'id')
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0].read_bytes(), self.source.read_bytes())

    def test_upload_real_segments_decode_and_preserve_range(self):
        import av
        paths = exporter.export_upload_audio(self.source, self.root / '输出', '中文标题', 'id',
                                             start=1, end=3, max_bytes=10000, chunk_seconds=1)
        self.assertEqual(len(paths), 2)
        for number, path in enumerate(paths, 1):
            self.assertIn(f'第{number:03d}段', path.name)
            self.assertLessEqual(path.stat().st_size, 10000)
            with av.open(str(path)) as audio:
                self.assertAlmostEqual(sum(f.samples for f in audio.decode(audio=0)) / 48000,
                                       1, delta=0.06)
        self.assertIn('00-00-01 至 00-00-02', paths[0].name)
        self.assertIn('00-00-02 至 00-00-03', paths[1].name)
        self.assertTrue(self.source.exists())

    def test_upload_oversized_encoded_part_is_split_again(self):
        original_export = exporter.export_audio
        calls = []
        def oversized(*args, **kwargs):
            path = original_export(*args, **kwargs)
            calls.append(path)
            if kwargs['end'] - kwargs['start'] > 1.5:
                with path.open('ab') as out:
                    out.write(b'\0' * 20000)
            return path
        with mock.patch.object(exporter, 'export_audio', side_effect=oversized):
            paths = exporter.export_upload_audio(self.source, self.root / '输出', '测试', 'id',
                                                 max_bytes=10000)
        self.assertEqual(len(paths), 2)
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(p.stat().st_size <= 10000 for p in paths))

    def test_upload_pause_rolls_back_completed_parts_not_user_files(self):
        folder = self.root / '输出'; folder.mkdir()
        retained = folder / '用户文件.txt'; retained.write_text('keep')
        def pause():
            return len(list(folder.glob('*.m4a'))) > 0
        with self.assertRaises(exporter.ExportPaused):
            exporter.export_upload_audio(self.source, folder, '暂停', 'id', max_bytes=10000,
                                         chunk_seconds=1, should_pause=pause)
        self.assertEqual(list(folder.iterdir()), [retained])
        self.assertTrue(self.source.exists())

    def test_real_file_over_30mb(self):
        import av
        large = self.root / '大音频.wav'
        with wave.open(str(large), 'wb') as audio:
            audio.setnchannels(2); audio.setsampwidth(2); audio.setframerate(48000)
            for _ in range(180):
                audio.writeframes(b'\x64\0\x64\0' * 48000)
        self.assertGreater(large.stat().st_size, 30_000_000)
        paths = exporter.export_upload_audio(large, self.root / '输出', '大音频', 'id', chunk_seconds=60)
        self.assertEqual(len(paths), 3)
        total = 0
        for path in paths:
            self.assertLessEqual(path.stat().st_size, 30_000_000)
            with av.open(str(path)) as audio:
                total += sum(f.samples for f in audio.decode(audio=0)) / 48000
        self.assertAlmostEqual(total, 180, delta=0.15)
