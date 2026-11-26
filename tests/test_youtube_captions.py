import json
import unittest
from unittest.mock import patch

import youtube_captions as captions


VIDEO_ID = "abcdefghijk"
URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"


def timedtext(language, extra="", video_id=VIDEO_ID):
    return [{
        "ext": "json3",
        "url": f"https://www.youtube.com/api/timedtext?v={video_id}&lang={language}&fmt=json3{extra}",
    }]


class CaptionTests(unittest.TestCase):
    def setUp(self):
        self.manual = timedtext("zh-Hans")
        self.auto = timedtext("zh", "&kind=asr")
        self.info = {
            "id": VIDEO_ID, "availability": "public", "duration": 120, "channel": "测试频道",
            "subtitles": {"en": timedtext("en"), "zh-Hans": self.manual},
            "automatic_captions": {"zh-orig": self.auto},
        }

    def test_manual_original_takes_priority_and_only_caption_file_is_read(self):
        with patch("yt_dlp.YoutubeDL") as factory:
            downloader = factory.return_value.__enter__.return_value
            downloader.extract_info.return_value = self.info
            response = downloader.urlopen.return_value.__enter__.return_value
            response.url = self.manual[0]["url"]
            response.read.return_value = json.dumps({"events": [
                {"tStartMs": 2000, "dDurationMs": 3000, "segs": [{"utf8": "第一句\n "}, {"utf8": "再补充"}]},
                {"tStartMs": 8000, "dDurationMs": 1500, "segs": [{"utf8": "第二句"}]},
                {"tStartMs": 20000},
            ]}).encode()
            result = captions.fetch_captions(f"https://youtu.be/{VIDEO_ID}?list=abc")
        downloader.extract_info.assert_called_once_with(URL, download=False)
        self.assertTrue(factory.call_args.args[0]["skip_download"])
        self.assertFalse(factory.call_args.args[0]["check_formats"])
        downloader.urlopen.assert_called_once_with(self.manual[0]["url"])
        self.assertEqual(result["caption_source"], "channel")
        self.assertEqual(result["caption_language"], "zh-Hans")
        self.assertEqual(result["text"], "第一句 再补充\n第二句")
        self.assertEqual((result["first_caption_second"], result["last_caption_second"]), (2, 9.5))
        self.assertEqual(result["video_duration_seconds"], 120)

    def test_auto_fallback_excludes_translated_and_wrong_video_tracks(self):
        self.info["subtitles"]["zh-Hans"] = timedtext("zh-Hans", video_id="lmnopqrstuv")
        self.info["automatic_captions"]["en"] = timedtext("zh", "&kind=asr&tlang=en")
        self.assertEqual(captions.select_caption(self.info, VIDEO_ID),
                         (self.auto[0]["url"], "youtube_auto", "zh", "zh"))
        del self.info["automatic_captions"]["zh-orig"]
        self.info["subtitles"].pop("en")
        with self.assertRaisesRegex(ValueError, "没有找到"):
            captions.select_caption(self.info, VIDEO_ID)

    def test_unknown_original_with_multiple_manual_tracks_requires_selection(self):
        self.info["automatic_captions"] = {}
        with self.assertRaisesRegex(ValueError, "可用语言.*频道方字幕为 en, zh-Hans"):
            captions.select_caption(self.info, VIDEO_ID)
        _, source, lang, original = captions.select_caption(self.info, VIDEO_ID, "en")
        self.assertEqual((source, lang, original), ("channel", "en", None))

    def test_multiple_manual_variants_do_not_fall_back_to_automatic_captions(self):
        traditional = timedtext("zh-Hant")
        self.info["subtitles"]["zh-Hant"] = traditional
        for language in (None, "zh"):
            with self.subTest(language=language), self.assertRaises(ValueError) as failure:
                captions.select_caption(self.info, VIDEO_ID, language)
            message = str(failure.exception)
            self.assertIn("频道方字幕有多个匹配的语言或变体（zh-Hans, zh-Hant）", message)
            self.assertIn("YouTube 自动字幕为 zh", message)
            self.assertIn("--language", message)
        for language, track in (("zh-Hans", self.manual), ("zh-Hant", traditional)):
            with self.subTest(language=language):
                self.assertEqual(captions.select_caption(self.info, VIDEO_ID, language),
                                 (track[0]["url"], "channel", language, "zh"))

    def test_unmatched_language_lists_available_same_video_tracks(self):
        self.info["subtitles"]["fr"] = timedtext("fr", video_id="lmnopqrstuv")
        with self.assertRaisesRegex(ValueError, "可用语言.*频道方字幕为 en, zh-Hans；YouTube 自动字幕为 zh。"):
            captions.select_caption(self.info, VIDEO_ID, "ja")

    def test_unconfirmed_video_id_or_public_status_stops_before_caption_request(self):
        for change in ({"id": "lmnopqrstuv"}, {"availability": "subscriber_only"}):
            with self.subTest(change=change), patch("yt_dlp.YoutubeDL") as factory:
                downloader = factory.return_value.__enter__.return_value
                downloader.extract_info.return_value = {**self.info, **change}
                with self.assertRaises(ValueError):
                    captions.fetch_captions(URL)
                downloader.urlopen.assert_not_called()

    def test_wrong_video_response_is_rejected_before_reading_body(self):
        with patch("yt_dlp.YoutubeDL") as factory:
            downloader = factory.return_value.__enter__.return_value
            downloader.extract_info.return_value = self.info
            response = downloader.urlopen.return_value.__enter__.return_value
            response.url = timedtext("zh-Hans", video_id="lmnopqrstuv")[0]["url"]
            with self.assertRaisesRegex(ValueError, "字幕响应"):
                captions.fetch_captions(URL)
        response.read.assert_not_called()
