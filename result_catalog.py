"""Build a readable catalogue from result tree paths; never move source files."""
import hashlib
import json
import re
from pathlib import PurePosixPath
from urllib.parse import quote


def group_title(title):
    title = re.sub(r' 第\d+段.*$', '', title)
    title = re.sub(r'\s*[\[_【](?:已上传分段合并|分段合并|完整系列).*$','', title)
    if title.startswith('EP'):
        return title
    if '错失一个时代' in title:
        return '但斌：错失一个时代比泡沫更贵'
    if 'AI投资机会和布局' in title or '下一步财富机会' in title:
        return '但斌：AI投资机会和布局'
    return title.strip()


def category(title):
    if '批量功能验证' in title:
        return '测试样例'
    if title.startswith('EP'):
        return '科技节目（EP 系列）'
    if '儿童' in title:
        return '教育与访谈'
    return '投资、经济与访谈'


def build_catalog(entries, repo):
    """Return generated UTF-8 files. Entries use Git trees API blob format."""
    blobs = {e['path']: e for e in entries if e.get('type') == 'blob'}
    groups = {}
    for path, entry in sorted(blobs.items()):
        if not path.startswith('jobs/') or not path.endswith('.txt'):
            continue
        title = PurePosixPath(path).stem
        name = group_title(title)
        kind = '单段' if re.search(r'第\d+段', title) else ('合并版' if '合并' in title else '单项结果')
        files = {ext: path[:-3] + ext for ext in ('txt', 'srt', 'json', 'zip') if path[:-3] + ext in blobs}
        groups.setdefault(name, []).append(dict(title=title, kind=kind, path=path,
                                                sha=entry.get('sha'), files=files))
    output, records = {}, []
    home = '# 转录资料库\n\n按节目或视频查找；有合并版时优先阅读合并版。合并版仅覆盖已上传内容，不代表原视频完整。\n\n'
    home += '[工具和使用说明](https://github.com/' + repo + ') · [旧版 Actions 结果](LEGACY.md)\n\n'
    home += 'TXT 用于阅读和交给 ChatGPT，SRT 用于时间定位，JSON 是报告，ZIP 是下载包。\n\n'
    home += '## 存放规则\n\n`catalog/` 是阅读目录；`jobs/` 保存实际结果；`batches/` 和 `collections/` 保存批次副本及打包；`progress/` 是未完成检查点。源音频另在 Releases 或本地，不在本分支。现有文件和链接均保留。\n\n'
    for cat in ('科技节目（EP 系列）', '投资、经济与访谈', '教育与访谈', '测试样例'):
        selected = [(n, rows) for n, rows in sorted(groups.items()) if category(n) == cat]
        if not selected:
            continue
        home += f'## {cat}\n\n| 节目／视频 | 版本 |\n| --- | --- |\n'
        for name, rows in selected:
            slug = hashlib.sha256(name.encode()).hexdigest()[:16]
            page = f'catalog/{slug}.md'
            rows.sort(key=lambda r: (r['kind'] == '单段', r['title']))
            label = name.replace('|', '｜').replace('[', '［').replace(']', '］')
            home += f'| [{label}]({page}) | ' + '、'.join(f'{k} {sum(r["kind"] == k for r in rows)}' for k in ('合并版', '单项结果', '单段') if any(r['kind'] == k for r in rows)) + ' |\n'
            body = f'# {label}\n\n[返回资料库](../README.md)\n\n合并版与单段分别列出；版本名称沿用原文件，未重新核验文字准确性或全片覆盖范围。\n\n'
            seen = {}
            for row in rows:
                body += f'## {row["kind"]}：{row["title"]}\n\n'
                if row['sha'] and row['sha'] in seen:
                    body += '此 TXT 与上方版本内容完全相同（Git blob 相同）；保留原链接。\n\n'
                seen[row['sha']] = row['path']
                body += ' · '.join(f'[{ext.upper()}](https://github.com/{repo}/blob/transcripts/{quote(p, safe="/")})' for ext, p in row['files'].items()) + '\n\n'
                body += '[TXT 直链](https://raw.githubusercontent.com/' + repo + '/transcripts/' + quote(row['path'], safe='/') + ') · [原结果页](../' + row['path'].rsplit('/', 1)[0] + '/README.md)\n\n'
            output[page] = body.encode()
            records.append(dict(title=name, category=cat, page=page, versions=rows))
        home += '\n'
    home += '## 批次与打包归档\n\n'
    for path in sorted(blobs):
        if path.startswith(('batches/', 'collections/')) and path.endswith('README.md'):
            home += f'- [{path.rsplit("/", 1)[0]}]({quote(path, safe="/")})\n'
    output['README.md'] = home.encode()
    output['catalog/index.json'] = json.dumps(records, ensure_ascii=False, indent=2).encode()
    return output
