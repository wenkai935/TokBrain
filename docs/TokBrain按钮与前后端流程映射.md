# TokBrain 前端→后端 全流程与按钮级映射（全量）

> 生成日期：2026-10-31
> 数据来源：合并版 PRD（`docs/TokBrain产品需求文档_合并版.md`）+ 实际代码（`frontend/lib/api.ts` 契约 + 各 `components/*.tsx` 按钮处理器）。
> 说明：本表以「真实代码」为准，凡 PRD 描述但前端未接线之处，单独标为 **[漂移]**。

---

## 0. 一句话结论

- **架构**：前端 Next.js 16 / React 19（端口 3001）→ 后端 FastAPI（127.0.0.1:8001）→ 服务层（导入/处理流水线/检索/桥接）→ SQLite + 文件系统。
- **唯一 API 契约**：`frontend/lib/api.ts`，所有按钮最终都经此文件调用后端；前端**无任何按钮直接 fetch 其他路径**。
- **⚠️ 重大漂移**：PRD §3.2「外部数据包导入」、§3.3「公开作品链接导入」的接口已在 `api.ts` 定义（`createPackageImportBatch` / `createImportBatch`），但**前端 0 处调用**——当前真实可用的导入入口只有「本地视频」与「抖音平台采集/同步」两路。

---

## 1. 完整前后端全流程（PRD 定义 + 代码事实）

```
[前端四页签]                         [后端 API]                       [服务层]                         [存储]
导入页 ──本地视频──► POST /local-import-batches
        └─抖音采集──► POST /platforms/douyin/dom-collect
                     GET  /platforms/douyin/dom-collect/{id}  ──► f2_links / platforms/* ──► douyin_rag.db
                     GET  /platforms/douyin/collected
                     POST /platforms/douyin/sync-selected ──► import_queue（3 worker 预检协调器）
        └─(未接线)公开链接 ──► POST /import-batches          [漂移]
        └─(未接线)数据包   ──► POST /package-import-batches   [漂移]
解析页 ──确认入库──► POST /import-batches/{id}/confirm ──► 创建 ingest 任务（job）
        └─删除预检──► DELETE /import-items/{id}
        └─安全停止──► POST /import-batches/{id}/cancel
        └─重试失败──► POST /jobs/retry
知识库 ──入库/总结──► POST /library/ingest|summaries/jobs
        ──补件────► POST /library/works/{id}/supplement
        ──恢复────► POST /library/works/{id}/restore
        ──重处理──► POST /library/works/{id}/retry
        ──永久删除► DELETE /library/works/{id}
        ──导出────► POST /library/obsidian/manifest
        ──收藏夹──► /library/collections (+/works)
设置页 ──保存────► PUT /api/settings   ──► runtime_settings（可持久化、可校验）
        ──模型────► /api/model-profiles (GET/POST/PUT/DELETE/activate)
        ──令牌────► /api/settings/integration-token (GET/POST/DELETE)
        ──凭据清理► DELETE /api/settings/secrets
桥接流水线（独立子系统，非前端按钮触发）:
  飞书/本地inbox/HTTP入站 ──► POST /integrations/v1/inbox/items ──► dispatch_store 状态机
  ──► 双轨引擎(占位笔记+回填) ──► Obsidian / TokBrain 处理
  ──► 60s 轮询飞书状态回写
```

**状态机（贯穿点击后状态流转）**
- 批次 `batch.state`：`queued→running→automating→cancelling→{succeeded|partial|failed|cancelled}`
- 任务 `job.state`：同上 + `await_fix`（待补件，终态）
- 条目 `item.status`（解析页）：激活 `queued/resolving`；可确认 `ready`；收尾 `needs_local_file/confirmed/failed/blocked/cancelled/duplicate`
- 桥接 `dispatch`：`pending→queued→processing→{processed|needs_supplement|failed}`

---

## 2. 按钮级映射表

列说明：**默认值** = 该控件初始化/无操作时的取值；**异常值** = 禁用态、报错文案、或失败后的状态；**点击触发流程** = 调用的 `api.*`（→ 后端接口）→ 是否发生页面跳转/刷新。

### 2.1 全局导航（`app/page.tsx`）

