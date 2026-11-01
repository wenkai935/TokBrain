# TokBrain 项目完整变更日志（从克隆到本地至今）

> 本文档整合三处史料写成：
> 1. `D:/code/new/TokBrain`（带 `.git` 的原始仓库，remote = `github.com/fyfsxkh/TokBrain`，8 个提交，止 2026-10-29 `codex` 分支）
> 2. `D:/code/TokBrain-main`（当前工作副本，GitHub 源码 ZIP 形态、无 git，CHANGELOG 主线到 2026-11-02）
> 3. 本地工作记忆（2026-11-01 ~ 11-02 的逐日操作记录）与专项文档 `docs/抖音解析通道替换_playwright.md`
>
> 版本/功能详述以 `CHANGELOG.md` 为权威主线；本文额外补齐**失败路径、弃用原因、分支分歧**这三块 `CHANGELOG.md` 没有写透的内容。

---

## 0. 项目形态与两份工作副本

| 路径 | 性质 | 时间范围 | 说明 |
|---|---|---|---|
| `D:/code/new/TokBrain` | git 仓库（8 commits） | 2026-07-28 → 2026-10-29 | 原始克隆，分支 `codex`/`main`，含 Unreleased 的「F2 头修复」「动态主题」两节 |
| `D:/code/TokBrain-main` | ZIP 解压工作副本（无 git） | ~2026-08 → 2026-11-02 | 实际开发所在地，CHANGELOG 主线最新，含控制面、Cookie 库、Playwright 替换、控制台 `[11]` |

> 注：`D:/code` 根目录的 `clone.log` / `lsremote.txt` / `git_err.txt` 是 **ooosplat / resume 等其他项目** 的克隆日志（git 502 失败），与 TokBrain 无关，不纳入本史。

---

## 1. 克隆起点与版本时间线

**克隆起点**：2026-07-28 17:07:07 +0800，作者 `fyfsxkh`，首提交 `0406f14 Initial TokBrain release`（remote `https://github.com/fyfsxkh/TokBrain.git`）。

| 版本 / 阶段 | 时间 | schema | API | 关键点 |
|---|---|---|---|---|
| v0.4.0 | 2026-07-31 | v5 | 4 | 收藏夹提示词、两段式导入、去重、删除清理 |
| v0.5.0（并入 1.0） | 2026-08 | v8/v9 | 6/7 | 证据可信度、安装发布工程、外部数据包、本地视频 |
| **v1.0.0** | 2026-08-11 | v9 | 7 | 全流程重梳理、预检确认闸门、知识库分类 |
| Unreleased（安全/同步） | 2026-10-29~30 | v11 | 7 | 控制面 MCP、Hermes/NAS 只读副本、安全修复、飞书同步、多模型档 |
| Unreleased（控制面/采集） | 2026-11-01 | v11 | — | 控制面接入、平台采集修复、启动入口统一、`[11]` 停止开发模式 |
| Unreleased（解析/密钥） | 2026-11-02 | — | — | 统一 Cookie 库、抖音解析换 Playwright、控制台 dev-stop |

> 应用版本号长期停在 **v1.0.0**，但数据库已迁移到 **schema v11**（迁移前自动在 `data/backups/` 留 `douyin_rag-pre-vN-*.db`）。

---

## 2. 分阶段变更详述

### 2.1 v0.4.0 — 2026-07-31
- **收藏夹专属总结提示词**：每收藏夹存自己的提示词，入库/补齐时按"最近加入的收藏夹"选用，清空后回退全局。
- **两段式导入**：预检完成 → 选收藏夹 → "确认加入待入库"（只建关系、不下载、不调 AI、不建任务）→ 再"入库"才处理。
- **自动去重**：同批/跨批/并发/短链归一均去重，只显示"已去掉 N 条重复"，不显示误导卡片。
- **删除与清理**：各状态作品及已完成总结均可永久删除（同步清总结、知识块、索引、关键帧、本地资产，不可恢复）。
- **schema v5**：新增收藏夹提示词字段，升级前自动时间戳备份。
- 验证：后端 114 项、前端契约 30 项、lint/构建通过（人工数据，不触真实抖音）。

