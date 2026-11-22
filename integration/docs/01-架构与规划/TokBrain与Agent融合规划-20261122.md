# TokBrain × Agent 产品级融合规划

> 2026-11-22。目标：把 TokBrain（视频知识库）与 agent（NAS 家居 AI）**合并为一个项目**——产品级融合，共进程共库。
> 本轮只做调查与规划，未改任何代码、未动任何运行服务。

## 0. 一句话结论

两者**已经在 Hermes 层互通**（只读检索 MCP + 控制 MCP），融合不是从零连线，而是把「两套运行时、两台机器」收敛成「一个大脑、一个库、两类出口」——让同一个助手既控你家、又懂你沉淀的职业知识。

## 1. 事实依据（全部实测/读库确认，非推测）

### 1.1 两个项目的真身

| 项目 | 真正本体 | 性质 | 运行时 |
|---|---|---|---|
| agent | `D:/agent/`（`D:/code/agent/` 是空壳） | NAS 家居 AI，7×24 运行 | Hermes systemd(:8642) + 桥接 Docker + HA + life-agent |
| TokBrain | `D:/code/TokBrain-main/`（活跃开发版；publish/test/new 是发布/测试/初版副本） | 单机单用户本地知识库，MIT，fork 自 fyfsxkh/TokBrain | Windows Next.js(:3001) + FastAPI(:8001) + NAS 上的 SQLite |

### 1.2 已经存在的连接（关键）

- `D:/agent/hermes/live/config.yaml:68-73`：`tokbrain` stdio MCP，`TOKBRAIN_DB_PATH=/vol1/1000/tokbrain/data/douyin_rag.db`。
- 实测工具清单：`tokbrain_search`、`tokbrain_get_work`、`tokbrain_get_transcript`、`tokbrain_list_collections`、`tokbrain_stats`（**只读 5 个**）。
- 同配置 `:132-139`：`tokbrain_control` HTTP MCP（`192.168.10.100:8011`），暴露 33 个工具（导入/控制）。
- TokBrain README 自述：「网页端尚无问答入口，知识检索通过只读 Hermes/MCP」。

**结论**：融合的技术底座已经铺好一半——Hermes 早就把知识库当工具用了。缺的是「问答入口体验」和「写入闭环」，以及最终「共进程」。

### 1.3 知识库内容（这是"根据我知识库的知识"的实体）

库：`/vol1/1000/tokbrain/data/douyin_rag.db`（13 MB，2026-10-30 最后写入，只读打开查询）。

| 项 | 数量 |
|---|---|
| works 作品 | 14 |
| knowledge_chunks 知识分块 | 305（带 text / start_seconds / embedding） |
| keyframes 关键帧 | 149 |
| collections 合集 | 3（手动导入 / 技术分享 / 硬件PM） |
| work_summaries 摘要 | 14（一句话摘要 + 标签 + content_json） |

**内容主题高度聚焦**：14 部中 **11 部是硬件/产品/AI 产品经理面试与职业规划**（如"硬件产品经理面试""PM产品实验室""智能硬件产品经理培训""AI产品经理JD分析""硬件PM职业规划"），其余为摄影机位、AI 求职 skill、UI 设计 skill、PromptOps。

**这个发现决定产品定位**：这不是通用视频库，是老张的**职业知识库**（呼应 2026-10 从软件 PM 转硬件 PM 的背景）。融合后的助手应该是「懂你职业上下文的家庭 AI」，不是 generic 语音助手 + 视频库的拼盘。

### 1.4 关键技术事实

- 知识库是 SQLite，作品/摘要/分块/向量/关键帧俱全，`evidence_state`、`processing_state` 有证据约束设计——只有已入库已处理有证据的内容进检索，这是**可直接复用的质量边界**。
- 摘要模型 `qwen3.6-flash`，标签体系已成（硬件产品经理 ×3、AI产品经理 ×2 等）。
- 向量在 `chunk_vec` 系列表，检索链路由 TokBrain 的 `vector_store` 提供。

### 1.5 飞书表格 → 知识库的上游链路（2026-11-22 补审）

> 原规划遗漏了整条数据来源链路。详细审查见 `飞书链路审查-大肥鱼主动查询与下载入库-20261122.md`。

