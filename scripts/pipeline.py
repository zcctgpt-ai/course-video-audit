# -*- coding: utf-8 -*-
"""授课视频批量核验流水线（断点续跑版）

核验口径（李总 2026-10-28 定）:
  - 时长: 实测分钟 M vs 申报课时 D(小时*60)。M >= D - max(5, D*0.1) → '时长满足'；否则标红核减
  - 费用: 授课费 ≈ 课时 × 课时费率，差 > 1 元标红（申报课时多为两位小数四舍五入）
  - 开播日期: 回放页 liveStart 日期 vs 记录日期，不一致标红
  - wind / applive / tencent 平台 → 待人工（无直连视频流）
特性: CDP 复用已登录浏览器、SQLite 断点续跑、失败重试 3 次、workers=2 + 随机抖动（保利威风控）。

用法:
  python pipeline.py --workdir <工作目录> [--trainer 讲师名] [--workers 2] [--frames 8] [--limit N]
工作目录内需有 records.jsonl（parse_excel.py 产出）；产出 results.db + frames/<rid>/。
"""
import argparse
import json
import os
import random
import re
import sqlite3
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

FFMPEG = os.environ.get('AUDIT_FFMPEG')  # imageio_ffmpeg 自带 ffmpeg 的绝对路径
NODE = os.environ.get('AUDIT_NODE')      # node.exe 绝对路径
NODE_MODULES = os.environ.get('AUDIT_NODE_MODULES', '')  # 含 playwright-core 的 node_modules
SKILL_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
HDR = "Referer: https://live.hsqh.net/\r\nUser-Agent: Mozilla/5.0 Chrome/126.0\r\n"
print_lock = threading.Lock()


def log(msg):
    with print_lock:
        print(msg, flush=True)


