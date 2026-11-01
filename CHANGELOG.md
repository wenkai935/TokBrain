# 更新日志 / Changelog

本文件记录 TokBrain 面向用户的重要版本变化。版本日期采用上海时区。

## [Unreleased] — 2026-11-02 · 下载板块入库 + 安装自动补依赖 + 飞书桥授权确认

clone 用户此前拿不到下载脚本（它躺在 `D:/Temp`，不在版本库），且 `install` 不装 Playwright / yt-dlp / Chromium，导致下载板块与浏览器解析通道在他人机器上根本跑不起来。

- 下载脚本 `bridge_douyin.py` 移入 `tools/douyin_bridge/` 并纳入版本管理；硬编码路径全部相对化：`_PROJECT_ROOT` 由 `__file__` 推导，Chrome / ffmpeg 改走 `CHROME_PATH` / `FFMPEG_BIN` → PATH → 常见目录探测，Cookie 默认 `data/cookies`（`TOKBRAIN_COOKIE_DIR` 可覆盖）。
- 新增 `requirements-download.txt`（playwright、yt-dlp）；`scripts/setup.ps1` 在主依赖装完后安装它并执行 `python -m playwright install chromium`，失败仅警告、不中断核心安装。
- 前端 `package.json` 加 `optionalDependencies: @playwright/cli`，抖音浏览器解析通道随 `npm ci` 到位。
- 飞书桥 `[9]` 启动前加授权确认：列出数据访问范围并要求 `Y/N` 同意；不同意则取消。命令行 `bridge-start` 保持非交互直接启动。
- 启动器状态面板把下载 / 解析 / 飞书桥依赖纳入检测（Playwright、playwright-cli、Chrome、ffprobe、yt-dlp、lark-cli），缺失标黄。详见 [完整变更日志](docs/CHANGELOG-FULL.md)。

## [Unreleased] — 2026-11-02 · 日额度跨日重置：延后作品自动续跑

额度爆掉被延后的作品，第二天额度已经重置，卡片却还挂着昨天的「已达到今日音视频
分钟上限」——`budget_deferred` 这个戳打上去之后就再没人管。文档里写的「延后非失败，
明天额度重置自动重试」其实一直没有实现，只能手动点「开始入库」。

- 处理协调器每次循环对一遍上海时区日期：一跨天（以及每次启动后的第一圈），就把
  仍带 `budget_deferred` 戳、且不在活跃任务里的作品重新排队；排队即清掉旧错误戳。
  若今天额度仍然爆掉，新的尝试会当场重新打戳，消息保持真实。
- `enqueue_ingest_job` 统一清理被排队作品的旧错误戳：单个「开始入库」早就清，
  批量入口不清，导致排队期间卡片一直显示上一次尝试的错误。
- 额度会计本身（按天一行计数器、跨零点按上海日期结算）没有问题，坏的只是
  「跨天之后把延后作品捞回来」这一段。

## [Unreleased] — 2026-11-02 · 控制台补上「停止开发模式实例」

`[4] 停止服务` 点下去没反应，不是脚本坏了，是两套进程视图没接上：`stop.ps1` 只认
`data/runtime.json` 里记录的四个身份（pid + 创建时间 + 可执行路径 + 命令行哈希），
而 `dev.cmd` 用 `start cmd /k` 直接拉 `uvicorn` 和 `next dev`，从不写这份状态。
于是 `[4]` 走到「没有找到本项目的运行状态」就 `exit 0`，一个进程都没停；菜单顶部
却按 `netstat` 显示「运行中」——一个按端口判活、一个按身份杀进程。

- `scripts/menu.ps1` 新增 `dev-stop` 动作与菜单 `[11]`：独立入口，只停 `dev.cmd`
  拉起的前后端，**不读也不写 `runtime.json`**。判定三个条件——端口来自 `netstat`
  （事实）、进程名 + 命令行证明是本项目（`python` + `uvicorn app.main:app`；`node` +
  含本项目 `frontend` 目录的 `next`）、`runtime.json` 未记录该 PID（记录了就拒绝并指回 `[4]`）。
- 终止按进程树走：先向上找同类进程里最靠上的根（`next dev` 会再拉一个 `next-server`
  子进程，只杀子进程父进程还会重起），再后进先出、先叶子后根。进程名必须一起判，
  否则 `cmd /k "…uvicorn…"` 外壳的命令行也含关键字，会一路溯到 cmd 窗口上去。
