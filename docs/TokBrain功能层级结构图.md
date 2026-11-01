# TokBrain 功能层级结构图

> 检阅时间：2026-08-29 ｜ **校订：2026-10-30（按当前代码重写，对应应用 v1.0.0 / schema v11 / API contract 7）**
> 检阅范围：`D:\code\TokBrain-main`（后端 + Web 前端）+ `bridge_feishu_obsidian.py` + `mcp_server/` + `doubaosave Skill` + 外部集成
> 架构：FastAPI 后端（`127.0.0.1:8001`）+ Next.js 前端（`127.0.0.1:3001`）+ SQLite 本地知识库（含 sqlite-vec 向量索引）+ 飞书多维表格 + Obsidian Vault + 只读 MCP 双通道

```
TokBrain 本地多模态知识库系统
│
├── ① Web 前端界面（Next.js 16 / React 19 / Tailwind 4）
│   │
│   ├── 主界面 "/"（4 个功能 Tab：导入 / 解析 / 知识库 / 设置）
│   │   │
│   │   ├── 【导入】ImportWorkspace
│   │   │   ├── 风险提示横幅（主动、低频、可中断原则）
│   │   │   ├── 今日处理 / 知识库概况（今日处理件数、今日 AI 用量、已入库、待入库）
│   │   │   ├── 本地视频导入 LocalVideoImporter
│   │   │   │   ├── 拖拽/选择 MP4/MOV/MKV/WebM（单文件 ≤1GB，每批 ≤10 个）
│   │   │   │   ├── 逐个顺序上传（一文件一作品，无并发上传）+ SHA-256 指纹去重
│   │   │   │   ├── 标题/描述/收藏夹编辑、权利声明勾选
│   │   │   │   └── 状态机：待上传→准备→上传中→可确认/已存在/失败
│   │   │   ├── 外部数据包导入 PackageVideoImporter
│   │   │   │   ├── 文件夹 / ZIP 两种模式（候选 ≤1000 文件，文件夹 ≤100 视频，总 ≤20GB，ZIP 只能 1 个）
│   │   │   │   ├── 识别 douyin_videos.db / JSON / CSV / 文件名中的抖音作品 ID
│   │   │   │   ├── 按批次持久化 + 断点续传 + 自动安全解压
│   │   │   │   └── 检测完成→逐条核对确认
│   │   │   ├── 公开作品链接导入（每行一条，预检不下载）
│   │   │   │   ├── 批量上限（默认 10/批）、每日上限（默认 150）
│   │   │   │   ├── 重复链接自动剔除（去重）
│   │   │   │   └── 只做预检，不会自动入库
│   │   │   └── 批量任务队列 JobProgress（进度、队列位次、安全停止）
│   │   │
│   │   ├── 【解析】ParseWorkspace（预检结果与确认都在这里，不在导入页）
│   │   │   ├── 解析进度面板
│   │   │   │   ├── 进度条 + 状态（排队/进行中/中断/完成/部分/失败）
│   │   │   │   ├── Worker 网格（链接预检固定 3 worker）、熔断暂停提示（403/429 后停 30 分钟）
│   │   │   │   └── 中断解析（安全停止）
│   │   │   ├── 预检结果列表
│   │   │   │   ├── 封面 + 状态标签（预检重复/已在库/可确认/需补件/失败/风控）
│   │   │   │   ├── 媒体处理策略标签（作者允许下载/仅字幕音频/仅基础信息/本地完整处理）
│   │   │   │   ├── 收藏夹归属选择、本地补件上传、删除预检结果
│   │   │   │   └── 保留多批次预检结果（刷新恢复）
│   │   │   ├── 确认入库流程
│   │   │   │   └── 选择全部可确认项 → 确认加入待入库 → 入库（创建 ingest 任务）
│   │   │   └── 模型配置（左：对话模型 / 右：音频模型；多配置档切换与激活）
│   │   │
│   │   ├── 【知识库】Library
│   │   │   ├── 状态分段导航：待处理 / 在库 / 待补件 / 异常 / 已归档（含计数）
│   │   │   ├── 收藏夹筛选（本地分组）+ 新建收藏夹 + 收藏夹专属总结提示词
│   │   │   ├── 作品卡片网格（封面、标题、作者、时长、摘要、标签、状态）
│   │   │   ├── 待补件视图
│   │   │   │   ├── 材料状态报告（音频/字幕/转写/视频/画面/OCR/图片 各轨道有效性）
│   │   │   │   ├── 材料缺口说明（缺完整视频/图片组等）
│   │   │   │   └── 上传完整视频/完整图片组（≤12 张）→ 自动重新处理
│   │   │   ├── 单作品操作：查看总结、开始入库、重新处理、恢复、永久删除、查看原作品
│   │   │   ├── 批量操作：加入收藏夹、批量入库、补齐/更新总结、导出到 Obsidian
│   │   │   ├── 导出到 Obsidian（manifest 生成 + 笔记导出 + 关键帧/图片存放）
│   │   │   │   └── 设置图片统一存放位置
│   │   │   └── 分页加载更多（返回时保留浏览位置）
│   │   │
│   │   ├── 【设置】Settings
│   │   │   ├── 主题选择：3 套内置（晚霞云海·默认 / 东方雾山 / 海洋晨光）+ 背景显现度
│   │   │   ├── 本地健康检查（数据库/音视频工具/后台协调器/敏感数据清理）
│   │   │   ├── 处理额度：每日媒体分钟、每日 AI Token、月度费用预警、画面参数
│   │   │   ├── AI 总结提示词编辑器（12,000 字符，一键恢复默认）
│   │   │   ├── 凭据管理：百炼 API Key、平台 Cookie、账单 AccessKey（DPAPI 加密）
│   │   │   ├── 账单：本地估算 vs 阿里云官方账单（刷新、延迟说明）
│   │   │   ├── 外部批量导入令牌（生成/轮换/撤销，明文仅显示一次）
│   │   │   └── 敏感凭据一键清理（遍历全部模型配置档）
│   │   │
│   │   └── 侧边栏飞书状态（非 Tab）
│   │       ├── 连接态指示（已连接 / 授权过期 / 检测中）
│   │       ├── 最近查询时间与行数（读 data/feishu_last_query.json）
│   │       └── 扫码续期入口（授权过期时出现，走 lark-cli 设备码流程）
│   │
│   └── 作品总结详情页 "/works/[id]"（WorkSummaryPage）
│       ├── 封面、标题、作者、收藏夹、一句话总结
│       ├── 总结标签 + 分段正文（Markdown/数学公式渲染）
│       ├── 相关图片画廊（本地资产）
│       └── 查看原作品、永久删除、返回知识库（保留浏览位置）
│
│   ⚠️ 没有【对话】页：网页端不提供问答入口，检索能力只经 MCP 对外提供（见 ⑦）。
│
├── ② 后端 API（FastAPI，仅本机 127.0.0.1:8001，TrustedHost + Origin 校验）
│   │
│   ├── /api/import-batches（链接预检批次）
│   │   ├── POST 创建预检批次 → GET 查询 → PATCH 编辑条目 → DELETE 删除条目
│   │   ├── POST /confirm 确认加入待入库 → POST /cancel 中断
│   │   └── POST /items/{id}/assets 上传补件
│   ├── /api/local-import-batches（本地视频上传导入：创建 / 补传视频）
│   ├── /api/package-import-batches（数据包导入：创建 / 上传文件 / analyze 分析）
│   ├── /api/integrations/v1（外部批量导入 API v1：Bearer 令牌、Idempotency-Key、rights_attested）
│   ├── /api/integrations/v1/inbox/items（入站收件：单批 ≤100 条原子校验，只入队不处理）
│   ├── /api/jobs（任务队列：list / detail / cancel / retry）
│   ├── /api/library（知识库，18 个端点）
│   │   ├── 收藏夹：列表 / 创建 / 更新 / 专属提示词
│   │   ├── 作品：分页列表（按状态/收藏夹/平台/类型/作者/来源/收藏夹 ID 过滤）、加入收藏夹
│   │   ├── 总结：创建总结任务 / 读取总结 / 封面 / 资产
│   │   ├── ingest 任务 / 重试 / 批量重试 / 补件 / 恢复 / 永久删除
│   │   ├── /works/{id}/location（详情页返回定位）
│   │   └── /obsidian/manifest（Obsidian 导出清单）
│   ├── /api/platforms（平台采集）
│   │   ├── POST /{platform}/handoff、GET /{platform}/status、POST /{platform}/pull（Cookie 握手与拉取）
│   │   └── /douyin/dom-collect 等（DOM 采集：走 doubaosave，Playwright）
│   ├── /api/settings（设置读写、用量汇总、官方账单刷新、integration-token、secrets）
│   ├── /api/model-profiles（多模型配置档：增删改 / 激活）
│   ├── /api/sync（同步总览 GET /overview；飞书续期 POST、GET /feishu/relogin）
│   ├── /api/system-health（聚合健康）+ /api/system-health/probes/{probe}
│   │   └── 4 个探针：database / media_runtime / coordinators / security_cleanup
│   └── /health（进程级健康，scope=process_only）+ "/" 服务索引
│
├── ③ 核心服务层 app/services
│   ├── 导入与素材
│   │   ├── import_queue ── 链接预检协调器（解析、去重、配额、熔断；固定 3 worker）
│   │   ├── f2_links ───── 链接解析（URL 归一、LID、公网 DNS 校验、下载权限判定；F2 为 legacy 兜底）
│   │   ├── downloader ─── 媒体下载（尊重作者下载权限、SSRF 防护、403/429 熔断）
│   │   ├── local_assets ─ 本地视频校验与落盘（魔数/容器/视频轨/时长/SHA-256）
│   │   ├── package_imports ─ 数据包导入（ZIP 安全解包、元数据识别、视频匹配）
│   │   └── platforms/ ─── 平台采集（douyin 自研签名、bilibili、xiaohongshu、cookies）
│   ├── 处理流水线
│   │   ├── content_pipeline ─ 主链路：字幕优先→ASR 兜底→关键帧候选→OCR/视觉描述→选帧→总结→分块→嵌入
│   │   ├── jobs ───────── 任务协调器（ingest / summarize / 失败恢复 / 用量结算；单 worker）
│   │   ├── keyframes ──── 关键帧提取（场景检测 + 均匀采样混合候选池、感知哈希去重）
│   │   ├── summaries ──── AI 总结（结构化 JSON → 分节 Markdown + frontmatter）
│   │   ├── providers/ ─── 模型供应商客户端（DashScope 百炼；另含 OpenAI 兼容实现）
│   │   ├── prompts / collection_prompts ─ 提示词管理与收藏夹专属规则
│   │   └── model_profiles ─ 多模型配置档（各档独立密钥与能力声明）
│   ├── 检索
│   │   ├── vector_store ─ sqlite-vec vec0 虚表、KNN 候选、按作品替换向量
│   │   └── knowledge ──── 混合检索（语义 + 词法加权，向量不可用时自动降级纯词法）
│   ├── 同步与桥接
│   │   ├── dispatch_store ─ 分发队列（data/pipeline/dispatch.db，状态机）
│   │   ├── sync_status ─── 同步总览（真查一次飞书表并落盘）
│   │   ├── feishu_auth ─── 飞书扫码续期（驱动 lark-cli 设备码流程，产出二维码）
│   │   └── doubaosave_bridge ─ 子进程调用 doubaosave Skill（Playwright DOM 采集）
│   └── 支撑
│       ├── budget / pricing / bss_bill ─ 预算预留-结算-回收、模型价格估算、官方账单
│       ├── secrets ─────── 凭据加密存储（Windows DPAPI，非 Windows 不可用）
│       ├── runtime_settings / health / migrations / temp_files / errors
│       └── import_integrations ─ 外部导入 v1 的批次/幂等/权利声明逻辑
│
├── ④ 数据模型与存储（SQLite + 文件系统）
│   ├── douyin_rag.db ── AppSetting / SecretRecord / Collection / Work / Job / ImportBatch / ImportItem / UsageEvent…
│   ├── chunk_vec ───── sqlite-vec vec0 向量虚表（+4 张 shadow 表，勿手工写）
│   ├── data/keyframes ── 关键帧（唯一实体，Vault 侧硬链接镜像）
│   ├── data/media ────── 图文/OCR 资产
│   ├── data/tmp ──────── 临时媒体（启动自动清理）
│   ├── data/source-assets ── 本地源文件
│   ├── data/package-imports ── 数据包暂存
│   ├── data/backups ───── schema 迁移前自动备份
│   └── data/pipeline/dispatch.db ── 分发队列与状态机（已实施并在跑）
│
├── ⑤ 桥接与自动化脚本
│   ├── bridge_feishu_obsidian.py（**1822 行**）── 飞书→TokBrain→Obsidian 主桥接
│   │   ├── TokBrain 客户端封装（预检/确认/ingest/外部批次/manifest/下载资产）
│   │   ├── Feishu 客户端封装（record-list、字段、增量行、状态回写）
│   │   ├── Obsidian 笔记生成（快轨占位笔记秒级可见、慢轨正文回填、frontmatter、标签目录）
│   │   ├── 硬链接工具（关键帧镜像 + 孤儿链接清理）
│   │   └── 队列处理（--watch 常驻轮询 / --retry <record_id> 人工重投；幂等 record_id + aweme_id）
│   ├── 启动飞书同步.cmd ── 内置飞书表参数 + VAULT_PATH，以 --watch 常驻（独立进程，不随后端启动）
│   ├── scripts/push_to_tokbrain.py ── 命令行推送（--attest-rights 权利声明）
│   ├── scripts/audit_library.py ── 知识库只读审计
│   ├── scripts/export_tokbrain_snapshot.py ── SQLite 一致快照 + integrity_check + sha256 清单
│   ├── scripts/deploy_tokbrain_snapshot.ps1 / install_tokbrain_hermes_nas.py / probe_*.py ── NAS 部署与探活
│   ├── start.ps1 / stop.ps1 / setup.ps1 / 启动.cmd / 停止.cmd / 重启.cmd
│   └── doubaosave Skill（抖音数据采集）
│       ├── main.py ── Playwright 采集（喜欢/收藏含子分类/观看历史，API 拦截）
│       └── import_to_bitable.py ── 采集结果批量导入飞书多维表格（去重、分批 ≤200）
│
├── ⑥ 外部系统集成
│   ├── 抖音平台（作品链接、作者下载权限；主路径为自研签名解析，f2 为 legacy 兜底）
│   ├── 小红书 / B 站（粘贴链接为主，小红书含 Cookie 出站白名单校验）
│   ├── 飞书多维表格（obsidian知识库收件箱 → 视频收藏表；含处理状态/work_id/LID/最后修改时间列）
│   ├── Obsidian Vault（D:\ObsidianVault：00收件箱\视频收藏 + 99附件硬链接镜像）
│   ├── 阿里云百炼 DashScope（OCR / ASR / 视觉描述 / 选帧 / 总结 / 嵌入；**不含问答生成与 rerank**）
│   └── 阿里云 BSS 账单（官方费用核对；需可选依赖与只读 AccessKey）
│
└── ⑦ 知识检索出口（只读 MCP 双通道，均无写入工具）
    ├── 本机网络 MCP ── mcp_server/tokbrain_mcp.py
    │   ├── 默认 127.0.0.1:8010；绑非回环地址必须配 TOKBRAIN_MCP_TOKEN，否则拒绝启动
    │   ├── Bearer 校验中间件 + readOnlyHint
    │   └── 工具：kb_search / kb_get_work / kb_get_transcript / kb_list_collections / kb_stats
    └── Hermes stdio MCP ── mcp_server/tokbrain_hermes_stdio.py
        ├── NAS 上以 mode=ro&immutable=1 打开只读快照，不开任何网络端口
        └── 工具：tokbrain_search / tokbrain_get_work / tokbrain_get_transcript / tokbrain_list_collections / tokbrain_stats
```

