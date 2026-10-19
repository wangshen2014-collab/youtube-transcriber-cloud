"""Export reusable source audio, or an accurately trimmed AAC file using PyAV."""
from fractions import Fraction
from pathlib import Path
import re
import json
import os
import time
import uuid


class ExportPaused(Exception):
    pass


def safe_name(title):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', title).strip().rstrip('.')[:100] or 'YouTube'


def clock_label(seconds):
    hours, seconds = divmod(int(seconds), 3600)
    minutes, seconds = divmod(seconds, 60)
    return f'{hours:02}-{minutes:02}-{seconds:02}'


def export_registry():
    return Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'YouTubeTranscriber' / 'audio-exports.json'


def write_registry(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def track_export(path, registry=None):
    registry = registry or export_registry()
    entries = json.loads(registry.read_text(encoding='utf-8')) if registry.exists() else []
    path = Path(path).resolve()
    stat = path.stat()
    entries.append({'path': str(path), 'exported_at': time.time(),
                    'expires_at': time.time() + 7 * 86400,
                    'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns})
    write_registry(registry, entries)


def cleanup_exports(registry=None, now=None):
    registry = registry or export_registry()
    if not registry.exists():
        return 0
    now = time.time() if now is None else now
    entries = json.loads(registry.read_text(encoding='utf-8'))
    kept, removed = [], 0
    for entry in entries:
        path = Path(entry['path'])
        if not path.is_file():
            continue
        if entry['expires_at'] > now:
            kept.append(entry)
            continue
        stat = path.stat()
        # Only delete our recorded export if the file has not since been replaced/edited.
        if stat.st_size != entry['size'] or stat.st_mtime_ns != entry['mtime_ns']:
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:
            kept.append(entry)
    write_registry(registry, kept)
    return removed


def export_audio(source, output_dir, title, identifier, start=0, end=None,
                 partial=False, should_pause=lambda: False):
    """Never modify cached audio or overwrite a user file. Partial timestamps reset to 0."""
    source, output_dir = Path(source), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    identifier = re.sub(r'[^A-Za-z0-9_-]', '_', str(identifier))[:30]
    suffix = '.m4a' if partial else source.suffix
    label = f' [{clock_label(start)} 至 {clock_label(end) if end is not None else "结尾"}]' if partial else ''
    target = output_dir / f'{safe_name(title)}{label}{suffix}'
    if target.exists():
        target = output_dir / f'{safe_name(title)}{label} ({time.strftime("%Y%m%d-%H%M%S")}-{uuid.uuid4().hex[:4]}){suffix}'
    try:
        with target.open('xb') as destination:
            if not partial:
                with source.open('rb') as original:
                    while block := original.read(1024 * 1024):
                        if should_pause():
                            raise ExportPaused()
                        destination.write(block)
            else:
                import av
                rate = 48000
                first_sample = int(start * rate)
                last_sample = int(end * rate) if end is not None else None
                consumed = written = 0
                with av.open(str(source)) as container, av.open(destination, mode='w', format='ipod') as trimmed:
                    audio = next((s for s in container.streams if s.type == 'audio'), None)
                    if audio is None:
                        raise ValueError('文件不包含音轨')
                    stream = trimmed.add_stream('aac', rate=rate)
                    stream.layout = 'mono'
                    stream.bit_rate = 96000
                    resampler = av.AudioResampler(format='fltp', layout='mono', rate=rate)

                    def write_frame(frame):
                        nonlocal consumed, written
                        if should_pause():
                            raise ExportPaused()
                        left = max(0, first_sample - consumed)
                        right = min(frame.samples, last_sample - consumed) if last_sample is not None else frame.samples
                        consumed += frame.samples
                        if right > left:
                            samples = frame.to_ndarray()[:, left:right].copy()
                            clipped = av.AudioFrame.from_ndarray(samples, format='fltp', layout='mono')
                            clipped.sample_rate = rate
                            clipped.pts = written
                            clipped.time_base = Fraction(1, rate)
                            written += right - left
                            for packet in stream.encode(clipped):
                                trimmed.mux(packet)

                    for frame in container.decode(audio):
                        for resampled in resampler.resample(frame):
                            write_frame(resampled)
                        if last_sample is not None and consumed >= last_sample:
                            break
                    else:
                        for resampled in resampler.resample(None):
                            write_frame(resampled)
                    if not written:
                        raise ValueError('指定时间段没有音频，请检查开始和结束时间')
                    for packet in stream.encode(None):
                        trimmed.mux(packet)
        return target
    except BaseException:
        target.unlink(missing_ok=True)
        raise
