# TokBrain 按钮 → 业务功能映射表（v3 原型）

> **基线**：本文映射对象为 `prototypes/tokbrain-prototype-v3.html`（Sep 5 23:39，148KB，当前最新工作稿）。
> **方法论**：① 抽取 v3 真实 `onclick/onchange` 事件（45 类 handler / 77 处控件，含 JS 模板内动态渲染），逐条到代码；② v3 为**纯前端演示稿**（零 `fetch`/`XMLHttpRequest`，数据全部来自内置 mock 常量：`KB_WORKS`/`PARSE_DATA`/`HEALTH_PROBES` 等，用 `setTimeout` 模拟进度）——因此表中「真实后端接口」列一律标注该交互在**生产形态下应对接的后端端点**，行号以本次核验为准（后端 `app/routers_v2/*` 今日无改动，行号已复核）；③ 与上一版基线《TokBrain按钮业务映射表_v2.md》比对，标出 **★ 新增** 与 **🔄 变更**。
> **配套可交互原型**：`prototypes/tokbrain-prototype-v3.html`（Sep 5 23:39，148KB，单文件、内置 mock 数据，双击用浏览器打开）。
>
> **时效声明（2026-10-30 校订）**：文中反复出现的"部署版"指当年由 Next.js 托管的 `frontend/public/tokbrain-prototype-v2.html`，**该文件与托管方式现已不存在**（`frontend/public/` 只剩 `themes/`）。因此本文所有"部署版 vs 工作稿"的对照只作**历史差异记录**，读的时候把"部署版"理解为"2026-09 的 v2 稿"即可。当前生产页面为「导入 / 解析 / 知识库 / 设置」四页签，见[功能层级结构图](TokBrain功能层级结构图.md)。

---

## 一、v2 → v3 按钮功能差异速览（新增 / 变更）

