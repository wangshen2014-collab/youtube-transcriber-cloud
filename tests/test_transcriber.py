import argparse
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import transcribe_cli as cli


class TranscriptionTests(unittest.TestCase):
    def setUp(self):
        cli.STOP = False
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        root = Path(self.folder.name)
        self.args = argparse.Namespace(url="https://youtu.be/abcdefghijk", language="auto",
            model="medium", threads=6, beam_size=3, start=2.0, end=6.0,
            state_dir=root / "state", model_dir=root / "models", output_dir=root / "out",
            pause_file=None)
        self.calls = []
        calls = self.calls

        class Model:
            def __init__(self, name, **options):
                calls.append(("model", name, options))

            def transcribe(self, samples, **options):
                calls.append(("transcribe", len(samples), options))
                duration = len(samples) / cli.SAMPLE_RATE
                segments = [types.SimpleNamespace(start=0.0, end=min(1.0, duration), text=" 第一段 ")]
                if duration > 1:
                    segments.append(types.SimpleNamespace(start=1.0, end=duration, text=" 第二段 "))
                return iter(segments), types.SimpleNamespace(language="zh", language_probability=0.98)

        modules = {"faster_whisper": types.SimpleNamespace(WhisperModel=Model),
                   "faster_whisper.audio": types.SimpleNamespace(decode_audio=lambda *a, **k: [0] * (10 * cli.SAMPLE_RATE))}
        self.patches = [patch.dict(sys.modules, modules),
            patch.object(cli, "cached_audio", return_value=(root / "audio.webm", {"title": "中文测试"}, True))]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_partial_transcription_options_outputs_and_report(self):
        self.assertEqual(cli.run(self.args), 0)
        model = next(call for call in self.calls if call[0] == "model")
        self.assertEqual(model[1], "medium")
        self.assertEqual(model[2]["cpu_threads"], 6)
        self.assertEqual(model[2]["device"], "cpu")
        self.assertEqual(model[2]["compute_type"], "int8")
        call = next(call for call in self.calls if call[0] == "transcribe")
        self.assertEqual(call[1], 4 * cli.SAMPLE_RATE)
        self.assertNotIn("language", call[2])
        self.assertEqual(call[2], {"task": "transcribe", "vad_filter": False,
                                  "beam_size": 3, "condition_on_previous_text": True})
        srt = next(self.args.output_dir.rglob("*.srt")).read_text(encoding="utf-8")
        self.assertIn("00:00:02,000 --> 00:00:03,000", srt)
        self.assertIn("00:00:03,000 --> 00:00:06,000", srt)
        report = cli.read_json(next(self.args.output_dir.rglob("run-report.json")))
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["detected_language"], "zh")
        self.assertEqual(list((self.args.state_dir / "checkpoints").glob("*.json")), [])

    def test_pause_resume_keeps_prior_segments_and_bounds(self):
        original = cli.atomic_json

        def stop_after_segment(path, data):
            original(path, data)
            if path.parent.name == "checkpoints" and len(data.get("segments", [])) == 1:
                cli.STOP = True

        with patch.object(cli, "atomic_json", side_effect=stop_after_segment):
            self.assertEqual(cli.run(self.args), 75)
        state = cli.read_json(next((self.args.state_dir / "checkpoints").glob("*.json")))
        self.assertEqual(state["resume_from"], 3.0)
        self.assertFalse(list(self.args.output_dir.rglob("*.txt")))
        cli.STOP = False
        self.assertEqual(cli.run(self.args), 0)
        calls = [c for c in self.calls if c[0] == "transcribe"]
        self.assertEqual(calls[-1][1], 3 * cli.SAMPLE_RATE)
        self.assertEqual(calls[-1][2]["language"], "zh")
        self.assertEqual(calls[-1][2]["initial_prompt"], "第一段")
        self.assertEqual(len(list(self.args.output_dir.iterdir())), 1)
        text = next(self.args.output_dir.rglob("*.txt")).read_text(encoding="utf-8")
        self.assertEqual(len(text.splitlines()), 3)

    def test_two_completed_runs_do_not_overwrite(self):
        self.assertEqual(cli.run(self.args), 0)
        self.assertEqual(cli.run(self.args), 0)
        self.assertEqual(len(list(self.args.output_dir.rglob("*.txt"))), 2)

    def test_out_of_range_fails_and_records_error(self):
        self.args.start, self.args.end = 11, 12
        self.assertEqual(cli.run(self.args), 1)
        report = cli.read_json(next(self.args.output_dir.rglob("run-report.json")))
        self.assertEqual(report["status"], "failed")
        self.assertFalse(list(self.args.output_dir.rglob("*.txt")))