- 停完轮询端口 10 秒复查；没释放就明说哪个 PID 还占着，而不是报一句「已停止」了事。
- 状态面板把 8001 / 3001 分成「受管实例」/「非受管实例」/「其他程序」三种，非受管显示黄色；
  `[4]` 执行前先记下非受管监听者，停完指路 `[11]`。
- `dev-stop -DryRun` 只打印将停止的进程与整棵树规模，不动手。

## [Unreleased] — 2026-11-01 · 启动入口统一 + 飞书「预检后等确认」

此前飞书链路有两个断层：**桥不在启动链里**（`启动.cmd` / `start.ps1` 完全不碰它，
只有单独双击 `启动飞书同步.cmd` 才会跑），**前端轮询只查不处理**
（`GET /api/sync/overview` 真查表但只保留行数，链接全丢弃）。所以「手机丢链接 → 本机自动处理」
从来没成立过。

**启动入口统一**

- `TokBrain.cmd start` / `start-bg` / `restart` 在服务就绪后自动拉起飞书桥
  （`start.ps1::Start-FeishuBridge`，复用 `tokbrain_control.bridge`，不写第二份逻辑）；
  `stop` 先停桥再停服务。设 `TOKBRAIN_AUTOSTART_BRIDGE=0` 可关闭自动拉起。
- `scripts/menu.ps1`：新增 `bridge` / `bridge-start` / `bridge-stop` 动作、状态面板加
  「飞书同步桥」一行、菜单加 `[9]`（运行中就停，没运行就起），打开网页顺延到 `[10]`。
- 桥由 `start.ps1` 拉起时属于同一个 Job Object，`停止.cmd` 会一并回收，不留孤儿进程。

**飞书链路改成两段式（预检 → 等人放行）**

- 新增 `FS_AUTO_CONFIRM`，**默认 0**：桥发现新行后只做预检（f2 补齐标题/作者/封面），
  然后停在 `awaiting_confirm`，回写飞书「处理状态=待确认」。手机随手丢链接不该自动
  烧解析额度、自动撞风控。设 1 回到旧的一条龙行为。
- `dispatch` 表新增 `batch_id` 列（老库自动 backfill），`awaiting_confirm` 加入终态集合，
  慢轨不会每轮重复导入同一行。
- 修一个隐蔽 bug：`pending_items()` 的 SQL 把 `NOT IN (?,?,?)` 写死三个占位符，
  终态集合从 3 个加到 4 个后直接抛 `ProgrammingError`。改成按集合长度生成。
- 新增 `tb_sync_pending`（只读，带标题/作者）与 `tb_sync_confirm`（真的 confirm + ingest）。
  控制面工具总数 33 → **35**（15 只读 / 20 写）。Hermes 白名单同步加上这两个。

**平台采集**

- `enqueued` 字段不再一律返回 `true`：B 站/小红书没有链接解析器，条目不进预检队列，
  这个字段如实返回 `false` 并附 `queued_count`。

## [Unreleased] — 2026-11-01 · 修复平台采集条目永不解析

平台采集（`POST /api/platforms/{pf}/pull` 与 `dom-collect` 勾选同步）建出的批次，
条目被写成 `status="pending"`，而链接预检 worker 的取件条件是 `status == "queued"`。
两者不匹配的后果是：批次和任务一建好就被标成 `succeeded`，条目却永远停在
"解析中"；`confirm` 只接受 `ready` / `needs_local_file`，于是这批视频既不会解析、
也确认不了——整条网页采集链路是断的。

- `create_platform_import_batch` 按平台分流条目状态：有解析器的平台（当前只有抖音，
  见 `f2_links.PAGE_HOST_SUFFIXES`）写 `queued` 进预检；没有解析器的平台
  （B 站 / 小红书）直接写 `needs_local_file`，走"元数据入库 + 视频补件"。
- 批次状态跟着条目走：有排队条目时用新增的 `_new_pending_batch`（`queued` +
  `job_type=link_preview`），纯补件清单仍用 `_new_terminal_batch`（`succeeded`）。
  任务列表里这类批次现在显示「视频链接预检」而不是「本地任务」。
- `enqueue_platform_batch` 只把 `queued` 条目放进协调器队列；此前会把
  `needs_local_file` 一并塞进去，worker 取到后直接 return，白跑一趟。
- `confirm_import_items` 放宽 `platform_pull`：允许无视频资产建作品（媒体缺失留到
  ingest 阶段由 `LOCAL_SUPPLEMENT_CODES` 标补件）。`local_upload` / `external_batch`
  仍然要求必须有上传的视频，行为不变。
- 新增 `tests/test_platform_import_flow.py`（5 项）钉住以上约束。全量 349 passed。