| 标记 | 页面 | 位置 / 按钮 | v2 基线现状 | v3 现状（本文档落点） |
|---|---|---|---|---|
| ★ | 全局 | **第 4 页签 🔧 设置** | 仅 p1/p2/p3 三页（v2 部署版无设置 tab） | 四页签导航 `go('p1'~'p4')`，设置独立成页 |
| ★ | 全局 | **主题切换器**（4 套） | v2 工作稿仅有变量级支持，无 UI；部署版无 | `setTheme('aura'/'sunset-cloudsea'/'ocean-dawn'/'eastern-mist')`，aura 为默认（Aura 设计语言） |
| ★ | p1 | **🔄 页内刷新**（导入进度） | 全局「🔄 刷新数据」`refreshAll()`（部署版，预取四接口） | 收敛为 p1 内 `refreshProgress()`：按钮旋转 + toast「已刷新导入进度」，**纯本地演示**（不拉数据） |
| 🔄 | p1 | 账号卡按钮组 | v2 部署版含「🔗 连接(令牌)」「🔐 握手登录(粘贴 Cookie)」两范式按钮 | **UI 收敛为单「登录」`loginNet(pf)`**：新标签页打开平台首页 + 按钮置「拉取中…」禁用；连接/握手入口不再出现在 v3 卡片（生产接口仍存在，见 p1 表） |
| ★ | p1 | 平台范围 chips 全选 | v2 无「全选」独立控件 | 每平台新增全选/取消全选 `toggleSelectAll(pf)`（按当前已选范围） |
| 🔄 | p1 | 任务失败「重试」 | 「🔁 重试失败导入」`retryFailedImports()`（一键重排全部失败） | 改为**任务行级**「重试」`retryTask(this)`（仅重跑该行，90% 模拟成功） |
| 🔄 | p2 | 「暂停」语义 | 部署版「取消批次」`cancelBatch()`（整批，红钮二次确认） | 工作稿系列用「暂停该任务」`pauseParseItem(id)`（行级，toast 演示）；生产仍走 cancel 端点（见 p2 表） |
| ★ | p2 | 模型启用开关 | 仅表单保存/取消/Key 显隐 | 新增 `toggleModelEnable()` / `toggleAudioModelEnable()`（模型启用/停用拨动） |
| ★ | p2 | 音频模型独立管理 | 「＋ 接入新音频模型」单一入口 | 独立表单 ×2 + 列表 + 删除：`toggleAudioModelForm/saveAudioModel/deleteAudioModel/renderAudioModelList/toggleAudioModelKeyVisibility/toggleAudioModelEnable/cancelAudioModelForm` |
| ★ | p3 | **6 维组合筛选栏** | v2 仅状态 chips + 分类 tabs + 标签 pills（单选） | `renderKbFilterBar()` 渲染 **collection/platform/kind/author/source/state** 6 个下拉；`onKbFilterChange()` 联动刷新 |
| ★ | p3 | **筛选条件 pill（可单删）** | 无 | 生效条件显示为 pill，`clearKbFilter(key)` 逐项 × 移除；状态 tabs 与下拉双向同步（`filterKb`/`onKbFilterChange`） |
| ★ | p3 | 状态计数实时渲染 | 无独立计数 | `renderKbResults()` 维护 pending/inlib/await/error/archive 计数徽标 + 结果计数 + 空态 |
| 🔄 | p3 | 筛选触发函数 | v2 工作稿 `selectKbCat/toggleKbPill`（已删除） | 重构为 `filterKb(state)` + `onKbFilterChange` + `clearKbFilter` + `renderKbResults`（6 维统一走 `KB_FILTERS` 状态对象） |
| 🔄 | p3 | 作品级操作 | v2 含 重试/补件/删除/恢复（部署版有真接口） | **v3 稿未纳入作品卡操作**（kbItemCard 仅展示 + 卡片进入筛选），生产接口仍列于文末供参考 |
| 🔄 | p4 | 令牌/密钥按钮 | v2 工作稿为演示；部署版接真实接口 | v3 同为演示：`toast('…（原型演示）')`；真实接口存在（`POST/DELETE /api/settings/integration-token`、`DELETE /api/settings/secrets`） |
| ★ | p4 | **重新检测（健康）** | v2 无 UI 按钮 | `runHealthDetection()`：4 探针（database/media_runtime/coordinators/security_cleanup）逐项 signal 动画 → 对应 `GET /api/system-health` + `/probes/{probe}` |
| ★ | 全局 | 飞书状态栏 + 轮询间隔 | v2 工作稿新增（映射表 v2 差异 #7 记为待同步） | v3 保留：`setFeishuPoll(sec)`（10s~30min，默认 30s），toast 反馈；仍为 ⚠️ 无后端 |
| 🔄 | p1 | 文件选择函数名 | v1/v2 工作稿 `onFiles()` | 更名 `handleFilesPick()/handleFiles(fileList)`，预览支持 视频/图片/音频；dropzone 文案加「压缩包」 |

---

## 二、全局工具栏 / 导航

| 页面 | 按钮 / 交互 | 触发函数 | v3 演示行为 | 生产后端接口（method + path · router:行） | 边界 / 备注 |
|------|------------|----------|------------|--------------------------------------------|--------------|
| 全局 | 📥导入 / ⚙️解析 / 📚知识库 / 🔧设置 页签 + 侧栏 | `go(id,btn)` | 切换 `.page` 显隐，同步 `.nv.on`；`window.scrollTo(0,0)`；无数据懒加载 | 无（纯前端路由）；四页数据各由 p1~p4 预取接口提供（见第六节） | v3 为 4 页签（★ 相对 v2 三页签新增 p4） |
| 全局 | 左下飞书状态栏（两行信号） | — | 常亮「内容已同步」+ 脉冲「轮询中」静态演示 | ⚠️ 无后端（预期 `:8001` 飞书同步/写入状态端点，待立项） | v3 保留 v2 工作稿改动 |
| 全局 | 轮询间隔下拉（默认 30 秒） | `setFeishuPoll(sec)` | 仅内存 `feishuPollSec` + toast「飞书轮询间隔已设为 …」 | ⚠️ 无后端；不触发真实计时器 | 10 秒/30 秒/1 分钟/5 分钟/10 分钟/30 分钟 |

## 三、p1 导入