### 2.2 v0.5.0 开发记录（已并入 v1.0.0）
- **证据可信度 / schema v9**：已入库受限视频若无本地源、且无字幕/转写/OCR/视觉描述，标"证据不足/需补件"，迁移时清正文/总结/知识块/关键帧，防止无来源文本被当知识检索。
- **安装发布工程**：`setup.ps1` 在临时目录建干净 Python 3.12 环境，固定依赖 + `pip check` + 核验 F2 仅从 `.vendor/` 加载；安装阶段生成 Next 生产构建，日常改用 `next start`；启动状态原子写（实例 ID/工作区/端口/创建时间/exe/命令行哈希），停止只结束身份匹配进程；CI 走正式 Windows 脚本，门槛含 ffprobe、F2 来源、TS typecheck、覆盖率、依赖审计。
- **链接入口回退**：移除自动确认/自动 AI 入库，恢复"预检→手工确认→主动入库"，旧批次遗留自动化标记重启不恢复。
- **外部视频数据包**：选下载文件夹/ZIP，一次 ≤100 视频，自动识别清单 JSON/CSV、F2 `douyin_videos.db`、侧车、文件名抖音 ID；不访问 F2、不耗链接额度、不自动产生模型费；ZIP 拒绝加密/嵌套/符号链接/`..`/异常压缩比。
- **本地视频**：独立入口，一次 ≤10 个，一文件一作品，魔数/容器/视频轨/大小/SHA-256/时长/预算预检 + 去重，不耗额度。
- **外部批量导入 API v1**：Bearer 令牌建清单/逐文件上传/提交批次，每批 ≤100，需权利声明 + `Idempotency-Key`，默认只进待入库、`start_processing=true` 才建任务；令牌明文仅显一次，库存 SHA-256 + 前缀。
- **音画协同关键帧**：候选池含转场稳定帧 + 全时段均匀采样，ASR 句级时间戳驱动视觉证据需求，最终精确二次抽帧；schema v6。

### 2.3 v1.0.0 — 2026-08-11（schema v9 / API 7）
- 导入方式扩展（链接/本地/文件夹/ZIP），预检全停确认页，重复识别，今日统计只算成功入库。
- 处理综合字幕+语音+画面文字+关键帧；材料不足显式标"需补件"并允许补本地视频后继续。
- 知识库分类（待处理/已入库/待补件/异常/归档）、收藏夹、批量、详情；Obsidian 导出整理笔记与图片。
- 稳定性：干净安装、进程身份识别、中断恢复、密钥由 Windows 保护、默认仅本机访问、日志脱敏。
- **更正（2026-10-30）**：原发布说明里"网页端问答/对话""流式回答"**从未实现**——后端无 `/api/chat`、无 SSE，前端 `chat()`/`chat_stream()` 无调用方；实际只经 MCP 提供**检索**能力。该误述已在 `CHANGELOG.md` 原文保留并加注。

### 2.4 2026-10-29~30 — 安全/一致性/外部接入一轮大改
- **知识检索接入外部**：新增本机只读 MCP `tokbrain_mcp.py`（`:8010`，5 工具 `kb_search/get_work/get_transcript/list_collections/stats`）；Hermes 专用 stdio MCP `tokbrain_hermes_stdio.py`（`tokbrain_*`）。**绑定非回环必须 `TOKBRAIN_MCP_TOKEN`，否则拒绝启动**（旧默认 `0.0.0.0`+空令牌已修）。
- **Hermes/NAS 只读副本**：`export_tokbrain_snapshot.py` 出一致快照 + integrity_check + sha256；`deploy_*.ps1` 上传→校验→原子替换（旧库 `.bak`）；NAS 侧 `mode=ro&immutable=1`，Hermes 不可写。首部署验收：14 作品 / 305 片段 / 13,217,792 字节，SHA-256 与本地一致。禁止 SMB/NFS 直开 SQLite。
- **安全与一致性修复（独立代码审查发现的 7 项）**：
  - 平台 Cookie 出站收紧（小红书只发 HTTPS 官方域 + DNS 校验 + 逐跳重定向校验）；
  - MCP 鉴权（默认回环 + 非回环强制令牌，补 `启动MCP服务.cmd` + `mcp.env.example`）；
  - 活动模型配置贯通（处理/总结/检索统一读激活档，百炼专属媒体能力下 OpenAI 兼容配置**显式报错**，不再静默回退）；
  - 清密钥覆盖全部配置档；
  - 预算跨日修正（上海时区绑定）；
  - 幂等重放语义（重放返回与首次一致状态码 + `replayed=true`）。