## [Unreleased] — 2026-11-01 · 控制面接入

外部 agent（NAS 上的 Hermes）此前只能读一份手动 deploy 的只读快照，无法导入、
无法触发解析、无法知道"现在处理到哪了"。本次补一条**实时控制通道**。

- 新增 `tokbrain_control/`（纯标准库，约 2400 行）：`errors / http / upload / client /
  service / commands / search / audit / cli`。不 import `app.*`（除 `search` 惰性加载），
  因此 `.venv`、`.venv-mcp`、系统 Python 三个解释器都能直接跑。
- 新增 `mcp_server/mcp_control.py`：8011 控制 MCP，**33 个 `tb_*` 工具**（14 只读 /
  16 写），全部转发到既有 8001 REST，不复制业务逻辑。传输层沿用 streamable-http，
  新增显式启用开关 `TOKBRAIN_CONTROL_ENABLE=1`、令牌最短 32 位、端口预检。
- 新增 `tbctl.cmd` / `python -m tokbrain_control`：`status / doctor / service /
  import / batch / ingest / job / work / collection / search / platform / fs / audit`，
  `--json` 统一信封，退出码 0/1/2/3。
- `mcp_server/_http_guard.py`：把 `BearerGuard` 抽成纯 stdlib 模块，8010 与 8011 共用；
  `tokbrain_mcp.py` 改为别名导入，`isinstance` 断言不变。
- 服务生命周期：只经 `scripts/stop.ps1`（保留 `runtime.json` 身份校验），启停用
  `启动.cmd` 并注入 `TOKBRAIN_SKIP_LOCAL_UI_OPEN=1` 避免远端调用弹浏览器；
  明确不用 `停止.cmd` / `重启.cmd`（stdin 重定向下会挂死）。
- 新增 `mcp_server/control.env.example`、`hermes-control-config.example.yaml`、
  `放行控制端口.cmd`（自提权，限定 /24 与 private 配置）、`挂载NAS媒体盘.cmd`、
  `scripts/probe_control_mcp.py`。
- **新增媒体路径约定**（`tokbrain_control/paths.py` + `tb_fs_roots` 工具）：外部 agent 在
  NAS 上、控制面在 Windows 上，同一个文件两边路径不同。用
  `TOKBRAIN_CONTROL_MEDIA_ROOTS=<Windows 根目录>|<NAS 前缀>` 声明对应关系后，传 NAS 路径、
  Windows 路径或裸文件名都能正确解析；`tb_fs_roots` 返回约定表与每个目录的在线状态，
  NAS 映射盘掉线时能直接看出来。含 `..` 的路径一律拒绝。
- **新增飞书同步桥控制**（`tokbrain_control/bridge.py` + 5 个 `tb_sync_*` 工具）：
  `tb_sync_status` 看进程/队列/失败原因/抖音连接，`tb_sync_start` 拉起常驻轮询（可切
  `download=true` 真下载），`tb_sync_stop` 按命令行精确匹配停止，`tb_sync_replay` 分批重投
  failed（支持 `newest_first`），`tb_sync_wait` 等队列跑完并返回 delta。
  定位原则：飞书表的读取能力在 bridge 里，**不让 agent 自己读表**——会与 bridge 抢同一批行、
  破坏「处理状态」回写。
- 修复 `bridge_feishu_obsidian.py` 在本机代理环境下必失败的问题：`requests` 默认
  `trust_env`，系统里配了 `HTTP_PROXY`（Clash/VPN/CI 都会配）时连 `127.0.0.1:8001` 也被推给
  代理，后端收到 path 是 `http://127.0.0.1:8001/api/...` 直接 404。控制面拉起桥时注入
  `NO_PROXY=127.0.0.1,localhost,::1`，`启动飞书同步.cmd` 同步加上。
- 导入节流：`tb_import_links` 单次上限 10 条，`tb_sync_replay` 单次上限 20 条（默认 5）。
  依据是实测数据——队列里积压的 120 条 failed 全是 429，一次性放开就会重演。
- 新增《[TokBrain 控制面运行手册](docs/tokbrain-control-plane.md)》。
- **并入项目统一入口**：`scripts/menu.ps1` 增加 `control` 动作与菜单 `[8]`，
  `status` 多显示「控制面配置 control.env」与「控制端口 8011」两行；README、操作说明书
  （新增第 11 章）、SECURITY（MCP 攻击面由两套改三套，标明 8011 可写）同步更新。
  控制面不受 `TokBrain.cmd stop` 管辖，与主服务生命周期相互独立。
