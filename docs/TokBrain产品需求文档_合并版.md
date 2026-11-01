# TokBrain 产品需求文档（合并版）

> **合并声明（2026-10-31）**：本文由三份 PRD 级文档按板块合并而成：
> - `TokBrain原型PRD_v1.md`（UI/按钮级历史原型规格，自认非当前需求）
> - `TokBrain流水线v2改造_PRD与研发任务清单.md`（飞书→Obsidian 桥接流水线工程改造 PRD，P0–P5 已实施）
> - `TokBrain功能层级结构图.md`（当前生产系统全栈结构图，自封事实来源，2026-10-30 重写）
>
> **权威来源**：以《功能层级结构图》为当前生产事实来源；原型 PRD_v1 仅作历史按钮规格沿革。两者冲突处一律以结构图为准（v1 PRD 第351行亦自承此点）。
>
> **本会话合并时的 4 项决策（已征询用户意见）**：
> 1. **主题**：采用 **3 套（晚霞云海·sunset-cloudsea 默认 / 东方雾山·eastern-mist / 海洋晨光·ocean-dawn）**。结构图末尾差异表旧的「4 套 aura 默认」已同步校正。
> 2. **解析页 pending/等待中 态**：**不纳入**本合并文档（结构图解析面板只列粗状态：排队/进行中/中断/完成/部分/失败）。
> 3. **限额数字**：按类目取最高值当默认——**网页链接每批 100 / 每日 150**，**外部 HTTP 入站单批 100**；三者均接进运行期设置（`import_batch_limit` / `import_daily_limit` / `inbox_batch_limit`），可经设置项由前端调整。
> 4. **v1 历史注记**：原型 PRD_v1 中「原型 vs 产品差异」「历史原型规格」「从未在生产实现」等表述，统一降级为各章节内「ⓗ 历史沿革」脚注，留痕但不干扰正文规格。

---

## 1. 产品概述与范围

### 1.1 背景与目标
TokBrain 是面向「视频 → 知识」工作流的**本地优先**产品：把抖音 / 小红书 / B站 / 本地视频导入后，经解析（ASR 音频转写、模型总结、标签分类）产出知识切片，沉淀进个人知识库，并支撑后续问答（仅经 MCP 对外）与克隆训练。

原型目标：以低保真、纯前端、可交互方式快速验证主流程交互与信息架构，打磨「导入 → 解析 → 知识库」三段链路。

### 1.2 范围
- **范围内**：4 个页面（导入 / 解析 / 知识库 / 设置）的全部可见交互、按钮、状态与边界；以及飞书→Obsidian 桥接流水线子系统。
- **不在范围内 / 未落地**：克隆训练页（脑图规划中的独立模块，尚未落地）；真实浏览器型 OAuth 授权模块（需第三方应用凭据，属后续）。

### 1.3 合规红线（贯穿全产品）
- 主动、低频、可中断；不扫描账号/收藏夹、不绕过下载权限。
- 入站 API / 外部批量导入须 `rights_attested` 确认「有权处理」；抖音链接走 HTTPS-only 规范化（主机白名单 + 去追踪参数）。
- 低频轮询（60s）、状态回写仅变化触发，尊重外部接口额度。

> ⓗ **历史沿革**：原型 PRD_v1 曾把「对话/问答/流式回答」「克隆训练」列入范围并仅占位；经 2026-10-30 校订确认，网页端**无对话页**（检索只经 MCP 双通道），克隆训练仍未落地。原型 PRD_v1 亦曾声明「设置页已从原型与 PRD 移除」——该声明已过时，生产页面已恢复并扩展为完整设置页（见 §6）。

---

## 2. 信息架构与页面地图

