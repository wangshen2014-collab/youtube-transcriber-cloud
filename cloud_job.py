"""One-command submission of uploaded audio to GitHub Actions (stdlib only)."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse, unquote
from urllib.request import Request, urlopen
import uuid
import zipfile

REPO = 'wangshen2014-collab/youtube-transcriber-cloud'
MARKER = 'YouTube Transcriber temporary transfer. TTL: 7 days.'


def auth_token():
    token = os.getenv('GH_TOKEN') or os.getenv('GITHUB_TOKEN')
    if token:
        return token
    try:
        result = subprocess.run(['git', 'credential', 'fill'],
            input='protocol=https\nhost=github.com\n\n', text=True, capture_output=True,
            timeout=15, env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
        fields = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        if result.returncode == 0 and fields.get('password'):
            return fields['password']
    except (OSError, subprocess.TimeoutExpired):
        pass
    raise RuntimeError('缺少 GitHub 授权。请在环境密钥中设置 GH_TOKEN（此仓库 Contents/Actions 读写）。不要把令牌发到聊天或提交 Git。')


class GitHub:
    def __init__(self, token, repo=REPO):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
            raise ValueError('无效仓库名称')
        self.token, self.repo = token, repo

    def call(self, method, path, body=None, upload=None):
        url = path if path.startswith('https://') else f'https://api.github.com/repos/{self.repo}' + ('/' + path if path else '')
        if urlparse(url).hostname not in ('api.github.com', 'uploads.github.com'):
            raise ValueError('拒绝将授权发送至非 GitHub API 主机')
        headers = {'Authorization': f'Bearer {self.token}', 'Accept': 'application/vnd.github+json',
                   'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'youtube-transcriber-cloud'}
        if upload:
            with Path(upload).open('rb') as stream:
                headers.update({'Content-Type': 'application/zip', 'Content-Length': str(Path(upload).stat().st_size)})
                with urlopen(Request(url, data=stream, headers=headers, method=method), timeout=180) as response:
                    return json.load(response)
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            headers['Content-Type'] = 'application/json'
        try:
            with urlopen(Request(url, data=data, headers=headers, method=method), timeout=60) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except HTTPError as exc:
            raise RuntimeError(f'GitHub API HTTP {exc.code}（{method} {path.split("?")[0]}）；请检查授权和 Actions 是否开启。') from None


def validate_tag(tag):
    if not re.fullmatch(r'yt-job-\d{14}-[a-f0-9]{12}', tag):
        raise ValueError('无效任务 ID')
    return tag


def release_reference(url):
    """Parse a GitHub release page or attached-file URL, never an arbitrary host."""
    parsed = urlparse(url)
    parts = parsed.path.strip('/').split('/')
    if (parsed.scheme != 'https' or parsed.hostname != 'github.com' or
            parsed.username or parsed.password or parsed.port or len(parts) < 5 or
            parts[2] != 'releases' or parts[3] not in ('tag', 'download')):
        raise ValueError('请提供 https://github.com/所有者/仓库/releases/tag/标签 或 Release 附件链接')
    repo = '/'.join(parts[:2])
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise ValueError('无效仓库名称')
    filename = None
    tag_parts = parts[4:]
    if parts[3] == 'download':
        if len(parts) < 6:
            raise ValueError('Release 附件链接缺少文件名')
        tag_parts, filename = parts[4:-1], unquote(parts[-1])
    tag = unquote('/'.join(tag_parts))
    if not tag:
        raise ValueError('Release 标签为空')
    return repo, tag, filename


def submit(args, api):
    from transcribe_cli import timestamp, video_id
    start = timestamp(args.start)
    end = timestamp(args.end) if args.end else None
    if end is not None and end <= start:
        raise ValueError('结束时间必须晚于开始时间')
    tag = 'yt-job-' + time.strftime('%Y%m%d%H%M%S', time.gmtime()) + '-' + uuid.uuid4().hex[:12]
    release_url = getattr(args, 'release_url', None)
    if not release_url and getattr(args, 'url', None) and urlparse(args.url).hostname == 'github.com':
        release_url = args.url
    if release_url:
        release_reference(release_url)
        api.call('POST', 'actions/workflows/transcribe.yml/dispatches', {'ref': 'main', 'inputs': {
            'job_tag': tag, 'audio_url': release_url, 'asset_name': getattr(args, 'asset_name', None) or '',
            'start': str(int(start)), 'end': str(int(end)) if end is not None else '',
            'language': args.language}})
        return tag
    if getattr(args, 'url', None):
        url = 'https://www.youtube.com/watch?v=' + video_id(args.url)
        api.call('POST', 'actions/workflows/transcribe.yml/dispatches', {'ref': 'main', 'inputs': {
            'job_tag': tag, 'youtube_url': url, 'start': str(int(start)),
            'end': str(int(end)) if end is not None else '', 'language': args.language}})
        return tag
    audio = args.audio_file.expanduser().resolve()
    if not audio.is_file() or audio.stat().st_size == 0:
        raise ValueError('音频文件不存在或为空')
    if audio.stat().st_size > 512 * 1024 * 1024:
        raise ValueError('本入口单个音频上限为 512 MiB，请先在本地选择片段')
    suffix = audio.suffix.lower()
    if not re.fullmatch(r'\.[a-z0-9]{1,8}', suffix):
        suffix = '.audio'
    spec = {'audio_filename': 'input' + suffix, 'original_name': audio.name,
            'start': start, 'end': end, 'language': args.language}
    release = None
    dispatched = False
    try:
        with tempfile.TemporaryDirectory(prefix='yt-submit-') as temporary:
            payload = Path(temporary) / 'payload.zip'
            with zipfile.ZipFile(payload, 'w', compression=zipfile.ZIP_STORED) as archive:
                archive.write(audio, spec['audio_filename'])
                archive.writestr('request.json', json.dumps(spec, ensure_ascii=False))
            release = api.call('POST', 'releases', {'tag_name': tag, 'target_commitish': 'main',
                'name': '音频传输 ' + tag, 'body': MARKER, 'prerelease': True})
            upload_url = release['upload_url'].split('{')[0] + '?' + urlencode({'name': 'payload.zip'})
            api.call('POST', upload_url, upload=payload)
            api.call('POST', 'actions/workflows/transcribe.yml/dispatches', {'ref': 'main', 'inputs': {'job_tag': tag}})
            dispatched = True
    finally:
        if release and not dispatched:
            delete_transfer(api, release)
    return tag


def delete_transfer(api, release):
    tag = validate_tag(release['tag_name'])
    if release.get('body') != MARKER:
        raise ValueError('拒绝删除非本工具创建的传输记录')
    api.call('DELETE', f'releases/{release["id"]}')
    api.call('DELETE', f'git/refs/tags/{tag}')


def status(api, tag):
    validate_tag(tag)
    runs = api.call('GET', 'actions/workflows/transcribe.yml/runs?event=workflow_dispatch&per_page=100')['workflow_runs']
    run = next((r for r in runs if tag in r.get('display_title', '')), None)
    if not run:
        return {'job': tag, 'status': 'waiting_for_runner'}
    result = {'job': tag, 'status': run['status'], 'conclusion': run['conclusion'], 'run_url': run['html_url']}
    if run['status'] == 'completed':
        artifacts = api.call('GET', f'actions/runs/{run["id"]}/artifacts')['artifacts']
        result['artifacts'] = [{'id': a['id'], 'name': a['name'], 'expires_at': a['expires_at'],
            'expired': a['expired'], 'download_api': a['archive_download_url']} for a in artifacts]
    return result


def download_results(api, result, destination):
    for artifact in result.get('artifacts', []):
        if artifact['expired']:
            continue
        # The API redirects to a signed URL. Do not forward the GitHub token there.
        from urllib.request import build_opener, HTTPRedirectHandler
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, *unused):
                return None
        request = Request(artifact['download_api'], headers={'Authorization': 'Bearer ' + api.token,
                          'User-Agent': 'youtube-transcriber-cloud'})
        try:
            build_opener(NoRedirect).open(request, timeout=60)
            raise RuntimeError('未收到 GitHub 结果下载重定向')
        except HTTPError as exc:
            if exc.code != 302:
                raise RuntimeError(f'下载结果失败：HTTP {exc.code}') from None
            location = exc.headers['Location']
        if urlparse(location).scheme != 'https':
            raise ValueError('无效结果下载地址')
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / f'{result["job"]}-{artifact["id"]}.zip'
        with urlopen(location, timeout=120) as response, target.open('xb') as stream:
            import shutil
            shutil.copyfileobj(response, stream)
        print('结果已下载：' + str(target))


def main(argv=None):
    parser = argparse.ArgumentParser(description='提交 YouTube 链接或音频，交给 GitHub 转录，结果保留 60 天')
    parser.add_argument('--repo', default=os.getenv('YT_GITHUB_REPO', REPO))
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--audio-file', type=Path)
    source.add_argument('--url', help='由 GitHub 下载 YouTube 音频；本进程不连接 YouTube')
    source.add_argument('--release-url', '--audio-url', dest='release_url', help='GitHub Release 页面或音频附件链接，由 GitHub 直接下载')
    source.add_argument('--job', help='查询或下载已经提交的任务，无需重新转录')
    parser.add_argument('--asset-name', help='Release 有多个音频时指定完整附件名')
    parser.add_argument('--start', default='0')
    parser.add_argument('--end')
    parser.add_argument('--language', choices=('auto', 'zh', 'en'), default='auto')
    parser.add_argument('--wait', action='store_true')
    parser.add_argument('--download-dir', type=Path)
    parser.add_argument('--wait-seconds', type=int, default=14400)
    args = parser.parse_args(argv)
    try:
        api = GitHub(auth_token(), args.repo)
        tag = args.job or submit(args, api)
        print('任务 ID：' + tag, flush=True)
        print(f'任务列表：https://github.com/{args.repo}/actions/workflows/transcribe.yml', flush=True)
        deadline = time.monotonic() + args.wait_seconds
        while True:
            result = status(api, tag)
            if result['status'] == 'completed' or not args.wait:
                break
            if time.monotonic() >= deadline:
                print('等待超时；GitHub 任务仍可继续，之后使用 --job 查询。')
                return 4
            time.sleep(15)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.download_dir and result.get('status') == 'completed' and result.get('artifacts'):
            download_results(api, result, args.download_dir)
        return 0 if result.get('conclusion') in (None, 'success') else 1
    except (OSError, ValueError, RuntimeError) as exc:
        print('操作未完成：' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