| 按钮 | 默认值 | 异常值 | 点击触发流程 |
|---|---|---|---|
| 顶部 Tab：导入/解析/知识库/设置 | 默认 `import` | 无 | `setTab(...)` 仅切 React 视图，**不发请求、不换路由**；hash 可定位 |
| 飞书「一键续期」 | idle | `reloginBusy` 时禁用显示「发起中…」 | `api.feishuReloginStart()` → `POST /api/sync/feishu/relogin` → 弹窗轮询 `feishuReloginState()`（GET 同路径）→ 成功自动关闭 |
| 续期弹窗「关闭 / 重新发起」 | — | failed 态显示错误文案 | 关闭=`setRelogin(null)`；重新发起=再调 `feishuReloginStart` |
| 续期弹窗「×」关闭 | — | — | `setRelogin(null)` |
| 成功/错误 Toast「×」 | — | — | 清除对应提示 |
| 导入完成后的「查看解析进度」(平台批次) | 取 `lastBatch.id` | 无 | `onPlatformBatchReady` → `api.importBatch(id)` → `setTab("parse")` 跳解析页 |
| 任务「安全停止」(job) | — | `state==="cancelling"` 禁用 | `api.cancelJob(id)` → `POST /api/jobs/{id}/cancel` → 刷新任务列表 |

### 2.2 导入页（`ImportWorkspace.tsx`）

| 按钮 | 默认值 | 异常值 | 点击触发流程 |
|---|---|---|---|
| 本地视频：拖拽/点击上传区 | 空列表 | 单文件 >1GB 或类型不符 → 该草稿 `error` 标记 | `fileInputRef.click()` → 选择后 `addDrafts` |
| 本地视频：移除草稿「×」 | — | — | `remove(id)` 释放预览 blob |
| 本地视频：确认上传 | `disabled` 当 `!validCount \|\| uploading` | 无有效文件→`notify("请先拖入或选择文件")`；超 `LOCAL_VIDEO_LIMIT=10`→`每批最多10个，本次仅加入前N个` | `createLocalImportBatch({rights_attested:true, items})` → `POST /api/local-import-batches` → 逐条 `uploadLocalImportVideo` → `importBatch(id)` → `onBatchReady` → **跳解析页 `setTab("parse")`**；失败保留草稿并 `setError(reason)` |
| 抖音平台 Tab 切换 | 首个平台 | — | `setTab(platform.key)` 仅视图切换 |
| 平台「连接」 | — | — | `openLogin()` → 打开浏览器完成授权（走 `POST /api/settings/integration-token` 登录态，见 §2.5） |
| 范围标签（收藏/喜欢/我的作品/观看历史/读取失败） | 默认某 scope | `scope==="fail"`（读取失败）时同步按钮禁用 | `setScopeByTab` 仅视图切换；之后 `collectedVideos(scope)` 拉预览 |
| 子分类切换 | — | — | `selectSubcat` 视图切换 |
| 全选当前范围 | 全不选 | — | `toggleSelectAll` 维护 `selAll` 计数 |
| 「采集」 | 显示「采集」/「采集中…」 | `domReady=false` 显示「待接入」；超时→`采集超时` | `api.domCollect(scope)` → `POST /api/platforms/douyin/dom-collect` → 轮询 `domCollectStatus(taskId)`（GET）→ 渲染 `collectedVideos(scope)` |
| 视频单选/勾选 | 不选 | — | `toggleVideo(aweme_id)` 累计 `selAll` |
| 「同步当前 N 条」 | `disabled` 当 `selAll===0 \|\| scope==="fail"` | 失败→`notify("同步失败")` | `api.syncSelected(scope, ids)` → `POST /api/platforms/douyin/sync-selected`（含 `enqueue:true` 入解析管线）→ 跳解析页 |
| 上次批次「查看解析进度」 | 取 `lastBatch` | — | `onPlatformBatchReady(lastBatch.id)` → 跳解析页 |
| 批量任务队列：刷新 | — | `loading` 时按钮转圈 | `load()` → `api.jobs()`（GET /api/jobs）|
| 批量任务队列：展开/收起 | 展开 | — | `setCollapsed` 切换 |
| 批量任务队列：清除已完成 | — | — | `clearCompleted()` 仅前端过滤 |
| 批量任务队列：重试失败导入「已重试全部失败导入」 | — | — | `api.retryFailedImports()` → `POST /api/jobs/retry` → 刷新列表 |
| 批量任务队列：移除任务「×」 | — | — | `dismissJob(id)` 仅前端移除展示 |
| 批量任务队列：重置隐藏 | — | — | `setDismissed(new Set())` |

> **[漂移]** PRD §3.2「外部数据包导入」、§3.3「公开作品链接导入」在 `api.ts` 已有 `createPackageImportBatch` / `createImportBatch` 等接口，但 **ImportWorkspace 内无任何文本框或按钮调用** → 这两路当前在前端不可用。

### 2.3 解析页（`ParseWorkspace.tsx`，处理器由 `app/page.tsx` 注入）