| 按钮 / 交互 | 触发函数 | v3 演示行为 | 生产后端接口（method + path · router:行） | 边界 / 备注 |
|------------|----------|------------|------------------------------------------|--------------|
| 拖入区 / 点击上传（视频/音频/压缩包） | `handleFilesPick()` / `handleFiles(fileList)` | `input[type=file]` 选择，`URL.createObjectURL` 本地预览（video/image/audio 缩略） | 无（仅 FileList） | 🔄 由 v1 `onFiles()` 更名 |
| 预览 × 移除 | `removePreview(el)` | 移除预览行 | 无 | |
| 确认上传（开始导入） | `confirmUpload()` | 空列表 toast 拦截；逐文件 `enqueueTask` 进演示任务列表 | `POST /api/local-import-batches`（imports.py:88）+ `PUT /api/local-import-batches/{batch_id}/items/{item_id}/video`（imports.py:103） | v3 为本地演示队列 |
| 平台页签 抖音/B站/小红书 | `switchNetTab(pf,btn)` | 切换 `#p1-panel-dy/bz/xhs` | 无 | 🔄 v2 部署版函数名 `switchNet(pf)` |
| 账号卡「登录」（每平台 1 个） | `loginNet(pf)` | `window.open('https://www.<平台>.com/','_blank',noopener)`；按钮禁用置「拉取中…」 | ⚠️ **新增路径无后端**（v2 差异 #6 保留：待实测 cookie 直连 or 模拟接管两分支） | v3 UI 以「网页拉起登录」单范式呈现（★ 收敛 v2 双范式按钮）；生产仍保有两接口：`POST /api/platforms/{p}/handoff`（platforms.py:52，Cookie 握手）、`POST /api/settings/integration-token`（settings.py:117，连接令牌） |
| 范围 chips 收藏/喜欢/我的作品/观看历史/读取失败 | `switchScopeTag(pf,scope,btn)` | 切换本地 `CUR_SCOPE` + 重渲染列表 | 无（真实范围数据由已连接工具拉取） | scope 值 `fav/like/work/history/fail`；对应生产 `POST /api/platforms/{p}/pull` body `mode`（platforms.py:89，可取值 like/collect/post/favorite/history）— 映射关系按 f2/zhicui 语义对齐，待实测确认 |
| 范围全选/取消 | `toggleSelectAll(pf)` | 全选当前已渲染视频卡 `.vg-card` | 无 | ★ 新增 |
| 同步当前 N 条（N=已选数） | `startNetSync(pf)` | 演示：按钮禁用 2.2s「正在准备文案…」后向任务列表注入 mock 同步任务 | ⚠️ 占位（无后端；预期走 `platforms pull → enqueue` 导入管线） | 按钮文案由已选数动态刷新（`updateSyncBtn`） |
| 视频卡 封面/子分类 | `toggleVg(el)` / `selectSubcat(pf,i,el)` | 选中/展开子分类 | 无（本地渲染） | 动态模板 |
| 状态说明 ? | `toggleProgressHelp()` | 弹层说明任务 5 态 | 无 | |
| ↻ 刷新（导入进度） | `refreshProgress()` | 按钮 spin 600ms + toast「已刷新导入进度」（不拉数据） | 生产应轮询 `GET /api/jobs`（jobs.py:14） | 🔄 取代全局 `refreshAll()` 演示位 |
| 任务行 重试（失败行出现） | `retryTask(el)` | 隐藏重试钮、清除 failed 态，`runTask(row)` 重跑（~90% 成功演示） | 生产批量语义：`POST /api/jobs/retry`（jobs.py:22）→ `retry_failed_imports()` | 🔄 由「一键重试全部失败」改为行级 |

## 四、p2 解析