- **飞书↔Obsidian 同步进主流程**：`app/routers_v2/sync.py`（`/api/sync/overview`、`/feishu/relogin`）、`sync_status.py`、`feishu_auth.py`（驱动 `lark-cli` 设备码出二维码）；前端侧边栏飞书连接状态；桥接 `bridge_feishu_obsidian.py`（1822 行，`--watch` 常驻、`--retry` 重投）；入站 `/integrations/v1/inbox/items` 只入队，真正处理必须由常驻桥完成。
- **多模型配置档**：`/api/model-profiles`（增删改查+激活），替代"文本/音频各填一套 Key"。
- **前端**：页签固定「导入/解析/知识库/设置」；主题精简为 4 套（`aura` 默认 / `sunset-cloudsea` / `eastern-mist` / `ocean-dawn`）；本地视频逐个顺序上传；补预算/网络文案、批次持久化、分页、长任务轮询。
- **文档**：重写 README、更正操作说明书、校正多份 `docs/*` 与代码不符处。

### 2.5 2026-11-01 — 控制面接入 / 平台采集修复 / 启动入口统一
- **控制面接入**（`tokbrain_control/`，纯标准库 ~2400 行，不 import `app.*`）：
  - `mcp_server/mcp_control.py`（`:8011`，33 个 `tb_*` 工具，14 只读/16 写），转发既有 REST，不复制逻辑。显式开关 `TOKBRAIN_CONTROL_ENABLE=1`、令牌 ≥32 位、端口预检。
  - `tbctl.cmd` / `python -m tokbrain_control`：`status/doctor/service/import/batch/ingest/job/work/collection/search/platform/fs/audit`，`--json` 信封，退出码 0/1/2/3。
  - 媒体路径约定（`paths.py` + `tb_fs_roots`）：Windows↔NAS 路径映射，`TOKBRAIN_CONTROL_MEDIA_ROOTS=<Win根>|<NAS前缀>`；含 `..` 拒绝。
  - 飞书桥控制（`bridge.py` + 5 个 `tb_sync_*`）：状态/启动常驻轮询/按命令行精确停止/分批重投 failed/等队列跑完；原则：**不让 agent 自读飞书表**（会和桥抢行、破坏状态回写）。
  - 修复 `bridge_feishu_obsidian.py` 在本机代理下必败：`requests` 默认 `trust_env`，系统配了 `HTTP_PROXY` 时连 `127.0.0.1:8001` 也被推给代理 → 后端收 path 成 `http://127.0.0.1:8001/api/...` 直接 404；控制面拉桥注入 `NO_PROXY=127.0.0.1,localhost,::1`。
  - 导入节流：`tb_import_links` ≤10、`tb_sync_replay` ≤20（默认 5），依据实测 120 条 failed 全是 429，一次性放开会重演。
  - `scripts/menu.ps1` 加 `control` 动作 + 菜单 `[8]`；`status` 多两行；README/操作说明书（新增第 11 章）/SECURITY 同步；控制面不受 `stop` 管辖。