| 按钮 | 默认值 | 异常值 | 点击触发流程 |
|---|---|---|---|
| 全选可确认 | 空 | — | `setSelected(all selectable ids)` |
| 「确认加入待入库（N）」 | 选中可见可确认项 | 无可见可确认→按钮隐含禁用 | `onConfirm(ids)` → `api.confirmImportBatch(id, items)` → `POST /api/import-batches/{id}/confirm` → 创建 ingest 任务 |
| 单条「删除预检结果」 | — | — | `onDelete(item)` → `api.removeImportItem(item.id)` → `DELETE /api/import-items/{id}` → 刷新批次 |
| 预检结果卡片单选 | 不选 | — | `toggle(item.id)` 维护选中集 |
| 分页「‹ / › / 数字」 | 第 0 页 | 首页禁用「‹」、末页禁用「›」 | `setPage` 仅视图切换 |
| 「安全停止 / 暂停」 | — | `busy` 时禁用 | `onCancel` → `api.cancelImportBatch(id)` → `POST /api/import-batches/{id}/cancel` → 刷新 |
| 「重试失败导入」 | — | `busy` 时禁用 | `onRetryFailedImports` → `api.retryFailedImports()` → `POST /api/jobs/retry` → 刷新 |
| 模型配置：保存并设为当前 | `disabled` 当 `!processingModel \|\| saving` | — | `api.saveSettings({processing_model})` → `PUT /api/settings` → 刷新设置视图 |

### 2.4 知识库页（`Library.tsx`）

| 按钮 | 默认值 | 异常值 | 点击触发流程 |
|---|---|---|---|
| 状态分段导航：待处理/在库/待补件/异常/已归档 | 默认 `in_library` | 计数为 0 仍可点 | `setState` → `api.works(state)` → `GET /api/library/works?library_state=...` 刷新网格 |
| 筛选 pill「×」移除 | — | — | `pill.clear()` 移除筛选条件 → 重新拉取 |
| 新建收藏夹：创建/取消 | 空标题禁用「创建」 | `!newCollectionTitle.trim()` 禁用 | `createCollection` → `api.createCollection(title)` → `POST /api/library/collections` |
| 设置图片统一存放位置 | — | — | `chooseImageFolder()` 调系统选择目录 |
| 新建收藏夹触发 | — | — | `setCreatingCollection` 切换表单 |
| 选择当前页/取消选择 | 空选集 | — | `setSelected(page items / empty)` |
| 收藏夹专属提示词：使用全局/保存 | 空禁用保存 | `!collectionPromptDraft.trim()` 禁用 | `updateCollectionSummaryPrompt` → `PUT /api/library/collections/{id}` |
| 作品标题（已入库且总结就绪） | — | 否则纯文本不可点 | `<a href="/works/{id}">` → **真实路由跳转**到作品总结详情页 |
| 作品「查看公开原作品」 | — | 无 `source_url` 不显示 | `<a target="_blank">` 新标签打开原视频 |
| 作品「开始入库」(待处理态) | — | — | `api.retry(id)` → `POST /api/library/works/{id}/retry`（创建入库任务）→ 刷新 |
| 作品「重新处理」(异常态) | — | — | 同上 `api.retry(id)` |
| 作品「恢复」(已归档态) | — | — | `api.restore(id)` → `POST .../restore` → 刷新 |
| 作品「永久删除」 | — | 弹 `window.confirm` 二次确认 | `api.remove(id)` → `DELETE /api/library/works/{id}` → 刷新 |
| 待补件：上传完整视频/图片组 | 空 | — | `uploadWorkSupplement` → `POST /api/library/works/{id}/supplement`（`rights_attested=true`，多文件 ≤12 张）→ 自动重新处理 |
| 加载更多 | `disabled` 当 `loading` | 无更多则按钮不渲染 | `loadMore()` → `api.works(state, {offset})` 分页追加 |
| 批量：加入收藏夹 | `disabled` 当 `!targetCollectionId` | — | `api.addWorksToCollection` → `POST /api/library/collections/{id}/works` |
| 批量：批量开始入库（待处理） | — | 仅 `pending` 态显示 | `api.ingest([...selected])` → `POST /api/library/ingest/jobs` |
| 批量：补齐/更新总结（在库） | — | 仅 `in_library` 显示 | `api.summarize([...selected])` → `POST /api/library/summaries/jobs` |
| 批量：导出到 Obsidian（在库） | — | 仅 `in_library` 显示 | `api.obsidianManifest([...selected])` → `POST /api/library/obsidian/manifest` → 触发 Obsidian 写入 |

### 2.5 设置页（`Settings.tsx`）

