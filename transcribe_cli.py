"""CPU transcription of YouTube or local audio; no GUI or account cookies."""
import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import signal
import sys
import time
from urllib.parse import parse_qs, urlparse
import uuid

LOG = logging.getLogger("transcriber")
STOP = False
SAMPLE_RATE = 16000


def timestamp(value):
    parts = value.strip().split(":")
    if not 1 <= len(parts) <= 3 or any(not p.isdigit() for p in parts):
        raise argparse.ArgumentTypeError("时间格式应为秒、分:秒或时:分:秒")
    numbers = list(map(int, parts))
    if len(numbers) > 1 and numbers[-1] >= 60:
        raise argparse.ArgumentTypeError("秒应小于 60")
    if len(numbers) == 3 and numbers[-2] >= 60:
        raise argparse.ArgumentTypeError("小时格式中的分钟应小于 60")
    return float(sum(number * 60 ** index for index, number in enumerate(reversed(numbers))))


def video_id(url):
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("https", "http"):
        raise ValueError("需要 http/https YouTube 链接")
    if host in ("youtu.be", "www.youtu.be"):
        value = parsed.path.strip("/").split("/")[0]
    elif host in ("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"):
        if parsed.path in ("/watch", "/watch/"):
            value = parse_qs(parsed.query).get("v", [""])[0]
        else:
            match = re.fullmatch(r"/(?:shorts|live|embed)/([A-Za-z0-9_-]{11})/?", parsed.path)
            value = match.group(1) if match else ""
    else:
        value = ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        raise ValueError("无法识别 YouTube 视频 ID；请提供单个视频链接")
    return value


def read_json(path, default=None):
    if not path.exists():
        return default
    # Accept Windows UTF-8 BOM, but never silently discard a damaged checkpoint.
    return json.loads(path.read_text(encoding="utf-8-sig"))


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def request_stop(_signum, _frame):
    global STOP
    STOP = True
    LOG.warning("收到暂停请求；将在下一个安全点保存并退出。")


def stop_requested(args):
    return STOP or (args.pause_file is not None and args.pause_file.exists())


def cached_audio(url, cache, force=False):
    """Cache uses one directory per ID; metadata is not embedded in Git."""
    import yt_dlp
    identifier = video_id(url)
    folder = cache / identifier
    folder.mkdir(parents=True, exist_ok=True)
    metadata_path = folder / "metadata.json"
    entry = read_json(metadata_path, {})
    name = entry.get("audio_filename", "")
    audio = folder / name
    if name and Path(name).name == name and audio.is_file() and audio.stat().st_size > 0 and not force:
        entry["last_used"] = time.time()
        atomic_json(metadata_path, entry)
        LOG.info("正在使用缓存音频：%s", identifier)
        return audio, entry, True
    if force and name and Path(name).name == name and audio.is_file():
        audio.unlink()
    LOG.info("正在下载音频：%s", identifier)
    with yt_dlp.YoutubeDL({
        "format": "bestaudio", "noplaylist": True, "quiet": True,
        "outtmpl": str(folder / "source.%(ext)s"),
        "overwrites": force,
    }) as downloader:
        info = downloader.extract_info(url, download=True)
        if info.get("id") != identifier:
            raise RuntimeError("下载结果的视频 ID 与请求不符")
        audio = Path(downloader.prepare_filename(info))
    if not audio.is_file() or audio.stat().st_size == 0:
        raise RuntimeError("下载完成但未找到有效音频文件")
    now = time.time()
    entry = {"video_id": identifier, "title": info.get("title", identifier),
             "source_url": url, "download_time": now, "last_used": now,
             "audio_filename": audio.name, "duration": info.get("duration")}
    atomic_json(metadata_path, entry)
    return audio, entry, False


