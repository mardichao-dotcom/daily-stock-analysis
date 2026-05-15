"""
render.py — 讀 filtered_result.json + signal_state.json，
           用 Jinja2 模板產出 output/index.html 與 output/archives/{date}.html
"""
import argparse
import json
import os
import shutil
from datetime import datetime, timedelta

from jinja2 import Environment, FileSystemLoader

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.load_config import get_name, get_sector_of, is_leader, symbol_to_code

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")

WEEKDAY_ZH = ['一', '二', '三', '四', '五', '六', '日']

# ── 判讀說明：固定句型套 score_breakdown ─────────────────────────────────────

_POS_MAP = {
    '自身是族群長子且發動':        lambda s: f'{s}長子發動',
    '突破60日高':                  '突破60日高',
    '跳空開高':                    '跳空開高',
    '大爆量（>2x）':               '大爆量',
    '族群整體啟動（量比加乘）':    '族群量比加乘',
    '強紅K（實體比>60%，收紅）':   '強紅K',
    'ETF共識加碼（≥2檔）':         'ETF共識加碼',
    'ETF連續加碼（雙軌）':         'ETF連續加碼',
    'ETF點火（異常爆量建倉）':     'ETF異常點火建倉',
}

_NEG_MAP = {
    '多長子背離':               '長子間出現背離',
    '族群長子大跌（>3%）':      '族群長子大跌（警告）',
    'ETF經理人分歧（淨流入）':  'ETF經理人內部分歧（淨流入正向）',
    'ETF共識減碼（≥2檔）':      'ETF共識減碼',
}


def _pos_text(item, sector):
    key = item['項目']
    if key in _POS_MAP:
        v = _POS_MAP[key]
        return v(sector) if callable(v) else v
    return item['項目']


def _neg_text(item):
    return _NEG_MAP.get(item['項目'], item['項目'])


def build_judgment(sym, v):
    sector = v['板塊']
    breakdown = v.get('score_breakdown', [])
    positives = [b for b in breakdown if b['分數'] > 0]
    negatives = [b for b in breakdown if b['分數'] < 0]

    pos_parts = [_pos_text(b, sector) for b in positives]
    neg_parts = [_neg_text(b) for b in negatives]

    etf = v['籌碼']
    etf_cnt = etf.get('etf_consensus_buy_count', 0)
    is_cont  = etf.get('is_continuous_buy', False)

    sentences = []
    if pos_parts:
        sentences.append('、'.join(pos_parts))
    if neg_parts:
        sentences.append('但' + '、'.join(neg_parts))

    result = '；'.join(sentences) + '。' if sentences else ''

    if etf_cnt == 0:
        result += '無 ETF 籌碼背書，純技術面發動。'
    elif etf_cnt >= 2:
        cont_str = '連續' if is_cont else ''
        result += f'{etf_cnt} 檔 ETF {cont_str}共識加碼，籌碼面有支撐。'
    else:
        result += 'ETF 籌碼關注中。'

    return result


# ── 個股資料整理 ─────────────────────────────────────────────────────────────

def build_stock_info(sym, v, data_date, data_dir):
    code = symbol_to_code(sym)
    exchange = sym.split(':')[0]
    name = get_name(sym)
    sector = v['板塊']

    price = v.get('價量', {})
    chip  = v.get('籌碼', {})

    # chart JSON 路徑（相對路徑；prefix 由呼叫端注入到模板）
    chart_filename = f'{exchange}_{code}.json'
    chart_path = f'data/{data_date}/{chart_filename}'   # prefix 會在模板層加
    has_chart = os.path.exists(os.path.join(data_dir, chart_filename))

    # 計分負面項目（黑名單卡片用）
    neg_items = [b['項目'] for b in v.get('score_breakdown', []) if b['分數'] < 0]

    return {
        'sym':         sym,
        'code':        code,
        'name':        name,
        'sector':      sector,
        'grade':       v['grade'],
        'tags':        v.get('tags', []),
        'score':       v['score'],
        'breakdown':   v.get('score_breakdown', []),
        'judgment':    build_judgment(sym, v),
        'close':       price.get('close', 0),
        'change_pct':  price.get('change_pct', 0),
        'vol_ratio':   price.get('vol_ratio', 0),
        'k_pattern':   price.get('k_pattern', ''),
        'break_60d':   price.get('break_60d_high', False),
        'is_gap_up':   price.get('is_gap_up', False),
        'etf_count':   chip.get('etf_consensus_buy_count', 0),
        'is_continuous': chip.get('is_continuous_buy', False),
        'is_leader':   is_leader(sym),
        'key_prices':  '',   # 階段 6 填入
        'has_chart':   has_chart,
        'chart_id':    f'chart-{exchange}-{code}',
        'chart_path':  chart_path,
        'neg_items':   neg_items,
    }


# ── 訊號追蹤表 ───────────────────────────────────────────────────────────────