- 新增两张架构图（`docs/diagrams/`，Archify 生成，带分场景视图与导出）：
  `tokbrain-flow.html` 画内在功能链路（含预检闸门、单 worker、运维节点），
  `tokbrain-boundary.html` 画进程与信任边界（三条 MCP 通道、唯一写路径、回环范围）。
- 明确不做：`/api/chat`（检索只给证据）、克隆训练工具（产品侧未落地）、
  把 8001/8010 暴露到局域网。

## [Unreleased] — 2026-10-29 ~ 2026-10-30

以下变更已在代码和运行环境中生效。**应用版本号未变，仍为 v1.0.0**，但数据库已迁移到
schema v11；下一次正式发版时统一归入版本号。本次不推翻原有流程，主要补了三件事：把
知识库**接到外部使用场景**（本机 MCP 与 Hermes/NAS 只读副本）、把**安全与一致性缺口**
补上（一次独立代码审查发现的 7 项问题）、把**飞书→Obsidian 同步链路**正式纳入主流程
并配好状态入口。

### 知识检索接入外部

- 新增本机只读 MCP `mcp_server/tokbrain_mcp.py`，暴露 `kb_search` / `kb_get_work` /
  `kb_get_transcript` / `kb_list_collections` / `kb_stats` 五个工具，默认监听
  `127.0.0.1:8010`。**绑定非回环地址时必须配置 `TOKBRAIN_MCP_TOKEN`，否则进程拒绝启动**
  （此前默认 `0.0.0.0` 且允许空令牌，属于可被局域网直连的风险面）。
- 新增 Hermes 专用 stdio MCP `mcp_server/tokbrain_hermes_stdio.py`，工具名 `tokbrain_*`。
  它不监听任何网络端口，由 Hermes 按 stdio 拉起，直连只读快照。
- 两套 MCP 都只提供读取工具，不包含新增、修改、删除能力。

### Hermes / NAS 只读知识副本

- 新增 `scripts/export_tokbrain_snapshot.py`（SQLite backup API 出一致快照 + integrity_check
  + sha256 清单）、`scripts/deploy_tokbrain_snapshot.ps1`（上传临时文件 → 远端校验 → 原子替换，
  旧库保留为 `.bak`）、`scripts/install_tokbrain_hermes_nas.py` 与两个探活脚本。
- NAS 侧以 `mode=ro&immutable=1` 打开副本，Hermes 无法写库。首次部署验收数据：
  **14 个已入库作品 / 305 个知识片段 / 13,217,792 字节**，SHA-256 与本地库一致。
- 新增《[Hermes NAS 运行手册](docs/tokbrain-hermes-nas.md)》，含调用约束、快照更新、验证与回滚。
- 明确禁止 Windows 或其他主机通过 SMB/NFS 直接打开 NAS 上的 SQLite。

### 安全与一致性修复（来自一次独立代码审查）

- **平台 Cookie 出站收紧**：小红书 Cookie 只发往 HTTPS 官方域名，请求前做公网 DNS 校验，
  并逐跳校验重定向目标；不再可能把凭据带到第三方域名或明文 HTTP。
- **MCP 鉴权**：见上，默认回环 + 非回环强制令牌，并补 `启动MCP服务.cmd` 与 `mcp.env.example`。
- **活动模型配置贯通**：处理后端、总结、检索统一读取当前激活的模型配置档，不再各自读全局项；
  OpenAI 兼容配置遇到百炼专属媒体能力时**明确报错拒绝**，不再静默回退。
- **清密钥覆盖全部配置档**：一键清理会遍历 `model_profile:*:api_key`，不再漏删。
- **预算跨日修正**：预算预留按上海时区日期绑定，避免午夜前后预留落到错误的一天。
- **幂等重放语义**：重放历史请求返回与首次一致的 400/201 状态码，并带 `replayed=true`，
  不再一律返回 200 掩盖首次失败。
- 更新了一条过时的"sync 模块必须不存在"测试。

### 飞书 ↔ Obsidian 同步进主流程

- 新增 `app/routers_v2/sync.py`：`GET /api/sync/overview` 同步总览、`POST/GET /api/sync/feishu/relogin`
  扫码续期；新增 `app/services/sync_status.py`（真查一次飞书表并落盘）与 `feishu_auth.py`
  （驱动 `lark-cli` 设备码流程，产出登录二维码）。
