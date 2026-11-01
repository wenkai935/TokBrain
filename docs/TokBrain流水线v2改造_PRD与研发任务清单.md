# TokBrain 飞书→Obsidian 流水线 v2 改造 PRD 与研发任务清单

> **时效声明（2026-10-30 校订）**：本文所列 P0–P5 均已落地，任务清单转为验收对照表；剩余事项是评审与回归，不再是"待开发"。

> 文档类型：PRD（需求文档）+ 研发任务拆解
> 依据：本地代码 `D:\code\TokBrain-main`（`app/routers_v2/inbox.py`、`app/services/dispatch_store.py`、`bridge_feishu_obsidian.py`、`app/main.py`）
> 当前落地状态：**已实施**——后端路由已挂载、桥接进程常驻（`启动飞书同步.cmd`），剩余收尾/测试/评审

---

## 一、背景

v1 的"飞书收藏收件箱 → TokBrain 深度处理（转写/抽帧/OCR/总结）→ Obsidian 笔记"是一条**脚本串行**流水线：用户把抖音链接丢进飞书多维表格后，必须等后端整段处理完，Obsidian 才出现笔记；处理中无法感知进度，队列与飞书状态也容易漂移。

v2 的目标是把这条链路升级为**可并发、可中断、可对外接入**的工程化流水线：收件即落占位笔记（秒级），慢速处理在后台跑完自动回填；飞书行、本地 `inbox.json`、HTTP 入站三路来源走**同一状态机**，杜绝双份状态。

## 二、目标

| 维度 | 目标 |
|------|------|
| 时效 | 收件到 Obsidian 可见占位笔记 **≤ 数秒**（双轨解耦） |
| 一致 | 飞书/本地/HTTP 三路来源共享单一调度队列，状态不漂移 |
| 可接入 | 提供对外入站 API，支持幂等、批量、合规前置校验 |
| 可观测 | 飞书「处理状态」列实时回写；仅状态变化时回写省额度 |
| 合规 | 全链路遵循"主动、低频、可中断"，不扫描账号/收藏夹、不绕过下载权限 |

## 三、用户故事

- **作为** 知识库使用者，我**希望** 在飞书里丢一条抖音链接后几秒内就能在 Obsidian 看到占位笔记，**以便** 尽早确认收录、不打断心流。
- **作为** 入库维护者，我**希望** 处理进度自动回写到飞书状态列，**以便** 不用反复打开后端看日志。
- **作为** 系统集成方，我**希望** 通过 HTTP API 批量推送链接且支持幂等重放，**以便** 我的脚本可以安全重试。
- **作为** 合规负责人，我**希望** 未声明"有权处理"的链接被直接拒绝，**以便** 守住版权与平台规则红线。
- **作为** 研发，我**希望** 飞书/本地/API 三路走同一队列，**以便** 状态逻辑只维护一份。

## 四、功能清单（模块）

| 模块 | 功能点 | 代码落点 |
|------|--------|----------|
| 入站 API | HTTP 批量入站、Bearer 鉴权、合规前置、幂等、批量上限、逐条容错、URL 规范化去重 | `app/routers_v2/inbox.py` |
| 共享调度队列 | 单一事实源、状态机、适配器注册、幂等表、旧库 backfill | `app/services/dispatch_store.py` |
| 双轨引擎 | 快轨占位笔记、慢轨处理回填、run_once 编排、双轨一致性兜底 | `bridge_feishu_obsidian.py` |
| 存储与关键帧 | 硬链接优先+降级、关键帧筛选、孤儿链接回收 | `bridge_feishu_obsidian.py` |
| 飞书同步 | 60s 增量轮询、状态回写映射、仅变化回写、同步状态持久化 | `bridge_feishu_obsidian.py` |
| 路由挂载 | routers_v2 全组 include 进主应用 | `app/main.py` |

## 五、核心流程说明

```
外部来源(飞书多维表格 / 本地 inbox.json / HTTP 入站 API)
        │
        ▼
[入站 API]  app/routers_v2/inbox.py   （规范化 + 合规校验 + 幂等 + 去重）
        │
        ▼
[共享 Dispatch 队列]  app/services/dispatch_store.py   （状态机单一事实源）
   pending → queued → processing → {processed | needs_supplement | failed}
        │
        ▼
[双轨引擎]  bridge_feishu_obsidian.py run_once()
   快轨: 写占位笔记(秒级)  ＋  慢轨: 处理+回填(分钟级)
        │                         │
        ▼                         ▼
   Obsidian 占位笔记          TokBrain 处理 → 回填正文+关键帧
        │                         │
        └──── 60s 增量轮询 ───────┘
                              │
                              ▼
              回写飞书「处理状态」列 + 关键帧硬链接入库
```

