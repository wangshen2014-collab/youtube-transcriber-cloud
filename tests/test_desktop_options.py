"""Isolated desktop widget/queue checks; never touch the user's live task queue."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest import mock


class DesktopOptionsTests(unittest.TestCase):
    def test_split_checkbox_defaults_off_and_is_snapshotted_per_task(self):
        try:
            import tkinter as tk
        except ImportError:
            self.skipTest('Tkinter unavailable')
        try:
            root = tk.Tk()
        except tk.TclError:
            self.skipTest('No display available')
        root.withdraw()
        self.addCleanup(root.destroy)
        desktop = Path(__file__).resolve().parents[1] / 'desktop'
        with mock.patch.object(sys, 'path', [str(desktop), *sys.path]):
            spec = importlib.util.spec_from_file_location('desktop_options_test_app', desktop / 'transcriber.py')
            app_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(app_module)
        with mock.patch.object(app_module, 'load_task_queue', return_value=[]), \
             mock.patch.object(app_module, 'cleanup_old_temp'), \
             mock.patch.object(app_module, 'cleanup_audio_cache'), \
             mock.patch.object(app_module.App, 'refresh_cache_info'), \
             mock.patch.object(app_module.App, 'save_tasks'):
            app = app_module.App(root)

        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        checkbox = next(w for w in descendants(root)
                        if w.winfo_class() == 'TCheckbutton' and '自动拆分音频' in w.cget('text'))
        self.assertFalse(app.split_audio.get())
        self.assertFalse(app.use_login.get())
        self.assertEqual(app.login_browser.get(), '本地 Cookie 文件')
        app.url.set('https://www.youtube.com/watch?v=DuG4PXAe084')
        app.operation.set('仅下载音频')
        with mock.patch.object(app_module, 'atomic_write_json'), \
             mock.patch.object(app, 'ensure_scheduler'):
            app.start()
            checkbox.invoke()
            self.assertTrue(app.split_audio.get())
            app.start()
            checkbox.invoke()
        self.assertFalse(app.tasks[0]['split_audio'])
        self.assertFalse(app.tasks[0]['use_login'])
        self.assertEqual(app.tasks[0]['video_height'], 720)
        self.assertTrue(app.tasks[1]['split_audio'])
        self.assertFalse(app.split_audio.get())
        app.operation.set('下载视频（含声音）')
        app.video_quality.set('1080p')
        app.on_operation_changed()
        self.assertEqual(app.transcribe_btn.cget('text'), '下载视频')
        with mock.patch.object(app_module, 'atomic_write_json'), \
             mock.patch.object(app, 'ensure_scheduler'):
            app.start()
        self.assertEqual(app.tasks[-1]['operation'], 'download_video')
        self.assertEqual(app.tasks[-1]['video_height'], 1080)
        self.assertFalse(app.tasks[-1]['use_login'])
