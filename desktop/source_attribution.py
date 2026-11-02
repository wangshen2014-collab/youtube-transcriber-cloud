"""Preserve source credits without inventing speaker identities or timestamps."""
import json
from pathlib import Path
import re

BEGIN = '【来源信息，非音频原话】'
END = '【转录正文】'


def one_line(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip()


def youtube_metadata(info):
    return {key: info[key] for key in ('title', 'channel', 'uploader', 'channel_id',
                                     'channel_url', 'webpage_url', 'source_url') if info.get(key)}


def attribution(options=None, metadata=None, source=None):
    metadata = metadata or {}
    if options is not None:
        metadata = {**getattr(options, 'source_metadata', {}), **metadata}
    result = {}
    channel = one_line(metadata.get('channel') or metadata.get('uploader'))
    if channel:
        result.update(channel=channel, channel_source='YouTube 元数据')
    if source:
        prefix = re.match(r'^(?:【(?:频道|作者) .*?】)+', Path(source).name)
        for label, value in re.findall(r'【(频道|作者) (.*?)】', prefix[0] if prefix else ''):
            key = 'channel' if label == '频道' else 'author'
            if key not in result:
                result[key] = one_line(value)
                result[key + '_source'] = '音频文件名'
    for key in ('author', 'interviewee', 'opinion_owner', 'channel'):
        value = one_line(getattr(options, key, None)) if options is not None else ''
        if value:
            result[key] = value
            result[key + '_source'] = '用户提供'
    url = one_line(metadata.get('webpage_url') or metadata.get('source_url'))
    if not url and options is not None:
        url = one_line(getattr(options, 'source_url', None) or getattr(options, 'url', None))
    if url and url.startswith(('https://www.youtube.com/', 'https://youtu.be/', 'https://youtube.com/')):
        result['source_url'] = url
    return result


def credited_title(title, credits):
    title = one_line(title) or 'YouTube'
    # Prefixes survive existing filename-length limits; keep segment suffixes intact.
    labels = []
    for key, label in (('author', '作者'), ('channel', '频道')):
        value = one_line(credits.get(key))
        if value and not title.startswith(f'【{label} ') and value not in [v for _, v in labels]:
            labels.append((label, value[:36]))
    return ''.join(f'【{label} {value}】' for label, value in labels) + title


def strip_header(text):
    text = text.replace('\r\n', '\n')
    if text.startswith(BEGIN + '\n'):
        _, found, body = text.partition('\n' + END + '\n')
        if found:
            return body.lstrip('\n')
    return text


def header(credits):
    lines = []
    for key, label in (('channel', '频道'), ('author', '作者／主讲者'),
                       ('interviewee', '受访者'), ('opinion_owner', '观点归属')):
        if credits.get(key):
            lines.append(f'{label}：{credits[key]}（{credits.get(key + "_source", "来源未注明")}）')
    if credits.get('source_url'):
        lines.append('视频来源：' + credits['source_url'])
    if not lines:
        return ''
    lines.append('以上为来源说明，不代表已逐句识别说话人或核实观点归属。')
    return BEGIN + '\n' + '\n'.join(lines) + '\n' + END + '\n'


def annotate_text(text, credits):
    return header(credits) + strip_header(text)


def annotate_segments(segments, credits):
    result = [{**segment, 'text': strip_header(segment['text'])} for segment in segments]
    if result:
        result[0]['text'] = header(credits) + result[0]['text']
    return result


def add_arguments(parser):
    parser.add_argument('--author', help='用户明确提供的作者或主讲者，非自动说话人识别')
    parser.add_argument('--interviewee', help='用户明确提供的受访者，可列出多个名字')
    parser.add_argument('--opinion-owner', help='用户明确指定的观点归属，不据此改写逐字稿')
    parser.add_argument('--channel', help='明确提供的 YouTube 频道名')
    # Internal transport to the worker, restricted to public source metadata.
    def metadata_value(value):
        parsed = json.loads(value)
        if not isinstance(parsed, dict) or any(not isinstance(v, str) for v in parsed.values()):
            raise ValueError('来源元数据必须是字符串字典')
        return youtube_metadata(parsed)
    import argparse
    parser.add_argument('--source-metadata-json', dest='source_metadata', type=metadata_value,
                        default={}, help=argparse.SUPPRESS)


def copy_options(options):
    return {key: getattr(options, key, None) for key in ('author', 'interviewee', 'opinion_owner', 'channel')}


def worker_arguments(args):
    result = []
    for key, value in copy_options(args).items():
        if value:
            result += ['--' + key.replace('_', '-'), value]
    metadata = getattr(args, 'source_metadata', {})
    if metadata:
        result += ['--source-metadata-json', json.dumps(youtube_metadata(metadata), ensure_ascii=False)]
    return result