- **修复平台采集条目永不解析**：`pull`/勾选同步建的批次条目写 `pending`，而预检 worker 只取 `queued` → 批次秒标 `succeeded`、条目永远"解析中"。改 `create_platform_import_batch` 按平台分流（抖音写 `queued` 进预检；B 站/小红书无解析器写 `needs_local_file`）；批次状态跟条目走；`confirm_import_items` 放宽 `platform_pull` 允许无视频资产建作品；新增 `tests/test_platform_import_flow.py`（5 项），全量 349 passed。
- **启动入口统一 + 飞书「预检后等确认」**：
  - `start`/`start-bg`/`restart` 服务就绪后自动拉飞书桥（复用 `tokbrain_control.bridge`，不写第二份），`stop` 先停桥；`TOKBRAIN_AUTOSTART_BRIDGE=0` 关闭。
  - 新增 `FS_AUTO_CONFIRM`（默认 0）：桥发现新行只做预检（f2 补标题/作者/封面）后停 `awaiting_confirm`，回写飞书"处理状态=待确认"；设 1 回旧一条龙。
  - `dispatch` 加 `batch_id` 列（老库 backfill），`awaiting_confirm` 入终态；修 `pending_items()` SQL 把 `NOT IN (?,?,?)` 写死三占位符、终态加到 4 个后抛 `ProgrammingError` → 改按集合长度生成。
  - 新增 `tb_sync_pending`（只读）/ `tb_sync_confirm`（真 confirm+ingest），控制面工具 33→**35**（15 只读/20 写）。
  - `enqueued` 不再一律 `true`：B 站/小红书无解析器如实返 `false` + `queued_count`。

### 2.6 2026-11-02 — 统一 Cookie 库 / 抖音解析换 Playwright / 控制台 dev-stop
- **统一 Cookie 库（cookie-vault）**：原 Cookie 散三处（`SecretRecord(f2_cookie)`、`SecretRecord(douyin_cookie)`、下载脚本 `douyin_cookies.txt`），换号必漏改。改为 `data/cookies/<platform>.txt` 唯一事实来源（`TOKBRAIN_COOKIE_DIR` 可覆盖），设置页统一上传，各消费者按需解析。
  - 新增 `app/services/cookie_store.py`（Netscape/Cookie-Editor JSON/header 三格式解析、严格域名后缀过滤、同名去重、原子写 0600）、`app/routers_v2/cookies.py`（固定路径须声明在 `/{platform}` 之前）、`frontend/components/CookieVault.tsx`。
  - 接线：F2/平台采集/handoff/旧粘贴框/外部下载器 `bridge_douyin.py::cookie_source()` 全改读同一份；旧 Secret **解不开显式抛 `f2_cookie_unreadable`**，不再静默降级匿名。
  - 验收：`test_cookie_store.py` 14 passed；后端 378 passed/1 skipped；tsc+eslint 干净。现有 `douyin_cookies.txt`（63 条）已迁入 `data/cookies/douyin.txt`。文档 `docs/cookie-vault.md`。
- **抖音解析通道替换 f2 → Playwright 浏览器**（详见 `docs/抖音解析通道替换_playwright.md`）：f2 `0.0.1.7`（PyPI 最后版 2024-12-31）拼的 `a_bogus`+随机 `webid` 被 Argus 拦 → `403 Blocked by ArgusSecurityPlugin Uifid Not Found`。排查定主因是 **`_random_webid()` 造的 19 位数字与 Cookie 的 UIFID 不匹配**（换 zhicui 路径也共用同一签名救不了）。改为 `app/services/browser_resolver.py`：系统 Chrome 开作品页、**只读已渲染 DOM**，登录坏也不连带失败，不读 Cookie，不请求接口。环境变量 `TOKBRAIN_LINK_RESOLVER`（默认 `browser`，`f2` 仅排障）。
- **控制台 `[4] 停止` 停不掉开发模式实例（新增 `[11]`）**：`dev.cmd` 用 `start cmd /k` 直接拉 uvicorn/next dev，**从不写 `runtime.json`**，而 `[4]` 只认 `runtime.json` 四身份 → 走到"没有找到运行状态"直接 `exit 0`；顶部状态却按 netstat 显示"运行中"。新增 `dev-stop` 动作 + 菜单 `[11]`，独立生命周期，不读不写 `runtime.json`，三条件判定（端口来自 netstat + 进程名/命令行证本项目 + runtime.json 未记录该 PID 否则指回 `[4]`），按进程树（先上溯根、再后进先出）终止，停完轮询 10 秒复查。