```
TokBrain 本地多模态知识库系统
├─ ① Web 前端（Next.js 16 / React 19 / Tailwind 4，端口 3001）
│  ├─ 主界面 "/"（4 个功能 Tab：导入 / 解析 / 知识库 / 设置）
│  │  ├─ 【导入】ImportWorkspace（风险提示横幅 · 今日处理/知识库概况 · 本地视频/数据包/公开链接 三路导入 · 批量任务队列）
│  │  ├─ 【解析】ParseWorkspace（解析进度面板 · 预检结果列表 · 确认入库流程 · 模型配置）
│  │  ├─ 【知识库】Library（状态分段导航 · 收藏夹 · 作品网格 · 待补件视图 · 单/批量操作 · 导出 Obsidian）
│  │  └─ 【设置】Settings（主题 · 本地健康检查 · 处理额度 · 提示词 · 凭据 · 账单 · 外部令牌 · 敏感清理）
│  └─ 作品总结详情页 "/works/[id]"
├─ ② 后端 API（FastAPI，127.0.0.1:8001）
├─ ③ 核心服务层（导入/处理流水线/检索/同步桥接/支撑）
├─ ④ 数据模型与存储（SQLite + 文件系统）
├─ ⑤ 桥接与自动化脚本（bridge_feishu_obsidian.py 等）
├─ ⑥ 外部系统集成（抖音/小红书/B站/飞书/Obsidian/百炼/BSS）
└─ ⑦ 知识检索出口（只读 MCP 双通道，均无写入工具）
```

> ⓗ **历史沿革**：原型 PRD_v1 的页面地图仅含导入/解析/知识库三页（无设置页），且编辑模式（`contentEditable` 文案改写、`localStorage` 持久化、隔离守卫）为原型专有交互机制；生产页面以结构图四页签为准，编辑模式等非生产机制不纳入正文。

---

## 3. 导入模块

### 3.1 本地视频导入（LocalVideoImporter）
- 拖拽/选择 MP4/MOV/MKV/WebM（单文件 ≤1GB，每批 ≤10 个）。
- 逐个顺序上传（一文件一作品，无并发）+ SHA-256 指纹去重。
- 标题/描述/收藏夹编辑、权利声明勾选；状态机：待上传→准备→上传中→可确认/已存在/失败。

### 3.2 外部数据包导入（PackageVideoImporter）
- 文件夹 / ZIP 两种模式（候选 ≤1000 文件，文件夹 ≤100 视频，总 ≤20GB，ZIP 只能 1 个）。
- 识别 douyin_videos.db / JSON / CSV / 文件名中的抖音作品 ID；按批次持久化 + 断点续传 + 自动安全解压；检测完成→逐条核对确认。

### 3.3 公开作品链接导入（每行一条，预检不下载）
- 批量上限 **每批 100（默认，可调）/ 每日 150（默认，可调）**。
- 重复链接自动剔除（去重）；只做预检，不会自动入库。
- 熔断：403/429 后停 30 分钟。

### 3.4 外部 HTTP 入站 API（流水线 v2 子系统）
- `POST /api/integrations/v1/inbox/items`：Bearer 令牌鉴权 + `Idempotency-Key` 幂等 + `rights_attested` 合规前置。
- **单批 ≤100（默认，可调）**，超出整批原子拒绝；非法链接逐条 `rejected` 不影响同批合法链接。
- 经 `normalized_key`（sha256）去重；入站即落 `dispatch` 共享队列（与飞书/本地三路同源）。

### 3.5 平台连接器（网络视频 Tab）
- 「连接」= 真实令牌（`POST /api/settings/integration-token`）；「握手登录」= 粘贴 Cookie 握手（`POST /api/platforms/{pf}/handoff`，存为密文 SecretRecord）。
- 「拉取视频」=`POST /api/platforms/{pf}/pull`（抖音弃用 F2 发现路径，三端统一走 zhicui 方案，均 `enqueue=true` 入 platforms 导入管线）。
- 范围标签（收藏/喜欢/我的作品/观看历史/读取失败）与子分类切换、视频单选/全选、同步。
- **采集运行环境（硬依赖）**：抖音「采集」经 `doubaosave_bridge` 以 subprocess 调 `doubaosave Skill/source/scripts/main.py`，该脚本依赖 Playwright。Playwright 装于**项目内专用环境 `.venv-collect`**（与后端 `.venv` 隔离，后端不需要 Playwright）；解释器解析优先级为 `DOUBAOSAVE_PYTHON` 环境变量 → `.venv-collect/Scripts/python.exe` → 旧默认环境。
- **浏览器不下载 chromium**：脚本优先以 `channel="chrome"/"msedge"` 复用本机浏览器（2026-10-31 实测 Chrome 156 / Edge 156 均可直接驱动），全部失败才回退自带 chromium。因此**只需 `pip install playwright`，无需 `playwright install chromium`**。
- **失败提示口径**：子进程输出统一以 UTF-8 强制（`PYTHONIOENCODING`/`PYTHONUTF8`）并按 utf-8 → gbk 兜底解码，避免中文报错变成方块；缺少 Playwright、缺浏览器、浏览器被提前关闭等常见故障转成可执行的中文提示，不再把原始 traceback 直接抛给前端。

