"""Publish small transcription results, never audio, without running Actions."""
import base64
import json
import re
import time
from urllib.parse import quote

FORMAT = {'format': 'youtube-transcriber-results', 'version': 1}
BRANCH = 'transcripts'


class ResultStore:
    def __init__(self, api):
        self.api = api
        self.home = f'https://github.com/{api.repo}/tree/{BRANCH}'

    def head(self):
        try:
            return self.api.call('GET', f'git/ref/heads/{BRANCH}')['object']['sha']
        except RuntimeError as exc:
            if 'HTTP 404' in str(exc):
                return None
            raise

    def read(self, path, default=None):
        try:
            data = self.api.call('GET', f'contents/{quote(path, safe="/")}?ref={BRANCH}')
        except RuntimeError as exc:
            if 'HTTP 404' in str(exc):
                return default
            raise
        if data.get('encoding') != 'base64':
            data = self.api.call('GET', 'git/blobs/' + data['sha'])
        if data.get('encoding') != 'base64':
            raise ValueError('无法读取结果存储内容')
        return base64.b64decode(data['content'])

    def ensure(self):
        if self.head():
            if self.read('FORMAT.json') != self.encode(FORMAT):
                raise ValueError('transcripts 分支已有其他内容，拒绝覆盖；请检查分支用途')
            self.update({'FORMAT.json': self.encode(FORMAT)}, 'Verify result storage access')
            return
        self.update({'FORMAT.json': self.encode(FORMAT),
                     'README.md': '# 音频转录结果\n\n计算由 Codex Cloud 执行；这里只保存文字、报告、下载包和续做检查点。\n'
                                  '内容公开，不自动过期。不要向本分支上传源音频。\n'.encode()},
                    'Initialize transcription results')

    @staticmethod
    def encode(value):
        return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')

    def update(self, files, message, index_entry=None):
        """One atomic commit, fast-forward only. Rebase on concurrent writes, no force push."""
        for attempt in range(3):
            head = self.head()
            body = {'tree': []}
            if head:
                body['base_tree'] = self.api.call('GET', 'git/commits/' + head)['tree']['sha']
            changes = dict(files)
            if index_entry:
                key, title = index_entry
                old = (self.read('README.md') or '# 音频转录结果\n'.encode('utf-8')).decode('utf-8')
                marker = f'<!-- job:{key} -->'
                old = '\n'.join(line for line in old.splitlines() if marker not in line)
                label = re.sub(r'[\[\]<>\r\n]', '_', title)
                changes['README.md'] = (old + f'\n- [{label}](jobs/{key}/README.md) {marker}\n').encode('utf-8')
            for path, content in changes.items():
                if content is None:
                    if self.read(path) is not None:
                        body['tree'].append({'path': path, 'mode': '100644', 'type': 'blob', 'sha': None})
                    continue
                if len(content) > 50 * 1024 * 1024:
                    raise ValueError('结果文件超过 50 MiB，拒绝写入 Git；请保留本地结果并检查')
                blob = self.api.call('POST', 'git/blobs',
                    {'content': base64.b64encode(content).decode('ascii'), 'encoding': 'base64'})
                body['tree'].append({'path': path, 'mode': '100644', 'type': 'blob', 'sha': blob['sha']})
            tree = self.api.call('POST', 'git/trees', body)
            commit = self.api.call('POST', 'git/commits', {'message': message + ' [skip ci]',
                'tree': tree['sha'], 'parents': [head] if head else []})
            try:
                if head:
                    self.api.call('PATCH', f'git/refs/heads/{BRANCH}', {'sha': commit['sha'], 'force': False})
                else:
                    self.api.call('POST', 'git/refs', {'ref': f'refs/heads/{BRANCH}', 'sha': commit['sha']})
                return commit['sha']
            except RuntimeError as exc:
                if not any(code in str(exc) for code in ('HTTP 409', 'HTTP 422')) or attempt == 2:
                    raise
                time.sleep(1)

    def completed(self, key, config):
        raw = self.read(f'jobs/{key}/manifest.json')
        if raw is None:
            return None
        record = json.loads(raw)
        if record.get('settings') != config or record.get('status') != 'completed':
            raise ValueError('远程结果标识或配置不匹配，拒绝混用')
        return record

    def checkpoint(self, key, value=None):
        path = f'progress/{key}.json'
        if value is None:
            raw = self.read(path)
            return json.loads(raw) if raw else None
        self.update({path: self.encode(value)}, f'Save checkpoint {key}')

    def publish(self, key, config, title, files):
        if not re.fullmatch(r'[a-f0-9]{24}', key):
            raise ValueError('无效结果标识')
        # Strict allowlist: no source audio, model, arbitrary local files or credentials.
        if set(files) != {'txt', 'srt', 'json', 'zip'}:
            raise ValueError('必须提供 TXT/SRT/JSON/ZIP 四种结果')
        root = f'jobs/{key}'
        urls = {ext: f'https://raw.githubusercontent.com/{self.api.repo}/{BRANCH}/{root}/{quote(path.name)}'
                for ext, path in files.items()}
        record = {'status': 'completed', 'settings': config, 'title': title, 'files': urls,
                  'result_url': self.home + '/' + root, 'saved_at': time.time()}
        contents = {f'{root}/{path.name}': path.read_bytes() for path in files.values()}
        contents[f'{root}/manifest.json'] = self.encode(record)
        label = re.sub(r'[\[\]<>\r\n]', '_', title)
        contents[f'{root}/README.md'] = (f'# {label}\n\n' + '\n'.join(
            f'- [{ext.upper()}]({quote(path.name)}) · [直接下载]({urls[ext]})'
            for ext, path in files.items()) + '\n\n由 Codex Cloud 转录，原始音频未上传到本分支。\n').encode('utf-8')
        contents[f'progress/{key}.json'] = None
        self.update(contents, f'Save transcript {key}', index_entry=(key, title))
        return record
