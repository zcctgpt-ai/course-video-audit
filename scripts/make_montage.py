# -*- coding: utf-8 -*-
"""人脸核验拼图生成：把某讲师某条记录的抽帧拼成 4x2 网格，供多模态视觉比对。

产出两类（都放 <workdir>/montages_face/）:
  1. <rid>.jpg          —— 主讲画面(画中画)裁剪拼图：右侧区域 x 0.63-1.0, y 0.06-0.78
  2. <rid>_full_1/2.jpg —— 全帧缩略拼图（画中画裁不到人时用，如纯屏幕录制课）

用法:
  python make_montage.py --frames-dir <frames/讲师_rid> --out <输出jpg> [--mode pip|full]
"""
import argparse
import glob

from PIL import Image


def load_thumbs(frames_dir, max_w, max_h):
    thumbs = []
    for f in sorted(glob.glob(f'{frames_dir}/*.jpg')):
        im = Image.open(f)
        im.thumbnail((max_w, max_h))
        thumbs.append(im)
    return thumbs


def grid4x2(thumbs, out, bg='white', quality=88):
    if not thumbs:
        return False
    w = max(t.size[0] for t in thumbs)
    h = max(t.size[1] for t in thumbs)
    cols, rows = 4, 2
    grid = Image.new('RGB', (w * cols, h * rows), bg)
    for i, t in enumerate(thumbs[:cols * rows]):
        grid.paste(t, ((i % cols) * w, (i // cols) * h))
    grid.save(out, quality=quality)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--frames-dir', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--mode', choices=['pip', 'full'], default='pip')
    args = ap.parse_args()

    if args.mode == 'pip':
        thumbs = []
        for f in sorted(glob.glob(f'{args.frames_dir}/*.jpg')):
            im = Image.open(f)
            w, h = im.size
            # 画中画在画面右侧（李总项目实测：x 0.63-1.0, y 0.06-0.78）
            im = im.crop((int(w * 0.63), int(h * 0.06), w, int(h * 0.78)))
            im.thumbnail((480, 480))
            thumbs.append(im)
        ok = grid4x2(thumbs, args.out)
        # 第二块拼图（超过 8 帧时）
        if ok and len(thumbs) > 8:
            grid4x2(thumbs[8:], args.out.replace('.jpg', '_b.jpg'))
    else:
        thumbs = load_thumbs(args.frames_dir, 640, 360)
        grid4x2(thumbs[:4], args.out)
        if len(thumbs) > 4:
            grid4x2(thumbs[4:8], args.out.replace('.jpg', '_2.jpg'))
    print('ok' if thumbs else 'no frames')


if __name__ == '__main__':
    main()