def init_db(db):
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE IF NOT EXISTS results(
        rid TEXT PRIMARY KEY, trainer TEXT, date TEXT, topic TEXT, level TEXT, online TEXT, worktime TEXT,
        declared_hours REAL, rate REAL, fee REAL, slot TEXT,
        url TEXT, platform TEXT, title TEXT, stream_type TEXT, stream_url TEXT, live_start TEXT,
        video_minutes REAL, verdict TEXT, fee_check TEXT, note TEXT,
        frames_dir TEXT, status TEXT, attempts INTEGER DEFAULT 0)""")
    cols = [c[1] for c in con.execute('PRAGMA table_info(results)')]
    if 'slot' not in cols:  # 旧库升级
        con.execute('ALTER TABLE results ADD COLUMN slot TEXT')
    con.commit()
    return con


def probe_duration(stream_type, url):
    """秒。m3u8: 跟随 master playlist 解析 EXTINF 求和；mp4: ffmpeg stderr Duration"""
    import ssl
    import urllib.request
    from urllib.parse import urljoin
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    def fetch(u):
        req = urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0 Chrome/126.0',
                                                 'Referer': 'https://live.hsqh.net/'})
        return urllib.request.urlopen(req, timeout=30, context=ctx).read().decode('utf-8', 'ignore')

    if stream_type == 'm3u8':
        text = fetch(url)
        if '#EXT-X-STREAM-INF' in text and '#EXTINF' not in text:
            m = re.search(r'URI="([^"]+)"', text) or re.search(r'^([^#\n]+\.m3u8[^\s]*)$', text, re.M)
            if not m:
                return None
            text = fetch(urljoin(url, m.group(1)))
        if '#EXTINF' not in text:
            return None
        total = sum(float(m.group(1)) for m in re.finditer(r'#EXTINF:([\d.]+)', text))
        return total if total > 0 else None
    else:
        # mp4 直链无 ffprobe 可用：用 ffmpeg -t 0.1 读 stderr 的 Duration 行
        r = subprocess.run([FFMPEG, '-hide_banner', '-i', url, '-t', '0.1', '-f', 'null', '-'],
                           capture_output=True, text=True, timeout=120,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        m = re.search(r'Duration:\s*(\d+):(\d{2}):(\d{2}\.\d+)', r.stderr or '')
        if not m:
            return None
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def extract_frames(stream_type, url, n, outdir, dur_sec):
    os.makedirs(outdir, exist_ok=True)
    ok = 0
    step = max(dur_sec / (n + 1), 30)  # 按实测时长布点，长视频不会被固定步长漏采
    for i in range(n):
        t = step * (i + 1)
        if t >= dur_sec:
            break
        ts = f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{int(t % 60):02d}"
        dst = os.path.join(outdir, f"f{i:02d}_{ts.replace(':', '')}.jpg")
        r = subprocess.run([FFMPEG, '-y', '-hide_banner', '-loglevel', 'error', '-ss', ts,
                            '-headers', HDR, '-i', url, '-frames:v', '1', '-q:v', '4',
                            '-vf', 'scale=960:-1', dst],
                           capture_output=True, timeout=180,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        if r.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 5000:
            ok += 1
    return ok


def check_fee(hours, rate, fee):
    if hours is None or rate is None or fee is None:
        return '费用字段缺失'
    expect = hours * rate
    if abs(fee - expect) <= 1.0:
        return '费用正确'
    return f'费用异常: 表内{fee} vs 应为{expect:.2f}'


def process(rec, n_frames, workdir, db):
    rid = f"{rec['trainer']}_{rec['channel_id'] or rec['playback_vid'] or 'x'}"
    con = sqlite3.connect(db)
    row = con.execute("SELECT status, attempts FROM results WHERE rid=?", (rid,)).fetchone()
    if row and row[0] == 'done':
        con.close()
        return ('skip', rid)
    attempts = (row[1] if row else 0) + 1
    if attempts > 3:
        con.execute("UPDATE results SET status='failed_max' WHERE rid=?", (rid,))
        con.commit()
        con.close()
        return ('fail', rid)
    # 硬规则：所有 dict 键先备齐（SQLite 命名参数不允许缺键）
    res = dict(rid=rid, trainer=rec['trainer'], date=rec['date'], topic=rec['topic'],
               level=rec.get('level'), online=rec.get('online'), worktime=rec.get('worktime'),
               declared_hours=rec.get('hours'), rate=rec.get('rate'), fee=rec.get('fee'),
               slot=rec.get('slot'),
               url=rec['url'], platform=rec['platform'], status='running', attempts=attempts,
               title=None, stream_type=None, stream_url=None, live_start=None,
               video_minutes=None, verdict=None, fee_check=None, note=None, frames_dir=None)
    try:
        time.sleep(random.uniform(0, 3))  # 抖动防风控
        env = dict(os.environ)
        if NODE_MODULES:
            env['NODE_PATH'] = NODE_MODULES
        p = subprocess.run([NODE, os.path.join(SKILL_SCRIPTS, 'fetch_stream.js'),
                            rec['url'], os.path.join(workdir, 'cookies.json')],
                           capture_output=True, text=True, timeout=200, env=env)
        stream = json.loads(p.stdout.strip().splitlines()[-1])
        if stream.get('loginWall'):
            res.update(status='login_wall', note='回放页要求登录（cookie 失效？）')
        elif not stream.get('ok'):
            raise RuntimeError('取流失败: ' + stream.get('error', 'no stream'))
        else:
            res.update(title=stream.get('title', ''), stream_type=stream['type'],
                       stream_url=stream['url'], live_start=stream.get('liveStart'))
            dur = probe_duration(stream['type'], stream['url'])
            if not dur:
                raise RuntimeError('时长探测失败')
            res['video_minutes'] = round(dur / 60, 1)
            D = rec.get('hours')
            if D:
                Dm = D * 60
                tol = max(5, Dm * 0.1)
                if res['video_minutes'] >= Dm - tol:
                    res['verdict'] = '时长满足'
                else:
                    res['verdict'] = f'视频短于申报{Dm - res["video_minutes"]:.0f}分钟'
            else:
                res['verdict'] = '无申报课时'
            res['fee_check'] = check_fee(rec.get('hours'), rec.get('rate'), rec.get('fee'))
            if res['live_start']:
                ld = res['live_start'].split(' ')[0].replace('.', '-')
                if ld and rec['date'] and ld != rec['date']:
                    res['verdict'] = (res['verdict'] + '；开播日期不符').strip('；')
            fdir = os.path.join(workdir, 'frames', rid)
            n_ok = extract_frames(stream['type'], stream['url'], n_frames, fdir, dur)
            res['frames_dir'] = fdir
            res['note'] = f'抽帧{n_ok}/{n_frames}'
            res['status'] = 'done'
    except Exception as e:
        res['status'] = 'retry' if attempts < 3 else 'failed'
        res['note'] = str(e)[:150]
    con.execute("""INSERT INTO results(rid,trainer,date,topic,level,online,worktime,declared_hours,rate,fee,slot,
        url,platform,title,stream_type,stream_url,live_start,video_minutes,verdict,fee_check,note,frames_dir,status,attempts)
        VALUES(:rid,:trainer,:date,:topic,:level,:online,:worktime,:declared_hours,:rate,:fee,:slot,
        :url,:platform,:title,:stream_type,:stream_url,:live_start,:video_minutes,:verdict,:fee_check,:note,:frames_dir,:status,:attempts)
        ON CONFLICT(rid) DO UPDATE SET title=:title,stream_type=:stream_type,stream_url=:stream_url,
        live_start=:live_start,video_minutes=:video_minutes,verdict=:verdict,fee_check=:fee_check,note=:note,
        frames_dir=:frames_dir,status=:status,attempts=:attempts""", res)
    con.commit()
    con.close()
    return (res['status'], rid)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--workdir', required=True)
    ap.add_argument('--trainer', type=str, default=None)
    ap.add_argument('--workers', type=int, default=2)  # 并发>2 会触发保利威风控，勿调大
    ap.add_argument('--frames', type=int, default=8)
    ap.add_argument('--limit', type=int, default=0)
    args = ap.parse_args()

    workdir = os.path.abspath(args.workdir)
    os.makedirs(workdir, exist_ok=True)
    db = os.path.join(workdir, 'results.db')
    recs = [json.loads(l) for l in open(os.path.join(workdir, 'records.jsonl'), encoding='utf-8')]
    todo = [r for r in recs if r['platform'] == 'hsqh']
    if args.trainer:
        todo = [r for r in todo if r['trainer'] == args.trainer]
    if args.limit:
        todo = todo[:args.limit]
    log(f'目标 {len(todo)} 条，并发 {args.workers}')
    init_db(db)
    cnt = {'done': 0, 'skip': 0, 'retry': 0, 'fail': 0, 'login_wall': 0}
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(process, r, args.frames, workdir, db): r for r in todo}
        for fu in as_completed(futs):
            st, rid = fu.result()
            cnt[st] = cnt.get(st, 0) + 1
            log(f'[{sum(cnt.values())}/{len(todo)}] {st} {rid}')
    con = sqlite3.connect(db)
    rows = con.execute("SELECT trainer,date,topic,declared_hours,video_minutes,verdict,fee_check,note "
                       "FROM results WHERE status='done'").fetchall()
    print('\n===== 结果汇总 =====')
    for r in rows:
        print(f'{r[0]} | {r[1]} | {str(r[2])[:16]} | 申报{r[3]}h 实测{r[4]}min | {r[5]} | {r[6]}')
    pending = con.execute("SELECT COUNT(*) FROM results WHERE status IN ('retry','failed','failed_max','login_wall')").fetchone()[0]
    print(f'done={cnt["done"]} skip={cnt["skip"]} retry={cnt["retry"]} fail={cnt["fail"]} wall={cnt["login_wall"]} | 库内未完成 {pending}')


if __name__ == '__main__':
    main()
