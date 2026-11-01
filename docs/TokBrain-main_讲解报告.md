# TokBrain-main 讲解报告

> 目标: `D:\code\TokBrain-main`（GitHub ZIP 解压副本，无 `.git`）
> 扫描范围: 读全 `app/main.py`、`app/config.py` 关键行、12 个 router 的全部路由定义（grep）、32 个 service 的文件名、`app/models.py` 表定义、`mcp_server/` 四个模块头部、`tokbrain_control/commands.py` 函数签名、`README.md` 功能表与处理流程、`frontend/package.json` 与组件目录、`cli/package.json`、`bridge_feishu_obsidian.py` 头部
> 未读部分: 各 service 函数体、`frontend/` 组件实现、`tokbrain_control/` 实现细节、`prototypes/`、`doubaosave Skill/`、`scripts/`

## 假设确认表

| # | 假设 | 依据 | 若假设错误的影响 |
|---|---|---|---|
| 1 | 这份与 `D:\code\new\TokBrain` 是**两条独立演进的分支**，不是新旧关系 | 后者 API contract 8、9 个 router；本份 contract 7、12 个 router，端口 8001 vs 8000 | 若其实是同一份的不同快照，"以哪份为准"的结论要改 |
| 2 | 后端 8001、前端 3001 | `app/config.py:41-42`、`frontend/package.json:18` | 联调端口要改 |
| 3 | 问答能力**不在网页端**，由 MCP 通道承担 | README「网页端尚无问答入口」；本份**没有 chat router** | 若网页问答其实有别处入口，架构图要加一条边 |
| 4 | 单机单用户，SQLite 本地库 | `app/config.py:45`、`app/database.py` | 多机场景全部要重做 |
| 5 | AI 全链路走阿里百炼 | `app/config.py` 模型配置段 | 换供应商是最大改造项 |

## 一、一句话结论

**这是一份"本地知识库 + 对外 Agent 接口"的工具型副本**：比 `new\TokBrain` 多了 MCP 双通道、飞书同步、Cookie 库、模型档案，但**网页端反而没有问答入口**；它不是"更完整的新版"，而是把问答体验挪到了 Hermes/Claude 这类外部 Agent 上，二次开发前得先确认你要沿哪条线走。

## 二、代码架构

- **分层**：`Next.js 前端(:3001)` → `FastAPI(:8001, 仅回环)` → `routers_v2`(12 个) → `services`(32 个) → `SQLAlchemy async` → `SQLite(WAL)` + 本地媒体文件
- **模块边界**：
  - `app/routers_v2/` — 纯 HTTP 适配，挂载于 `app/main.py:59-72`：`imports` `integrations` `inbox` `package_imports` `jobs` `health` `library` `settings` `platforms` `cookies` `model_profiles` `sync`
  - `app/services/` — 32 个业务模块，本份新增 `cookie_store` `platforms` 相关 `discovery` `dispatch_store` `doubaosave_bridge` `feishu_auth` `model_profiles` `sync_status` `vector_store` `browser_resolver`
  - `app/models.py` — 16 张表
  - `tokbrain_control/` — **独立控制层**：`commands.py` 暴露 33 个工具，`client.py` 走 HTTP 调本机 API，CLI 与 MCP 共用同一份实现
  - `mcp_server/` — 三个独立 MCP：`tokbrain_mcp.py`（只读检索，`:8010`，工具 `kb_*`）、`mcp_control.py`（控制，`:8011`，33 工具）、`tokbrain_hermes_stdio.py`（NAS 上的只读 stdio 适配器）
  - `bridge_feishu_obsidian.py` — 飞书多维表格 → TokBrain → Obsidian 的常驻桥接进程
  - `cli/` — Node 包，依赖 `@larksuite/cli`，飞书侧入口