> ⓗ **历史沿革**：原型 PRD_v1 网络同步按钮曾写死「同步当前收藏夹 50 条」演示值；该值已废弃，生产以运行期限额为准（§3.3/§3.4）。前端「每批/每日上限」输入控件曾于 2026-10-31 从设置页移除，接口与运行期设置（`import_batch_limit`/`import_daily_limit`/`inbox_batch_limit`）仍完整可调，重新接入 UI 属待办（见 §12）。

---

## 4. 解析模块

### 4.1 解析进度面板
- 进度条 + 状态（排队/进行中/中断/完成/部分/失败）；Worker 网格（链接预检固定 3 worker）；熔断暂停提示；中断解析（安全停止）。

### 4.2 预检结果列表
- 封面 + 状态标签（预检重复 / 已在库 / 可确认 / 需补件 / 失败 / 风控）。
- 媒体处理策略标签（作者允许下载 / 仅字幕音频 / 仅基础信息 / 本地完整处理）。
- 收藏夹归属选择、本地补件上传、删除预检结果；保留多批次预检结果（刷新恢复）。

### 4.3 确认入库流程
- 选择全部可确认项 → 确认加入待入库 → 入库（创建 ingest 任务）。
- 单条卡片状态机（解析页 `itemStatus`）：
  - 激活态：显示旋转 spinner + 中文态名——`queued` 排队中、`resolving` 解析中。
  - 终态/收尾态：小胶囊标签——`ready` 可以确认（绿）、`needs_local_file` 需要补件（黄）、`confirmed` 已加入待入库（绿）、`failed` 解析失败（粉）、`blocked` 因风控停止（粉）、`cancelled` 已中断（灰）、`duplicate` 已在知识库（灰）。
  - 时间轴 4 节点（预检 / 音视分离 / 模型总结 / 标签分类）按 `stageDone(status)` 填充。
  - 顶部统计卡顺序固定：重复 / 中断 / 失败 / 需补件 / 待分类 / 可确认。

### 4.4 模型配置（左对话模型 / 右音频模型）
- ＋ 接入新模型 / 取消 / API Key 显隐 / 启用开关 / 保存并设为当前 / 列表删除（二次确认）。

> ⓗ **历史沿革**：原型 PRD_v1 曾在 `itemStatus` 中加入 `pending` 等待中（平台拉取导入初始态），并指出后端 `pending` 卡死根因（协调器只处理 `queued`）。**本合并版按 2026-10-31 决策不纳入 pending 态**，解析页状态以 §4.3 为准；若后续后端补齐 `pending→queued` 提升，再回补该态。

---

## 5. 知识库模块

- 状态分段导航：待处理 / 在库 / 待补件 / 异常 / 已归档（含计数）。
- 收藏夹筛选（本地分组）+ 新建收藏夹 + 收藏夹专属总结提示词。
- 作品卡片网格（封面、标题、作者、时长、摘要、标签、状态）。
- 待补件视图：材料状态报告（音频/字幕/转写/视频/画面/OCR/图片 各轨道有效性）+ 材料缺口说明 + 上传完整视频/图片组（≤12 张）→ 自动重新处理。
- 单作品操作：查看总结、开始入库、重新处理、恢复、永久删除、查看原作品。
- 批量操作：加入收藏夹、批量入库、补齐/更新总结、导出到 Obsidian。
- 分页加载更多（返回时保留浏览位置）。

> ⓗ **历史沿革**：原型 PRD_v1 知识库为静态占位（计数硬编码），产品需实现真实按筛选+标签+分类拉取。

---

## 6. 设置模块（当前生产页面，权威）