- 前端侧边栏新增飞书连接状态：已连接 / 授权过期 / 检测中，附最近查询时间与行数，过期时出现扫码续期入口。
- 桥接脚本 `bridge_feishu_obsidian.py` 增至 1822 行，仍以 `--watch` 常驻、`--retry <record_id>`
  人工重投；日常由 `启动飞书同步.cmd` 启动（独立进程，不随后端一起起）。
- 入站接口 `POST /api/integrations/v1/inbox/items` 只负责入队（单批 ≤100 条原子校验），
  真正处理必须由常驻桥接完成——文档已写明这个前提，避免误以为后端单独就能跑通。

### 多模型配置档

- 新增 `/api/model-profiles`（增删改查 + 激活），每档独立密钥；设置页可切换，
  处理与总结按激活档执行。这替代了此前"文本、音频各填一套 Key"的写法。

### 前端

- 页签固定为 **导入 / 解析 / 知识库 / 设置** 四个；预检结果与"确认加入待入库"从导入页
  移到「解析」页，模型配置也放在「解析」页。
- 主题精简为 **4 套**：`aura`（Aura 暗壳，默认）、`sunset-cloudsea`（晚霞云海）、
  `eastern-mist`（东方雾山）、`ocean-dawn`（海洋晨光）。
- 本地视频改为**逐个顺序上传**，每个文件独立显示校验/上传/重复/失败状态。
- 补充预算与网络异常的文案、批次持久化、分页返回定位、长任务轮询。

### 文档

- 重写 `README.md`：写清版本、schema、端口，删除不存在的网页问答承诺，新增 Hermes/NAS 章节。
- 更正 `操作说明书.md`：本地视频为逐个顺序上传（原文"同时最多 2 个"错误）、外部数据包上限
  （1000 候选 / 文件夹 100 视频 / ZIP 仅 1 个 / 总 20 GB）、重启语义、链接预检固定 3 worker、
  4–8 秒冷却、日 150 次额度、403/429 熔断。
- 新增本文 1.1.0 段；校正 `docs/表结构说明.md`、`docs/TokBrain功能层级结构图.md`、
  `docs/本地资源储存结构.md`、`docs/pipeline-v2-plan.md`、`docs/test-cases-pipeline-v2.md`
  等文档中与代码不符的描述（详见各文头部校订说明）。

### 升级提醒

- 应用版本号仍为 **v1.0.0**，数据库已迁移到 **schema v11**，API contract 仍为 **7**。
  升级会在 `data/backups/` 自动创建迁移前备份。
- MCP 默认端口从"监听所有地址"改为 `127.0.0.1:8010`。若你此前在别的机器上直连过 8010，
  需要显式设置 `TOKBRAIN_MCP_HOST` 并同时配置 `TOKBRAIN_MCP_TOKEN`。
- 需要 Hermes 侧检索时，先跑一次快照部署脚本，再重启 `hermes-household`。

## [1.0.0] - 2026-08-11

TokBrain 1.0 把导入、处理、整理和检索短视频知识的整个流程重新梳理了一遍。现在从
添加作品到真正入库，每一步都更清楚，也更容易在出错后继续。

### 导入方式更多，也更清楚

- 除了粘贴公开链接，现在还可以直接选择本地视频、整个下载文件夹或 ZIP 数据包。
- 所有预检结果都会先停在确认页面。只有明确点击入库后才开始处理，不会因为选中文件或
  预检完成就自动产生 AI 用量。
- 重复作品会自动识别；页面刷新或切换栏目后，尚未确认的结果仍会保留。
- “今日处理”和“今日入库剩余”现在只按成功入库的作品计算。预检、待入库和失败作品
  都不会被误算进去。

### 处理结果更可信

- 视频会综合字幕、语音、画面文字和关键画面整理内容，不再只依赖标题或简单元数据。
- 当原始材料不足时，作品会明确显示需要补充什么，并允许上传有权使用的本地视频或图片
  后继续处理。
- 没有足够依据的旧总结不会继续作为可靠知识参与检索，减少看似完整但没有来源的回答。
- 网络中断、服务超时、模型未配置和文件异常会给出更容易理解的提示，也可以安全重试。

### 知识库和问答更好用

> **2026-10-30 更正**：以下两条是 1.0.0 发布说明中的原始表述，与实现不符，保留原文并加注。
> 网页端**从未提供对话/问答入口**：后端无 `/api/chat`、无 SSE 流式接口，前端的
> `chat()` / `chat_stream()` 也没有任何调用方。实际只提供**检索**能力，且只经 MCP 对外
> 暴露（见 [Unreleased] 段）。保留 Markdown 与公式渲染的是**总结详情页**，不是问答页。