## 六、交互说明

- **Obsidian 侧**：收件即生成 `record_id.md` 占位笔记（frontmatter `status: pending` + "⏳ 正在处理"提示）；慢轨完成后回填正文与关键帧，frontmatter 推进为 `processed`。
- **飞书侧**：不改动用户表结构，仅在「处理状态」列回写；用本地 `同步状态.json` 记录已同步 record_id。
- **CLI 侧**：`bridge_feishu_obsidian.py` 支持 `--watch` 常驻轮询模式。

## 七、需求描述（EARS 原则）

**Ubiquitous（始终满足）**
- 系统应始终通过 `normalized_key`（sha256）对同一链接去重，避免重复入队。
- 系统应始终仅向真实飞书 `record_id` 回写状态，跳过 `local-*` / `inbox-*` 来源。

**Event-driven（事件触发）**
- 当入站 API 收到批量请求时，系统应校验 `rights_attested` 与 `Idempotency-Key` 后进行规范化入队。
- 当 `run_once` 被执行时，系统应完成一轮"快轨收件归档 + 慢轨回填"。
- 当飞书行最后修改时间进入增量窗口时，系统应拉取并更新对应 dispatch 记录。

**Unwanted（异常/故障）**
- 若 `rights_attested` 不为 `True`，则系统应拒绝该批并返回 `400`。
- 若单批链接数 > 100，则系统应整批原子拒绝。
- 若某链接非法（非白名单主机/无法规范化），则系统应将其标记 `rejected` 且不影响同批合法链接入队。
- 若关键帧硬链接失败，则系统应降级为 symlink → 实体复制，保证功能不中断。
- 若飞书状态回写失败，则系统应仅记日志、下轮重试，不阻塞主流程。

**State-driven（状态相关）**
- 当 dispatch 记录处于 `needs_supplement`（作者未开放下载）时，系统应将其视为等待人工的终态，不自动重投。
- 当 dispatch 记录处于 `processing` 时，系统应将飞书状态映射为"处理中"。

**Optional（可选）**
- 当启用 `--watch` 模式时，系统应按 `POLL_CYCLE=60s` 周期常驻轮询。

## 八、边界场景与异常状态

| 场景 | 预期行为 |
|------|----------|
| 重复投递同一链接（不同来源） | 同一 `normalized_key` 去重，仅一条 pending |
| 幂等键重放（相同清单） | 返回首次入库结果，不重复创建 |
| 幂等键重放（不同清单） | 返回 `409` 冲突 |
| 作者关闭下载权限 | 状态置 `needs_supplement`，等待人工补件 |
| 飞书/本机时钟偏差 | `CURSOR_SKEW_SECONDS=300` 安全边界防漏行 |
| 增量拉取退化为全量 | 按 `record_id` 二次去重仍正确 |
| 跨卷/非 NTFS 环境 | 硬链接降级 symlink/copy，关键帧仍可用 |
| 作品在 TokBrain 被删 | `prune_orphan_keyframe_links` 回收孤儿硬链接 |

## 九、权限规则与合规红线

- **鉴权**：入站 API 使用 Bearer integration token（与 v1 外部批次 API 同套）。
- **合规前置**：`rights_attested` 必须确认"有权处理"；抖音链接走 HTTPS-only 规范化（主机白名单 + 去追踪参数）。
- **不越界**：不扫描账号/收藏夹；不绕过下载权限；单批上限 + 逐条拒绝非法链接。
- **可中断**：低频轮询（60s）、状态回写仅变化触发，尊重飞书接口额度。

## 十、数据指标（埋点/口径）

| 指标 | 口径 |
|------|------|
| 占位笔记延迟 | 飞书收件 → Obsidian 占位出现的时间差 |
| 回填完成时长 | pending → processed 中位耗时 |
| 入站拒绝率 | 被 `rejected` 链接 / 总投递链接 |
| 幂等命中率 | 重放返回首结果的请求占比 |
| 状态回写次数 | 仅"状态变化"触发计数（应远小于处理条数） |
| 需补件占比 | `needs_supplement` / 总入站 |
| 硬链接降级率 | 硬链接失败降级次数 / 总关键帧数 |

## 十一、验收标准