class InputTests(unittest.TestCase):
    def test_source_arguments_are_mutually_exclusive_and_required(self):
        for argv in ([], ["--url", "https://youtu.be/abcdefghijk", "--audio-file", "audio.wav"]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    cli.main(argv)
                self.assertEqual(error.exception.code, 2)

    def test_invalid_local_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            empty = root / "empty.wav"
            empty.touch()
            for path in (root / "missing.wav", root, empty):
                with self.subTest(path=path):
                    self.assertEqual(cli.main(["--audio-file", str(path),
                                               "--state-dir", str(root / "state")]), 2)
            self.assertFalse(list(root.rglob("run-report.json")))

    def test_time_formats(self):
        for value, seconds in [("90", 90), ("02:15", 135), ("90:00", 5400), ("01:02:15", 3735)]:
            self.assertEqual(cli.timestamp(value), seconds)
        for invalid in ("-1", "1:60", "a", "1:60:00"):
            with self.assertRaises(argparse.ArgumentTypeError):
                cli.timestamp(invalid)

    def test_video_urls(self):
        for url in ("https://youtu.be/abcdefghijk?si=test", "https://www.youtube.com/watch?v=abcdefghijk",
                    "https://youtube.com/shorts/abcdefghijk"):
            self.assertEqual(cli.video_id(url), "abcdefghijk")
        with self.assertRaises(ValueError):
            cli.video_id("https://other.example/watch?v=abcdefghijk")

    def test_corrupt_checkpoint_is_not_discarded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.json"
            path.write_text("{broken", encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                cli.read_json(path)


class LocalAudioTests(TranscriptionTests):
    """Run the same output, time-range, and resume checks for local inputs."""

    def setUp(self):
        super().setUp()
        self.args.url = None
        self.args.audio_file = Path(self.folder.name) / "中文录音.wav"
        self.args.audio_file.write_bytes(b"local audio fixture")
        self.no_download = patch.object(cli, "cached_audio",
                                       side_effect=AssertionError("Local audio must not download"))
        self.downloader = self.no_download.start()
        self.addCleanup(self.no_download.stop)

    def tearDown(self):
        self.downloader.assert_not_called()

    def test_local_source_metadata_and_safe_output_names(self):
        self.assertEqual(cli.run(self.args), 0)
        report = cli.read_json(next(self.args.output_dir.rglob("run-report.json")))
        self.assertTrue(report["download_skipped"])
        self.assertFalse(report["used_cached_audio"])
        self.assertEqual(report["timings_seconds"]["download_or_cache"], 0)
        self.assertEqual(report["source"]["path"], str(self.args.audio_file.resolve()))
        self.assertEqual(report["source"]["type"], "audio_file")
        self.assertEqual(report["source"]["sha256"], report["settings"]["audio_sha256"])
        self.assertNotIn("video_id", report["settings"])
        self.assertEqual(next(self.args.output_dir.rglob("*.txt")).stem,
                         report["settings"]["source_id"])

    def test_content_identity_survives_rename_and_changes_with_contents(self):
        original = cli.settings(self.args)
        renamed = self.args.audio_file.with_name("renamed.wav")
        self.args.audio_file.rename(renamed)
        self.args.audio_file = renamed
        self.assertEqual(cli.settings(self.args), original)
        renamed.write_bytes(b"different audio of same name")
        self.assertNotEqual(cli.task_key(cli.settings(self.args)), cli.task_key(original))

    def test_decode_failure_is_reported_without_download_fallback(self):
        with patch("faster_whisper.audio.decode_audio", side_effect=ValueError("invalid audio")):
            self.assertEqual(cli.run(self.args), 1)
        report = cli.read_json(next(self.args.output_dir.rglob("run-report.json")))
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["error"], "invalid audio")
        self.assertTrue(report["download_skipped"])
        self.assertFalse(list(self.args.output_dir.rglob("*.txt")))


if __name__ == "__main__":
    unittest.main()
