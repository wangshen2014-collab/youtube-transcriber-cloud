"""Sequential Codex-hosted transcription; GitHub stores outputs, not computation."""
import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import zipfile
from urllib.parse import unquote, urlparse

from cloud_job import GitHub, REPO, auth_token
from github_results import ResultStore
import transcribe_cli as cli

STOP = False
SUFFIXES = {'.webm', '.m4a', '.mp3', '.wav', '.flac', '.ogg', '.opus', '.aac', '.mp4'}


def stop(_signal, _frame):
    global STOP
    STOP = True


def natural(path):
    return tuple((1, int(s)) if s.isdigit() else (0, s.casefold())
                 for s in re.split(r'(\d+)', str(path)))


def inputs(paths, directory=None):
    candidates = list(paths)
    if directory:
        candidates += sorted((p for p in directory.iterdir() if p.suffix.lower() in SUFFIXES), key=natural)
    unique = []
    for item in candidates:
        path = item.expanduser().resolve()
        if path.suffix.lower() not in SUFFIXES or not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f'无效或空音频：{path.name}')
        if path not in unique:
            unique.append(path)
    if not unique:
        raise ValueError('请提供音频附件路径或 --input-dir 音频目录')
    return unique


def job_args(options, source, key=None):
    root = options.state_dir / 'jobs' / (key or 'pending')
    return argparse.Namespace(url=None, audio_file=source, language=options.language,
        model='medium', threads=6, beam_size=3, start=options.start, end=options.end,
        state_dir=root, model_dir=options.model_dir, output_dir=options.output_dir / (key or 'pending'),
        pause_file=options.pause_file)


def restore_checkpoint(store, key, config, args):
    path = args.state_dir / 'checkpoints' / (key + '.json')
    if path.exists():
        return path
    job = store.checkpoint(key)
    if job:
        if job.get('settings') != config or not isinstance(job.get('segments'), list):
            raise ValueError('远程检查点与当前音频/参数不匹配')
        # Never trust a path from a different machine; preserve only the run identity.
        run_id = job.get('run_id', '')
        if not re.fullmatch(r'\d{8}-\d{6}-[a-f0-9]{8}', run_id):
            raise ValueError('检查点任务 ID 无效')
        job['output_directory'] = str((args.output_dir / run_id).resolve())
        cli.atomic_json(path, job)
    return path


def run_worker(args, checkpoint, store, key, interval=600):
    args.state_dir.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(Path(__file__).with_name('transcribe_cli.py')),
        '--audio-file', str(args.audio_file), '--language', args.language,
        '--model', 'medium', '--threads', '6', '--beam-size', '3',
        '--start', str(int(args.start)), '--state-dir', str(args.state_dir),
        '--model-dir', str(args.model_dir), '--output-dir', str(args.output_dir)]
    if args.end is not None:
        command += ['--end', str(int(args.end))]
    if args.pause_file:
        command += ['--pause-file', str(args.pause_file)]
    last_sync, last_digest, sent_stop = time.monotonic(), None, False

    def sync():
        nonlocal last_digest
        if not checkpoint.exists():
            return
        data = checkpoint.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != last_digest:
            store.checkpoint(key, json.loads(data))
            last_digest = digest

    with (args.state_dir / 'worker.log').open('a', encoding='utf-8') as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                   env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
        try:
            while process.poll() is None:
                if STOP and not sent_stop:
                    process.send_signal(signal.SIGTERM)
                    sent_stop = True
                if time.monotonic() - last_sync >= interval:
                    try:
                        sync()
                    except Exception as exc:
                        print(f'检查点同步暂未成功（继续计算）：{exc}', flush=True)
                    last_sync = time.monotonic()
                time.sleep(2)
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
                process.wait()
            try:
                sync()
            except Exception as exc:
                print(f'检查点未同步，仍保存在当前环境：{exc}', flush=True)
    return process.returncode


