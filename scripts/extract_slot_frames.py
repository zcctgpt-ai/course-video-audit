# -*- coding: utf-8 -*-
"""切片区间抽帧：对带"授课时间段"（分钟区间，如 7-46）的记录，在申报区间内均匀抽帧。

产出 <workdir>/frames/<rid>/slot_fNN_<时分秒>.jpg，供人脸比对验证"申报切片区间内确为本人授课"。

用法:
  python extract_slot_frames.py --workdir <workdir> --trainer <名字> [--n 5]
依赖 results.db（pipeline.py 已跑完）与 AUDIT_FFMPEG 环境变量。
"""
import argparse
import json
import os
import re
import sqlite3
import subprocess

HDR = "Referer: https://live.hsqh.net/\r\nUser-Agent: Mozilla/5.0 Chrome/126.0\r\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--workdir', required=True)
    ap.add_argument('--trainer', required=True)
    ap.add_argument('--n', type=int, default=5)
    args = ap.parse_args()
    ffmpeg = os.environ['AUDIT_FFMPEG']
    db = os.path.join(args.workdir, 'results.db')
    con = sqlite3.connect(db)
    rows = con.execute("SELECT rid, slot, video_minutes, stream_type, stream_url, frames_dir, status "
                       "FROM results WHERE trainer=? AND status='done'", (args.trainer,)).fetchall()
    con.close()
    for rid, slot, vmin, stype, surl, fdir, status in rows:
        if not slot or not re.match(r'^\s*(\d+)\s*-\s*(\d+)\s*$', str(slot)):
            print(f'{rid}: 无切片区间，跳过')
            continue
        a, b = map(int, re.match(r'^\s*(\d+)\s*-\s*(\d+)\s*$', str(slot)).groups())
        if vmin and b > vmin + 1:  # 区间超出视频实际长度则收敛到视频末端
            b = int(vmin) - 1
        if b <= a:
            print(f'{rid}: 区间异常 {slot}')
            continue
        os.makedirs(fdir, exist_ok=True)
        ok = 0
        step = (b - a) / (args.n + 1)
        for i in range(args.n):
            t = (a + step * (i + 1)) * 60
            ts = f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{int(t % 60):02d}"
            dst = os.path.join(fdir, f"slot_f{i:02d}_{ts.replace(':', '')}.jpg")
            r = subprocess.run([ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-ss', ts,
                                '-headers', HDR, '-i', surl, '-frames:v', '1', '-q:v', '4',
                                '-vf', 'scale=960:-1', dst],
                               capture_output=True, timeout=180,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            if r.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 5000:
                ok += 1
        print(f'{rid}: 区间[{slot}] 抽帧 {ok}/{args.n}')


if __name__ == '__main__':
    main()
