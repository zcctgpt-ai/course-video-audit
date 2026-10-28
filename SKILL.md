---
name: 授课时长核对
description: 讲师授课视频批量核验。当用户提供"研究所授课汇总"类 Excel（每讲师一个 sheet、含视频回放链接、课时/课时费/授课费）和讲师照片（文件名=讲师名），要求核验"视频里是否本人授课、实际授课时长、授课费算术"并出报告时使用。触发词：授课汇总、核验授课、授课时长核对、内训师视频核对、授课费审计、人脸核验视频。全自动流水线：解析 Excel → 浏览器取流 → 时长探测 → 费用勾稽 → 抽帧人脸比对 → HTML 报告，缺料时先一次性向用户要齐。
---

# 授课时长核对（course-video-audit）

对培训客户提交的《研究所授课汇总》Excel 做全量自动核验：①回放可访问性 ②视频实测时长 vs 申报课时 ③授课费算术与汇总勾稽 ④人脸身份核验（底库照片 vs 回放出镜帧），产出客户可直接转发的 HTML 核验报告。

## 铁律

1. **全程不停**：材料齐了就一口气跑完全部流程再交付；中途不逐条请示。只有两种情况允许停下来找用户——登录墙（需要用户人工登录）和取流持续失败需用户换链接。
2. **缺料先问齐再跑**：开工前按下文"开工前清单"自查，缺什么一次性向用户要齐（AskUserQuestion 或一条消息列全），不要挤牙膏式反复追问。
3. **人脸是身份的唯一证明**：姓名条 OCR 只作旁证。没有讲师照片时，时长/费用照跑，人脸列标"待照片"，并把"提供照片"写进报告下一步。
4. **时长探测失败的记录永远不许标 done**，只能重试（≤3 次）；宁可报告里写"待复核"，不许出假数据。
5. 并发 workers=2 + 随机抖动，**不许调大**（保利威风控）。工程细节见 `references/pitfalls.md`，判定口径见 `references/checks.md`，报告样式见 `references/report_spec.md`。

## 环境准备（首次运行检查一次即可）

- Python venv：`C:\Users\HP\.workbuddy\binaries\python\envs\default\Scripts\python.exe`（依赖 `openpyxl pillow`）。
- ffmpeg：`<venv>\Lib\site-packages\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-*.exe`（无 ffprobe，时长探测走 stderr 解析，脚本已内置）。
- node：`C:\Users\HP\.workbuddy\binaries\node\versions\22.22.2-6\node.exe`，跑 fetch_stream.js 时设 `NODE_PATH=C:\Users\HP\.workbuddy\binaries\node\workspace\node_modules`（playwright-core 用本机 Chrome，无需下载浏览器）。
- 缺哪个装哪个，装进 venv / node workspace，不污染全局。

## 开工前清单（缺一项就在同一条消息里向用户要齐）

| 材料 | 用途 | 缺失时的问法 |
|---|---|---|
| Excel 路径（每讲师一个 sheet） | 全流程 | "请提供授课汇总 Excel 的路径" |
| 讲师照片文件夹（文件名=讲师名，正脸） | 人脸核验 | "请提供讲师照片，文件名要与 Excel sheet 名一致；没有照片的讲师我先跳过人脸" |
| 直播平台登录 | hsqh 回放有登录墙 | "回放需要登录：我启动一个浏览器窗口，请你在里面登录一次"（见下"登录态"） |
| 费率标准（图/表） | 费率口径存疑时佐证 | "如有授课费支付标准文件请一并提供；没有就只做算术校验" |
| 云 ASR API key（可选） | 精确核切片起止/纯音频课 | 默认不做 ASR，报告注明边界即可，不主动拦流程 |

## 工作目录约定

在当前项目下建 `pipeline/` 作为工作目录：`records.jsonl`、`results.db`、`cookies.json`、`frames/`、`montages_face/`、报告 HTML 都放这里。断点续跑：重跑自动跳过已 done 的记录。

## 流程

### Phase 0 — 登录态检查（先于一切批量操作）
1. 从 records.jsonl 取一条 hsqh 链接跑 `fetch_stream.js`。
2. **自动尝试登录恢复，能不动用户就不动**，按序尝试：
   a. CDP 连接 `127.0.0.1:9222`（已有审计 Chrome 在跑则直接复用其登录态）；
   b. 端口不通 → 用 `chrome_profile/` 目录重新拉起有头 Chrome（`--remote-debugging-port=9222`），profile 里通常还留有上次登录态，拉起后直接重探测，**不要先问用户**；
   c. 仍 loginWall → 才请用户在窗口里登录一次，登录后从 CDP 会话导出 cookie 存 `pipeline/cookies.json`。
