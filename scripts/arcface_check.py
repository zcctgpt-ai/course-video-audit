# -*- coding: utf-8 -*-
"""ArcFace 人脸身份比对（本 skill 人脸判定的主依据）。

用法：
  python arcface_check.py --ref 讲师照片.jpg --frames-dir frames/讲师_rid/ [--pip] [--negatives-dir 其他讲师帧目录]

输出：每帧与底库照片的余弦相似度 + 判定（本人/非本人/不确定）。
铁律：必须跑阴性对照校准阈值后才允许下结论；姓名条不作为身份证据。
"""
import argparse, os, sys, subprocess
import numpy as np
import cv2
import onnxruntime as ort

MODEL_DIR = r'C:/temp/face_models'  # 必须纯 ASCII：OpenCV DNN 读不了中文路径
YUNET_URL = 'https://hf-mirror.com/opencv/opencv_zoo/resolve/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx'
ARC_URL = 'https://hf-mirror.com/immich-app/buffalo_l/resolve/main/recognition/model.onnx'
ARC_DST = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
                    [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float32)


def ensure_models():
    os.makedirs(MODEL_DIR, exist_ok=True)
    yunet = os.path.join(MODEL_DIR, 'yunet_2023mar.onnx')
    arc = os.path.join(MODEL_DIR, 'w600k_r50.onnx')
    for path, url, min_size in [(yunet, YUNET_URL, 200_000), (arc, ARC_URL, 100_000_000)]:
        if not (os.path.exists(path) and os.path.getsize(path) > min_size):
            print(f'[models] 下载 {os.path.basename(path)} ...', file=sys.stderr)
            subprocess.run(['curl', '-L', '--max-time', '550', '-o', path, url, '-s'], check=True)
            assert os.path.getsize(path) > min_size, f'{path} 下载不完整'
    return yunet, arc


def imread_u(path):
    data = np.fromfile(path, dtype=np.uint8)  # Windows 中文路径安全
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    assert img is not None, f'读图失败: {path}'
    return img


def make_embedder():
    yunet_path, arc_path = ensure_models()
    det = cv2.FaceDetectorYN.create(yunet_path, '', (320, 320), 0.6)
    so = ort.SessionOptions(); so.intra_op_num_threads = 4
    sess = ort.InferenceSession(arc_path, so, providers=['CPUExecutionProvider'])
    return det, sess, sess.get_inputs()[0].name


DET, REC_SESS, REC_IN = None, None, None


def detect_max_face(bgr):
    h, w = bgr.shape[:2]
    DET.setInputSize((w, h))
    ok, faces = DET.detect(bgr)
    if faces is None or len(faces) == 0:
        return None
    f = max(faces, key=lambda x: x[2] * x[3])
    if f[2] * f[3] < 40 * 40:  # 小于 40px 的脸不可靠
        return None
    return f[4:14].reshape(5, 2)


def embed(bgr):
    lm = detect_max_face(bgr)
    if lm is None:
        return None
    M, _ = cv2.estimateAffinePartial2D(lm, ARC_DST, method=cv2.LMEDS)
    aligned = cv2.warpAffine(bgr, M, (112, 112))
    blob = cv2.dnn.blobFromImage(aligned, 1.0 / 127.5, (112, 112), (127.5, 127.5, 127.5), swapRB=True)
    emb = REC_SESS.run(None, {REC_IN: blob})[0][0]
    return emb / np.linalg.norm(emb)


def cos(a, b):
    return float(np.dot(a, b))


def batch_sim(ref, paths, label):
    sims = []
    for p in sorted(paths):
        e = embed(imread_u(p))
        if e is None:
            print(f'  {label} {os.path.basename(p):30s} 未检出人脸（跳过）')
            continue
        s = cos(ref, e)
        sims.append(s)
        print(f'  {label} {os.path.basename(p):30s} sim={s:.3f}')
    return sims


def main():
    global DET, REC_SESS, REC_IN
    ap = argparse.ArgumentParser()
    ap.add_argument('--ref', required=True, help='讲师底库照片')
    ap.add_argument('--frames-dir', required=True, help='待核验帧目录')
    ap.add_argument('--negatives-dir', help='阴性对照帧目录（确定非本人的帧）')
    ap.add_argument('--pip', action='store_true', help='先裁右侧画中画区域再检测（画面太小且多脸时用）')
    args = ap.parse_args()

    DET, REC_SESS, REC_IN = make_embedder()

    ref = embed(imread_u(args.ref))
    assert ref is not None, '底库照片未检出人脸'
    print(f'底库: {args.ref}')

    if args.negatives_dir:
        print('=== 阴性对照（已知非本人）===')
        negs = batch_sim(ref, [os.path.join(args.negatives_dir, f)
                               for f in os.listdir(args.negatives_dir) if f.lower().endswith('.jpg')], '[阴性]')
        neg_max = max(negs) if negs else 0.0
    else:
        neg_max = 0.08  # 经验基线（无对照时的默认噪声上界）

    print('=== 待核验帧 ===')
    frames = [os.path.join(args.frames_dir, f) for f in os.listdir(args.frames_dir)
              if f.lower().endswith(('.jpg', '.png'))]
    # 画中画模式：裁右侧区域另存临时图
    if args.pip:
        tmp = os.path.join(os.path.dirname(args.frames_dir.rstrip('/')) or '.', '_pip_tmp')
        os.makedirs(tmp, exist_ok=True)
        cropped = []
        for p in frames:
            img = imread_u(p); w, h = img.shape[1], img.shape[0]
            crop = img[int(h*0.06):int(h*0.78), int(w*0.63):w]
            tp = os.path.join(tmp, os.path.basename(p))
            cv2.imencode('.jpg', crop)[1].tofile(tp)
            cropped.append(tp)
        frames = cropped

    sims = batch_sim(ref, frames, '[待验]')

    pos = [s for s in sims if s >= max(0.30, neg_max + 0.20)]
    ambiguous = [s for s in sims if max(0.15, neg_max + 0.08) <= s < max(0.30, neg_max + 0.20)]
    print('=== 判定 ===')
    print(f'阴性基线 max={neg_max:.3f} ｜ 待验帧 n={len(sims)}, 本人(≥阈值)={len(pos)}, 灰区={len(ambiguous)}')
    if sims:
        m = max(sims)
        if m >= max(0.30, neg_max + 0.20):
            print(f'结论: 出镜帧与底库为同一人（max sim={m:.3f}）')
        elif m >= max(0.15, neg_max + 0.08):
            print(f'结论: 不确定（max sim={m:.3f} 落在灰区），需人工/ASR 复核')
        else:
            print(f'结论: 未发现与底库同一人（max sim={m:.3f} 接近阴性基线）')


if __name__ == '__main__':
    main()
