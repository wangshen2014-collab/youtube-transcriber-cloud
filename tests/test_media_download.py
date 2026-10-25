import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

DESKTOP = Path(__file__).resolve().parents[1] / 'desktop'
with mock.patch.object(sys, 'path', [str(DESKTOP), *sys.path]):
    spec = importlib.util.spec_from_file_location('local_media_download', DESKTOP / 'media_download.py')
    media = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(media)


class MediaDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.info = {'title': '中文会员视频', 'id': 'zscZ446Irdk', 'formats': [
            {'format_id': 'v720', 'height': 720, 'ext': 'mp4', 'vcodec': 'avc1', 'acodec': 'none'},
            {'format_id': 'v1080', 'height': 1080, 'ext': 'mp4', 'vcodec': 'avc1', 'acodec': 'none'},
            {'format_id': 'audio', 'ext': 'm4a', 'vcodec': 'none', 'acodec': 'mp4a', 'abr': 128}]}

    def test_no_login_is_default_and_chrome_selection_is_explicit(self):
        self.assertEqual(media.login_options({'url': 'bad'}, self.root), {})
        task = {'url': 'https://youtu.be/zscZ446Irdk', 'use_login': True, 'login_browser': 'Chrome'}
        self.assertEqual(media.login_options(task, self.root), {'cookiesfrombrowser': ('chrome', None)})
        task['url'] = 'https://other.example/watch?v=zscZ446Irdk'
        with self.assertRaises(ValueError):
            media.login_options(task, self.root)

    def test_manual_cookie_source_is_copied_not_modified(self):
        original = self.root / '用户原始文件.txt'; original.write_text('test-cookie-placeholder')
        work = self.root / 'work'; work.mkdir()
        options = media.login_options({'url': 'https://youtu.be/zscZ446Irdk', 'use_login': True,
            'login_browser': '本地 Cookie 文件', 'cookie_file': str(original)}, work)
        self.assertNotEqual(options['cookiefile'], str(original))
        self.assertEqual(Path(options['cookiefile']).read_text(), original.read_text())

    def test_resolution_cap_and_no_drm_or_silent_video(self):
        self.assertEqual(media.choose_formats(self.info, 720)[0]['format_id'], 'v720')
        self.assertEqual(media.choose_formats(self.info, 1080)[0]['format_id'], 'v1080')
        self.info['formats'][1]['has_drm'] = True
        self.assertEqual(media.choose_formats(self.info, 1080)[0]['format_id'], 'v720')
        self.info['formats'].pop()
        with self.assertRaises(ValueError):
            media.choose_formats(self.info, 720)

    def test_h264_wins_over_higher_bitrate_av1_at_same_resolution(self):
        self.info['formats'].append({'format_id': 'av1', 'height': 1080, 'ext': 'mp4',
            'vcodec': 'av01.0.08M.08', 'acodec': 'none', 'tbr': 99999})
        self.assertEqual(media.choose_formats(self.info, 1080)[0]['format_id'], 'v1080')

    def fixtures(self):
        import av
        from fractions import Fraction
        video, audio = self.root / 'video.mp4', self.root / 'audio.m4a'
        with av.open(str(video), 'w') as output:
            stream = output.add_stream('libx264', rate=24)
            stream.width = 64; stream.height = 64; stream.pix_fmt = 'yuv420p'
            for index in range(24):
                frame = av.VideoFrame(64, 64, 'yuv420p')
                for plane in frame.planes:
                    plane.update(bytes([100]) * plane.buffer_size)
                frame.pts = index; frame.time_base = Fraction(1, 24)
                for packet in stream.encode(frame): output.mux(packet)
            for packet in stream.encode(None): output.mux(packet)
        with av.open(str(audio), 'w') as output:
            stream = output.add_stream('aac', rate=48000); stream.layout = 'mono'
            for index in range(47):
                frame = av.AudioFrame(format='fltp', layout='mono', samples=1024)
                for plane in frame.planes: plane.update(bytes(plane.buffer_size))
                frame.sample_rate = 48000; frame.pts = index * 1024; frame.time_base = Fraction(1, 48000)
                for packet in stream.encode(frame): output.mux(packet)
            for packet in stream.encode(None): output.mux(packet)
        return video, audio

    def check_decode(self, path):
        import av
        with av.open(str(path)) as container:
            self.assertEqual(sum(1 for f in container.decode(video=0)), 24)
        with av.open(str(path)) as container:
            self.assertGreater(sum(f.samples for f in container.decode(audio=0)), 47000)

    def test_real_remux_separate_and_combined_tracks_without_dropped_frames(self):
        video, audio = self.fixtures()
        first, second = self.root / 'merged.mp4', self.root / 'copied.mp4'
        media.remux([video, audio], first)
        self.check_decode(first)
        media.remux([first], second)
        self.check_decode(second)

    def test_download_pipeline_real_mux_and_never_overwrites_or_expires_video(self):
        video, audio = self.fixtures()
        info = self.info; calls = []
        class Downloader:
            def __init__(self, options): self.options = options; calls.append(options)
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def extract_info(self, url, download=False):
                if download:
                    original = audio if self.options['format'] == 'audio' else video
                    self.path = Path(self.options['outtmpl'].replace('%(ext)s', original.suffix[1:]))
                    shutil.copyfile(original, self.path)
                return info
            def prepare_filename(self, _result): return str(self.path)
        with mock.patch('yt_dlp.YoutubeDL', Downloader), mock.patch('audio_export.track_export') as ttl:
            one, _ = media.download_video('https://youtu.be/zscZ446Irdk', self.root / '输出', self.root / 'work1')
            two, _ = media.download_video('https://youtu.be/zscZ446Irdk', self.root / '输出', self.root / 'work2')
            self.assertEqual(one.name, '中文会员视频.mp4')
            self.assertNotEqual(one, two)
            self.check_decode(one); self.check_decode(two)
            ttl.assert_not_called()
        self.assertTrue(all('cookiesfrombrowser' not in call and 'cookiefile' not in call for call in calls))

    def test_readable_errors_redact_signed_parameters(self):
        self.assertIn('系统加密', media.friendly_error('Failed to decrypt with DPAPI'))
        self.assertIn('手动关闭', media.friendly_error('Could not copy Chrome cookie database'))
        self.assertNotIn('SECRET', media.friendly_error('HTTP error https://example.org/file?token=SECRET'))

    def test_runtime_prefers_app_owned_node_even_without_path(self):
        runtime = self.root / 'runtime'; runtime.mkdir()
        node = runtime / 'node.exe'; node.write_bytes(b'test-placeholder')
        with mock.patch.object(media.shutil, 'which', return_value=None):
            self.assertEqual(media.runtime_options(self.root), {'js_runtimes': {'node': {'path': str(node)}}})

    def test_runtime_enables_path_node_explicitly(self):
        with mock.patch.object(media.shutil, 'which', return_value='/trusted/bin/node'):
            self.assertEqual(media.runtime_options(self.root), {'js_runtimes': {'node': {'path': '/trusted/bin/node'}}})

    def test_important_warnings_are_visible_deduplicated_and_clean(self):
        messages = []
        logger = media.QuietLogger(lambda kind, value: messages.append((kind, value)))
        logger.debug('Cookie: DO_NOT_LOG')
        logger.warning('n challenge solving failed')
        logger.warning('n challenge solving failed')
        self.assertEqual(len(messages), 1)
        self.assertIn('JavaScript/EJS', messages[0][1])
        self.assertNotIn('DO_NOT_LOG', str(messages))
        cleaned = media.friendly_error('\x1b[0;31mERROR:\x1b[0m Requested format is not available')
        self.assertNotIn('\x1b', cleaned)
        self.assertIn('预览图片', cleaned)
