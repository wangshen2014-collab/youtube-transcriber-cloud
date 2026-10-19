import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import ctypes
import uuid
from datetime import datetime
from pathlib import Path
from tkinter import Tk, Toplevel, StringVar, Text, END, DISABLED, NORMAL, filedialog, messagebox
from tkinter import ttk
from audio_export import export_audio, ExportPaused, track_export, cleanup_exports

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
LOCAL_APP_DATA = Path(os.environ.get("LOCALAPPDATA", APP_DIR)) / "YouTubeTranscriber"
TEMP_DIR = LOCAL_APP_DATA / "temp"
MODEL_DIR = LOCAL_APP_DATA / "models"
AUDIO_CACHE_DIR = LOCAL_APP_DATA / "audio-cache"
CACHE_METADATA_PATH = AUDIO_CACHE_DIR / "metadata.json"
TASK_QUEUE_PATH = LOCAL_APP_DATA / "task-queue.json"
CHECKPOINT_DIR = LOCAL_APP_DATA / "checkpoints"

def atomic_write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)

def load_task_queue():
    try:
        data = json.loads(TASK_QUEUE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []

def checkpoint_path(task):
    return CHECKPOINT_DIR / f"{task['id']}.json"

def load_checkpoint(task):
    try:
        data = json.loads(checkpoint_path(task).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"segments": []}
    except Exception:
        return {"segments": []}

def save_checkpoint(task, checkpoint):
    atomic_write_json(checkpoint_path(task), checkpoint)

def set_system_sleep_prevention(enabled):
    """Keep Windows awake from automatic sleep; this does not block shutdown."""
    continuous = 0x80000000
    system_required = 0x00000001
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(continuous | system_required if enabled else continuous)
        return True
    except Exception:
        return False

def load_config():
    defaults = {"cpu_threads": 6, "temp_max_age_hours": 24,
                "default_output_folder": "Documents/YouTube Transcripts"}
    try:
        defaults.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except Exception:
        pass
    return defaults

CONFIG = load_config()
DEFAULT_OUTPUT = Path.home() / CONFIG["default_output_folder"]
PROFILE_THREADS = CONFIG.get("performance_threads", {"Quiet": 6, "Balanced": 6, "Fast": 6})
BEAM_SIZES = {"Fast": 1, "Balanced": 3, "Maximum Accuracy": 5, "快速": 1, "均衡（推荐）": 3, "最高准确率": 5}
RETENTION_SECONDS = {"48 hours": 48 * 3600, "7 days": 7 * 86400, "30 days": 30 * 86400, "Keep until manually deleted": None, "48 小时": 48 * 3600, "7 天（推荐）": 7 * 86400, "30 天": 30 * 86400, "手动清理前一直保留": None}

def load_cache_metadata():
    try:
        data = json.loads(CACHE_METADATA_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"videos": {}}
    except Exception:
        return {"videos": {}}

def save_cache_metadata(data):
    AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_METADATA_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def cache_file_path(entry):
    return AUDIO_CACHE_DIR / entry.get("audio_filename", "")

def cleanup_audio_cache(retention_label):
    AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data = load_cache_metadata(); videos = data.setdefault("videos", {})
    retention = RETENTION_SECONDS.get(retention_label, RETENTION_SECONDS["7 days"])
    if retention is None:
        return
    cutoff = time.time() - retention
    for video_id, entry in list(videos.items()):
        last_used = float(entry.get("last_used", entry.get("download_time", 0)))
        if last_used < cutoff or not cache_file_path(entry).is_file():
            try: cache_file_path(entry).unlink(missing_ok=True)
            except OSError: pass
            videos.pop(video_id, None)
    save_cache_metadata(data)

def clear_audio_cache():
    if AUDIO_CACHE_DIR.exists():
        for item in AUDIO_CACHE_DIR.iterdir():
            if item.is_file(): item.unlink(missing_ok=True)
            elif item.is_dir(): shutil.rmtree(item, ignore_errors=True)
    AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    save_cache_metadata({"videos": {}})

def cache_summary():
    AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    files = [p for p in AUDIO_CACHE_DIR.iterdir() if p.is_file() and p.name != CACHE_METADATA_PATH.name]
    total = sum(p.stat().st_size for p in files)
    return f"音频缓存：{len(files)} 个文件 / {total / 1024 / 1024:.1f} MB"

def get_cached_or_download_audio(url, emit, force_download=False):
    """Returns (audio_path, video_info, came_from_cache), keyed by video ID."""
    import yt_dlp
    AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    probe_options = {"quiet": True, "no_warnings": True, "noplaylist": True}
    with yt_dlp.YoutubeDL(probe_options) as ydl:
        video_info = ydl.extract_info(url, download=False)
    video_id = video_info.get("id")
    if not video_id:
        raise RuntimeError("Could not determine the YouTube video ID.")
    data = load_cache_metadata(); videos = data.setdefault("videos", {})
    entry = videos.get(video_id)
    if entry and not force_download and cache_file_path(entry).is_file():
        entry["last_used"] = time.time(); save_cache_metadata(data)
        emit("status", "正在使用缓存音频…")
        return cache_file_path(entry), video_info, True
    if entry:
        try: cache_file_path(entry).unlink(missing_ok=True)
        except OSError: pass
        videos.pop(video_id, None)
    emit("status", "正在下载最佳音频到本地缓存…")
    options = {"format": "bestaudio/best", "outtmpl": str(AUDIO_CACHE_DIR / f"{video_id}.%(ext)s"), "noplaylist": True, "quiet": True, "no_warnings": True}
    with yt_dlp.YoutubeDL(options) as ydl:
        downloaded_info = ydl.extract_info(url, download=True)
    candidates = [p for p in AUDIO_CACHE_DIR.glob(f"{video_id}.*") if p.name != CACHE_METADATA_PATH.name]
    if not candidates:
        raise RuntimeError("yt-dlp completed but did not produce cached audio.")
    audio_path = max(candidates, key=lambda p: p.stat().st_mtime)
    now = time.time()
    videos[video_id] = {"video_id": video_id, "video_title": downloaded_info.get("title", video_info.get("title", "YouTube transcript")), "source_url": url, "download_time": now, "last_used": now, "audio_filename": audio_path.name}
    save_cache_metadata(data)
    emit("cache_updated", None)
    return audio_path, downloaded_info, False

def remove_cached_audio_for_url(url):
    """Best-effort removal used only after a cached file fails to decode."""
    data = load_cache_metadata(); videos = data.setdefault("videos", {})
    for video_id, entry in list(videos.items()):
        if entry.get("source_url") == url:
            try: cache_file_path(entry).unlink(missing_ok=True)
            except OSError: pass
            videos.pop(video_id, None)
            save_cache_metadata(data)
            return

def format_duration(seconds):
    seconds = max(0, int(seconds))
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"

def parse_timestamp(value):
    """Accept seconds, MM:SS, or HH:MM:SS and return seconds."""
    value = value.strip()
    if not value:
        return None
    parts = value.split(":")
    if not 1 <= len(parts) <= 3 or any(not part.isdigit() for part in parts):
        raise ValueError("时间请使用 秒、分:秒 或 时:分:秒，例如 90、02:15 或 01:02:15。")
    numbers = [int(part) for part in parts]
    if len(numbers) == 1:
        return float(numbers[0])
    if numbers[-1] >= 60 or numbers[-2] >= 60:
        raise ValueError("分钟和秒必须小于 60。")
    if len(numbers) == 2:
        return float(numbers[0] * 60 + numbers[1])
    return float(numbers[0] * 3600 + numbers[1] * 60 + numbers[2])

def cleanup_old_temp():
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - int(CONFIG["temp_max_age_hours"]) * 3600
    for item in TEMP_DIR.iterdir():
        try:
            if item.stat().st_mtime < cutoff:
                if item.is_dir(): shutil.rmtree(item)
                else: item.unlink()
        except OSError:
            pass

def sanitize(name):
    name = re.sub(r'[<>:"/\\\\|?*\\x00-\\x1f]', '_', name).strip().rstrip('.')
    return (name[:150] or "YouTube transcript")

def unique_base(folder, title):
    base = sanitize(title)
    candidate = folder / base
    if not candidate.with_suffix('.txt').exists() and not candidate.with_suffix('.srt').exists():
        return candidate
    stamped = f"{base} {datetime.now():%Y%m%d-%H%M%S}"
    candidate = folder / stamped
    suffix = 2
    while candidate.with_suffix('.txt').exists() or candidate.with_suffix('.srt').exists():
        candidate = folder / f"{stamped} ({suffix})"
        suffix += 1
    return candidate

def srt_time(seconds):
    milliseconds = max(0, round(seconds * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    seconds, milliseconds = divmod(milliseconds, 1_000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("YouTube 视频转文字")
        self.root.geometry("760x800")
        self.root.minsize(650, 640)
        self.events = queue.Queue()
        self.tasks_lock = threading.Lock()
        self.tasks = load_task_queue()
        for task in self.tasks:
            # A process ending while a task ran leaves its checkpoint intact.
            if task.get("state") == "running":
                task["state"] = "queued"
        self.scheduler_thread = None
        self.running_task_id = None
        self.pause_requested = threading.Event()
        self.last_txt = None
        self.loaded_models = {}
        self.url = StringVar()
        self.language = StringVar(value="自动检测（推荐）")
        self.quality = StringVar(value="均衡（medium，推荐）")
        self.performance = StringVar(value=CONFIG.get("performance_profile", "Balanced"))
        self.decoding = StringVar(value="均衡（推荐）")
        self.operation = StringVar(value="转录文字（默认）")
        self.range_mode = StringVar(value="完整视频（默认）")
        self.range_start = StringVar(value="")
        self.range_end = StringVar(value="")
        self.cache_retention = StringVar(value="7 天（推荐）")
        self.output = StringVar(value=str(DEFAULT_OUTPUT))
        self.cache_info = StringVar(value=cache_summary())
        self.queue_info = StringVar(value="任务队列：等待开始")
        self.sleep_info = StringVar(value="防止自动休眠：未启用")
        self.progress = StringVar(value="转录进度：等待开始")
        self.remaining = StringVar(value="预计剩余时间：—")
        self.job_started = None
        self.title = StringVar(value="视频标题：—")
        self.detected = StringVar(value="检测到的语言：—")
        self.elapsed = StringVar(value="已用时间：—")
        self.build()
        cleanup_old_temp()
        cleanup_audio_cache(self.cache_retention.get())
        self.refresh_cache_info()
        self.save_tasks()
        self.update_queue_info()
        self.root.after(100, self.poll)
        self.root.after(1000, self.clean_exported_audio)
        if any(task.get("state") == "queued" for task in self.tasks):
            self.append("检测到上次未完成的任务，已加入队列继续。")
            self.ensure_scheduler()

    def build(self):
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)
        header = ttk.Frame(outer)
        header.grid(column=0, row=0, columnspan=3, sticky="ew", pady=(0,14))
        ttk.Label(header, text="YouTube 视频转文字", font=("Microsoft YaHei UI", 16, "bold")).pack(side="left")
        operation = ttk.Combobox(header, textvariable=self.operation, values=["转录文字（默认）", "仅下载音频"], state="readonly", width=20)
        operation.pack(side="right")
        operation.bind("<<ComboboxSelected>>", lambda _event: self.transcribe_btn.configure(text="下载音频" if self.operation.get() == "仅下载音频" else "开始转录"))
        ttk.Label(outer, text="YouTube 链接:").grid(column=0, row=1, sticky="w", pady=5)
        self.url_entry = ttk.Entry(outer, textvariable=self.url)
        self.url_entry.grid(column=1, row=1, columnspan=2, sticky="ew", pady=5)
        ttk.Label(outer, text="视频语言:").grid(column=0, row=2, sticky="w", pady=5)
        ttk.Combobox(outer, textvariable=self.language, values=["自动检测（推荐）", "中文", "英文"], state="readonly", width=20).grid(column=1, row=2, sticky="w", pady=5)
        ttk.Label(outer, text="转录模型:").grid(column=0, row=3, sticky="w", pady=5)
        ttk.Combobox(outer, textvariable=self.quality, values=["快速（small）", "均衡（medium，推荐）", "高精度（large-v3）"], state="readonly", width=24).grid(column=1, row=3, sticky="w", pady=5)
        ttk.Label(outer, text="实测中文准确率很高，适合绝大多数视频。", foreground="#555555").grid(column=1, row=4, columnspan=2, sticky="w", pady=(0,5))
        ttk.Label(outer, text="解码模式:").grid(column=0, row=5, sticky="w", pady=5)
        ttk.Combobox(outer, textvariable=self.decoding, values=["快速", "均衡（推荐）", "最高准确率"], state="readonly", width=20).grid(column=1, row=5, sticky="w", pady=5)
        ttk.Button(outer, text="?", width=3, command=lambda: self.show_info("解码模式", "本机实测（medium + 6线程）：beam 1 为44.8秒，beam 3 为47.0秒。推荐 beam 3：只增加约5%时间，但文字输出存在差异。beam 5 计算开销明显更高且测试波动较大。")).grid(column=2, row=5, padx=(8,0), pady=5)
        ttk.Label(outer, text="beam 3：实测只比快速模式慢约 5%，但文字更稳定。", foreground="#555555").grid(column=1, row=6, columnspan=2, sticky="w", pady=(0,5))
        ttk.Label(outer, text="转录范围:").grid(column=0, row=7, sticky="w", pady=5)
        range_selector = ttk.Combobox(outer, textvariable=self.range_mode, values=["完整视频（默认）", "自定义时间段"], state="readonly", width=20)
        range_selector.grid(column=1, row=7, sticky="w", pady=5)
        range_selector.bind("<<ComboboxSelected>>", self.on_range_changed)
        range_fields = ttk.Frame(outer)
        range_fields.grid(column=1, row=8, columnspan=2, sticky="w", pady=(0,5))
        ttk.Label(range_fields, text="开始").pack(side="left")
        self.range_start_entry = ttk.Entry(range_fields, textvariable=self.range_start, width=10, state=DISABLED)
        self.range_start_entry.pack(side="left", padx=(4,12))
        ttk.Label(range_fields, text="结束").pack(side="left")
        self.range_end_entry = ttk.Entry(range_fields, textvariable=self.range_end, width=10, state=DISABLED)
        self.range_end_entry.pack(side="left", padx=4)
        ttk.Label(outer, text="格式：分:秒或时:分:秒；结束留空表示到视频结尾。", foreground="#555555").grid(column=1, row=9, columnspan=2, sticky="w", pady=(0,5))
        ttk.Label(outer, text="音频缓存保留时间:").grid(column=0, row=10, sticky="w", pady=5)
        retention = ttk.Combobox(outer, textvariable=self.cache_retention, values=["48 小时", "7 天（推荐）", "30 天", "手动清理前一直保留"], state="readonly", width=24)
        retention.grid(column=1, row=10, sticky="w", pady=5)
        retention.bind("<<ComboboxSelected>>", self.on_retention_changed)
        ttk.Label(outer, text="再次处理同一视频时无需重新下载。", foreground="#555555").grid(column=1, row=11, columnspan=2, sticky="w", pady=(0,5))
        ttk.Label(outer, text="输出目录:").grid(column=0, row=12, sticky="w", pady=5)
        ttk.Entry(outer, textvariable=self.output).grid(column=1, row=12, sticky="ew", pady=5)
        ttk.Button(outer, text="浏览", command=self.browse).grid(column=2, row=12, padx=(8,0), pady=5)
        primary_controls = ttk.Frame(outer)
        primary_controls.grid(column=0, row=13, columnspan=3, sticky="w", pady=(12,4))
        controls = ttk.Frame(outer)
        controls.grid(column=0, row=14, columnspan=3, sticky="w", pady=(0,8))
        self.transcribe_btn = ttk.Button(primary_controls, text="开始转录", command=self.start)
        self.transcribe_btn.pack(side="left")
        self.pause_btn = ttk.Button(primary_controls, text="暂停当前任务", command=self.request_pause, state=DISABLED)
        self.pause_btn.pack(side="left", padx=8)
        ttk.Button(primary_controls, text="继续未完成任务", command=self.resume_incomplete_tasks).pack(side="left", padx=8)
        ttk.Button(controls, text="更新下载组件", command=self.update_ytdlp).pack(side="left", padx=8)
        self.open_btn = ttk.Button(controls, text="打开字幕目录", command=self.open_folder, state=DISABLED)
        self.open_btn.pack(side="left")
        self.copy_btn = ttk.Button(controls, text="复制文字", command=self.copy_text, state=DISABLED)
        self.copy_btn.pack(side="left", padx=8)
        ttk.Button(controls, text="打开音频缓存", command=self.open_audio_cache).pack(side="left", padx=8)
        ttk.Button(controls, text="清空音频缓存", command=self.confirm_clear_audio_cache).pack(side="left")
        ttk.Button(primary_controls, text="使用说明", command=self.show_quick_help).pack(side="left", padx=8)
        ttk.Button(primary_controls, text="高级设置", command=self.show_advanced_settings).pack(side="left")
        ttk.Button(primary_controls, text="查看完整测试结果", command=self.open_full_results).pack(side="left", padx=8)
        ttk.Label(outer, textvariable=self.cache_info).grid(column=0, row=15, columnspan=3, sticky="w", pady=(8,2))
        ttk.Label(outer, textvariable=self.queue_info).grid(column=0, row=16, columnspan=3, sticky="w", pady=2)
        ttk.Label(outer, textvariable=self.sleep_info).grid(column=0, row=17, columnspan=3, sticky="w", pady=2)
        ttk.Label(outer, textvariable=self.title).grid(column=0, row=18, columnspan=3, sticky="w", pady=(8,2))
        ttk.Label(outer, textvariable=self.detected).grid(column=0, row=19, columnspan=3, sticky="w", pady=2)
        ttk.Label(outer, textvariable=self.elapsed).grid(column=0, row=20, columnspan=3, sticky="w", pady=2)
        ttk.Label(outer, textvariable=self.progress).grid(column=0, row=21, columnspan=3, sticky="w", pady=2)
        self.progress_bar = ttk.Progressbar(outer, mode="determinate", maximum=100)
        self.progress_bar.grid(column=0, row=22, columnspan=3, sticky="ew", pady=(0,2))
        ttk.Label(outer, textvariable=self.remaining).grid(column=0, row=23, columnspan=3, sticky="w", pady=2)
        ttk.Label(outer, text="运行状态:").grid(column=0, row=24, sticky="nw", pady=(10,2))
        self.log = Text(outer, height=16, wrap="word", state=DISABLED, font=("Consolas", 10))
        self.log.grid(column=0, row=25, columnspan=3, sticky="nsew")
        outer.rowconfigure(25, weight=1)
        self.url_entry.focus()

    def browse(self):
        folder = filedialog.askdirectory(initialdir=self.output.get() or str(DEFAULT_OUTPUT))
        if folder: self.output.set(folder)

    def on_range_changed(self, _event=None):
        state = NORMAL if self.range_mode.get() == "自定义时间段" else DISABLED
        self.range_start_entry.configure(state=state)
        self.range_end_entry.configure(state=state)

    def selected_range(self):
        if self.range_mode.get() != "自定义时间段":
            return 0.0, None
        start = parse_timestamp(self.range_start.get())
        end = parse_timestamp(self.range_end.get())
        if start is None:
            raise ValueError("自定义时间段需要填写开始时间。")
        if end is not None and end <= start:
            raise ValueError("结束时间必须晚于开始时间。")
        return start, end

    def refresh_cache_info(self):
        self.cache_info.set(cache_summary())

    def clean_exported_audio(self):
        try:
            count = cleanup_exports()
            if count: self.append(f"已永久清理 {count} 个超过 7 天的导出音频。")
        except (OSError, ValueError) as exc:
            self.append("音频过期清理暂未完成：" + str(exc))
        self.root.after(3600000, self.clean_exported_audio)

    def on_retention_changed(self, _event=None):
        CONFIG["audio_cache_retention"] = self.cache_retention.get()
        CONFIG_PATH.write_text(json.dumps(CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
        cleanup_audio_cache(self.cache_retention.get())
        self.refresh_cache_info()

    def open_audio_cache(self):
        AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(AUDIO_CACHE_DIR)

    def confirm_clear_audio_cache(self):
        if messagebox.askyesno("清空音频缓存", "将永久删除所有缓存的 YouTube 音频。不会删除字幕文件或 Whisper 模型。是否继续？"):
            clear_audio_cache()
            self.refresh_cache_info()
            self.append("音频缓存已永久清空。")

    def open_full_results(self):
        os.startfile(APP_DIR / "README_中文.md")

    def show_info(self, title, body):
        messagebox.showinfo(title, body)

    def restore_recommended(self):
        self.quality.set("均衡（medium，推荐）")
        self.performance.set("Balanced")
        self.decoding.set("均衡（推荐）")
        self.cache_retention.set("7 天（推荐）")
        self.on_retention_changed()

    def show_quick_help(self):
        window = Toplevel(self.root); window.title("使用说明"); window.geometry("620x430")
        text = Text(window, wrap="word", padx=14, pady=12); text.pack(fill="both", expand=True)
        text.insert("1.0", "快速帮助\n\n推荐：模型 medium、6线程、解码均衡（beam 3）、参考前文开启、int8、VAD关闭、音频缓存7天。\n\n普通中文/英文视频直接使用默认值。想稍快可选快速（beam 1）；仅当均衡模式有明显识别问题时再尝试最高准确率（beam 5）。同一视频重试会复用缓存音频。\n\n点击“高级设置”可查看本机 benchmark 的量化结果和每个技术参数的说明。")
        text.configure(state=DISABLED)

    def show_advanced_settings(self):
        window = Toplevel(self.root); window.title("高级设置"); window.geometry("760x700")
        outer = ttk.Frame(window, padding=12); outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="高级设置", font=("Segoe UI", 15, "bold")).pack(anchor="w")
        ttk.Label(outer, text="“实测”仅来自 2026-09-21 本机 180 秒普通话样本；一般预期与异常结果已明确标注。", wraplength=700).pack(anchor="w", pady=(2,8))
        text = Text(outer, wrap="word", height=31, padx=10, pady=8)
        text.pack(fill="both", expand=True)
        body = f'''模型（当前：{self.quality.get()}；推荐：medium）
快速=small，均衡=medium，高精度=large-v3。通常 small 更小、计算需求更低但可能降低识别准确率；medium 是本机充分测试的平衡值；large-v3 更大，通常会消耗更多 CPU 与内存，但本机尚未 benchmark，不能给出速度数字。
实测：8线程/beam1 时 small 48.2秒、medium 50.1秒，small 仅快约3.8%。10线程结果不一致（small 121.2秒、medium 51.7秒），属于异常/不稳定数据，不能证明 small 有稳定速度优势。

CPU线程数（当前：6；推荐：6）
控制 Whisper 在 CPU 并行计算时使用的线程。线程更多不一定更快，P/E 核调度、缓存与内存带宽都会影响效率。
实测（medium+beam3）：6线程 47.0秒 / RTF 0.261；8线程 52.8秒 / 0.293，慢约12.3%；10线程 53.8秒 / 0.299，慢约14.5%。beam1 也支持此结论：44.8 / 50.1 / 51.7秒。结论：本机 6线程最快；只有重新 benchmark 后才建议修改。

解码模式 / Beam（当前：{self.decoding.get()}；推荐：均衡=beam3）
快速=beam1；均衡=beam3；最高准确率=beam5。Beam 越高会同时探索更多候选文字，通常可能提高困难片段的稳定性，但并非每个视频都会明显更准确。
实测（medium+6线程）：beam1 44.8秒 / RTF0.249；beam3 47.0秒 / 0.261，约慢4.9%，但输出有明显文字差异，所以推荐 beam3。beam5 在6线程为448.0秒，但该结果波动/异常很大，不能理解为固定倍数变慢。较可比的8线程为162.5秒 vs beam3 52.8秒（约3.08倍）；10线程为148.0秒 vs 53.8秒（约2.75倍）。日常不建议 beam5。

参考前文保持上下文连续（当前：开启；推荐：开启）
每段会参考前文，有助于长视频的人名、措辞和上下文一致性。关闭有时可能减少错误累积，也可能降低连续内容一致性。本机尚未做可靠量化 benchmark；不提供虚构百分比。

计算精度（当前：int8；推荐：int8，只读）
CPU int8 推理通常可减少计算量与内存占用；这是本机已验证稳定的配置，当前不建议修改。

语音活动检测 VAD（当前：已关闭；推荐：关闭，只读）
VAD 可跳过静音，但本机 Windows 环境相关 DLL 曾加载失败。为稳定性保持关闭；除非以后专门修复并重新测试，否则不建议开启。

音频缓存保留（当前：{self.cache_retention.get()}；推荐：7 days）
按最后使用时间清理本地可重新下载的音频。不会删除 TXT/SRT 或 Whisper 模型。'''
        text.insert("1.0", body); text.configure(state=DISABLED)
        bar = ttk.Frame(outer); bar.pack(fill="x", pady=(8,0))
        ttk.Button(bar, text="模型：恢复推荐", command=lambda: self.quality.set("均衡（medium，推荐）")).pack(side="left")
        ttk.Button(bar, text="CPU：恢复推荐", command=lambda: self.performance.set("Balanced")).pack(side="left", padx=4)
        ttk.Button(bar, text="解码：恢复推荐", command=lambda: self.decoding.set("均衡（推荐）")).pack(side="left")
        ttk.Button(bar, text="全部恢复推荐值", command=self.restore_recommended).pack(side="left", padx=4)
        ttk.Button(bar, text="关闭", command=window.destroy).pack(side="right")

    def append(self, text):
        self.log.configure(state=NORMAL); self.log.insert(END, text + "\n"); self.log.see(END); self.log.configure(state=DISABLED)

    def save_tasks(self):
        with self.tasks_lock:
            atomic_write_json(TASK_QUEUE_PATH, self.tasks)

    def update_queue_info(self):
        with self.tasks_lock:
            running = sum(task.get("state") == "running" for task in self.tasks)
            waiting = sum(task.get("state") == "queued" for task in self.tasks)
            paused = sum(task.get("state") == "paused" for task in self.tasks)
        if running:
            self.queue_info.set(f"任务队列：正在执行 1 个，等待 {waiting} 个")
        elif paused:
            self.queue_info.set(f"任务队列：已暂停 {paused} 个，等待 {waiting} 个")
        else:
            self.queue_info.set(f"任务队列：等待 {waiting} 个")

    def ensure_scheduler(self):
        if self.scheduler_thread is None or not self.scheduler_thread.is_alive():
            self.scheduler_thread = threading.Thread(target=self.run_queue, daemon=True)
            self.scheduler_thread.start()

    def resume_incomplete_tasks(self):
        with self.tasks_lock:
            count = 0
            for task in self.tasks:
                if task.get("state") in ("failed", "paused"):
                    task["state"] = "queued"; task.pop("error", None); count += 1
            atomic_write_json(TASK_QUEUE_PATH, self.tasks)
        if count:
            self.append(f"已将 {count} 个未完成任务重新加入队列。已有检查点的任务将从断点继续；尚无检查点的任务会从头开始。")
            self.ensure_scheduler()
        else:
            self.append("没有可继续的未完成任务。")
        self.update_queue_info()

    def request_pause(self):
        if not self.running_task_id:
            self.append("当前没有可暂停的任务。")
            return
        self.pause_requested.set()
        self.pause_btn.configure(state=DISABLED)
        self.append("已收到暂停请求：将在当前下载、模型加载或语音片段结束后的安全点暂停。")

    def start(self):
        url = self.url.get().strip()
        if not url: return messagebox.showwarning("YouTube 视频转文字", "请粘贴 YouTube 链接。")
        try:
            range_start, range_end = self.selected_range()
        except ValueError as exc:
            return messagebox.showwarning("转录范围", str(exc))
        if self.operation.get() != "仅下载音频" and self.quality.get() == "高精度（large-v3）" and not messagebox.askyesno("高精度模式", "large-v3 模型较大，在 CPU 上可能需要较长时间。是否继续？"):
            return
        task = {"id": uuid.uuid4().hex, "url": url, "language": self.language.get(),
                "quality": self.quality.get(), "decoding": self.decoding.get(),
                "output": self.output.get(), "range_start": range_start, "range_end": range_end,
                "operation": "download_audio" if self.operation.get() == "仅下载音频" else "transcribe",
                "state": "queued", "created_at": time.time()}
        with self.tasks_lock:
            self.tasks.append(task)
            position = sum(item.get("state") in ("queued", "running") for item in self.tasks)
            atomic_write_json(TASK_QUEUE_PATH, self.tasks)
        self.append(f"已加入队列（当前第 {position} 个）。")
        self.update_queue_info(); self.ensure_scheduler()

    def emit(self, kind, value): self.events.put((kind, value))

    def run_queue(self):
        protected = set_system_sleep_prevention(True)
        self.emit("sleep", protected)
        try:
            while True:
                with self.tasks_lock:
                    task = next((item for item in self.tasks if item.get("state") == "queued"), None)
                    if task is None:
                        break
                    task["state"] = "running"; task.pop("error", None)
                    self.running_task_id = task["id"]
                    atomic_write_json(TASK_QUEUE_PATH, self.tasks)
                self.pause_requested.clear()
                self.emit("task_started", task.copy())
                paused = self.process_task(task) == "paused"
                self.emit("queue_changed", None)
                if paused:
                    break
        finally:
            set_system_sleep_prevention(False)
            self.running_task_id = None
            self.emit("sleep", False)
            self.emit("queue_changed", None)

    def mark_task(self, task, state, error=None):
        with self.tasks_lock:
            task["state"] = state
            if error: task["error"] = error
            atomic_write_json(TASK_QUEUE_PATH, self.tasks)

    def pause_task(self, task):
        self.mark_task(task, "paused")
        self.emit("task_paused", None)
        self.emit("status", "任务已暂停，检查点已保存；现在可以安全关闭应用。")
        return "paused"

    def process_task(self, task):
        url = task["url"]; language_label = task["language"]; quality = task["quality"]
        decoding = task["decoding"]; output_text = task["output"]
        # The Performance selector was intentionally removed from the normal UI;
        # retain its benchmarked internal value of Balanced (6 CPU threads).
        performance = "Balanced"
        started = time.time(); audio_path = None
        try:
            output_dir = Path(output_text).expanduser(); output_dir.mkdir(parents=True, exist_ok=True)
            job_dir = TEMP_DIR / f"job-{datetime.now():%Y%m%d-%H%M%S}-{os.getpid()}"
            job_dir.mkdir(parents=True, exist_ok=True)
            audio_path, info, used_cache = get_cached_or_download_audio(url, self.emit)
            if self.pause_requested.is_set():
                return self.pause_task(task)
            title = info.get("title", "YouTube transcript")
            audio_duration = float(info.get("duration") or 0)
            task["video_title"] = title; task["audio_duration"] = audio_duration
            range_start = float(task.get("range_start", 0) or 0)
            requested_end = task.get("range_end")
            range_end = float(requested_end) if requested_end is not None else None
            custom_range = range_start > 0 or range_end is not None
            if audio_duration:
                if range_start >= audio_duration:
                    raise RuntimeError("开始时间已超过视频时长。")
                if range_end is None:
                    range_end = audio_duration
                elif range_end > audio_duration:
                    range_end = audio_duration
                    self.emit("status", "结束时间超过视频时长，已自动截到视频结尾。")
            if range_end is not None and range_end <= range_start:
                raise RuntimeError("所选时间段没有可转录的内容。")
            self.emit("title", title)
            if task.get("operation") == "download_audio":
                self.emit("status", "正在导出音频到输出目录…" if not custom_range else "正在截取所选音频片段，导出后从 00:00 开始计时…")
                try:
                    audio_output = export_audio(audio_path, output_dir, title, info.get("id", "audio"),
                                                start=range_start, end=range_end, partial=custom_range,
                                                should_pause=self.pause_requested.is_set)
                except ExportPaused:
                    return self.pause_task(task)
                track_export(audio_output)
                task["audio_output"] = str(audio_output)
                self.mark_task(task, "completed")
                self.emit("audio_done", str(audio_output))
                return
            from faster_whisper import WhisperModel
            models = {"Fast": "small", "Balanced": "medium", "Accurate": "large-v3", "快速（small）": "small", "均衡（medium，推荐）": "medium", "高精度（large-v3）": "large-v3"}
            model_name = models[quality]
            self.emit("status", f"正在加载 {model_name} 模型（首次使用会下载并保存在本地）…")
            threads = int(PROFILE_THREADS.get(performance, CONFIG["cpu_threads"]))
            model_key = (model_name, threads)
            model = self.loaded_models.get(model_key)
            if model is None:
                model = WhisperModel(model_name, device="cpu", compute_type="int8", cpu_threads=threads, num_workers=1, download_root=str(MODEL_DIR))
                self.loaded_models[model_key] = model
            if self.pause_requested.is_set():
                return self.pause_task(task)
            language = {"Chinese": "zh", "English": "en", "中文": "zh", "英文": "en"}.get(language_label)
            self.emit("status", "正在使用本机 CPU 转录…")
            # Keep the app self-contained on Windows: VAD requires a separate
            # onnxruntime DLL which is not needed for normal Whisper decoding.
            beam_size = BEAM_SIZES[decoding]
            transcribe_options = {"task": "transcribe", "vad_filter": False, "beam_size": beam_size, "condition_on_previous_text": True}
            if language:
                transcribe_options["language"] = language
            checkpoint = load_checkpoint(task)
            collected = checkpoint.get("segments", [])
            resume_from = float(checkpoint.get("resume_from", 0) or 0)
            if resume_from > 0:
                # Resume from the last saved boundary with a tiny overlap, then
                # discard overlap segments so completed audio is not redone.
                resume_start = max(range_start, resume_from - 0.2)
                transcribe_options["clip_timestamps"] = f"{resume_start:.3f},{range_end:.3f}" if range_end is not None else f"{resume_start:.3f}"
                recent = " ".join(item.get("text", "") for item in collected[-3:]).strip()
                if recent: transcribe_options["initial_prompt"] = recent
                self.emit("status", f"正在从 {format_duration(resume_from)} 继续转录…")
            elif custom_range:
                transcribe_options["clip_timestamps"] = f"{range_start:.3f},{range_end:.3f}" if range_end is not None else f"{range_start:.3f}"
                end_text = format_duration(range_end) if range_end is not None else "视频结尾"
                self.emit("status", f"仅转录 {format_duration(range_start)} 到 {end_text}…")
            for decode_attempt in range(2):
                try:
                    segments, info = model.transcribe(str(audio_path), **transcribe_options)
                    self.emit("detected", info.language)
                    for segment in segments:
                        if resume_from and segment.end <= resume_from + 0.05:
                            continue
                        saved_segment = {"start": float(segment.start), "end": float(segment.end), "text": segment.text}
                        collected.append(saved_segment)
                        save_checkpoint(task, {"segments": collected, "resume_from": saved_segment["end"], "title": title, "audio_duration": audio_duration, "range_start": range_start, "range_end": range_end})
                        if self.pause_requested.is_set():
                            return self.pause_task(task)
                        if audio_duration:
                            progress_end = range_end if range_end is not None else audio_duration
                            current = min(segment.end, progress_end)
                            span = max(0.001, progress_end - range_start)
                            ratio = min(1.0, max(0.0, (current - range_start) / span))
                            elapsed = time.time() - started
                            remaining = (elapsed / ratio - elapsed) if ratio > 0.02 else None
                            self.emit("progress", (current, progress_end, ratio * 100, remaining))
                    segments = collected
                    break
                except Exception:
                    if not used_cache or decode_attempt:
                        raise
                    self.emit("status", "缓存音频无法解码，正在自动重新下载一次…")
                    remove_cached_audio_for_url(url)
                    audio_path, info, used_cache = get_cached_or_download_audio(url, self.emit, force_download=True)
                    audio_duration = float(info.get("duration") or 0)
            if task.get("txt_path") and task.get("srt_path"):
                txt_path = Path(task["txt_path"]); srt_path = Path(task["srt_path"])
            else:
                base = unique_base(output_dir, title); txt_path = base.with_suffix('.txt'); srt_path = base.with_suffix('.srt')
                task["txt_path"] = str(txt_path); task["srt_path"] = str(srt_path)
            self.emit("status", "正在保存 TXT…")
            with txt_path.open("w", encoding="utf-8") as f:
                for segment in collected: f.write(segment["text"].strip() + "\n")
            self.emit("status", "正在保存 SRT…")
            with srt_path.open("w", encoding="utf-8") as f:
                for index, segment in enumerate(collected, 1):
                    f.write(f"{index}\n{srt_time(segment['start'])} --> {srt_time(segment['end'])}\n{segment['text'].strip()}\n\n")
            completed = (str(txt_path), str(srt_path), time.time()-started)
            checkpoint_path(task).unlink(missing_ok=True)
            self.mark_task(task, "completed")
        except Exception as exc:
            message = str(exc)
            if "Sign in" in message or "cookies" in message.lower() or "login" in message.lower(): message = "This video requires login/cookies or is restricted. The app will not access browser cookies. Try a public video."
            elif "Unable to extract" in message or "ExtractorError" in message: message += "\nTry Update yt-dlp, then retry."
            self.mark_task(task, "failed", message)
            self.emit("task_error", message)
        finally:
            self.emit("status", "正在删除临时文件…")
            if 'job_dir' in locals(): shutil.rmtree(job_dir, ignore_errors=True)
            if 'completed' in locals(): self.emit("done", completed)

    def poll(self):
        if self.job_started is not None:
            self.elapsed.set("已用时间：" + format_duration(time.monotonic() - self.job_started))
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "status": self.append(value)
                elif kind == "cache_updated": self.refresh_cache_info()
                elif kind == "queue_changed": self.update_queue_info()
                elif kind == "sleep": self.sleep_info.set("防止自动休眠：已启用（任务执行期间）" if value else "防止自动休眠：未启用")
                elif kind == "task_started":
                    self.last_txt = None; self.open_btn.configure(state=DISABLED); self.copy_btn.configure(state=DISABLED)
                    self.pause_btn.configure(state=NORMAL)
                    self.job_started = time.monotonic(); self.progress_bar["value"] = 0
                    self.progress.set("转录进度：准备开始"); self.remaining.set("预计剩余时间：计算中…")
                    self.append("开始执行队列任务：" + value.get("url", ""))
                    self.update_queue_info()
                elif kind == "title": self.title.set("视频标题：" + value)
                elif kind == "detected": self.detected.set("检测到的语言：" + value)
                elif kind == "progress":
                    current, total, percent, remaining = value
                    self.progress_bar["value"] = percent
                    self.progress.set(f"转录进度：{format_duration(current)} / {format_duration(total)}  {percent:.0f}%")
                    self.remaining.set("预计剩余时间：" + ("约 " + format_duration(remaining) if remaining is not None else "计算中…"))
                elif kind == "done":
                    txt, srt, seconds = value; self.last_txt = txt; self.job_started = None; self.elapsed.set("已用时间：" + format_duration(seconds)); self.progress_bar["value"] = 100; self.progress.set("转录进度：完成"); self.remaining.set("预计剩余时间：00:00:00"); self.append("转录完成。\nTXT: " + txt + "\nSRT: " + srt); self.open_btn.configure(state=NORMAL); self.copy_btn.configure(state=NORMAL); self.pause_btn.configure(state=DISABLED); self.transcribe_btn.configure(state=NORMAL)
                elif kind == "audio_done":
                    self.job_started = None; self.progress_bar["value"] = 100
                    self.progress.set("音频下载：完成"); self.remaining.set("预计剩余时间：00:00:00")
                    self.pause_btn.configure(state=DISABLED); self.open_btn.configure(state=NORMAL)
                    self.append("音频已保存，可上传到云端转录：\n" + value)
                elif kind == "task_paused":
                    self.job_started = None; self.remaining.set("预计剩余时间：已暂停")
                    self.progress.set("转录进度：已暂停（可继续）")
                    self.pause_btn.configure(state=DISABLED); self.update_queue_info()
                elif kind == "task_error":
                    self.job_started = None; self.remaining.set("预计剩余时间：—")
                    self.pause_btn.configure(state=DISABLED)
                    self.append("错误：" + value + "\n如已生成检查点，可点击“继续未完成任务”重试。")
                    self.update_queue_info()
        except queue.Empty: pass
        self.root.after(100, self.poll)

    def update_ytdlp(self):
        self.append("正在更新下载组件…")
        def update():
            result = subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"], text=True, capture_output=True)
            self.emit("status", "下载组件更新" + ("完成。" if result.returncode == 0 else "失败：" + result.stderr[-500:]))
        threading.Thread(target=update, daemon=True).start()

    def open_folder(self):
        Path(self.output.get()).mkdir(parents=True, exist_ok=True); os.startfile(self.output.get())
    def copy_text(self):
        if self.last_txt:
            self.root.clipboard_clear(); self.root.clipboard_append(Path(self.last_txt).read_text(encoding="utf-8")); self.append("Transcript copied to clipboard.")

if __name__ == "__main__":
    cleanup_old_temp(); root = Tk(); App(root); root.mainloop()