def srt_time(seconds):
    milliseconds = max(0, round(seconds * 1000))
    hours, milliseconds = divmod(milliseconds, 3600000)
    minutes, milliseconds = divmod(milliseconds, 60000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"


def settings(args):
    config = {"language": args.language,
            "model": args.model, "threads": args.threads, "beam_size": args.beam_size,
            "device": "cpu", "compute_type": "int8", "vad_filter": False,
            "condition_on_previous_text": True, "start": args.start, "end": args.end}
    audio_file = getattr(args, "audio_file", None)
    if bool(args.url) == (audio_file is not None):
        raise ValueError("--url 与 --audio-file 必须且只能提供一个")
    if audio_file is None:
        # Preserve existing YouTube checkpoint keys.
        config["video_id"] = video_id(args.url)
    else:
        audio_file = Path(audio_file).expanduser().resolve()
        if not audio_file.is_file():
            raise ValueError(f"音频文件不存在或不是普通文件：{audio_file}")
        with audio_file.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if audio_file.stat().st_size == 0:
            raise ValueError(f"音频文件为空：{audio_file}")
        # Content identity prevents unrelated uploads from sharing a checkpoint,
        # and permits resuming the same file after it has been moved or renamed.
        config.update(source_type="audio_file", audio_sha256=digest,
                      source_id="audio-" + digest[:24])
    return config


def task_key(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:24]


def peak_ram_mb():
    try:
        import resource
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(value / (1024 * 1024 if sys.platform == "darwin" else 1024), 1)
    except ImportError:
        return None


def versions():
    from importlib.metadata import version
    result = {"python": sys.version.split()[0]}
    for package in ("faster-whisper", "ctranslate2", "yt-dlp"):
        try:
            result[package] = version(package)
        except Exception:
            result[package] = "unavailable"
    return result


def run(args):
    started = time.monotonic()
    config = settings(args)
    key = task_key(config)
    state_path = args.state_dir / "checkpoints" / f"{key}.json"
    old = read_json(state_path)
    if old and old.get("settings") != config:
        raise RuntimeError("检查点配置不匹配")
    job = old or {"settings": config, "segments": [], "resume_from": args.start,
                  "run_id": time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]}
    output = Path(job.get("output_directory", str((args.output_dir / job["run_id"]).resolve())))
    output.mkdir(parents=True, exist_ok=True)
    job["output_directory"] = str(output)
    report = {"settings": config, "versions": versions(), "status": "running",
              "resumed": bool(old), "output_directory": str(output), "timings_seconds": {}}

    def persist():
        atomic_json(state_path, job)

    def paused():
        persist()
        report["status"] = "paused"
        report["resume_from"] = job["resume_from"]
        LOG.info("已暂停；从 %.2f 秒续做，重新运行相同命令即可。", job["resume_from"])
        return 75

    persist()
    try:
        from faster_whisper import WhisperModel
        from faster_whisper.audio import decode_audio
        phase = time.monotonic()
        if config.get("source_type") == "audio_file":
            audio = Path(args.audio_file).expanduser().resolve()
            metadata, reused = {"title": audio.name}, False
            report["source"] = {"type": "audio_file", "path": str(audio),
                                "sha256": config["audio_sha256"]}
            report["download_skipped"] = True
            report["timings_seconds"]["local_source_selection"] = time.monotonic() - phase
            report["timings_seconds"]["download_or_cache"] = 0.0
        else:
            audio, metadata, reused = cached_audio(args.url, args.state_dir / "audio-cache")
            report["timings_seconds"]["download_or_cache"] = time.monotonic() - phase
            report["source"] = {"type": "youtube", "url": args.url}
            report["download_skipped"] = False
        report["used_cached_audio"] = reused
        report["title"] = metadata["title"]
        if stop_requested(args):
            return paused()
        phase = time.monotonic()
        try:
            waveform = decode_audio(str(audio), sampling_rate=SAMPLE_RATE)
            if len(waveform) == 0:
                raise ValueError("空音频")
        except Exception:
            if not reused:
                raise
            LOG.warning("缓存音频无法解码，重新下载一次。")
            audio, metadata, _ = cached_audio(args.url, args.state_dir / "audio-cache", force=True)
            waveform = decode_audio(str(audio), sampling_rate=SAMPLE_RATE)
            if len(waveform) == 0:
                raise ValueError("重新下载后仍为空音频")
        duration = len(waveform) / SAMPLE_RATE
        end = min(args.end, duration) if args.end is not None else duration
        if args.start >= end:
            raise ValueError(f"所选时间段超出视频长度（{duration:.2f} 秒）")
        offset = max(args.start, float(job["resume_from"]))
        report.update(audio_duration_seconds=duration, selected_start=args.start, selected_end=end)
        report["timings_seconds"]["audio_decode"] = time.monotonic() - phase
        if stop_requested(args):
            return paused()
        # Crop before language detection, not only before decoding segments.
        if offset < end:
            selected = waveform[int(offset * SAMPLE_RATE):int(end * SAMPLE_RATE)]
            phase = time.monotonic()
            LOG.info("加载 %s 模型；CPU int8，%s 线程。", args.model, args.threads)
            model = WhisperModel(args.model, device="cpu", compute_type="int8",
                                 cpu_threads=args.threads, num_workers=1,
                                 download_root=str(args.model_dir))
            report["timings_seconds"]["model_load"] = time.monotonic() - phase
            if stop_requested(args):
                return paused()
            options = {"task": "transcribe", "vad_filter": False, "beam_size": args.beam_size,
                       "condition_on_previous_text": True}
            language = job.get("detected_language") if args.language == "auto" else args.language
            if language:
                options["language"] = language
            if job["segments"]:
                options["initial_prompt"] = " ".join(s["text"] for s in job["segments"][-3:])
            phase = time.monotonic()
            segments, info = model.transcribe(selected, **options)
            job["detected_language"] = info.language
            report["language_probability"] = info.language_probability
            persist()
            LOG.info("检测语言：%s；转录范围 %.2f–%.2f 秒。", info.language, offset, end)
            for segment in segments:
                start = max(offset, float(segment.start) + offset)
                stop = min(end, float(segment.end) + offset)
                if stop <= start:
                    continue
                job["segments"].append({"start": start, "end": stop, "text": segment.text.strip()})
                job["resume_from"] = stop
                persist()
                LOG.info("已完成 %.2f / %.2f 秒（所选范围 %.1f%%）", stop, end,
                         100 * (stop - args.start) / (end - args.start))
                if stop_requested(args):
                    report["timings_seconds"]["transcription"] = time.monotonic() - phase
                    return paused()
            report["timings_seconds"]["transcription"] = time.monotonic() - phase
        # Complete outputs are atomic. Each new completed run uses its own folder.
        for extension in ("txt", "srt"):
            identifier = config.get("source_id", config.get("video_id"))
            path = output / (identifier + "." + extension)
            temporary = path.with_suffix(path.suffix + ".tmp")
            with temporary.open("w", encoding="utf-8") as stream:
                for index, segment in enumerate(job["segments"], 1):
                    if extension == "srt":
                        stream.write(f"{index}\n{srt_time(segment['start'])} --> {srt_time(segment['end'])}\n")
                    stream.write(segment["text"] + ("\n\n" if extension == "srt" else "\n"))
            temporary.replace(path)
        report["status"] = "completed"
        report["segment_count"] = len(job["segments"])
        state_path.unlink(missing_ok=True)
        LOG.info("完成：%s", output)
        return 0
    except Exception as exc:
        report.update(status="failed", error=str(exc))
        persist()
        LOG.error("任务失败：%s；已保留检查点，修复后可用相同命令重试。", exc)
        return 1
    finally:
        report["detected_language"] = job.get("detected_language")
        report["last_completed_audio_second"] = job["resume_from"]
        report["timings_seconds"]["total_this_invocation"] = time.monotonic() - started
        report["peak_ram_mb"] = peak_ram_mb()
        atomic_json(output / "run-report.json", report)