- 知识库按待处理、已入库、待补件、异常和归档分类，支持收藏夹整理、批量操作和详情返回。
- ~~问答支持快速和深度模式，保留 Markdown、公式、来源与分段显示。~~（未实现：网页端无问答入口）
- Obsidian 导出会整理笔记和本地图片，重复导出时尽量保留用户自己写的内容。
- 页面拆分后加载更轻，长任务轮询、分页和流式回答也更顺畅。（"流式回答"未实现，其余已落地）

### 日常使用更稳定

- 安装会重新准备干净环境并检查依赖，启动、停止和重启现在能识别本项目自己的进程，
  不会再因为旧记录或日志占用而误报成功、误停其他程序。
- 处理中断或电脑异常退出后，会尽量恢复任务、文件和用量记录，避免出现数据库显示成功
  但文件丢失，或失败任务被算作成功入库的情况。
- 设置页可以查看本地运行状态；链接预检、数据包导入和入库处理异常会分别显示。
- 本地密钥继续由 Windows 保护，服务默认只允许本机访问；日志中的敏感内容会被隐藏。

### 升级提醒

- 本版本为 **v1.0.0**，发布时使用数据库 **schema v9** 和 **API contract 7**。
  （2026-10-30 注：数据库此后已迁移至 **schema v11**，见 [Unreleased] 段与
  [操作说明书](操作说明书.md) 第 9 节。）
- 升级前建议备份整个 `data` 文件夹，然后双击“安装”，完成后再双击“启动”。
- 第一次启动会先备份旧数据库，再自动整理现有数据。数据较多时请耐心等待，不要强制关机。
- 如果更换过 Windows 用户，原来保存的模型或账单密钥可能无法读取，请在设置页重新输入。
- 本版本通过 GitHub 提供源码压缩包，不附带单独的安装程序。

## v0.5.0 开发阶段记录（已并入 v1.0.0）

### 证据可信度与 schema v9 迁移

- schema v9 新增证据与补件状态。已入库的受限视频若没有本地源文件，也没有字幕、转写、
  OCR 或视觉描述等原始证据，会被标记为“证据不足/需要补件”；迁移同时清除正文、总结、
  知识块和关键帧，避免标题、元数据或生成文本被误当作有来源知识继续检索。
- 升级前自动创建 `data/backups/douyin_rag-pre-v9-*.db`。恢复时必须先停止服务，并让
  数据库与 `source-assets/`、`media/`、`keyframes/` 使用同一备份时间点。
- API contract 7 对齐当前证据状态、本地补件与外部数据包导入契约；前端包版本同步为
  v0.5.0。

### 安装、启动与发布工程

- `setup.ps1` 不再复用可能混有遗留包的 `.venv`：它在临时目录创建干净的 Python 3.12
  环境，安装固定依赖，执行 `pip check` 并核验 F2 只能从 `.vendor/` 加载；验证成功后再
  切换环境。运行依赖、可选账单依赖和开发工具已拆分。
- 安装阶段生成 Next.js 生产构建，日常启动改用 `next start`；后端读取 `.env` 的
  `APP_HOST` / `APP_PORT`，修改端口后需重新安装以同步前端 API 地址。
- 启动状态改为原子写入，并记录实例 ID、工作区、端口、进程创建时间、可执行文件，以及
  Windows 允许读取时的命令行哈希。停止脚本只结束身份匹配的进程，不再根据陈旧 PID 或
  固定端口盲目终止程序。
- CI 现在走正式 Windows 安装脚本，要求 FFmpeg/ffprobe、`pip check`、F2 来源验证、
  Python 语法编译与覆盖率下限、前端测试/lint、严格 TypeScript typecheck、生产构建、
  依赖审计和干净提交发布检查；本地 `prepublish_check.ps1 -Full` 使用同一前端检查门槛。
- 系统健康检查新增链接预检、数据包导入和本地处理协调器状态，显示存活/期望 worker 与
  脱敏后的最近错误；启动就绪检查也要求三个协调器全部健康。
- 处理结果采用数据库事务与媒体文件代际共同提交。数据库提交失败或取消时恢复旧文件代际，
  预算预留按是否已经产生真实模型用量释放或结算；异常退出遗留预留在协调器启动时回收。
- `scripts/audit_library.py` 现可从仓库根目录直接运行，以 SQLite `mode=ro` 检查 schema v9
  的检索证据、补件状态、孤儿知识块和错误分布，不会因数据库不存在而创建空文件。

### 链接入口回退与外部视频数据包

- 链接入口恢复为“预检 → 手工确认加入待入库 → 用户主动入库”，移除自动确认和自动
  AI 入库选项；旧批次中遗留的自动化标记也不会在重启后恢复执行。