| 按钮 / 交互 | 触发函数 | v3 演示行为 | 生产后端接口（method + path · router:行） | 边界 / 备注 |
|------------|----------|------------|------------------------------------------|--------------|
| 状态 tabs（预检/音视分离/模型总结/标签分类）与进度 | `renderParseProgress()` | 渲染 `PARSE_DATA` 统计 + 条目 | 生产批次查询 `GET /api/import-batches/{batch_id}`（imports.py:189） | 数据源 mock 常量 `PARSE_DATA` |
| 条目勾选 | `toggleParseItem(el)` | 仅 `state=confirm` 可勾选 | 无 | |
| 选择全部可确认项 | `toggleParseSelectAll()` | 全选/取消 confirm 态条目 | 无 | 无可确认项时无操作 |
| 确认加入待入库（N） | `confirmParseQueue()` | 校验已选；从 `PARSE_DATA` 移除所选并更新计数 + toast | `POST /api/import-batches/{batch_id}/confirm`（imports.py:206）→ 返回 `confirmed_count` | 空选 toast 拦截 |
| 分页 ‹ 页码 › | `changeParsePage(p)` | `parsePage` 切换 + 首尾禁用 | 无（前端分页 5 条/页） | |
| 条目 暂停该任务 | `pauseParseItem(id)` | toast「已暂停该任务：{title}」，不改状态 | ⚠️ 占位；生产对应整批 `POST /api/import-batches/{batch_id}/cancel`（imports.py:197）或任务 `POST /api/jobs/{job_id}/cancel`（jobs.py:36），粒度语义待定 | 🔄 部署版 v2 对应「取消批次」（整批红钮） |
| 条目 删除预检结果 | `removeParseItem(id)` | 从 `PARSE_DATA` 删除并减计数 | `DELETE /api/import-items/{item_id}`（imports.py:255） | |
| 条目 分类下拉 | `updateParseLabel(sel)` | 写回 `item.labels[key]=val` | `PATCH /api/import-items/{item_id}`（imports.py:150，body 含 `target_collection_id`） | 下拉数据生产来自 `GET /api/library/collections`（library.py:121） |
| ＋ 接入新模型（对话） | `toggleModelForm()` | 展开模型表单 | 表单数据生产来自 `GET /api/settings`（settings.py:62） | |
| 👁 Key 显隐 | `toggleModelKeyVisibility()` | 明文/密文切换 | 无 | |
| 模型启用开关 | `toggleModelEnable()` | 拨动 enabled 状态 | ★ 新增；保存经 `PUT /api/settings`（settings.py:67） | |
| 取消 / 保存并设为当前 | `cancelModelForm()` / `saveModel()` | 收起表单 / 写入 `MODELS` 演示列表 | `PUT /api/settings`（settings.py:67） | |
| 模型列表 × 删除 | `deleteModel(id)` / `renderModelList()` | 从演示列表删除 / 渲染 | 经 `PUT /api/settings` 全量覆盖；无独立删除端点 | |
| ＋ 接入新音频模型 / 👁 / 开关 / 取消 / 保存 / 列表 / ×删除 | `toggleAudioModelForm` / `toggleAudioModelKeyVisibility` / `toggleAudioModelEnable` / `cancelAudioModelForm` / `saveAudioModel()` / `renderAudioModelList()` / `deleteAudioModel(id)` | 音频模型独立表单+列表演示 | `PUT /api/settings`（settings.py:67，模型段含音频供应商） | ★ 新增独立管理组 |

## 五、p3 知识库（v3 重构重点 ★）

> v3 数据源：内置 mock `KB_WORKS` + 元数据常量 `KB_COLLECTIONS/KB_PLATFORM/KB_KIND/KB_SOURCE/KB_STATE`；筛选状态统一存 `KB_FILTERS`（6 键）。

