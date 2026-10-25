"""Local, opt-in authenticated YouTube download. Never export browser cookies."""
from contextlib import ExitStack
import heapq
from pathlib import Path
import re
import shutil
import time
from urllib.parse import parse_qs, urlparse
import uuid

from audio_export import ExportPaused, safe_name

BROWSERS = {'Chrome': 'chrome', 'Edge': 'edge', 'Firefox': 'firefox'}


def youtube_url(value):
    parsed = urlparse(value)
    if parsed.scheme not in ('http', 'https'):
        raise ValueError('请提供 YouTube 视频链接')
    if parsed.hostname == 'youtu.be':
        identifier = parsed.path.strip('/').split('/')[0]
    elif parsed.hostname in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com'):
        identifier = parse_qs(parsed.query).get('v', [''])[0]
        if not identifier:
            match = re.fullmatch(r'/(?:shorts|live|embed)/([A-Za-z0-9_-]{11})/?', parsed.path)
            identifier = match[1] if match else ''
    else:
        identifier = ''
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', identifier):
        raise ValueError('请提供单个 YouTube 视频链接，不支持整频道批量抓取')
    return 'https://www.youtube.com/watch?v=' + identifier


def login_options(task, work_dir):
    """Only non-secret source selection is kept in tasks. Cookie values stay local."""
    if not task.get('use_login', False):
        return {}
    youtube_url(task['url'])
    browser = task.get('login_browser', 'Chrome')
    if browser == '本地 Cookie 文件':
        source = Path(task.get('cookie_file', ''))
        if not source.is_file():
            raise ValueError('请先选择本机导出的 YouTube Cookie 文件；不要上传到聊天或 GitHub')
        # yt-dlp may update its cookie jar: never modify the user's original file.
        copy = Path(work_dir) / 'login-cookies.txt'
        shutil.copyfile(source, copy)
        return {'cookiefile': str(copy)}
    if browser not in BROWSERS:
        raise ValueError('请选择 Chrome、Edge、Firefox 或本地 Cookie 文件')
    profile = str(task.get('browser_profile', '')).strip() or None
    return {'cookiesfrombrowser': (BROWSERS[browser], profile)}


def friendly_error(error):
    message = str(error)
    lower = message.lower()
    if 'cookie' in lower and ('copy' in lower or 'locked' in lower or 'permission' in lower):
        return '无法读取浏览器 Cookie 数据库，可能被占用。请手动关闭对应浏览器后重试；程序不会强制关闭浏览器。'
    if 'dpapi' in lower or 'app-bound' in lower or ('cookie' in lower and 'decrypt' in lower):
        return '浏览器登录数据受系统加密保护，当前下载组件无法解密。不会绕过系统保护；可选择你自行导出的 YouTube Cookie 文件，或在你授权的其他浏览器登录后重试。'
    if any(word in lower for word in ('sign in', 'join this channel', 'members-only', 'login required')):
        return '此视频需要有效的登录或频道会员权限。请确认所选浏览器账号及会员等级可观看此视频，再勾选使用登录状态；已勾选则可能登录已失效。'
    if 'drm' in lower:
        return '此内容受 DRM 保护，当前工具不支持下载，不会尝试绕过保护。'
    # Avoid logging signed request parameters, cookies, or verbose HTTP headers.
    message = re.sub(r'(https?://[^\s?]+)\?[^\s]+', r'\1?<请求参数已隐藏>', message)
    return message[:700]


class QuietLogger:
    def debug(self, _message):
        pass

    def info(self, _message):
        pass

    def warning(self, _message):
        pass

    def error(self, _message):
        pass


def choose_formats(info, height):
    formats = [f for f in info.get('formats', []) if not f.get('has_drm')]
    videos = [f for f in formats if f.get('vcodec') not in (None, 'none')
              and f.get('height') and f['height'] <= height]
    if not videos:
        raise ValueError('没有可用的非 DRM 视频格式；请检查观看权限或更新下载组件')
    video = max(videos, key=lambda f: (f['height'], f.get('ext') == 'mp4', f.get('fps') or 0, f.get('tbr') or 0))
    if video.get('acodec') not in (None, 'none'):
        return [video]
    audios = [f for f in formats if f.get('acodec') not in (None, 'none') and f.get('vcodec') == 'none']
    if not audios:
        raise ValueError('视频缺少可用音轨，停止下载，避免保存无声视频')
    audio = max(audios, key=lambda f: (f.get('ext') == 'm4a', f.get('abr') or f.get('tbr') or 0))
    return [video, audio]


