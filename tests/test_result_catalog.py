import unittest
from result_catalog import build_catalog


class CatalogTests(unittest.TestCase):
    def test_group_parts_keep_duplicates_and_unicode_links(self):
        entries = [{'path': f'jobs/{key}/{title}.{ext}', 'type': 'blob', 'sha': 'same' if ext == 'txt' else ext}
                   for key, title in [('a', '节目 第001段 [00-00-00 至 00-30-00]'),
                                      ('b', '节目 [已上传分段合并]'),
                                      ('c', '节目 [完整系列-五段合并]')]
                   for ext in ('txt', 'srt', 'json', 'zip')]
        files = build_catalog(entries, 'owner/repo')
        import json
        index = json.loads(files['catalog/index.json'])
        self.assertEqual(len(index), 1)
        self.assertEqual(len(index[0]['versions']), 3)
        page = files[index[0]['page']].decode()
        self.assertLess(page.index('合并版'), page.index('## 单段'))
        self.assertIn('内容完全相同', page)
        self.assertIn('%E8%8A%82', page)
        self.assertEqual(len(entries), 12)