---

## 3. 分支分歧：new/TokBrain 独有而未合并到 main 的改动

`main` 的 CHANGELOG 主线**未包含**以下两条（来自 `new/TokBrain` `codex` 分支，最新提交 2026-10-29 `feat: add custom themes and restore Douyin link parsing`）：

| 条目 | new/TokBrain 的取向 | main 的取向 | 分歧性质 |
|---|---|---|---|
| **抖音链接解析** | 「抖音链接解析兼容性」——为 F2 单作品详情请求补齐抖音网关要求的 `x-tt-argus` 和 `uifid` 请求头（从 Cookie 读 `UIFID`，缺时回退 `UIFID_TEMP`），**保留 f2 签名方式** | 直接**弃用 f2 解析**，换成 Playwright 浏览器只读 DOM（见 2.6） | **替代方案**：new 修 f2 头，main 认为 webid 随机才是主因、修头治标不治本，故换通道 |
| **主题系统** | 「动态与自定义主题」——新增樱花幽谷/海洋晨曦/极光雪原 3 套循环动态主题 + 13 套静态，支持上传静态图/动画/MP4/WebM 自定义（可改 12 项语义色），schema v10 / API contract 8 | 主题**精简为 4 套静态**（`aura`/`sunset-cloudsea`/`eastern-mist`/`ocean-dawn`） | **方向未统一**：new 走"丰富可定制"，main 走"克制稳定" |

> 这两条目前只在 `new/TokBrain` 的 `codex` 分支里，**未合并进 `TokBrain-main`**。若后续要把动态主题或 f2 头修复带回 main，需注意 schema/API 版本号（new 已到 v10/contract 8，main 仍在 v11/contract 7 且未升）与解析通道取向上的冲突。

---

## 4. 弃用的功能与原因（汇总）

| 弃用项 | 原状 | 弃用原因 | 替代 |
|---|---|---|---|
| 网页端问答/对话入口 | 1.0.0 文案写"问答支持快速/深度模式、流式回答" | **从未实现**：后端无 `/api/chat`、无 SSE，前端 `chat()`/`chat_stream()` 无调用方（2026-10-30 更正） | 仅经 MCP 提供**检索**（kb_search 等） |
| F2 CLI→manifest→外部令牌→推送脚本 流程 | 早期推荐批量导入方式 | 链路长、依赖外部令牌、易出错 | 网页"外部视频数据包"（文件夹/ZIP）+ 同机 `push_to_tokbrain.py` 保留给高级集成 |
| 链接入口自动确认 / 自动 AI 入库 | 早期可选自动 | 自动烧解析额度、自动撞风控，违背"手机随手丢链接"的低风险预期 | 两段式手工确认（预检→待确认→主动入库），`FS_AUTO_CONFIRM=1` 可回旧行为 |
| f2 单作品解析（`TOKBRAIN_LINK_RESOLVER=f2` 默认通道） | 默认链接解析器 | f2 `0.0.1.7` 签名被 Argus 拦 403，webid 随机是主因，PyPI 无更新可升 | Playwright 浏览器只读 DOM（默认 `browser`） |
| 把 8001/8010 暴露到局域网 | — | 安全面过大，易被同网段直连 | 绑定 `127.0.0.1`；控制面/只读 MCP 非回环必须令牌 |
| `/api/chat` 检索问答、克隆训练工具 | 早期设想 | 产品侧未落地、检索只给证据 | 明确不做（见控制面文档"明确不做"清单） |
| new 分支的 f2 `x-tt-argus`+`uifid` 头修复 | 试图修 f2 403 | main 判定 webid 随机才是根因，修头治标不治本 | main 选 Playwright 通道（见 3） |

