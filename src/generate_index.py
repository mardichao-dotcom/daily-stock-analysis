"""
generate_index.py — 掃描 output/archives/，產生歷史存檔索引頁。
"""
import argparse
import os
import re
import sys
from datetime import datetime

from jinja2 import Environment, FileSystemLoader

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
WEEKDAY_ZH   = ['一', '二', '三', '四', '五', '六', '日']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--archive', default=os.path.join(PROJECT_ROOT, 'docs', 'archives'))
    args = parser.parse_args()

    archive_dir = args.archive
    os.makedirs(archive_dir, exist_ok=True)

    # 掃描所有符合 YYYY-MM-DD.html 的存檔
    pattern = re.compile(r'^(\d{4}-\d{2}-\d{2})\.html$')
    archives = []
    for fname in os.listdir(archive_dir):
        m = pattern.match(fname)
        if m:
            date_str = m.group(1)
            try:
                dt = datetime.strptime(date_str, '%Y-%m-%d')
                archives.append({
                    'date':     date_str,
                    'weekday':  f'({WEEKDAY_ZH[dt.weekday()]})',
                    'filename': fname,
                    'dt':       dt,
                })
            except ValueError:
                pass

    archives.sort(key=lambda x: x['dt'], reverse=True)

    # 渲染
    templates_dir = os.path.join(PROJECT_ROOT, 'templates')
    env  = Environment(loader=FileSystemLoader(templates_dir), autoescape=False)
    tmpl = env.get_template('archive_index.html.j2')
    html = tmpl.render(archives=archives)

    out_path = os.path.join(archive_dir, 'index.html')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'[OK] {out_path}  ({len(archives)} 筆存檔)')


if __name__ == '__main__':
    main()