- **飞书多维表格（121 行）是素材入口**，Windows 本机常驻进程 `bridge_feishu_obsidian.py --watch` 用 lark-cli 用户身份读表 → 补元数据／真下载 → TokBrain 后端 8001 做转写/抽帧/OCR/总结 → 写 Windows 本地 SQLite → 导出 Obsidian → 回写飞书状态。
- **Hermes 读不到飞书表**（`mcp_control.py:119` 原话），只能通过 control MCP 的 `tb_sync_status/pending/start/confirm` **驱动** Windows 桥间接获取（pending 带标题）。
- **下载是两段式 + 人工闸**：默认 `download=false` 只补元数据；真下载须显式 `download=true` 重启桥，预检后必须问用户、`tb_sync_confirm` 才放行（防自动烧额度/撞风控）。
- **NAS 上的库是只读快照**（scp + chmod 400），不是主库；主库在 Windows，写入要绕回 Windows。原 §2「Hermes 读写同一个库」不成立，应理解为「读 NAS 快照，写经 control MCP 回 Windows，共库是阶段 4 目标」。
- 解析通道 2026-11-02 起依赖 Windows 桌面会话（playwright 真实 Chrome），「纯 SSH 环境不行」——这是阶段 3 的关键约束。

## 2. 目标架构

```
                 ┌─────────────────────────┐
                 │   Hermes 统一大脑        │
                 │   意图 → 决策 → 工具调用  │
                 └───────────┬─────────────┘
                    ┌────────┴────────┐
          家居控制面 │                 │ 知识问答面
       灯/空调/制冰机/电视          硬件PM/产品/面试/职业
       经 HA 反代 + 统一认证          带时间戳片段溯源
          音箱播报 │                 │ 音箱直接答 / 手机查
                    └────────┬────────┘
                 ┌──────────▼──────────┐
                 │ 共库 douyin_rag.db   │
                 │ 14作品/305分块/149帧 │
                 └─────────────────────┘
```

**设计原则**：
1. **一个大脑**：Hermes 是唯一决策者（延续 `AGENT_OPERATIONS.md` 2026-10-23 定调：NAS 自动化全由 Hermes 接管）。
2. **一个库**：`douyin_rag.db` 是知识唯一真源，Hermes 读写同一个库（读走 stdio MCP，写走 control MCP，最终收敛为同进程直连）。
3. **两类出口**：家居控制（物理世界）+ 知识问答（信息世界），共享同一意图解析和身份/审计边界（见 `D:/agent/xiaoqi-auth/` 全局认证）。
4. **证据边界不破**：TokBrain 的 `evidence_state` 机制保留——没证据的内容不进问答，避免大模型幻觉编造知识。

## 3. 四阶段迁移路径（每阶段独立可验收，不中断现有服务）

### 阶段 1 — 音箱知识问答（零风险，★2026-11-23 修正：不加规则，只验证）

- 让 Hermes 在语音里能主动用现有 5 个只读工具回答知识问题。
- **修正**：原写"在 SOUL.md 加问答规则"——**删掉**。stdio MCP 的 `INSTRUCTIONS`（`tokbrain_hermes_stdio.py:27-36`）已经写了完整规则（先 search、最多三次、忠于 evidence、带时间戳），Hermes 加载 MCP 时自动携带。再写进 SOUL.md 会造成两个规则源、行为漂移。
- 改为：验证现有 MCP `INSTRUCTIONS` 在语音链路里确实生效——对音箱问知识问题，看它是否调 `tokbrain_search`、是否带时间戳溯源。
- 验收：对音箱问"我知识库里关于硬件产品经理面试有什么"→ 能调工具、答出具体内容并溯源。
- 不改 TokBrain、不改数据库、不改 agent 代码、**不改 SOUL.md**。

### 阶段 2 — 写入闭环（低风险，走已有控制 MCP）

- 语音"收藏这条到硬件PM合集""把刚才那段记进知识库"→ Hermes 调 `tokbrain_control` 的对应工具。
- 具体：**不往 SOUL.md 加使用规则**。control MCP 的 `INSTRUCTIONS`（`mcp_control.py:86`）已经写清两套工具的分工、意图→工具映射、`tb_*` 两步式写入与 `backend_offline` 处置；再写一份到 SOUL.md 就是第二个规则源（同阶段 1 的修法）。本阶段只验证语音链路里这份 INSTRUCTIONS 生效，高风险写操作沿用 MCP 自带的人工确认闸。
- **硬性前提**：上线前对 `douyin_rag.db` 做可回滚快照（这是唯一真源）。
- 验收：语音加收藏 → 库里 `collection_memberships` 增行 → 网页端能看到。

### 阶段 3 — 收敛到 NAS（⚠️ 2026-11-22 修正：原方案会掐断飞书入口）