def prepare_results(args, config, source):
    """Recover completed outputs after interrupted publishing; never re-run inference."""
    reports = sorted(args.output_dir.glob('*/run-report.json'), reverse=True)
    report_path = next((p for p in reports if (r := cli.read_json(p)).get('status') == 'completed'
                        and r.get('settings') == config), None)
    if report_path is None:
        return None
    report = cli.read_json(report_path)
    checked = parse_srt((report_path.parent / (config['source_id'] + '.srt')).read_text(encoding='utf-8'))
    if any(s['start'] < args.start - .1 or s['end'] > report['selected_end'] + .1 for s in checked):
        raise ValueError('字幕时间超出所选范围，保留本地文件但停止发布')
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', source.stem).strip().rstrip('.')[:110] or '音频'
    if args.start or args.end is not None:
        base += f' [{int(args.start)}s-{int(args.end) if args.end is not None else "end"}s]'
    ready = args.output_dir / 'ready'
    ready.mkdir(exist_ok=True)
    files = {}
    for ext in ('txt', 'srt'):
        original = report_path.parent / (config['source_id'] + '.' + ext)
        if not original.is_file():
            raise ValueError('完成报告缺少实际字幕文件，停止发布')
        files[ext] = ready / (base + '.' + ext)
        files[ext].write_bytes(original.read_bytes())
    report['source']['original_name'] = source.name
    report['execution_backend'] = 'direct-python-process'
    report['storage_backend'] = 'github-git'
    files['json'] = ready / (base + '.json')
    cli.atomic_json(files['json'], report)
    files['zip'] = ready / (base + '.zip')
    with zipfile.ZipFile(files['zip'], 'w', zipfile.ZIP_DEFLATED) as archive:
        for ext in ('txt', 'srt', 'json'):
            archive.write(files[ext], files[ext].name)
    return files


def parse_srt(text):
    segments = []
    for block in re.split(r'\n\s*\n', text.strip().replace('\r\n', '\n')):
        if not block.strip():
            continue
        lines = block.splitlines()
        if len(lines) < 3 or lines[0] != str(len(segments) + 1):
            raise ValueError('SRT 序号或内容无效')
        match = re.fullmatch(r'(\d{2,}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2,}):(\d{2}):(\d{2}),(\d{3})', lines[1])
        if not match:
            raise ValueError('SRT 时间格式无效')
        values = list(map(int, match.groups()))
        if any(values[i] >= 60 for i in (1, 2, 5, 6)):
            raise ValueError('SRT 分钟或秒无效')
        start = values[0] * 3600 + values[1] * 60 + values[2] + values[3] / 1000
        end = values[4] * 3600 + values[5] * 60 + values[6] + values[7] / 1000
        if end <= start or (segments and start < segments[-1]['start']):
            raise ValueError('SRT 时间顺序无效')
        segments.append({'start': start, 'end': end, 'text': '\n'.join(lines[2:])})
    return segments


