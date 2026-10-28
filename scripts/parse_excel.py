# -*- coding: utf-8 -*-
"""解析《研究所授课汇总》类 Excel（每讲师一个 sheet）-> records.jsonl

用法:
  python parse_excel.py --excel <xlsx路径> --out <records.jsonl路径>

要点:
  - 跳过 '模板' / 'Sheet1' 两个 sheet；其余 sheet 名 = 讲师名
  - 表头行按关键字定位（授课讲师/讲师等级/时间/直播平台/授课主题/线上线下/工作时间/链接/课时/课时费/授课费）
  - classify() 必须先判 'applive.hsqh.net' 再判 'live.hsqh.net'（子串包含陷阱，勿改顺序）
  - 一行多链接拆成多条记录；过滤 visitong 统计链接
"""
import argparse
import json
import re

import openpyxl

url_re = re.compile(r'https?://[^\s，,；;"\'）)\u3001]+')

SKIP_SHEETS = ('模板', 'Sheet1')


def classify(url):
    # 顺序敏感：applive.hsqh.net 包含 live.hsqh.net，必须先判 applive
    if 'applive.hsqh.net' in url:
        return 'applive'
    if 'live.hsqh.net' in url:
        return 'hsqh'
    if '3c.wind.com.cn' in url:
        return 'wind'
    if 'meeting.tencent.com' in url:
        return 'tencent'
    return 'other'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--excel', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    records = []
    wb = openpyxl.load_workbook(args.excel, read_only=True, data_only=True)
    for ws in wb.worksheets:
        if ws.title in SKIP_SHEETS:
            continue
        trainer = ws.title
        header_map = {}
        for row in ws.iter_rows():
            vals = [(c.value if c.value is not None else '') for c in row]
            # 表头可能含换行（如 "课时\n（按分钟折算）"），统一压平再匹配
            strs = [str(v).replace('\n', '').replace('\r', '') for v in vals]
            if not header_map and any('授课讲师' in s for s in strs):
                for i, s in enumerate(strs):
                    if '讲师等级' in s:
                        header_map['level'] = i
                    elif s.startswith('时间') or s == '时间':
                        header_map['date'] = i
                    elif '直播平台' in s or s.startswith('地点'):
                        header_map['platform_name'] = i
                    elif '授课主题' in s:
                        header_map['topic'] = i
                    elif '线上/线下' in s:
                        header_map['online'] = i
                    elif '工作时间' in s:
                        header_map['worktime'] = i
                    elif '链接' in s:
                        header_map['link'] = i
                    elif s.startswith('授课时间段'):
                        header_map['slot'] = i  # 切片分钟区间，如 "7-46"
                    elif s.startswith('课时') and '费' not in s:
                        header_map['hours'] = i  # 兼容 "课时（按分钟折算）"
                    elif '课时费' in s:
                        header_map['rate'] = i
                    elif '授课费' in s and '汇总' not in s and '是否' not in s:
                        header_map['fee'] = i
                    elif '授课费' in s and '汇总' in s:
                        header_map['fee_sum'] = i
                continue
            if not header_map:
                continue

            def get(k):
                i = header_map.get(k)
                return str(vals[i]).strip() if i is not None and i < len(vals) and vals[i] != '' else None

            links = []
            for v in strs:
                links += url_re.findall(v)
            links = [u.rstrip('.,;') for u in links if 'visitong' not in u]
            if not links:
                continue
            hours = get('hours')
            try:
                hours = float(hours) if hours else None
            except Exception:
                hours = None
            rate = get('rate')
            try:
                rate = float(rate) if rate else None
            except Exception:
                rate = None
            fee = get('fee')
            try:
                fee = float(fee) if fee else None
            except Exception:
                fee = None
            date = get('date') or ''
            m = re.match(r'(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})', date)
            if m:
                date = f'{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}'
            else:
                date = date[:10]
            for u in links:
                plat = classify(u)
                m = re.search(r'/watch/(\d+)', u)
                mv = re.search(r'playBackVid=([0-9a-fA-F_]+)', u)
                records.append({
                    'trainer': trainer, 'level': get('level'), 'date': date,
                    'topic': get('topic'), 'platform_name': get('platform_name'),
                    'online': get('online'), 'worktime': get('worktime'),
                    'hours': hours, 'rate': rate, 'fee': fee,
                    'slot': get('slot'),  # 申报切片分钟区间 "起-止"（栏目切片记录）
                    'url': u, 'platform': plat,
                    'channel_id': m.group(1) if m else None,
                    'playback_vid': mv.group(1) if mv else None,
                })
    # 表内"本期授课费汇总"勾稽：逐 sheet 找该列数值（汇总常为公式/合并单元格）
    meta = {}
    for ws in wb.worksheets:
        if ws.title in SKIP_SHEETS:
            continue
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, (int, float)) and c.value > 100:
                    meta.setdefault(ws.title, round(float(c.value), 2))
    wb.close()
    print('表内汇总单元格:', meta if meta else '未找到')

    with open(args.out, 'w', encoding='utf-8') as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')

    from collections import Counter
    print(f'共 {len(records)} 条带链接记录，讲师 {len(set(r["trainer"] for r in records))} 位')
    print(Counter(r['platform'] for r in records))


if __name__ == '__main__':
    main()