- 导入页新增“外部视频数据包”：可选择其他工具生成的整个下载文件夹或 ZIP，一次最多
  100 个视频；后端自动识别清单 JSON/CSV、F2 `douyin_videos.db`、同名侧车文件和文件名
  中的抖音 ID。无法可靠匹配时按文件名和 SHA-256 作为本地视频继续，而不是导入失败。
- 文件夹/ZIP 上传后只做校验和预检，结果继续使用原有手工确认与主动入库流程；不访问 F2、
  不消耗链接额度，也不会在检测完成后自动产生模型费用。
- ZIP 拒绝加密、嵌套、符号链接、绝对路径、`..`、路径碰撞和异常压缩比；不支持远程
  媒体 URL、任意本机路径、纯元数据和图文作品。
- 可选 F2 Cookie 只需在设置页保存一次，后续应用内链接导入会自动复用 DPAPI 加密值。
- 日常使用不再推荐“F2 CLI → manifest → 外部令牌 → 推送脚本”流程。外部批量导入 API
  与 `push_to_tokbrain.py` 继续保留给同机可信工具的高级程序化集成。

### 本地视频与外部批量导入

- 导入页新增独立“本地视频”入口，一次可选择或拖入最多 10 个视频；一文件一作品，标题
  默认取文件名，并可逐条修改标题、描述和目标收藏夹。上传成功后默认只进入待确认，继续
  复用原有“确认加入待入库 → 主动入库”流程。
- 本地视频经过魔数、真实容器、视频轨、大小、SHA-256、ffprobe 时长和预算预检；同一
  视频自动去重。本地与外部导入不会访问 F2，也不消耗每日链接解析额度。
- 新增同机外部批量导入 API v1：调用方使用 Bearer 令牌创建 JSON 清单、逐条上传本地
  视频并提交批次。每批最多 100 条，默认只进入待入库；显式
  `start_processing=true` 时才创建 AI 入库任务。
- 外部批次要求权利声明与 `Idempotency-Key`，支持创建、上传和提交重试；重复提交不会
  重复创建作品、收藏夹关系或任务。接口不接受远程媒体 URL、任意本机路径和纯元数据条目。
- 设置页可生成、轮换和撤销外部导入令牌；令牌明文只显示一次，数据库只保存 SHA-256
  哈希和前缀。服务继续只监听 `127.0.0.1`。
- 数据库结构升级到 **schema v8**，新增可恢复的数据包文件暂存表；升级前自动创建
  `douyin_rag-pre-v8-*.db` 备份。旧作品默认保持
  `link / f2`，本地及外部作品使用 `never` 刷新策略。
- API contract 升级到 `6`。新增网页数据包创建、逐文件上传、检测和状态接口；
  外部接口错误统一返回包含 `code`、`message`、`retryable` 和 `field` 的 `detail` 对象。
- 新增[外部批量导入 API 文档](docs/external-import-v1.md)和仅依赖 Python 标准库的
  [`push_to_tokbrain.py`](scripts/push_to_tokbrain.py) 一键推送示例。

### 音画协同关键帧

- 允许下载的视频不再只按场景突变选图；候选池同时包含转场后的稳定画面和全时段均匀
  采样，并以视频文件真实时长为准。
- ASR 保留句级时间戳，模型据此生成视觉证据需求；候选画面先提取文字和客观视觉描述，
  再由模型按候选编号重排，模型异常时自动回退到清晰度与时间覆盖规则。
- 最终入库画面执行精确二次抽帧，减少转场帧和时间偏移；数据库记录候选来源、选择分数、
  选择理由、OCR 和视觉描述，便于排查。
- 数据库结构升级到 **schema v6**；从 schema v5 升级只增加关键帧解释字段，不重建
  或丢弃已有数据，升级前自动创建 `douyin_rag-pre-v6-*.db` 备份。

## [0.4.0] - 2026-07-31

### 收藏夹专属总结提示词

- 每个收藏夹现在可以保存自己的总结提示词，提示词以正常、可编辑的文本显示。
- 新入库或手动“补齐/更新总结”时，使用作品最近加入的收藏夹提示词。
- 清除收藏夹提示词后会回退到设置页的全局总结提示词。

### 两阶段导入流程

- 预检完成后，可为每条作品选择一个已有收藏夹，默认选择“手动导入”。
- “确认加入待入库”只创建或关联知识库作品、保存收藏夹关系，不下载媒体、不调用 AI，
  也不自动创建入库任务。