---

## 5. 失败的路径与解决方法（工程叙事）

> 以下按"怀疑点 → 排查结论 → 最终解法"记录，便于后人不再重走弯路。

### 5.1 抖音链接解析：f2 403（ArgusSecurityPlugin）
- **现象**：`GET /aweme/v1/web/aweme/detail/` → `403 Blocked by ArgusSecurityPlugin Uifid Not Found`。
- **逐个排查**：
  - 怀疑 Cookie 失效 → ❌ 新 Cookie 63 字段齐全（sessionid/sid_guard/ttwid/UIFID 都在）。
  - 怀疑 f2 版本旧 → ⚠️ 是背景但非主因（PyPI 无更新）。
  - 怀疑签名算法过期 → ❌ 同一套 ABogus 在真实浏览器里能用。
  - **主因**：`_random_webid()` 造的 19 位数字与 Cookie 的 UIFID 不匹配。
  - 关键点：zhicui 采集路径 `_abogus_class()` 也 `from f2.utils.abogus import ABogus`，两条路径共用同一签名+随机 webid，**换路径救不了**。
- **解法**：弃 f2 解析，换 `browser_resolver.py`（系统 Chrome 开页只读 DOM）。详见 `docs/抖音解析通道替换_playwright.md`。

### 5.2 playwright-cli 在后端环境"未安装"的隐蔽坑
- **现象**：`shutil.which("playwright-cli")` 在交互 shell 里 True，但后端由 `start.ps1` 拉起时 PATH 不含 `%APPDATA%\npm` 和 WorkBuddy node 目录 → 判 False。
- **坑上加坑**：当时错误码复用 `access_forbidden`，和"抖音拒绝解析"长得一模一样，排查会一路往风控跑偏。
- **解法**：新增 `_node_install_roots()`（纳入 `%APPDATA%\npm`、`%LOCALAPPDATA%\npm`、`~/.workbuddy/binaries/node/versions/*`，用 glob 容版本号）；node 查找优先 `script.parent^4/node.exe`，找不到退 `which`；**错误码独立成 `browser_unavailable`**，与抖音 `access_forbidden` 彻底分开。以后见 `browser_unavailable` 先查工具与开关，别怀疑风控。

### 5.3 抖音批量下载的五个失败形态（bridge_douyin.py）
| 失败 | 真因 | 解法 |
|---|---|---|
| **没声音** | 抖音 web 端用 MSE 把视频/音频拆两路 fMP4，只抓视频分片 | 改抓 `aweme/detail` 的 `play_addr`/`bit_rate[*].play_addr` **muxed 直链**（自带音轨） |
| **串片**（下到上一条的片） | 复用标签页时上一条预取污染下一条 | 每条独立 page + 按落地页 `/video/<id>` 校验 `aweme_id` + 非目标直链时重载一轮 |
| **长视频截成 59 秒** | 等待循环被 MSE 分片提前打断 | 改只等目标 muxed 直链，拿到后再多等 3 秒收齐各档画质 |
| **画质不是最高** | 取了 `play_addr` 默认档 | 按 `data_size` 降序取最大（#2 的 `bit_rate[0]` 是 4K/724MB，默认档仅 1080p/390MB） |
| **直播误判跳过 VOD** | `webcast.` 全站侧载水印/钻石 SDK，误当直播 | 只认投递主机 `douyinliving.com`/`pull-flv`/`pull-hls`/`pull-lls` |

### 5.4 Cookie 散落三处 → 统一库
- **失败**：`SecretRecord(f2_cookie)`、`SecretRecord(douyin_cookie)`、下载脚本 `douyin_cookies.txt` 三份，换号必漏改且失败现象各异；旧 Secret 解不开时曾被**静默降级成匿名请求**，用户只看到"登录态无效"这种误导报错。
- **解法**：`data/cookies/<platform>.txt` 唯一事实来源；旧 Secret 解不开**显式抛 `f2_cookie_unreadable`**；启动时一次性迁移旧 Secret 到文件。

