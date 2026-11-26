"""Read public captions attached to one YouTube video without downloading media."""
import argparse
import json
import sys
from urllib.parse import parse_qs, urlparse

from transcribe_cli import video_id, youtube_failure, youtube_options


def track_language(url, identifier, automatic):
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (parsed.scheme != "https" or parsed.netloc not in {"www.youtube.com", "youtube.com"}
            or parsed.path != "/api/timedtext" or query.get("v") != [identifier]
            or "tlang" in query or (query.get("kind") == ["asr"]) != automatic):
        return None
    languages = query.get("lang", [])
    return languages[0] if len(languages) == 1 and languages[0] else None


def caption_tracks(info, identifier, automatic):
    """Keep original, same-video YouTube timedtext tracks in JSON3 format."""
    tracks = {}
    key = "automatic_captions" if automatic else "subtitles"
    for entries in (info.get(key) or {}).values():
        for entry in entries:
            url = entry.get("url") or ""
            if entry.get("ext") == "json3" and (language := track_language(url, identifier, automatic)):
                tracks.setdefault(language, url)
    return tracks


def matching_language(tracks, original):
    if not original:
        return next(iter(tracks)) if len(tracks) == 1 else None
    exact = [lang for lang in tracks if lang.casefold() == original.casefold()]
    if len(exact) == 1:
        return exact[0]
    primary = original.casefold().split("-")[0]
    same_language = [lang for lang in tracks if lang.casefold().split("-")[0] == primary]
    return same_language[0] if len(same_language) == 1 else None


def select_caption(info, identifier, requested_language=None):
    manual = caption_tracks(info, identifier, automatic=False)
    automatic = caption_tracks(info, identifier, automatic=True)
    # YouTube's yt-dlp extractor labels original ASR tracks with "-orig"
    # and assigns language_preference=10 to audio it identifies as original.
    original_tracks = {lang: tracks for lang, tracks in (info.get("automatic_captions") or {}).items()
                       if lang.endswith("-orig")}
    originals = set(caption_tracks({"automatic_captions": original_tracks}, identifier, automatic=True))
    if not originals:
        originals = {fmt["language"] for fmt in info.get("formats", [])
                     if fmt.get("language_preference") == 10 and fmt.get("language")}
    original = next(iter(originals)) if len(originals) == 1 else None
    for tracks, source in ((manual, "channel"), (automatic, "youtube_auto")):
        language = matching_language(tracks, requested_language or original)
        if language:
            return tracks[language], source, language, original
    raise ValueError("没有找到该视频可明确选择的同源字幕；可能没有字幕，或无法确定原语言")


def caption_cues(data):
    cues = []
    for event in data.get("events") or []:
        segments = event.get("segs") or []
        text = " ".join("".join(segment.get("utf8", "") for segment in segments).split())
        start = event.get("tStartMs")
        duration = event.get("dDurationMs", 0)
        if text and isinstance(start, int) and isinstance(duration, int) and start >= 0 and duration >= 0:
            cues.append((start, start + duration, text))
    if not cues:
        raise ValueError("该视频的字幕轨道未返回可读文字")
    return sorted(cues, key=lambda cue: cue[0])


def fetch_captions(url, requested_language=None):
    import yt_dlp
    from yt_dlp.networking.exceptions import RequestError

    identifier = video_id(url)
    canonical_url = f"https://www.youtube.com/watch?v={identifier}"
    options = {
        **youtube_options(),
        "format": "bestaudio/best",
        "skip_download": True,
        "ignore_no_formats_error": True,
        "check_formats": False,
    }
    with yt_dlp.YoutubeDL(options) as downloader:
        try:
            info = downloader.extract_info(canonical_url, download=False)
        except yt_dlp.utils.DownloadError as exc:
            raise youtube_failure(exc, options, "YouTube 视频信息") from exc
        if not info or info.get("id") != identifier:
            raise ValueError("字幕来源的视频 ID 与请求不符")
        if info.get("availability") != "public":
            raise ValueError("无法确认视频为公开视频")
        track_url, source, language, original = select_caption(info, identifier, requested_language)
        try:
            with downloader.urlopen(track_url) as response:
                if track_language(response.url, identifier, source == "youtube_auto") != language:
                    raise ValueError("字幕响应与选定视频、语言或字幕类型不符")
                payload = response.read()
        except RequestError as exc:
            raise youtube_failure(exc, options, "字幕文件") from exc
    try:
        data = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("该视频的字幕轨道未返回有效 JSON3") from exc
    if not isinstance(data, dict):
        raise ValueError("该视频的字幕轨道未返回有效 JSON3")
    cues = caption_cues(data)
    return {
        "video_id": identifier,
        "url": canonical_url,
        "title": info.get("title"),
        "channel": info.get("channel"),
        "caption_source": source,
        "caption_language": language,
        "original_language": original,
        "video_duration_seconds": info.get("duration"),
        "first_caption_second": cues[0][0] / 1000,
        "last_caption_second": max(end for _, end, _ in cues) / 1000,
        "caption_cue_count": len(cues),
        "text": "\n".join(text for _, _, text in cues),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="读取单个 YouTube 视频的公开字幕，输出文字和来源 JSON")
    parser.add_argument("--url", required=True, help="单个 YouTube 视频链接")
    parser.add_argument("--language", help="明确指定同视频字幕语言，如 zh-Hans、yue 或 en")
    args = parser.parse_args(argv)
    try:
        result = fetch_captions(args.url, args.language)
    except (ValueError, RuntimeError, OSError, ImportError) as exc:
        print(f"无法获取公开字幕：{exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