- **依赖方向**：`routers → services → models/database`；控制层与 MCP 走 HTTP 回调主服务（不直接碰业务函数）；Hermes stdio 适配器**只读 SQLite 快照**，完全绕开主服务
- **主调用链**（README 处理流程，代码侧对应）：
  链接/本地视频/文件夹/ZIP → `imports`(`app/routers_v2/imports.py:48`) 预检 → 用户确认 → `job_coordinator` → 多模态（ASR/OCR/关键帧/视觉描述）→ 总结、分块、向量化（`app/services/vector_store.py`）→ SQLite + 媒体 + 证据 → MCP 检索 / Obsidian 导出
- **扩展机制**：新增 HTTP 能力 = 加 router 并登记进 `_ROUTERS`（`app/main.py:59-72`）；新增 MCP 工具 = 在 `tokbrain_control/commands.py` 加命令，`mcp_server/mcp_control.py` 自动代理

## 三、三级能力清单

### 核心

| 能力 | 说明 | 频度 | 位置 |
|---|---|---|---|
| 链接预检入库 | 粘贴分享文本，预检后人工确认才入库 | 高 | `app/routers_v2/imports.py:48` |
| 本地视频导入 | 单批最多 10 个文件，一文件一作品 | 高 | `app/routers_v2/imports.py:88` |
| 多模态处理 | ASR、关键帧、OCR、视觉描述、总结、分块、向量化 | 高 | `app/services/content_pipeline.py` |
| 向量索引 | sqlite-vec 的 vec0 扩展，缺失时降级内存扫描 | 高 | `app/services/vector_store.py:1` |
| 只读知识检索 | `kb_search` 等 5 个工具，复用 `knowledge.search` | 高 | `mcp_server/tokbrain_mcp.py:168` |
| 作品库管理 | 待入库/已入库/待补件/异常/归档 + 收藏夹 | 高 | `app/routers_v2/library.py:341` |

### 常用

| 能力 | 说明 | 频度 | 位置 |
|---|---|---|---|
| 任务编排 | 串行队列、进度、取消、重试 | 中 | `app/routers_v2/jobs.py:14` |
| 飞书收件箱 | 手机分享的链接写入多维表格再入队 | 中 | `app/routers_v2/inbox.py:79` |
| 平台交接 | 抖音等平台的 handoff / pull / 状态 | 中 | `app/routers_v2/platforms.py:84` |
| Cookie 库 | 各平台登录态的上传、迁移、按平台读取 | 中 | `app/routers_v2/cookies.py:86` |
| 模型档案 | 多套模型配置切换与激活 | 中 | `app/routers_v2/model_profiles.py:109` |
| 同步状态 | 发现、确认、重放、飞书重登 | 中 | `app/routers_v2/sync.py:39` |

### 边缘

| 能力 | 说明 | 频度 | 位置 |
|---|---|---|---|
| 控制 MCP | 33 个写操作工具给 Agent 用，默认关闭 | 低 | `mcp_server/mcp_control.py:1` |
| Hermes stdio | NAS 上只读快照适配器 | 低 | `mcp_server/tokbrain_hermes_stdio.py:1` |
| 抖音 DOM 采集 | 浏览器侧采集任务 | 低 | `app/routers_v2/platforms.py:227` |
| 外部批次 API | Bearer 令牌的程序化导入 | 低 | `app/routers_v2/integrations.py:111` |
| 官方账单刷新 | 手动触发账单同步 | 低 | `app/routers_v2/settings.py` |
| doubaosave 桥 | 第三方存档工具对接 | 低 | `app/services/doubaosave_bridge.py` |

### 声明但未找到实现

| README 声明 | 检索范围 | 结论 |
|---|---|---|
| 网页端问答 | `app/routers_v2/` 无 `chat.py`，只有 12 个 router | **本份确无网页问答入口**，README 说法属实 |
| Hermes 可检索"时间轴/合集/统计" | 仅读 `tokbrain_hermes_stdio.py` 头部 | 未逐一核对工具清单，未验证 |

## 四、可运行 Demo

本份的"核心路径"是**只读检索**，用现成库直接验证（需已有入库数据）：

```bash
cd D:\code\TokBrain-main
.venv\Scripts\python.exe -c "import asyncio; from app.services.knowledge import search; print(asyncio.run(search(None,'测试')))"
```

