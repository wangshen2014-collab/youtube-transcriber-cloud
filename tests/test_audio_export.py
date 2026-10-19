import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import time
import unittest
import wave

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
