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

## ffmpeg / 时长
- imageio_ffmpeg 自带的 ffmpeg **没有 ffprobe**，MP4 时长探测报 WinError 2 → 用 `ffmpeg -i url -t 0.1 -f null -` 解析 stderr 的 `Duration:` 行。
- 抽帧步长必须按实测时长计算（`step = dur/(n+1)`），固定"每 90 分钟一帧"会漏采长视频。
- 抽帧带 `-headers` Referer，`scale=960:-1` 控制体积；文件 <5KB 视为废帧。

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