未生成 demo 文件：本副本 `.venv` 与 `.vendor` 体积大、且检索必须先有入库作品才能产出真实结果，硬造一个会误导。上一份报告里的 `demo/core_path_demo.py`（分块 + 混合打分）在两份代码里是同一套实现，可直接复用。

## 五、踩坑点

### 环境坑
- 目录里带 `.venv`、`.vendor`(F2)、`node_modules`（cli 与 frontend 各一份），整包解压后体积很大 → 别整目录复制，用 git clone → 先确认 `.venv` 与系统 Python 版本一致
- F2 依然是 `--no-deps` 装进 `.vendor/`（`requirements.txt` 注释）→ 手工 `pip install -r requirements.txt` 会装坏 → 走 `scripts/setup.ps1`
- `cli/` 是 Node 包（依赖 `@larksuite/cli`），需要 Node ≥22 → 与 frontend 的 node_modules 是两套 → 不要在 frontend 目录里装 CLI 依赖

### 配置坑
- 端口全变了：后端 **8001**、前端 **3001**、只读 MCP **8010**、控制 MCP **8011** → 与 `new\TokBrain`（8000/3100）不一致 → 两份并存时极易连错服务，调试前先确认连的是哪份
- ffmpeg 不在 PATH 时要靠 `app/main.py:91-178` 的候选探测（环境变量 → 项目内 → 各种盘符 glob）→ 换机器部署必须显式设 `FFMPEG_BIN` → 该模块还专门补 `PATHEXT`，说明 Git Bash 下会踩坑
- 三个 MCP 各自有独立 `.env` 样例（`control.env.example` / `mcp.env.example`）→ 控制 MCP 必须 `TOKBRAIN_CONTROL_ENABLE=1` 才启动 → 非回环监听必须配 ≥32 位令牌

### 使用坑
- **网页端没有问答**，检索要走 MCP 客户端 → 直接在浏览器里问是做不到的 → 要么接 Claude Desktop/Hermes，要么自己加 chat router
- 只读 MCP 是独立进程 + 独立 venv（`.venv-mcp`），与主服务隔离 → 它直接读库，不受主服务状态影响 → 主服务停了它还能用
- 同源校验仍拦写请求（`app/main.py:225-239`）→ curl 直接 PATCH 会 403 → 脚本化写操作走控制 MCP 的令牌接口
- 飞书桥接是**独立常驻进程**，不是主服务的一部分 → 飞书侧看不到状态，往往是没起桥接进程 → 状态看侧边栏显示

### 维护坑
- `API_CONTRACT_VERSION = 7`（`app/main.py:56`），而 `new\TokBrain` 是 8 → 两份的 API 不兼容，**不要混用前端与后端** → 认准端口
- 迁移逻辑在 `app/services/migrations.py`，本份还有 `cookie_store.migrate_legacy_secrets` 的一次性搬运（`app/main.py:181-198`）→ 改 schema 前两个都要看
- 32 个 service、12 个 router，边界靠命名而非显式分层 → 找代码先看 `app/main.py:59-72` 的 router 清单，比全局搜更快

## 六、接入成本评估

未提供具体需求，本章跳过。给一条通用判断：**这份副本适合"让外部 Agent 读写知识库"，`new\TokBrain` 适合"用户在网页里问"**。两者选一条，不要试图合并。

## 附: 风险旗标

| 项 | 情况 |
|---|---|
| 鉴权 | 主服务无用户体系，`TrustedHostMiddleware` 只认回环（`app/main.py:58`）；控制 MCP 才有令牌 |
| 端口暴露 | 四个监听端口（8001/3001/8010/8011），非回环暴露需逐个确认令牌配置 |
| 敏感依赖 | `browser-cookie3` 会读浏览器 Cookie；`cookie_store` 集中存各平台登录态 → 权限面很敏感 |
| MCP 默认状态 | 控制 MCP 默认关闭且需显式开启，方向正确；只读 MCP 非回环时需令牌 |
| 供应商锁定 | AI 全链路阿里百炼 |
| 代码许可 | MIT |