def remux(paths, destination, should_pause=lambda: False):
    """Stream-copy packets with existing PyAV; no re-encoding or ffmpeg executable."""
    import av
    with ExitStack() as stack:
        output = stack.enter_context(av.open(str(destination), 'w'))
        streams = []
        for path in paths:
            with av.open(str(path)) as probe:
                indices = [s.index for s in probe.streams if s.type in ('video', 'audio')]
            for index in indices:
                container = stack.enter_context(av.open(str(path)))
                source = container.streams[index]
                target = output.add_stream_from_template(source)
                streams.append((iter(container.demux(source)), target))
        heap = []

        def advance(index):
            iterator, target = streams[index]
            for packet in iterator:
                if packet.size == 0:
                    continue
                stamp = packet.dts if packet.dts is not None else packet.pts
                if stamp is None:
                    raise ValueError('视频包缺少时间戳，不能安全合并')
                heapq.heappush(heap, (stamp * packet.time_base, index, packet, target))
                return

        for index in range(len(streams)):
            advance(index)
        while heap:
            if should_pause():
                raise ExportPaused()
            _, index, packet, target = heapq.heappop(heap)
            packet.stream = target
            output.mux(packet)
            advance(index)


def download_video(url, output_dir, work_dir, height=720, auth=None,
                   emit=lambda kind, value: None, should_pause=lambda: False):
    import av
    import yt_dlp
    url = youtube_url(url)
    if height not in (720, 1080):
        raise ValueError('请选择 720p 或 1080p')
    output_dir, work_dir = Path(output_dir), Path(work_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    options = {'noplaylist': True, 'quiet': True, 'no_warnings': True, 'logger': QuietLogger(),
               **(auth or {})}
    owned_target = None

    def progress(_data):
        if should_pause():
            raise ExportPaused()

    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(url, download=False)
        chosen = choose_formats(info, height)
        emit('title', info.get('title', 'YouTube'))
        emit('status', f'正在下载视频（最高 {height}p，实际 {chosen[0]["height"]}p）；视频文件不自动过期…')
        downloaded = []
        for index, fmt in enumerate(chosen):
            if should_pause():
                raise ExportPaused()
            template = str(work_dir / f'track-{index}.%(ext)s')
            with yt_dlp.YoutubeDL({**options, 'format': fmt['format_id'], 'outtmpl': template,
                                   'progress_hooks': [progress]}) as downloader:
                result = downloader.extract_info(url, download=True)
                path = Path(downloader.prepare_filename(result))
            if not path.is_file() or path.stat().st_size == 0:
                raise ValueError('下载组件未生成有效媒体文件')
            downloaded.append(path)
        extension = '.mp4' if chosen[0].get('ext') == 'mp4' and (
            len(chosen) == 1 or chosen[1].get('ext') == 'm4a') else '.mkv'
        merged = work_dir / ('merged' + extension)
        emit('status', '正在无损合并画面与声音…')
        remux(downloaded, merged, should_pause)
        # Check both tracks actually decode, not just a successful download status.
        with av.open(str(merged)) as media:
            if not media.streams.video or not media.streams.audio:
                raise ValueError('结果缺少画面或声音，未保存为完成视频')
            next(media.decode(video=0))
        with av.open(str(merged)) as media:
            next(media.decode(audio=0))
        base = safe_name(info.get('title', 'YouTube'))
        target = output_dir / (base + extension)
        if target.exists():
            target = output_dir / f'{base} ({time.strftime("%Y%m%d-%H%M%S")}-{uuid.uuid4().hex[:6]}){extension}'
        with target.open('xb') as out:
            owned_target = target
            with merged.open('rb') as source:
                while block := source.read(1024 * 1024):
                    if should_pause():
                        raise ExportPaused()
                    out.write(block)
        return target, info
    except Exception:
        if owned_target is not None:
            owned_target.unlink(missing_ok=True)
        if should_pause():
            raise ExportPaused() from None
        raise
