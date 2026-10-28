# 工程踩坑手册（血泪教训，逐条照做）

## 浏览器 / 登录态
- Chrome 155 App-Bound Encryption 使 browser_cookie3 解密 cookie 必报 RequiresAdminError → 不要走解密路线。
- 默认 profile 无法开 remote debugging（"requires a non-default data directory"）→ 复制最小 profile 子集（Local State + Default/Network + Default/Local Storage）到工作目录 `chrome_profile/`，再以该目录 + `--remote-debugging-port=9222` 启动有头 Chrome，让用户在里面登录一次。
- CDP 模式下 `fetch_stream.js` 结尾必须 `process.exit(0)`，**绝不能调 browser.close()**（会挂死宿主）；node 脚本偶发挂起可 `taskkill //F //IM node.exe` 后重跑（SQLite 断点续跑无损失）。
- cookies.json 被锁（"Device or resource busy"）= 自己先前启动的 Chrome 还占着 profile；先 kill chrome.exe 再复制。
- headless 实例也会占住 profile：用户说"没有 chrome 窗口"时，先杀干净再 `cmd //c start` 有头启动。

## 保利威（Polyv / live.hsqh.net）平台
- **并发 > 2 会触发风控**，稳定配置 = workers=2 + 每条随机抖动 0-3s。不要调大并发。
- 观看页有两类按钮：栏目落地页是"进入直播间"（面面观等多人轮播），专场页是"立即回看"。取流脚本按 `['进入直播间','立即回看','回看','看回放','播放回放']` 顺序点。**取流失败 ≠ 风控**，先查 debug 截图是不是落地页没点按钮。
- m3u8 有 master/media 两级：master 无 EXTINF，必须跟随 `#EXT-X-STREAM-INF` 的 URI（urljoin 补相对路径）到 media playlist 再求和。
- MP4 直链形如 `dpv.videocc.net/***.mp4`（无需 token），但 ffmpeg 探测时长要带 `Referer: https://live.hsqh.net/` 头。
- 取段失败（如 HLS segment 中途 403/超时）导致音频截断 → 重试时加 `-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 10 -rw_timeout 5000000`。
- **m3u8 取流 URL 会过期**（签名参数 `pid/ran` 时效短），DB 里存的 stream_url 隔一段时间再连会报 "Error opening input files: Invalid argument" → 重跑 fetch_stream.js 拿新 URL 再用。
- **短时间内反复新建连接抽单帧会连续失败**（第 1 帧成功、后续全部 Invalid argument）→ 一次连接抽多帧：`-ss <起点> -i url -t <时长> -vf fps=1/300 out%02d.jpg`（每 5 分钟 1 帧，30 分钟区间一条连接搞定），不要循环单帧抽取。

## ffmpeg / 时长
- imageio_ffmpeg 自带的 ffmpeg **没有 ffprobe**，MP4 时长探测报 WinError 2 → 用 `ffmpeg -i url -t 0.1 -f null -` 解析 stderr 的 `Duration:` 行。
- 抽帧步长必须按实测时长计算（`step = dur/(n+1)`），固定"每 90 分钟一帧"会漏采长视频。
- 抽帧带 `-headers` Referer，`scale=960:-1` 控制体积；文件 <5KB 视为废帧。
- **人脸比对要全分辨率帧**：`scale=640:-1` 的小图拼起来肉眼比对不可靠（误判教训），关键争议帧用 `-q:v 3` 无缩放重抽。

## 人脸模型（ArcFace / YuNet）
- 模型下载走 hf-mirror（GitHub 直连被墙）：YuNet=`opencv/opencv_zoo/.../face_detection_yunet_2023mar.onnx`（232KB），ArcFace=`immich-app/buffalo_l/resolve/main/recognition/model.onnx`（w600k_r50，174MB）。hf-mirror 偶发 429，退避 20s 重试即可。
- **模型与图片路径必须处理中文**：OpenCV DNN/FaceDetectorYN 在 Windows 读不了非 ASCII 路径 → 模型放 `C:/temp/face_models/`；图片用 `np.fromfile + cv2.imdecode` 读取（cv2.imread 同样读不了中文路径）。
- buffalo_l 的检测模型是 **SCRFD 不是 YuNet**，`cv2.FaceDetectorYN` 加载 det_10g.onnx 会报 "Can't read ONNX file"（该报错也可能是中文路径导致，先换纯 ASCII 路径再怀疑模型）→ 检测用 opencv_zoo 的真 YuNet，识别用 onnxruntime 直跑 w600k_r50。
- ArcFace 预处理：YuNet 五点 → `estimateAffinePartial2D` 对齐到标准 112×112 模板 → blobFromImage(1/127.5, mean=127.5, swapRB=True) → 余弦相似度。
- OpenCV 5.x 新图引擎警告 "Targets are not supported" 可忽略。

## SQLite / 数据纪律
- **硬规则：时长探测失败 = 永远不许标 done**，只能 retry（最多 3 次），否则报告出假数据。
- SQLite 命名参数：dict 所有键必须先备齐，缺键报 "did not supply a value for binding parameter"。
- 断点续跑以 rid=`讲师_channel_id` 为主键，重跑自动 skip 已 done 的。

## Python 环境（WorkBuddy 托管运行时）
- venv 的 python 在 `<venv>/Scripts/python.exe`（根目录没有 python.exe）。
- ffmpeg 路径：`<venv>/Lib/site-packages/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-*.exe`。
- playwright-core 装在 node workspace：跑 node 脚本时设 `NODE_PATH=<node_workspace>/node_modules`。
- 依赖清单：`pip install openpyxl pillow`；node 侧 `playwright-core`（免浏览器下载，用本机 Chrome）。

## ASR / 语音（可选增强）
- 在线抽音频（不下整片）：ffmpeg HTTP Range + `-vn -acodec copy -f ipod`，190MB 视频 → 52MB 音频约 1 分钟。
- AudioTranscribe 支持说话人分离：222 段，按"累计发言 ≥60s"过滤说话人，即可得每位讲师实际授课时长（示例：2 位讲师 26.7/29.2 分钟，交接点 29:44）。