def merge_named_parts(options, ledger, store):
    """Only the desktop's explicit title+sequence+absolute-range convention is trusted.

    Label as uploaded-parts merge, not whole video: other parts may not be uploaded.
    """
    if options.start or options.end is not None:
        return
    pattern = r'(.+) 第(\d+)段 \[(\d{2,}-\d{2}-\d{2}) 至 (\d{2,}-\d{2}-\d{2})\]'
    groups = {}
    for entry in ledger['jobs']:
        match = re.fullmatch(pattern, Path(entry['name']).stem)
        if match:
            def seconds(label):
                return sum(int(value) * factor for value, factor in zip(label.split('-'), (3600, 60, 1)))
            title, number, start, end = match.groups()
            groups.setdefault(title, []).append((int(number), seconds(start), seconds(end), entry))
    ledger['merges'] = []
    for title, parts in groups.items():
        parts.sort(key=lambda part: part[0])
        if len(parts) < 2 or any(p[3]['status'] not in ('completed', 'already_saved') for p in parts):
            continue
        if ([p[0] for p in parts] != list(range(1, len(parts) + 1)) or
                any(p[2] <= p[1] for p in parts) or
                any(a[2] != b[1] for a, b in zip(parts, parts[1:]))):
            continue
        result = {'title': title, 'status': 'merging'}
        ledger['merges'].append(result)
        try:
            records = [json.loads(store.read(f'jobs/{p[3]["key"]}/manifest.json')) for p in parts]
            config = {'source_type': 'verified-parts-merge', 'parts': [p[3]['key'] for p in parts]}
            key = cli.task_key(config)
            existing = store.completed(key, config)
            if existing:
                result.update(status='already_saved', url=existing['result_url'])
                continue
            texts, merged = [], []
            for part, record in zip(parts, records):
                def read_result(ext):
                    name = unquote(urlparse(record['files'][ext]).path.rsplit('/', 1)[-1])
                    if name != Path(name).name:
                        raise ValueError('远程结果文件名无效')
                    data = store.read(f'jobs/{part[3]["key"]}/{name}')
                    if data is None:
                        raise ValueError('分段结果缺失，不能合并')
                    return data.decode('utf-8')
                report = json.loads(read_result('json'))
                if report.get('selected_start') != 0:
                    raise ValueError('分段不是完整转录，不能自动合并')
                if abs(report.get('selected_end', -1) - (part[2] - part[1])) > .15:
                    raise ValueError('分段实际时长与文件名范围不符，拒绝猜测时间偏移')
                texts.append(read_result('txt').strip())
                for segment in parse_srt(read_result('srt')):
                    # AAC can pad the last frame by a few milliseconds.
                    start, end = segment['start'] + part[1], min(segment['end'] + part[1], part[2])
                    if end > start:
                        merged.append({**segment, 'start': start, 'end': end})
            label = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', title)[:100] + ' [已上传分段合并]'
            folder = options.output_dir / 'merged' / key; folder.mkdir(parents=True, exist_ok=True)
            files = {ext: folder / (label + '.' + ext) for ext in ('txt', 'srt', 'json', 'zip')}
            files['txt'].write_text('\n\n'.join(texts) + '\n', encoding='utf-8')
            files['srt'].write_text(''.join(f'{i}\n{cli.srt_time(s["start"])} --> {cli.srt_time(s["end"])}\n{s["text"]}\n\n'
                                           for i, s in enumerate(merged, 1)), encoding='utf-8')
            parse_srt(files['srt'].read_text(encoding='utf-8'))
            cli.atomic_json(files['json'], {'status': 'completed', 'settings': config,
                'selected_start': parts[0][1], 'selected_end': parts[-1][2],
                'segment_count': len(merged), 'parts': records,
                'note': '仅合并本批已上传且时间连续的分段，不保证包含原视频全部内容。'})
            with zipfile.ZipFile(files['zip'], 'w', zipfile.ZIP_DEFLATED) as archive:
                for ext in ('txt', 'srt', 'json'):
                    archive.write(files[ext], files[ext].name)
            saved = store.publish(key, config, label, files)
            result.update(status='completed', url=saved['result_url'])
            print('分段已合并并保存：' + saved['result_url'], flush=True)
        except Exception as exc:
            result.update(status='failed', error=str(exc))
            print(f'分段合并失败，单段结果仍保留：{exc}', flush=True)