1. **主题选择**：3 套内置（晚霞云海·sunset-cloudsea 默认 / 东方雾山·eastern-mist / 海洋晨光·ocean-dawn）+ 背景显现度。
2. **本地健康检查**：数据库 / 音视频工具 / 后台协调器 / 敏感数据清理。
3. **处理额度**：每日媒体分钟、每日 AI Token、月度费用预警、画面参数。
4. **AI 总结提示词编辑器**（12,000 字符，一键恢复默认）。
5. **凭据管理**：百炼 API Key、平台 Cookie、账单 AccessKey（DPAPI 加密）。
6. **账单**：本地估算 vs 阿里云官方账单（刷新、延迟说明）。
7. **外部批量导入令牌**（生成/轮换/撤销，明文仅显示一次）。
8. **敏感凭据一键清理**（遍历全部模型配置档）。

> 链接导入限额（`import_batch_limit` 每批 / `import_daily_limit` 每日 / `inbox_batch_limit` 外部入站单批）由运行期设置驱动、可经设置接口调整，但未在设置页提供输入控件（见 §3.5 脚注与 §12 待办）。

---

## 7. 飞书→Obsidian 桥接流水线（子系统 PRD）

### 7.1 背景与目标
v1 的「飞书收藏收件箱 → TokBrain 深度处理 → Obsidian 笔记」是脚本串行流水线；v2 升级为**可并发、可中断、可对外接入**的工程化流水线：收件即落占位笔记（秒级），慢速处理后台回填；飞书行、本地 `inbox.json`、HTTP 入站三路来源走**同一状态机**。

### 7.2 目标
| 维度 | 目标 |
|------|------|
| 时效 | 收件到 Obsidian 占位笔记 ≤ 数秒（双轨解耦） |
| 一致 | 三路来源共享单一调度队列，状态不漂移 |
| 可接入 | 对外入站 API，支持幂等、批量、合规前置 |
| 可观测 | 飞书「处理状态」列实时回写；仅变化回写省额度 |
| 合规 | 主动、低频、可中断，不扫描/不绕过下载权限 |

### 7.3 功能清单
| 模块 | 功能点 | 代码落点 |
|------|--------|----------|
| 入站 API | HTTP 批量入站、Bearer 鉴权、合规前置、幂等、批量上限、逐条容错、URL 规范化去重 | `app/routers_v2/inbox.py` |
| 共享调度队列 | 单一事实源、状态机、适配器注册、幂等表、旧库 backfill | `app/services/dispatch_store.py` |
| 双轨引擎 | 快轨占位笔记、慢轨处理回填、run_once 编排、双轨一致性兜底 | `bridge_feishu_obsidian.py` |
| 存储与关键帧 | 硬链接优先+降级、关键帧筛选、孤儿链接回收 | `bridge_feishu_obsidian.py` |
| 飞书同步 | 60s 增量轮询、状态回写映射、仅变化回写、同步状态持久化 | `bridge_feishu_obsidian.py` |
| 路由挂载 | routers_v2 全组 include 进主应用 | `app/main.py` |

### 7.4 核心流程
```
外部来源(飞书多维表格 / 本地 inbox.json / HTTP 入站 API)
   → [入站 API] 规范化 + 合规校验 + 幂等 + 去重
   → [共享 Dispatch 队列] 状态机: pending → queued → processing → {processed | needs_supplement | failed}
   → [双轨引擎] 快轨: 写占位笔记(秒级) ＋ 慢轨: 处理+回填(分钟级)
   → Obsidian 占位笔记 / TokBrain 处理回填正文+关键帧
   → 60s 增量轮询 → 回写飞书「处理状态」列 + 关键帧硬链接入库
```

### 7.5 交互 / 需求（EARS 节选）
- 始终：同一链接 `normalized_key` 去重；仅向真实飞书 `record_id` 回写状态。
- 事件：入站收到批量请求则校验 `rights_attested` 与 `Idempotency-Key` 后入队；`run_once` 完成一轮双轨。
- 异常：`rights_attested != True` 拒批(400)；单批 > 上限整批原子拒绝；非法链接标 `rejected` 不影响同批；关键帧硬链接失败降级 symlink→复制；飞书回写失败仅记日志下轮重试。
- 状态：`needs_supplement` 视为等待人工终态不自动重投；`processing` 映射飞书「处理中」。
- 可选：`--watch` 模式按 `POLL_CYCLE=60s` 常驻轮询。