| 按钮 / 交互 | 触发函数 | v3 演示行为 | 生产后端接口（method + path · router:行） | 边界 / 备注 |
|------------|----------|------------|------------------------------------------|--------------|
| 状态 chips 待处理/在库/待补件/异常/已归档（带计数） | `filterKb(state,btn)` | 写 `KB_FILTERS.state`，同步状态 tabs `.on` 与下拉框 value，联动 pill/结果 | `GET /api/library/works?library_state=`（library.py:341，取值 pending/in_library/issues/archived/supplement） | v3 计数徽标由 `renderKbResults` 前端统计 |
| **筛选栏 6 维下拉**（集合/平台/类型/作者/来源/状态） | `renderKbFilterBar()` 渲染；`onKbFilterChange(sel)` 响应 | 每维含「全部」；改值即写 `KB_FILTERS` 并 `renderKbPills()+renderKbResults()` | 后端 `GET /api/library/works`（library.py:341）已支持全部 6 维：`collection_id`、`platform`、`kind`、`author`、`source`、`library_state`（✅ 本轮补齐 platform/kind/author/source） | ★ 新增；v3 工作稿当前为前端内存过滤；生产接服务端时 `platform/kind/author/source` 参数同名直通，`state→library_state`、`collection→collection_id` 需做一层映射 |
| 生效筛选 pill（× 单删） | `clearKbFilter(key)` | 置空 `KB_FILTERS[key]` + 复位下拉/状态 chip + 重渲染 | 无（前端筛选状态） | ★ 新增 |
| 结果计数 + 空态 + 卡片 | `renderKbResults()` / `kbItemCard(w)` / `kbDisplay(k,v)` | 过滤 `KB_WORKS` 渲染卡片（标题/平台/时长/标签…） | 数据生产来自 `GET /api/library/works`（library.py:341）+ 分类 `GET /api/library/collections`（library.py:121） | 作品卡在 v3 为**浏览态**，无操作按钮 |
| （v3 未纳入的作品级操作，供生产参考） | 重试 | — | `POST /api/library/works/{work_id}/retry`（library.py:682） | 仅 issues/pending 态显示 |
| 〃 | 恢复 | — | `POST /api/library/works/{work_id}/restore`（library.py:782） | 仅 archived 态 |
| 〃 | 删除 | — | `DELETE /api/library/works/{work_id}`（library.py:799） | 二次确认 |
| 〃 | 批量重试 | — | `POST /api/library/works/retry-batch`（library.py:495） | |

## 六、p4 设置

| 按钮 / 交互 | 触发函数 | v3 演示行为 | 生产后端接口（method + path · router:行） | 边界 / 备注 |
|------------|----------|------------|------------------------------------------|--------------|
| 主题卡片 ×4（aura/晚霞云海/海洋晨光/东方雾山） | `setTheme(name)` | `data-theme` + CSS 变量热切换（含背景显现度调节） | 无（前端皮肤） | ★ 相对部署版 v2 新增 UI；aura=默认 |
| 重新检测（本地运行环境） | `runHealthDetection()` | 4 探针（database/media_runtime/coordinators/security_cleanup）逐个 `checking→ok/fail` 动画 + 汇总条 | `GET /api/system-health`（health.py:12）+ `GET /api/system-health/probes/{probe}`（health.py:17） | ★ 新增按钮；探针名与后端 `SYSTEM_PROBES` 待对齐 |
| 轮换令牌 | `toast('已生成新令牌（原型演示）')` | 仅提示 | 生产：`POST /api/settings/integration-token`（settings.py:117）→ 生成；`GET`（settings.py:112）查当前 | 演示实现 |
| 撤销令牌 | `toast('已撤销令牌（原型演示）')` | 仅提示 | `DELETE /api/settings/integration-token`（settings.py:124） | 演示实现 |
| 删除全部密钥 | `toast('已删除全部密钥（原型演示）')` | 仅提示 | `DELETE /api/settings/secrets`（settings.py:129） | 演示实现 |
| （v3 未渲染的其余设置项按钮，来自 v2 工作稿/部署版，供生产参考） | 保存设置 / 保存 Cookie / 更新账单凭据 / 刷新官方账单 / 删除全部 API Key 与 AccessKey / 一键恢复默认提示词 / 保存提示词 | — | `GET/PUT /api/settings`（settings.py:62/67）、`POST /api/settings/official-bill/refresh`（settings.py:101）、`GET /api/settings/usage`（settings.py:96）、平台握手 `POST /api/platforms/{p}/handoff`（platforms.py:52）、状态 `GET /api/platforms/{p}/status`（platforms.py:79） | v3 设置页仅实现 主题/健康/令牌/密钥 四组交互，其余为静态区块 |

## 七、预取接口（数据加载 / 懒加载清单）

> **v3 现状**：脚本尾部仅执行本地渲染 `renderScope×3 → renderParseProgress → renderModelList → renderAudioModelList → renderKbFilterBar → filterKb('pending') → go('p1')`，**无任何网络预取**（纯演示稿）。
> **生产形态预期**（以部署版 apiFetch 实现 + 后端可用端点为依据）：