- [ ] 三路来源（飞书/本地/HTTP）共享同一 dispatch 队列，状态不漂移
- [ ] 入站 API 通过 TC-P4-01~12 用例（双轨一致、批量上限、去重、幂等冲突、合规红线、逐条拒绝、适配器扩展、双轨兜底等）
- [ ] 飞书回写通过 TC-P3-06/07/10（失败不阻塞、仅变化回写、跳过本地/inbox 来源）
- [ ] 双轨：占位笔记秒级可见，处理后自动回填且 frontmatter 推进
- [ ] 关键帧硬链接在 NTFS 下同实体零额外占盘；跨卷降级不中断
- [ ] 增量轮询 60s 实时、时钟偏差不漏行、不改动飞书表结构

---

## 十二、研发任务清单

> 负责人为**建议分配**，最终以团队排期为准；括号内为对应源码/测试线索。

### Epic 1：入站接入层（inbox API）
- **T1.1** 入站端点 `POST /api/integrations/v1/inbox/items` 与 Bearer 鉴权 — `inbox.py`（依赖：T2.1）
- **T1.2** 合规前置校验：`rights_attested` 强制、抖音 HTTPS 规范化（主机白名单+去参） — TC-P4-06
- **T1.3** 幂等与批量容错：强制 `Idempotency-Key`、单批 ≤100、非法链接逐条 `rejected` — TC-P4-03/05/07
- **T1.4** 适配器注册与去重：`douyin_share`/`local_text` 归一化 + `normalized_key`(sha256) — TC-P4-04/08/11

### Epic 2：共享调度队列（dispatch_store）
- **T2.1** `DispatchStore` 状态机 `pending→queued→processing→{processed|needs_supplement|failed}` — `dispatch_store.py`
- **T2.2** 旧库 backfill：对老 bridge 表 `ALTER TABLE` 补 `source`/`normalized_key`
- **T2.3** 适配器扩展点 `ADAPTERS` 注册表，新来源零改动接入 — TC-P4-08

### Epic 3：双轨引擎（bridge）
- **T3.1** 快轨 `write_placeholder_note`：`record_id.md` 占位 + `status:pending` — TC-P4-01
- **T3.2** 慢轨 `process_queue`→`backfill_note`：处理→回填正文+关键帧→`processed`
- **T3.3** `run_once` 编排 + 为 inbox API 直接写的 pending 条目补建占位（双轨一致性兜底） — TC-P4-12

### Epic 4：存储与关键帧
- **T4.1** `hardlink_into_vault` 硬链接优先 + symlink/copy 降级 — 跨卷兼容
- **T4.2** `gather_keyframes` 按 `KEYFRAME_MIN_SCORE` 筛选，控量
- **T4.3** `prune_orphan_keyframe_links` 回收孤儿硬链接

### Epic 5：飞书同步
- **T5.1** 增量轮询 `get_updated_rows`（POLL_CYCLE=60s、`CURSOR_SKEW_SECONDS=300`、全量退化去重）
- **T5.2** `writeback_feishu_status` 仅真实 record_id、仅变化回写、失败仅日志 — TC-P3-06/07/10
- **T5.3** 同步状态持久化 `同步状态.json`（不改动飞书表结构）

### Epic 6：测试与验收
- **T6.1** 落地 P3/P4 用例（代码注释已引用 TC-P3-* / TC-P4-*，需对齐《流水线v2改造测试用例》）
- **T6.2** 端到端联调：飞书→占位→回填→状态回写 全链路走通 + P5 回归

### 依赖关系
```
T2.1 ──► T1.1 ──► T1.2/T1.3/T1.4
T2.1 ──► T3.1/T3.2/T3.3
T3.1/T3.2 ──► T3.3 ──► T6.2
T4.1/T4.2/T4.3（支撑 T3.2）
T2.1 ──► T5.1/T5.2/T5.3 ──► T6.2
T1.4/T2.3（横向扩展点，可并行）
```

---

## 后续流转提醒

- 本 PRD 可作为**评审材料**，建议上传至项目资料库形成统一上下文。
- 建议按上述 Epic 在 TAPD/CNB 创建事项，分配给技术负责人，并将本文附为需求交付物。
- 测试同学可认领 Epic 6，并一并拉取云端《流水线v2改造测试用例》《P3-P4-P5测试用例》对齐 TC 编号。
- 设计师在本 v2 改造中无强交互改动（界面仍为 Obsidian/飞书），可仅作为关注人。