- TokBrain 后端（FastAPI :8001）迁到 NAS，与 Hermes 同机；前端 Next.js 可保留或并入 Hermes dashboard(:9119)。
- 目的：达成"日常运行不依赖本地电脑"（`ARCHITECTURE.md` 既定目标，目前仍有电脑代理依赖）。
- **修正**：飞书入口链路依赖 Windows 本机（lark-cli 扫码授权 + playwright Chrome 桌面会话，纯 SSH 不行）。只迁后端到 NAS 会**直接断掉飞书表格→知识库的整个入口**。必须拆成两步：
  - **3a（可行）**：迁只读检索/问答到 NAS，基于同步快照；验收＝关掉 Windows 后语音问答仍可用（用 NAS 快照）。接受"最近新增还没进快照"的延迟。
  - **3b（需产品取舍）**：飞书写入桥要么保留一台 Windows 常驻机专做入口，要么改造成飞书官方 API + 无头浏览器 + 授权持久化后迁 NAS。后者改动大、涉及授权续期与平台风控，不可与 3a 混为一谈。
- 验收（3a）：关掉 Windows 电脑，知识**检索/问答**仍可用；飞书新增视频暂不入库（明确告知这是 3b 范围）。

### 阶段 4 — 共进程共库（最深，最后做）

- 把 TokBrain 的检索/写入能力以库形式嵌进 Hermes 进程，取消 MCP 跨进程边界；两个运行时合一。
- 可选：统一数据库（把 agent 现有的文件态 SOUL.md/策略 JSON 与知识库合并到同一 SQLite 或至少统一访问层）。
- 验收：单进程启动即同时提供家居控制 + 知识问答；故障点减少、延迟下降。
- **风险最高**：动核心链路，必须有阶段 1-3 全部验收 + 完整回滚方案才启动。

## 4. 待确认/未验证（不假装知道）

- TokBrain 控制 MCP 的 33 个工具里，哪些适合语音触发、哪些必须网页确认——需逐个过一遍（本轮未展开）。
- 阶段 3 后端迁 NAS 的 Python 依赖、ffmpeg、百炼 API key 的 NAS 侧配置——需实测（Windows 上有 `.venv`，NAS 上目前只有 `bin/` 和只读快照）。
- 阶段 4 是否真要"共库"（把 agent 文件态并进 SQLite）——价值待评估，可能不如"共进程 + 逻辑共库"划算。
- 知识库的知识"如何指导融合"：本轮读到的是内容主题（职业知识），据此定产品定位；若你指的是更具体的某条规划，知识库检索源当时不可用（IMA/腾讯文档/网盘/会议 exchange_failed），需重连后再补。

## 5. 风险清单

| 风险 | 缓解 |
|---|---|
| 写坏唯一知识库 | 阶段 2 前做快照；写入走 control MCP 的证据/去重机制；先只读后写入 |
| 幻觉编造知识 | 保留 `evidence_state` 边界；回答必须带时间戳溯源；找不到就说没收录 |
| 职业知识私密性 | 融合后音箱会答出个人职业信息——确认语音场景下的播报边界（谁在场都能听到） |
| 两套运行时迁移中断服务 | 阶段 3 双跑验证后再切流；保留 Windows 版可回退 |
| 认证边界不一致 | 统一用 `xiaoqi-auth` 全局模型（已设计，见 `D:/agent/xiaoqi-auth/`） |
| 抖音平台风控（423/429 熔断 30 分钟） | 保留飞书人工确认闸；一条一投不批量；遇 `access_forbidden`/`risk_verification` 立刻停、不换链接试探 |
| 飞书授权过期（token 3h / refresh 7d） | `tb_sync_status` 先查 `feishu.connected`，false 让用户网页扫码续期，agent 不代劳、不硬走 |
| 双重导入破坏状态回写 | 禁止 agent 自调 `tb_import_links` 读表，统一由 Windows 桥独占读表与回写 |
| NAS 快照滞后主库 | 阶段 4 前接受只读延迟；问答对"刚加的还没进快照"如实说明 |
| 飞书/control token 明文（审查新发现） | TokBrain 仓库内 `.cmd`/`.env.bak` 含明文 token，若开源 TokBrain 须轮换并脱敏（见飞书链路审查 §5） |

## 6. 下一步（2026-11-23 更新）

阶段 1 **已完成并闭环**：不加任何 SOUL.md 规则，路由靠 MCP `INSTRUCTIONS` + `tools.tool_search.listing_max_tokens: 8000` 修好了——受控复验中 `mcp__tokbrain__tokbrain_search` 首次被真实调用，答出带时间戳的真实条目（证据见 `阶段1验证/阶段1路由修复-同步重启复验通过-20261123.md`）。

下一步是**阶段 2 写入闭环**，但它写的是唯一真源 `douyin_rag.db`，动手前必须先做可回滚快照并单独授权，不能顺手带过。