def main(argv=None):
    parser = argparse.ArgumentParser(description="YouTube 或本地音频云端 CPU 转文字（无需窗口）")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--url", help="YouTube 视频链接")
    source.add_argument("--audio-file", type=Path, help="本地音频文件路径（跳过 YouTube 下载）")
    parser.add_argument("--language", choices=("auto", "zh", "en"), default="auto")
    parser.add_argument("--model", choices=("small", "medium", "large-v3"), default="medium")
    parser.add_argument("--threads", type=int, default=6)
    parser.add_argument("--beam-size", type=int, choices=(1, 3, 5), default=3)
    parser.add_argument("--start", type=timestamp, default=0.0)
    parser.add_argument("--end", type=timestamp)
    parser.add_argument("--state-dir", type=Path, default=Path(os.getenv("YT_STATE_DIR", "state")))
    parser.add_argument("--model-dir", type=Path, default=Path(os.getenv("YT_MODEL_DIR", "models")))
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--pause-file", type=Path, help="检测到此文件时在安全点暂停；恢复前删除它")
    args = parser.parse_args(argv)
    if args.threads < 1 or (args.end is not None and args.end <= args.start):
        parser.error("线程必须为正数，结束时间必须晚于开始时间")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    try:
        # Only one writer per state directory, including across independent tasks.
        args.state_dir.mkdir(parents=True, exist_ok=True)
        from filelock import FileLock, Timeout
        lock = FileLock(str(args.state_dir / "transcriber.lock"), timeout=0)
        try:
            with lock:
                return run(args)
        except Timeout:
            LOG.error("此状态目录已有任务运行，请等待或使用另一 --state-dir。")
            return 2
    except (ValueError, OSError) as exc:
        LOG.error("无法启动：%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
