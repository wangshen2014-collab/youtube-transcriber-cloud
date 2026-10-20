"""GitHub Actions transfer and inference stages. No transcript text in logs."""
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import re
import sys
from urllib.request import urlopen
from urllib.parse import quote
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloud_job import GitHub, MARKER, validate_tag, delete_transfer, release_reference

AUDIO_SUFFIXES = {'.webm', '.m4a', '.mp3', '.wav', '.flac', '.ogg', '.opus', '.aac', '.mp4'}


def resolve_release_audio(api, url, asset_name=''):
    repo, tag, linked_name = release_reference(url)
    release = api.call('GET', f'https://api.github.com/repos/{repo}/releases/tags/{quote(tag, safe="")}')
    if release.get('draft'):
        raise ValueError('请先发布 Release，再使用公开附件')
    requested = linked_name or asset_name
    assets = release.get('assets', [])
    if len(assets) >= 30:
        assets = []
        page = 1
        while True:
            batch = api.call('GET', f'https://api.github.com/repos/{repo}/releases/{release["id"]}/assets?per_page=100&page={page}')
            assets.extend(batch)
            if len(batch) < 100:
                break
            page += 1
    matches = [a for a in assets if Path(a['name']).suffix.lower() in AUDIO_SUFFIXES
               and (not requested or a['name'] == requested)]
    if len(matches) != 1:
        names = ', '.join(a['name'] for a in assets if Path(a['name']).suffix.lower() in AUDIO_SUFFIXES)
        raise ValueError('需要唯一音频附件；请通过 asset_name 或附件直链指定。可用音频：' + names)
    asset = matches[0]
    if asset.get('state') not in (None, 'uploaded') or not 0 < asset['size'] <= 512 * 1024 * 1024:
        raise ValueError('附件尚未完成上传，或大小不在 1 byte–512 MiB 范围内')
    release_reference(asset['browser_download_url'])
    return asset


def prepare(api, tag):
    audio_url = os.getenv('AUDIO_URL', '').strip()
    youtube_url = os.getenv('YOUTUBE_URL', '').strip()
    if audio_url and youtube_url:
        raise ValueError('音频链接和 YouTube 链接只能填写一个')
    if audio_url:
        from transcribe_cli import timestamp
        asset = resolve_release_audio(api, audio_url, os.getenv('ASSET_NAME', '').strip())
        start = timestamp(os.getenv('RANGE_START', '0') or '0')
        end = timestamp(os.environ['RANGE_END']) if os.getenv('RANGE_END', '').strip() else None
        language = os.getenv('AUDIO_LANGUAGE', 'auto') or 'auto'
        if language not in ('auto', 'zh', 'en') or (end is not None and end <= start):
            raise ValueError('无效语言或时间段')
        Path('incoming').mkdir(exist_ok=True)
        filename = 'input' + Path(asset['name']).suffix.lower()
        # Public download only: never forward GitHub credentials to a CDN redirect.
        with urlopen(asset['browser_download_url'], timeout=180) as response, open(Path('incoming') / filename, 'wb') as stream:
            size = 0
            while block := response.read(1024 * 1024):
                size += len(block)
                if size > asset['size'] or size > 512 * 1024 * 1024:
                    raise ValueError('附件下载大小超出预期')
                stream.write(block)
        if size != asset['size']:
            raise ValueError('附件下载不完整')
        spec = {'source_type': 'release_audio', 'audio_filename': filename, 'original_name': asset['name'],
                'release_asset_url': asset['browser_download_url'],
                'start': start, 'end': end, 'language': language}
        Path('incoming/request.json').write_text(json.dumps(spec), encoding='utf-8')
        return
    if youtube_url:
        from transcribe_cli import video_id, timestamp
        identifier = video_id(youtube_url)
        start = timestamp(os.getenv('RANGE_START', '0') or '0')
        end = timestamp(os.environ['RANGE_END']) if os.getenv('RANGE_END', '').strip() else None
        language = os.getenv('AUDIO_LANGUAGE', 'auto') or 'auto'
        if language not in ('auto', 'zh', 'en') or (end is not None and end <= start):
            raise ValueError('无效语言或时间段')
        Path('incoming').mkdir(exist_ok=True)
        spec = {'source_type': 'youtube', 'url': 'https://www.youtube.com/watch?v=' + identifier,
                'original_name': identifier, 'start': start, 'end': end, 'language': language}
        Path('incoming/request.json').write_text(json.dumps(spec), encoding='utf-8')
        return
    validate_tag(tag)
    release = api.call('GET', 'releases/tags/' + tag)
    if release.get('body') != MARKER:
        raise ValueError('不是本工具创建的音频传输')
    asset = next(a for a in release['assets'] if a['name'] == 'payload.zip')
    if asset['size'] > 513 * 1024 * 1024:
        raise ValueError('音频包过大')
    Path('incoming').mkdir(exist_ok=True)
    with urlopen(asset['browser_download_url'], timeout=120) as response, open('incoming/payload.zip', 'wb') as stream:
        import shutil
        shutil.copyfileobj(response, stream, length=1024 * 1024)
    with zipfile.ZipFile('incoming/payload.zip') as archive:
        if len(archive.infolist()) != 2 or sum(i.file_size for i in archive.infolist()) > 513 * 1024 * 1024:
            raise ValueError('音频包结构或大小错误')
        spec = json.loads(archive.read('request.json'))
        filename = spec['audio_filename']
        if Path(filename).name != filename or not filename.startswith('input.'):
            raise ValueError('无效音频文件名')
        if set(archive.namelist()) != {'request.json', filename}:
            raise ValueError('音频包包含未预期的文件')
        archive.extract(filename, 'incoming')
        Path('incoming/request.json').write_text(json.dumps(spec), encoding='utf-8')