### 7.6 边界与合规
- 重复投递同链接去重；幂等键重放返回首结果/冲突 409；作者关下载→`needs_supplement`；时钟偏差 `CURSOR_SKEW_SECONDS=300`；跨卷硬链接降级不中断；作品被删→`prune_orphan_keyframe_links`。
- 鉴权 Bearer integration token；合规前置 `rights_attested`；不越界（不扫描/不绕过/单批上限+逐条拒绝）。

### 7.7 验收与任务（已实施，转验收对照）
- 三路来源共享同一 dispatch 队列、状态不漂移；入站 API 通过 TC-P4-01~12；飞书回写 TC-P3-06/07/10；双轨占位秒级+回填；NTFS 硬链接零额外占盘、跨卷降级；增量轮询 60s 不漏行。
- Epic1-6 研发任务（入站接入 / 共享队列 / 双轨引擎 / 存储关键帧 / 飞书同步 / 测试验收）已落地；依赖关系 T2.1→T1.1→T1.x、T2.1→T3.x→T6.2、T2.1→T5.x→T6.2。

---

## 8. 合规红线（汇总）
- 主动、低频、可中断；不扫描账号/收藏夹、不绕过下载权限。
- `rights_attested` 强制确认「有权处理」；抖音 HTTPS-only 规范化。
- 入站 API Bearer 鉴权 + 幂等；单批原子上限；非法链接逐条拒绝。
- 低频轮询（60s）、状态回写仅变化触发。

---

## 9. 状态机与边界

### 9.1 批次状态（batch.state）
`queued`→等待中 · `running`→进行中 · `automating`→自动确认中 · `cancelling`→安全停止中 · `cancelled`→已中断 · `succeeded`→已完成 · `partial`→部分完成 · `failed`→失败。
终端态 `{succeeded, partial, failed, cancelled}`。

### 9.2 任务状态（job.state）
`queued`→排队中 · `running`→解析中 · `automating`→自动确认中 · `cancelling`→安全停止中 · `cancelled`→已中断 · `succeeded`→已完成 · `partial`→部分完成 · `failed`→失败 · `await_fix`→待补件（终态）。

### 9.3 条目状态（item.status，解析页）
活跃 `{queued, resolving}`；保留展示 `{ready, needs_local_file}`；其余随批次终态收口。（`pending` 不纳入，见 §4.3 脚注。）

### 9.4 分发队列状态（dispatch_store，桥接子系统）
`pending → queued → processing → {processed | needs_supplement | failed}`。

### 9.5 边界场景
- 批次终态但残留活跃条目：聚合视为「解析中」（不显示批次终态以免误导）。
- 同名多义：「解析中」在全站多处含义不同（任务运行中 / 批次收尾中 / 解析页），UI 文案与埋点需区分。
- 任务卡片删除误判：列优先排列 + 删除淡出过渡（0.25s）消除视觉错位。

---

## 10. 后端 API / 服务层 / 数据模型（提炼）

- **后端 API**：`/api/import-batches`（链接预检批次）、`/api/local-import-batches`、`/api/package-import-batches`、`/api/integrations/v1/inbox/items`、`/api/jobs`、`/api/library`(18端点)、`/api/platforms`、`/api/settings`、`/api/model-profiles`、`/api/sync`、`/api/system-health`。
- **服务层**：`import_queue`（链接预检协调器，3 worker）、`f2_links`（链接解析，F2 legacy 兜底）、`downloader`、`local_assets`、`package_imports`、`platforms/`；处理流水线 `content_pipeline`/`jobs`/`keyframes`/`summaries`/`providers/`；检索 `vector_store`/`knowledge`；同步桥接 `dispatch_store`/`sync_status`/`feishu_auth`/`doubaosave_bridge`；支撑 `budget`/`pricing`/`bss_bill`/`secrets`/`runtime_settings`/`health` 等。
- **数据模型**：`douyin_rag.db`（AppSetting/SecretRecord/Collection/Work/Job/ImportBatch/ImportItem/UsageEvent…）、`chunk_vec`（sqlite-vec）、`data/keyframes`、`data/media`、`data/tmp`、`data/source-assets`、`data/package-imports`、`data/backups`、`data/pipeline/dispatch.db`。

---

## 11. 验收标准（合并）