- 确认成功后，按钮切换为“入库（数量）”，用户可在导入页直接处理刚才确认的作品，
  也可以前往“知识库 → 待入库”单条或批量开始入库。
- 同时存在新预检作品和已确认待入库作品时，两组勾选、数量、状态和操作按钮分别管理，
  不会把待确认作品误当成可入库作品。

### 自动去重与预检保留

- 同一次粘贴中的相同链接会在后台自动移除，重复链接不占用每批 10 条唯一作品的限额。
- 后续批次会与尚在预检区的作品比较并自动去重；并发提交相同链接时也只创建一条预检。
- 不同短链接最终解析到同一作品时，只保留一条可确认结果。
- 页面仅提示“已去掉 N 条重复链接”，不再显示误导性的重复作品卡片；其余唯一作品继续
  正常预检。
- 已完成的预检结果在页面切换后仍会保留，并可逐条删除。删除已确认条目的预检记录不会
  连带删除对应的知识库作品。

### 删除与数据清理

- 知识库的待入库、已入库、处理异常和已归档作品均可永久删除。
- 已完成总结的作品和总结详情页也提供永久删除入口；删除会同步清理总结、知识块、
  检索索引、关键帧和对应本地资产。
- **永久删除不可恢复。** 如需保留内容，请先备份 `data/` 中的数据库和资产目录。

### 数据库升级

- 数据库结构升级到 **schema v5**，新增收藏夹总结提示词字段。
- 升级前会在 `data/backups/` 自动创建
  `douyin_rag-pre-v5-YYYYMMDD-HHMMSS.db` 时间戳备份。
- 从 schema v4 升级不会重建或丢弃已有预检与入库队列。

### API 与兼容性

产品版本升级为 v0.4.0；`API_CONTRACT_VERSION` 继续保持为 `4`。原有 `item_ids`
确认请求仍然兼容，但确认响应和入库语义已经调整；本次包含以下接口扩展：

- 收藏夹响应以及 `PUT /api/library/collections/{collection_id}` 支持
  `summary_prompt`。
- `POST /api/import-batches` 响应增加 `queued_count` 和 `duplicate_count`。
- `POST /api/import-batches/{batch_id}/confirm` 支持逐条提交
  `items: [{item_id, collection_id}]`；原有 `item_ids` 仍然兼容，未指定收藏夹的作品
  继续加入“手动导入”。
- 确认接口现在返回 `confirmed_count`、`work_ids` 和
  `library_state: "pending"`，且不再自动创建入库任务。
- 正式批量入库通过 `POST /api/library/ingest/jobs` 启动。
- 新增 `DELETE /api/import-items/{item_id}`；知识库的
  `DELETE /api/library/works/{work_id}` 现在覆盖已完成总结的作品及其本地派生数据。

### 验证结果

- 后端测试：114 项通过。
- 前端契约测试：30 项通过。
- 前端 lint 与生产构建通过。
- 测试使用人工构造数据和模拟传输层，不访问真实抖音。

### 升级说明

1. 停止正在运行的 TokBrain，并建议额外备份整个 `data/` 目录。
2. 获取 v0.4.0 源码后运行 `安装.cmd` 更新依赖，再运行 `启动.cmd`。
3. 首次启动会自动备份并迁移数据库到 schema v5，请勿在迁移过程中强制结束进程。
4. 升级后检查收藏夹提示词，并确认导入页的“确认待入库”和“入库”两步操作符合预期。

本版本只通过 GitHub 提供自动生成的源码 ZIP 和 TAR 包，不附加二进制安装包。

### English summary

TokBrain v0.4.0 adds per-collection summary prompts, per-item collection
selection, and an explicit two-stage Confirm-to-Pending / Ingest workflow.
Selection and action states remain separate when new previews and previously
confirmed works coexist.

Duplicate links are removed automatically within a submission, across preview
batches, during concurrent submissions, and after different short links resolve
to the same work. Users see a concise removed-duplicate count while unique works
continue previewing normally. Completed previews persist across page switches
and can be deleted individually.

Works in every Library state, including fully summarized works, can now be
permanently deleted together with derived knowledge and local assets. This
operation cannot be undone. Database schema v5 adds collection prompts and
creates a timestamped SQLite backup before migration while preserving schema v4
preview and ingestion queues.

API contract version 4 is retained. The release adds compatible response fields
and per-item collection assignments, changes confirmation to create Pending
works without starting ingestion, and introduces explicit bulk-ingestion and
preview-deletion endpoints. The release provides GitHub-generated source
archives only; no binary installer is included.