| 按钮 | 默认值 | 异常值 | 点击触发流程 |
|---|---|---|---|
| 主题选择（3 套：晚霞云海/东方雾山/海洋晨光） | 默认 `sunset-cloudsea`（晚霞云海） | — | `setTheme(item.id)` → 即时换肤（前端状态 + 持久化），**不发后端请求** |
| 模型：设为当前 | — | `busyKey` 占用时禁用 | `activateModelProfile(id)` → `POST /api/model-profiles/{id}/activate` |
| 模型：编辑 | — | — | `startEdit(profile)` 打开表单 |
| 模型：删除 | — | 占用时禁用 | `deleteModelProfile(id)` → `DELETE /api/model-profiles/{id}`（**二次确认**由 `perform` 包裹）|
| 模型：接入新模型 | — | `draft!==null` 禁用 | `openCreate()` 打开表单 |
| 模型表单：保存（提交） | `disabled` 当 `!name.trim() \|\| busyKey` | — | 新建→`createModelProfile` / 编辑→`updateModelProfile` → `POST`/`PUT /api/model-profiles` |
| 模型表单：取消 | — | — | `setDraft(null)` 关闭表单 |
| 本地健康检查：运行检测 | `disabled` 当 `checking \|\| busy` | — | `runHealthDetection` → `api.healthProbe(name)` → `GET /api/system-health/probes/{name}` |
| 处理额度下拉（processing_model / chat_fast_model / chat_deep_model / default_answer_format） | `defaultValue={settings.xxx}`（后端当前值；默认值见 `config.py`） | — | 随「保存设置」一起 `PUT /api/settings` 提交 |
| AI 总结提示词：一键恢复默认 / 保存专属 | 空禁用保存 | `!summaryPromptDraft.trim()` 禁用 | 恢复=`saveSettings({summary_prompt: DEFAULT})`；保存=`saveSummaryPrompt` → `PUT /api/settings` |
| 平台 Cookie：保存 Cookie | — | — | `saveF2Cookie` → `api.saveSettings({f2_cookie})` → `PUT /api/settings` |
| 账单凭据：保存 | — | — | `saveBillingCredentials` → `api.saveSettings({bss_access_key_id, bss_access_key_secret})` |
| 官方账单：刷新 | `disabled` 当 `busy` | — | `api.refreshOfficialBill()` → `POST /api/settings/official-bill/refresh` |
| 外部令牌：复制令牌 | — | — | `copyIntegrationToken()` 写剪贴板 |
| 外部令牌：生成新令牌 | — | — | `createIntegrationToken()` → `POST /api/settings/integration-token`（明文仅显示一次）|
| 外部令牌：撤销 | `disabled` 当 `integrationTokenBusy` | — | `revokeIntegrationToken()` → `DELETE /api/settings/integration-token` |
| 敏感凭据：一键清理 | `disabled` 当 `busy==="clear-all-keys"` | — | `clearAllKeys()` → `DELETE /api/settings/secrets` |
| **保存设置**（主按钮） | `disabled` 当 `busy==="settings"` | 显示「保存中…」 | `api.saveSettings(body)` → `PUT /api/settings` → 回写运行期设置 |

---

## 3. 点击后「跳转」汇总

- **真实路由跳转**（URL 变化、整页切换）：仅「作品标题 → `/works/{id}` 详情页」与「查看公开原作品 / 续期链接」新标签打开外部地址。
- **页签切换**（React 状态，无路由、无请求）：顶部 4 个 Tab、导入页内平台 Tab/范围/子分类、解析页分页。
- **跨页跳转**（同页内切 Tab，发请求拉数据）：导入完成 / 同步完成 / 查看解析进度 → `setTab("parse")` 跳解析页。
- **纯刷新**（原地重拉）：批量队列刷新、知识库分页/状态切换、设置各类保存后 `setView/setSettings`。

---

## 4. 待对齐清单（PRD ↔ 实现漂移）

1. **导入入口缺失**：PRD §3.2 数据包、§3.3 公开链接接口已定义但前端零调用 → 前端仅「本地视频 + 抖音采集/同步」两路可用。需补 UI 或标注 PRD 为「规划中」。
2. **链接导入限额控件已删**：PRD §3.3 所述「每批/每日上限」输入控件此前从设置页移除（见合并 PRD §3.5 脚注），但运行期 `import_batch_limit`/`import_daily_limit`/`inbox_batch_limit` 仍可调；且公开链接入口本身未接线，限额当前无前端入口可触达。
3. **`pending` 态**：合并 PRD 决策不纳入解析页，与代码一致（前端 `itemStatus` 无 pending 映射），后端 `pending` 卡死根因仍未修。
4. **状态命名歧义**：「解析中」在全站三处含义不同（任务运行 / 批次收尾 / 解析页激活态），建议埋点区分（PRD §9.5 已记）。

---

> 本文为「按钮级接线事实」记录，与 `TokBrain产品需求文档_合并版.md`、`TokBrain功能层级结构图.md` 互为补充：本文回答「每个按钮点了之后到底发生什么」，后两者回答「系统应当是什么样」。