3. 探测返回 `ok: true` 即进入 Phase 2。**注意坑**：`cmd //c start` 拉起 Chrome 可能静默失败（进程根本没起来），启动后必须 `curl http://127.0.0.1:9222/json/version` 确认端口在，不在就重拉；不要随手 `taskkill chrome.exe`——会把用户自己开着的 Chrome 一起杀掉，也把 CDP 会话弄丢。

### Phase 1 — 解析 Excel
```bash
python scripts/parse_excel.py --excel <xlsx路径> --out <workdir>/records.jsonl
```
- 核对输出：记录条数、讲师数、平台分布（hsqh/applive/wind/tencent/other）。
- 有讲师 sheet 无任何链接 → 记入"待人工"，写进报告。
- **注意**：classify() 先判 `applive.hsqh.net` 再判 `live.hsqh.net`（子串包含，顺序勿改）。

### Phase 2 — 批量核验（时长 + 费用 + 抽帧）
```bash
python scripts/pipeline.py --workdir <workdir> --workers 2 --frames 8
# 环境变量：AUDIT_FFMPEG=<venv路径>\...\ffmpeg-win-x86_64-v7.1.exe  AUDIT_NODE=<node.exe>
# 可选 --trainer <名字> 单跑某人
```
- 跑完检查：done/retry/fail/login_wall 分布；login_wall 说明 cookie 失效 → 回 Phase 0。
- 费用校验：授课费 ≈ 课时×课时费率（差 >1 元标红）；另做**逐条加总 vs 表内汇总单元格**的勾稽（需从 Excel 单独读汇总单元格），差额标红进报告。
- 类型判定以回放页标题为准（专场 vs 栏目切片，口径见 checks.md）。

### Phase 3 — 人脸核验（agent 亲自做多模态比对）
1. 对每位讲师（有照片者）逐条生成拼图：
```bash
python scripts/make_montage.py --frames-dir <workdir>/frames/<讲师_rid> --out <workdir>/montages_face/<讲师_rid>.jpg --mode pip
```
2. 先 Read 讲师底库照片建立基准，再逐张 Read 拼图比对（一次批量读 3-4 张）。
3. 每条记录判定：出镜帧数 N/8、人脸与照片是否一致、姓名条是否相符。
4. 画中画拼图看不到人 → `--mode full` 出全帧拼图复查；仍无人 → 判"纯屏幕录制·无人脸画面·无法核验"。
5. 比对不符（疑似冒名）→ 立即标红，单独向用户报告，不混进常规汇总。

### Phase 4 — 出报告 + 交付
1. 按 `references/report_spec.md` 写 `<客户名>核验报告_<年季度>.html`，六段结构，三色语义，数字逐项勾稽自检。
2. **必须调用 present_files 推送报告**，并在回复里给出：通过/异常/待人工条数、金额差额、需要客户确认的事项清单。

## 平台分流
- `live.hsqh.net` → 全自动。
- `applive.hsqh.net`（预约页）、`3c.wind.com.cn`、`meeting.tencent.com` → 无直连视频流，归"待人工核验"，金额照常计入勾稽。
- 其余未知平台 → 抽 1 条人工探测（Read 截图看页面结构）后再决定。

## Resources
- `scripts/parse_excel.py` — Excel → records.jsonl（表头关键字定位，多链接拆行）
- `scripts/fetch_stream.js` — 取流 worker（CDP 优先/cookie 回退，按钮点击，抓 m3u8/mp4 + liveStart）
- `scripts/pipeline.py` — 批量核验（SQLite 断点续跑，时长/费用/日期判定，抽帧）
- `scripts/make_montage.py` — 人脸比对拼图（画中画裁剪/全帧两种模式）
- `references/pitfalls.md` — 工程踩坑手册（浏览器/保利威/ffmpeg/SQLite/ASR，逐条照做）
- `references/checks.md` — 核验口径（类型语义、费率表、勾稽纪律、人脸判定三档）
- `references/report_spec.md` — 报告结构与交付纪律