def run_batch(options, store, worker=run_worker):
    sources = inputs(options.files, options.input_dir)
    store.ensure()  # Fail authorization before spending hours on inference.
    options.state_dir.mkdir(parents=True, exist_ok=True)
    ledger = {'status': 'running', 'results_url': store.home,
              'jobs': [{'name': source.name, 'status': 'queued'} for source in sources]}
    ledger_path = options.state_dir / 'batch-status.json'
    cli.atomic_json(ledger_path, ledger)
    for position, source in enumerate(sources, 1):
        if STOP or (options.pause_file and options.pause_file.exists()):
            ledger['status'] = 'paused'
            break
        entry = ledger['jobs'][position - 1]
        entry['status'] = 'checking'
        try:
            args = job_args(options, source)
            config = cli.settings(args)
            key = cli.task_key(config)
            entry['key'] = key
            args = job_args(options, source, key)
            previous = store.completed(key, config)
            if previous:
                entry.update(status='already_saved', url=previous['result_url'])
                print(f'[{position}/{len(sources)}] 已保存，跳过：{source.name}', flush=True)
                continue
            files = prepare_results(args, config, source)
            if files is None:
                checkpoint = restore_checkpoint(store, key, config, args)
                entry['status'] = 'transcribing'
                cli.atomic_json(ledger_path, ledger)
                print(f'[{position}/{len(sources)}] 开始转录：{source.name}', flush=True)
                code = worker(args, checkpoint, store, key, options.checkpoint_seconds)
                if code == 75:
                    entry['status'] = 'paused'
                    ledger['status'] = 'paused'
                    break
                if code != 0:
                    raise RuntimeError(f'转录失败（退出码 {code}），详见 {args.state_dir / "worker.log"}')
                files = prepare_results(args, config, source)
                if files is None:
                    raise ValueError('进程结束但没有有效完成报告，不能宣称成功')
            entry['status'] = 'publishing'
            entry['local_results'] = str(args.output_dir / 'ready')
            cli.atomic_json(ledger_path, ledger)
            result = None
            for attempt in range(3):
                try:
                    result = store.publish(key, config, source.name, files)
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    time.sleep(2)
            entry.update(status='completed', url=result['result_url'])
            print(f'[{position}/{len(sources)}] 已保存：{result["result_url"]}', flush=True)
        except Exception as exc:
            entry['status'] = 'publish_pending' if entry['status'] == 'publishing' else 'failed'
            entry['error'] = str(exc)
            print(f'[{position}/{len(sources)}] {entry["status"]}：{source.name}；{exc}', flush=True)
        finally:
            cli.atomic_json(ledger_path, ledger)
    if ledger['status'] != 'paused':
        merge_named_parts(options, ledger, store)
        ledger['status'] = 'completed_with_errors' if any(j['status'] in ('failed', 'publish_pending')
                                                        for j in ledger['jobs']) or any(
            m['status'] == 'failed' for m in ledger.get('merges', [])) else 'completed'
    cli.atomic_json(ledger_path, ledger)
    print(f'批次状态：{ledger["status"]}；结果目录：{store.home}', flush=True)
    return 75 if ledger['status'] == 'paused' else 1 if ledger['status'] != 'completed' else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Codex 云端批量转录，逐个存到 GitHub（不运行 Actions）')
    parser.add_argument('files', nargs='*', type=Path)
    parser.add_argument('--input-dir', type=Path, help='只扫描此目录顶层音频，按自然文件名顺序')
    parser.add_argument('--repo', default=os.getenv('YT_GITHUB_REPO', REPO))
    parser.add_argument('--language', choices=('auto', 'zh', 'en'), default='auto')
    parser.add_argument('--start', type=cli.timestamp, default=0.0)
    parser.add_argument('--end', type=cli.timestamp)
    parser.add_argument('--state-dir', type=Path, default=Path('state/cloud-batch'))
    parser.add_argument('--output-dir', type=Path, default=Path('outputs/cloud-batch'))
    parser.add_argument('--model-dir', type=Path, default=Path(os.getenv('YT_MODEL_DIR', 'models')))
    parser.add_argument('--pause-file', type=Path)
    parser.add_argument('--checkpoint-seconds', type=int, default=600)
    options = parser.parse_args(argv)
    if options.checkpoint_seconds < 60 or (options.end is not None and options.end <= options.start):
        parser.error('检查点同步间隔至少60秒，结束时间必须晚于开始时间')
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    logging.basicConfig(level=logging.WARNING)
    try:
        from filelock import FileLock
        options.state_dir.mkdir(parents=True, exist_ok=True)
        with FileLock(str(options.state_dir / 'batch.lock'), timeout=0):
            return run_batch(options, ResultStore(GitHub(auth_token(), options.repo)))
    except Exception as exc:
        print(f'无法执行批次：{exc}。GitHub 保存仅需本仓库 Contents 读写权限；不要在聊天粘贴令牌。', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