1. 四个页面（导入/解析/知识库/设置）可经顶部 Tab 与导航切换，hash 可定位。
2. 本地上传→确认→队列→进度→成功(计数+1)/失败(可重试) 全链路。
3. 网络三端 Tab/范围/子分类/视频选择/全选/同步 符合 §3.5；「读取失败」范围自动全宽。
4. 解析页统计/分页/全选/确认/删除/标签编辑 符合 §4；解析中态隐藏复选框与删除、显示暂停。
5. 模型接入表单校验、保存即设为当前、删除二次确认 符合 §4.4。
6. 知识库筛选/标签/分类、待补件视图、导出 Obsidian 符合 §5。
7. 飞书→占位→回填→状态回写全链路（TC-P3/P4）；三路来源共享 dispatch 队列不漂移。
8. 入站 API 合规前置/幂等/批量上限/逐条拒绝/双轨兜底 通过。

---

## 12. 待确认问题

- **Q1（来自 v1）**：克隆训练页如何安放？设置页已恢复丰富模块，是否需补回偏好类更细设置？
- **Q2**：知识库筛选计数/标签/分类是否需后端实时聚合（原型曾硬编码）？
- **Q3**：模型「当前模型」被删除后的回退策略（自动下一个 / 提示选择）？
- **Q4**：导入「失败随机」是否替换为真实状态机（含失败原因与重试）？
- **Q5**：网络同步「按范围整体」还是「仅勾选视频」？（当前按钮为同步当前收藏夹，卡片支持逐条勾选）
- **Q6（本会话新增）**：`import_batch_limit`/`import_daily_limit`/`inbox_batch_limit` 已接运行期设置、可经 `PUT /api/settings` 调整，但设置页前端输入控件曾移除——是否重新接入 UI？默认每批 100 / 每日 150 / 外部入站单批 100 是否需要下调？
- **Q7（本会话新增）**：解析页是否回补 `pending` 等待中 态（需后端先补齐 `pending→queued` 提升）？本合并版暂未纳入。

---

## 附录 A：三份源文档差异对照（合并依据）

| 维度 | 原型PRD_v1 | 流水线v2 PRD | 功能层级结构图（权威） |
|------|-----------|-------------|----------------------|
| 覆盖页面 | 导入/解析/知识库（三页）+ 全局机制 | 不覆盖前端，只讲桥接子系统 | 前端4页签 + 后端/服务/数据/桥接/外部/MCP |
| 导入 | 本地+网络视频（演示值50） | HTTP入站API（单批≤100） | 本地/数据包/公开链接(批10→**100**/日150) + 队列 |
| 解析 | itemStatus 含 pending | 不涉及 | 粗状态+预检标签（无 pending） |
| 设置页 | 声明已移除 | 不涉及 | 存在且丰富（7+子模块） |
| 对话/问答 | 列入范围仅占位 | 不涉及 | 明确无对话页，检索只经MCP |
| 主题 | — | — | 3套（晚霞云海默认，Aura已删） |
| 状态机 | 批次/任务/条目三套 | dispatch 状态机 | 解析粗状态+预检标签 |
| 限额 | 演示50 | 单批≤100 | 批100/日150/外部100，均前端可调 |

## 附录 B：本会话决策与改动清单

- **决策**：主题3套晚霞云海默认；不纳入 pending；限额按类目取最高值当默认且全前端可调；v1 历史注记转脚注。
- **结构图校正**：末尾差异表「4套 aura 默认」→「3套 sunset-cloudsea 默认」。
- **后端接线**：`config.py` 新增 `inbox_batch_limit=100`、网页链接 `import_batch_limit` 默认 10→100；`runtime_settings.py` 纳入 `inbox_batch_limit`（TypedDict/MUTABLE_FIELDS/NUMERIC_RULES/defaults）；`schemas.py` 的 `SettingsView`/`SettingsUpdate` 同步；`routers_v2/inbox.py` 弃用写死 `_MAX_BATCH=100`，改读运行期 `inbox_batch_limit`。
- **测试**：`test_import_queue.py::test_batch_limit_applies_after_duplicate_links_are_removed` 改为显式 pin `import_batch_limit=10`，脱离全局默认值依赖（已通过）。注：`test_import_batch_create_restores_manual_preview_contract` 为预先存在的无关失败（与 `ImportBatchCreate` schema 相关，非本次改动）。

> 三份源文档原文件保留于 `docs/`，本文为合并基线；后续以本文 + 功能层级结构图为权威，原型 PRD_v1 仅作按钮规格沿革。