---

## 与 2026-08-29 版本的差异（校订说明）

| 原描述 | 现状 |
|---|---|
| 有【对话】Chat 页，快速/深度双模式 + SSE 流式 | **不存在**。后端无 `/api/chat`、无 SSE；`providers/base.py` 的 `chat()`/`chat_stream()` 无任何调用方。检索只经 MCP |
| `/api/chat（search / ask / ask/stream）` | 已删除该行，替换为 MCP 双通道章节 ⑦ |
| 设置页含模型配置 | 模型配置在【解析】页；设置页只留额度、提示词、凭据、令牌、健康检查 |
| 本地视频"2 路并发上传" | 逐个顺序上传 |
| 解析进度含"自动确认"状态 | 自动确认已移除，确认必须人工点击 |
| `/api/inbox/items` | `/api/integrations/v1/inbox/items` |
| `/health/probes` | `/api/system-health` + `/api/system-health/probes/{probe}` |
| `bridge_feishu_obsidian.py（1153 行）` | 1822 行，新增 `--watch` / `--retry` 与快轨占位、孤儿清理 |
| DashScope"对话/重排" | 无问答生成、无 rerank；实际用于 OCR/ASR/视觉/选帧/总结/嵌入 |
| 流水线 v2"设计定稿，待排期" | P0–P5 全部已实施并在跑（`dispatch.db`、入站 API、快慢轨、硬链接、状态回写） |
| 主题"多主题" | 3 套：`sunset-cloudsea`（默认）/ `eastern-mist` / `ocean-dawn` |