### 5.5 控制台 `[4] 停止` 停不掉开发模式
- **失败**：`dev.cmd` 用 `start cmd /k` 拉 uvicorn/next dev，**不写 `runtime.json`**；`[4]` 只认 `runtime.json` 四身份 → "没有找到运行状态"`exit 0`；顶部状态却按 netstat 显示"运行中"，看着像"点了没反应"。
- **解法**：新增 `[11] dev-stop`，独立生命周期不碰 `runtime.json`；三条件判定（端口事实 + 进程名/命令行证本项目 + runtime.json 未记录该 PID 否则指回 `[4]`）；按进程树终止。
- **两个实现坑**：① 上溯父进程只按文本会把 `cmd /k "...uvicorn..."` 误判，必须**进程名+命令行一起判**；② 改造进程表缓存时把 `ParentProcessId` 弄丢，上溯一步就断，补回 `parent` 字段。

### 5.6 飞书桥在本机代理下必败
- **失败**：`requests` 默认 `trust_env`，系统配 `HTTP_PROXY`（Clash/VPN/CI）时连 `127.0.0.1:8001` 也被推给代理 → 后端收 path 成 `http://127.0.0.1:8001/api/...` 直接 404。
- **解法**：控制面拉桥注入 `NO_PROXY=127.0.0.1,localhost,::1`，`启动飞书同步.cmd` 同步加上。

### 5.7 `pending_items()` SQL 占位符写死
- **失败**：`NOT IN (?,?,?)` 写死三占位符，终态集合从 3 个加到 4 个后直接抛 `ProgrammingError`。
- **解法**：改成按集合长度动态生成占位符。

### 5.8 只读 MCP 暴露风险
- **失败**：旧默认 `0.0.0.0` 且允许空令牌，可被局域网直连。
- **解法**：默认 `127.0.0.1:8010`；绑定非回环必须 `TOKBRAIN_MCP_TOKEN`，否则进程拒绝启动。

---

## 6. 当前状态、未决项与下一步

**已完成（未正式发版，仍在 `Unreleased`）**
- 控制面接入、Hermes/NAS 只读副本、安全修复、飞书同步、多模型档（10-29~30）
- 控制面接入/平台采集修复/启动入口统一/`[11]`（11-01）
- 统一 Cookie 库、抖音解析换 Playwright、控制台 dev-stop（11-02）
- 抖音批量下载管线打通（飞书 121 条，前 5 条验收通过）

**未决 / 待办**
1. **统一 Cookie 库需重启后端**才生效（`/api/cookies` 路由未加载）。
2. **`[11]` 只验证到 DryRun 层，未真杀**（用户 dev 实例仍在跑）；要停直接点 `[11]`。
3. **剩余 116 条抖音链接**待按小批（每批 20、间隔 3 秒、遇风控即停）跑完。
4. 飞书表 **6 行缺标题/作者**的记录待用户补全（含曾误判为"错误视频"的那行 `LID=lid_462165e8d2cf065f`）。
5. **分支未合并**：`new/TokBrain` 的 `codex` 分支含 f2 头修复 + 动态主题（schema v10/contract 8），与 main 的 Playwright 通道 + 4 套静态主题方向分歧，需决定合并策略。
6. 前端 `next build` 在本环境被沙箱批量删除保护拦（删 `.next/trace`），属环境限制非代码问题；`tsc --noEmit` + eslint 均干净。

**下一步建议**
- 跑剩余 116 条抖音（Route A 谨慎自动化，不调 aweme API、不让 yt-dlp 解析页面）。
- 决定 new/TokBrain `codex` 分支的 f2 头修复 / 动态主题是否回并 main，先解决 schema/API 版本号冲突。
- 正式发版时把 `Unreleased` 各段归入版本号，并统一应用版本号（长期停在 v1.0.0）。