def build_signal_tracking(signal_state, data_date, result):
    rows = []
    tracking = signal_state.get('訊號追蹤', {})
    ref_date = datetime.strptime(data_date, '%Y-%m-%d')
    for sym, info in tracking.items():
        code = symbol_to_code(sym)
        name = get_name(sym)
        first = info.get('首次發出日', data_date)
        first_dt = datetime.strptime(first, '%Y-%m-%d')
        days = (ref_date - first_dt).days + 1
        grade = info.get('目前分級', result.get('個股結果', {}).get(sym, {}).get('grade', ''))
        rows.append({
            'sym': sym, 'code': code, 'name': name,
            'first_date': first, 'grade': grade, 'days': days,
        })
    rows.sort(key=lambda r: r['first_date'], reverse=True)
    return rows


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--result',  default=os.path.join(PROJECT_ROOT, 'output', 'filtered_result.json'))
    parser.add_argument('--signal',  default=os.path.join(PROJECT_ROOT, 'state', 'signal_state.json'))
    parser.add_argument('--out',     default=os.path.join(PROJECT_ROOT, 'docs', 'dashboard.html'))
    parser.add_argument('--archive', default=os.path.join(PROJECT_ROOT, 'docs', 'archives'))
    parser.add_argument('--data-dir', default=None)
    args = parser.parse_args()

    with open(args.result, encoding='utf-8') as f:
        result = json.load(f)
    with open(args.signal, encoding='utf-8') as f:
        signal_state = json.load(f)

    data_date = result['資料日期']
    data_dir  = args.data_dir or os.path.join(PROJECT_ROOT, 'docs', 'data', data_date)

    個股 = result['個股結果']
    分級 = result['分級彙整']

    # 日期格式化
    dt = datetime.strptime(data_date, '%Y-%m-%d')
    next_dt = dt + timedelta(days=1)
    # 跳週末
    while next_dt.weekday() >= 5:
        next_dt += timedelta(days=1)
    data_date_fmt = f'{dt.year}/{dt.month:02d}/{dt.day:02d} ({WEEKDAY_ZH[dt.weekday()]})'
    next_date_fmt = f'{next_dt.month:02d}/{next_dt.day:02d} ({WEEKDAY_ZH[next_dt.weekday()]})'

    # 建各區塊個股列表
    def build_list(syms):
        return [build_stock_info(s, 個股[s], data_date, data_dir) for s in syms if s in 個股]

    s_stocks    = build_list(分級.get('S級', []))
    a_stocks    = build_list(分級.get('A級', []))
    alert_stocks = build_list(分級.get('警報', []))
    black_stocks = build_list(分級.get('黑名單', []))

    # ETF 分歧：從 score_breakdown 找，不限分級
    div_syms = [sym for sym, v in 個股.items()
                if any('ETF經理人分歧' in b['項目'] for b in v.get('score_breakdown', []))]
    div_stocks = build_list(div_syms)

    # 強勢單兵作戰
    lone_syms = [sym for sym, v in 個股.items() if '單兵作戰' in v.get('tags', [])]
    lone_stocks = build_list(lone_syms)

    # 啟動族群文字
    啟動族群 = result.get('啟動族群', [])
    啟動族群_text = '、'.join(啟動族群) if 啟動族群 else '無'

    # Jinja2 渲染
    templates_dir = os.path.join(PROJECT_ROOT, 'templates')
    env = Environment(loader=FileSystemLoader(templates_dir), autoescape=False)
    tmpl = env.get_template('dashboard.html.j2')

    base_ctx = {
        'data_date':      data_date,
        'data_date_fmt':  data_date_fmt,
        'next_date_fmt':  next_date_fmt,
        '籌碼基準':       result.get('籌碼基準', ''),
        '產出時間':       result.get('產出時間', ''),
        '啟動族群':       啟動族群,
        '啟動族群_text':  啟動族群_text,
        'sa_count':       len(s_stocks) + len(a_stocks),
        'alert_count':    len(alert_stocks),
        'black_count':    len(black_stocks),
        'total_stocks':   87,
        's_stocks':       s_stocks,
        'a_stocks':       a_stocks,
        'div_stocks':     div_stocks,
        'alert_stocks':   alert_stocks,
        'black_stocks':   black_stocks,
        'lone_stocks':    lone_stocks,
        'signal_tracking': build_signal_tracking(signal_state, data_date, result),
    }

    # 寫 docs/dashboard.html（prefix='' → assets/ 就在同層）
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as f:
        f.write(tmpl.render(prefix='', **base_ctx))
    print(f'[OK] {args.out}')

    # 存檔 docs/archives/{date}.html（prefix='../' → 退一層到 docs/）
    os.makedirs(args.archive, exist_ok=True)
    archive_path = os.path.join(args.archive, f'{data_date}.html')
    with open(archive_path, 'w', encoding='utf-8') as f:
        f.write(tmpl.render(prefix='../', **base_ctx))
    print(f'[OK] {archive_path}')


if __name__ == '__main__':
    main()