| 时机 | 页面 | 应预取接口（method + path · router:行） | 说明 |
|---|---|---|---|
| 首屏 / 进 p1 | 导入 | `GET /api/jobs`（jobs.py:14） | 任务列表与进度（含导入/解析） |
| 〃 | 〃 | `GET /api/platforms/{p}/status`（platforms.py:79） | 各平台连接状态（dy/bz/xhs） |
| 〃 | 〃 | `GET /api/settings/integration-token`（settings.py:112） | 令牌有效性 → 驱动「连接中/未连接」按钮态 |
| 进 p2 | 解析 | `GET /api/import-batches/{batch_id}`（imports.py:189） | 当前导入批次的预检/待确认条目 |
| 〃 | 〃 | `GET /api/library/collections`（library.py:121） | 条目分类下拉选项 |
| 进 p3 | 知识库 | `GET /api/library/works?library_state=pending`（library.py:341） | 默认「待处理」视图（v3 初始态 pending） |
| 〃 | 〃 | `GET /api/library/collections`（library.py:121） | 筛选栏「集合」维选项 |
| 进 p4 | 设置 | `GET /api/settings`（settings.py:62） | 模型/供应商/Cookie/护栏配置回填表单 |
| 〃 | 〃 | `GET /api/settings/usage`（settings.py:96） | 用量统计（媒体/AI/费用） |
| 手动 | p4 健康检测 | `GET /api/system-health`（health.py:12） | 汇总检测；单探针可 `GET /probes/{probe}`（health.py:17） |
| 轮询（生产建议） | p1/p2 | `GET /api/jobs`（jobs.py:14）定时刷新 | v3 `refreshProgress()` 的演示位即此接口 |
| 全局刷新（部署版保留） | 全页 | `GET /api/jobs` + `GET /api/import-batches/{id}` + `GET /api/library/works` + `GET /api/settings` | 部署版 `refreshAll()` 的四件套语义 |

**已知预取能力缺口**：
1. ✅ **已补全**：`GET /api/library/works` 现新增 `platform` / `kind` / `author`（映射 `author_name`）/ `source`（映射 `import_source`）四个查询参数，与既有 `library_state/collection_id/lid/offset/limit` 组合，服务端可执行 v3 六维筛选；返回 item 同步补出 `platform`、`import_source` 字段（kind/author_name 原已返回）。
2. imports 侧无「批次列表」端点（仅单查 `GET /import-batches/{batch_id}`）。当前 p2/部署版均只用「当前批次」单查，无调用方 → 暂不补（YAGNI），待需要"最近批次切换"时再加。
3. 飞书同步状态/写入轮询无对应端点（v3 状态栏为纯占位）→ 属独立集成项，延后立项（见第八节 #4）。

## 八、勘误与待确认（相对 v2 映射表）

| # | 事项 | 说明 |
|---|---|---|
| 1 | `POST /api/library/works/{work_id}/supplement` **已存在**（library.py:737，多行装饰器） | 前版单行 grep 遗漏多行装饰器，勘误作废。该端点实现完整：`upload_work_supplement` → `store_work_supplement` + `enqueue_ingest_job`，且部署版 `supplementWork()` 已在调用（FormData `rights_attested` + `files`）。v2 映射表「补件」行正确，无需补 |
| 2 | `GET /api/jobs` 行号更正 | v2 表引 jobs.py:22 → 实际为 **jobs.py:14**（:22 为 `POST /api/jobs/retry`） |
| 3 | `GET/PUT /api/settings` 行号更正 | v2 表引 settings.py:59/64 → 实际为 **settings.py:62/67** |
| 4 | v3 登录范式收敛 | v3 UI 只留「网页拉起登录」(`loginNet`)，隐藏 令牌连接 / Cookie 握手 两入口；后端两套接口仍在。评审需定：产品最终保留哪套 UI 范式（v2 差异 #1/#6 待实测结论不变） |
| 5 | ~~部署版未同步~~ **已作废** | 原文提到的 `frontend/public/tokbrain-prototype-v2.html`（:3001 托管、直连 :8001）已随 `frontend/public/` 清理一起删除；原型统一放在 `prototypes/`，不再由前端托管，也不再有"是否替换部署版"这个待办 |

---
*本文档交互清单由 `prototypes/tokbrain-prototype-v3.html` 实抓（45 类 handler/77 控件），后端接口行号均复核于 `app/routers_v2/{imports,jobs,library,settings,platforms,health}.py`。演示（mock）行为以「v3 演示行为」列如实标注，未与生产接口混淆。*