def transcribe():
    import transcribe_cli
    spec = json.loads(Path('incoming/request.json').read_text(encoding='utf-8'))
    start, end = float(spec['start']), spec.get('end')
    if start < 0 or (end is not None and float(end) <= start):
        raise ValueError('无效时间段')
    # Call the tested CLI with numeric ranges; no shell interpolation of user inputs.
    source_args = ['--url', spec['url']] if spec.get('source_type') == 'youtube' else ['--audio-file', str(Path('incoming') / spec['audio_filename'])]
    args = source_args + ['--language', spec['language'], '--start', str(int(start)), '--output-dir', 'outputs']
    if end is not None:
        args += ['--end', str(int(end))]
    result = transcribe_cli.main(args)
    if result == 0:
        # Metadata is bundled with results, not echoed to the public job log.
        for report_path in Path('outputs').glob('*/run-report.json'):
            report = json.loads(report_path.read_text(encoding='utf-8'))
            original_name = report.get('title', spec['original_name']) if spec.get('source_type') == 'youtube' else spec['original_name']
            report['source']['original_name'] = original_name
            if spec.get('release_asset_url'):
                report['source']['release_asset_url'] = spec['release_asset_url']
            display_name = original_name if spec.get('source_type') == 'youtube' else Path(original_name).stem
            name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', display_name).strip().rstrip('.')[:120] or '音频'
            if start or end is not None:
                name += f' [{int(start)}s-{int(end) if end is not None else "end"}s]'
            for suffix in ('.txt', '.srt'):
                source = next(report_path.parent.glob('*' + suffix))
                source.rename(report_path.parent / (name + suffix))
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def cleanup_old(api):
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    page = 1
    expired = []
    while True:
        releases = api.call('GET', f'releases?per_page=100&page={page}')
        if not releases:
            break
        for release in releases:
            if release.get('body') == MARKER and datetime.fromisoformat(release['created_at'].replace('Z', '+00:00')) < cutoff:
                validate_tag(release['tag_name'])
                expired.append(release)
        page += 1
    for release in expired:
        delete_transfer(api, release)
    print(f'清理了 {len(expired)} 个超过 7 天的遗留音频传输。')


if __name__ == '__main__':
    command = sys.argv[1]
    api = GitHub(os.environ['GH_TOKEN'], os.environ['GITHUB_REPOSITORY'])
    tag = os.getenv('JOB_TAG', '')
    if command == 'prepare':
        prepare(api, tag)
    elif command == 'transcribe':
        sys.exit(transcribe())
    elif command == 'cleanup':
        delete_transfer(api, api.call('GET', 'releases/tags/' + validate_tag(tag)))
    elif command == 'cleanup-old':
        cleanup_old(api)
    else:
        raise ValueError('未知操作')
