# TokBrain 按钮 → 业务功能映射表（v2 原型）

> 方法论：**仅查询项目代码**。每一行都从原型 `prototypes/tokbrain-prototype-v2.html` 的真实 `onclick`/处理函数出发，回溯到后端 `app/routers_v2/*` 的真实接口（method + path + 文件:行号）。原型为单文件演示稿，数据来自内置 mock，不会真的请求后端。
>
> 判读规则：表中 **带 ⚠️ 标注的行为为新增原型路径，尚未对应任何后端代码接口**，仅前端原型占位（如 v1 原型 `loginNet()`），其最终态与标签页行为仍待实测后定，不计入"已回溯到代码行号"的正式条目。
>
> 配套可交互原型：`prototypes/tokbrain-prototype-v2.html`（单文件，直接双击用浏览器打开即可，数据为内置 mock）。
>
> **时效声明（2026-10-30 校订）**：① 原"部署版"`frontend/public/tokbrain-prototype-v2.html` **已不存在**——`frontend/public/` 现在只有 `themes/`，原型也不再由 Next.js 托管，请勿再按 `http://127.0.0.1:3001/tokbrain-prototype-v2.html` 访问；② 本表映射的是**原型**，不是当前生产页面。生产页面现为「导入 / 解析 / 知识库 / 设置」四页签，且没有对话页，页签分工见[功能层级结构图](TokBrain功能层级结构图.md)；③ 表中行号按 2026-09 版本核验，后端已多次改动，定位请以接口路径为准。

## 一、全局工具栏 / 导航

| 页面 | 按钮 / 交互 | 触发函数 | 业务功能（人话） | 真实后端接口（method + path · router:行） | 边界 / 降级 |
|------|------------|----------|------------------|--------------------------------------------|--------------|
| 全局 | 📥导入 / ⚙️解析 / 📚知识库 顶部 Tab + 左侧 Nav | `go(id)` | 切换页面，同步 `location.hash`（#p1/#p2/#p3），进入即懒加载该页数据 | 无（纯前端路由） | 点击当前页无副作用；编辑模式无守卫 |
| 全局 | 🔄 刷新数据 | `refreshAll()` | 重新拉取 导入任务 / 解析批次 / 知识库 / 设置 | `GET /api/jobs`（jobs.py:22）+ `GET /api/import-batches/{id}`（imports.py:189）+ `GET /api/library/works`（library.py:341）+ `GET /api/settings`（settings.py:59） | 任一接口失败则该块用 mock，toast 提示 |
| 全局 | 🗺️ 按钮→业务映射 | `toggleLegend()` / `renderLegend()` | 显示/隐藏「按钮→业务映射」图例页 | 无（渲染内置 `BIZ` 常量） | 无 |

## 二、p1 导入

| 页面 | 按钮 / 交互 | 触发函数 | 业务功能（人话） | 真实后端接口（method + path · router:行） | 边界 / 降级 |
|------|------------|----------|------------------|--------------------------------------------|--------------|
| p1 | 拖入区 / 点击选择文件 | `fileInput.click()` → `onFiles()` | 选择本地视频/音频文件，入预览列表（未上传） | 无（仅前端 FileList） | 非 video/audio 由 `accept` 过滤 |
| p1 | 预览 × 移除 | `rmPreview(id)` | 上传前从预览列表移除某文件 | 无 | 不影响已上传批次 |
| p1 | 确认上传（开始导入） | `confirmUpload()` | ① 创建本地导入批次 ② 逐文件上传视频 → 进入导入队列 | `POST /api/local-import-batches`（imports.py:88）+ `PUT /api/local-import-batches/{bid}/items/{iid}/video`（imports.py:103） | 空文件列表 toast 拦截；上传失败 toast 报错 |
| p1 | 平台 Tab（抖音/B站/小红书） | `switchNet(pf)` | 切换网络平台面板，改连接按钮文案 | 无 | 三平台复用同一共享令牌（见下注） |
| p1 | 🔗 连接抖音 | `connectPlatform()` | **生成平台连接令牌**（真实接入方式：把令牌配到 f2/doubaosave，外部工具据此把收藏/喜欢推入导入队列） | `POST /api/settings/integration-token`（settings.py:105） | 失败 toast |
| p1 | 🔐 握手登录（粘贴 Cookie） | `handoffLogin()` / `submitHandoff()` | 复用 zhicui 桌面端登录契约：粘贴平台登录态 Cookie，服务端校验后存为密文 SecretRecord（抖音→`douyin_cookie`、B站→`bilibili_cookie`、小红书→`xhs_cookie`；原抖音 `f2_cookie` 已弃用） | `POST /api/platforms/{pf}/handoff`（platforms.py）→ `set_secret` | 校验 Cookie 名（B站 SESSDATA+DedeUserID / 抖音 sessionid+sessionid_ss / 小红书 web_session）；缺失返回 401 |
| p1 | 📥 拉取视频 | `pullPlatform()` | 用已保存 Cookie 调平台接口拉取收藏/喜欢：B站=官方 Web API、抖音=zhicui 递归 aweme 遍历归一化、小红书=URL 提取（均移植自 zhicui `platform-account.js` / `douyin-login.js`） | `POST /api/platforms/{pf}/pull`（platforms.py）→ `collect_bilibili` / `collect_douyin` / `collect_xiaohongshu` | **三端均 `enqueue=true`** 走 platforms 导入管线（`create_platform_import_batch`→`enqueue_platform_batch`，`source_type=platform_pull`）；抖音已**弃用 F2 发现路径**；Cookie 失效/风控返回干净错误（502）。条目状态按平台分流：**抖音**写 `queued` 进链接预检（f2 解析媒体地址后下载）；**B站 / 小红书**没有链接解析器，直接写 `needs_local_file`（元数据入库，视频靠补件）。批次状态跟着条目走：有排队条目是 `queued`，纯补件清单是 `succeeded` |
| p1 | 🔓 登录（网页端拉起） | `loginNet(pf)` | 各渠道账号卡「登录」按钮：点击后在**新标签页**打开平台网页端登录页（抖音 `douyin.com` / B站 `bilibili.com` / 小红书 `xiaohongshu.com`）。按钮状态流转：**拉取中**（点击即进入，网页登录页拉起 + 收藏列表拉取进行中，按钮禁用）→ 待实测后转「连接中」或「接管中」。登录态获取方式**待实测**：① 若 Cookie 可直连拉取收藏列表 → 登录成功后**自动关闭新标签页** + 转「连接中」；② 若仅能模拟操作获取收藏列表 → 转「接管中」 | ⚠️ **新增路径，无实际后端代码接口对应**；仅前端原型 `prototypes/tokbrain-prototype-v1.html` 的 `loginNet()` 占位，pending 实测 | `window.open(url,'_blank')` + 按钮置「拉取中…」；退出/完成条件待实测确定（见下方差异 #6） |
| p1 | 🔄 同步当前（收藏夹） | `startNetSync(pf)` / `updateSyncBtn(pf)` | 账号卡下方「同步当前 * 条」按钮（* = 当前**已选视频数**，随勾选/全选实时变化）：点击将所选收藏夹视频加入导入队列。文案由原先固定「同步当前收藏夹 50 条」改为动态「同步当前 {已选数} 条」 | ⚠️ **新增路径，无实际后端代码接口对应**；仅前端原型占位（`startNetSync()` → `enqueueTask`，`updateSyncBtn()` 按 `.vg-card.sel` 计数刷新），pending 实测 | 按钮文案由 `updateSyncBtn(pf)` 按所选视频数实时刷新；初始 0 条 |
| p1 | 范围 chips（收藏/喜欢/我的作品/观看历史/读取失败） | `switchScope()` | 仅切换本地状态；真实数据由已连接平台的外部工具拉取 | 无（原型不直连平台私有接口） | 范围选择不改变后端状态 |
| p1 | 🔄 同步当前连接 | `syncNet()` | 校验连接令牌是否有效，并刷新导入进度 | `GET /api/settings/integration-token`（settings.py:100）+ `GET /api/jobs`（jobs.py:22） | 未连接则提示「请先点击连接」 |
| p1 | 状态说明 | `toggleProgressHelp()` | 弹层说明任务 5 态（queued/running/done/failed/cancelled） | 无 | 无 |
| p1 | ↻ 刷新（导入进度） | `refreshJobs()` | 拉取导入/解析任务列表与进度 | `GET /api/jobs`（jobs.py:22） | 失败用 mock 任务数据 |
| p1 | 🔁 重试失败导入 | `retryFailedImports()` | 一键重排**失败导入**（`processing_state=failed` 的作品重新进入入库队列） | `POST /api/jobs/retry`（jobs.py:22）→ `retry_failed_imports()` | 无可重试时返回提示；后端不可达原型降级为演示提示 |

## 三、p2 解析

| 页面 | 按钮 / 交互 | 触发函数 | 业务功能（人话） | 真实后端接口（method + path · router:行） | 边界 / 降级 |
|------|------------|----------|------------------|--------------------------------------------|--------------|
| p2 | 选择全部可确认项 | `toggleParseSelectAll()` | 选中批次内 `status=done` 的全部条目 | 无 | 无可确认项时 toast 提示 |
| p2 | 确认加入待入库(N) | `confirmParseQueue()` | 将选中预检结果**转为知识库作品** | `POST /api/import-batches/{id}/confirm`（imports.py:206） | 无批次/未选 toast 拦截；返回 `confirmed_count` |
| p2 | 分页 ‹ › | `parseGo(p)` | 批次内条目前端分页（每页 5） | 无 | 首尾页箭头自动禁用 |
| p2 | 单条复选 | `toggleParseItem(id)` | 选中/取消某待确认条目 | 无 | 仅 `status=done` 可勾选 |
| p2 | 删除（预检结果） | `rmParseItem(id)` | 删除某条预检结果 | `DELETE /api/import-items/{id}`（imports.py:255） | 二次 `confirm()` 确认 |
| p2 | 取消批次 | `cancelBatch()` | 取消当前导入批次（等同暂停） | `POST /api/import-batches/{id}/cancel`（imports.py:197） | 二次 `confirm()` 确认；**本次新增按钮** |
| p2 | 条目分类下拉 | `setItemCollection(id)` | 改条目的知识库分类（`target_collection_id`） | `PATCH /api/import-items/{id}`（imports.py:150） | 下拉选项来自 `GET /api/library/collections`（library.py:121）；**本次新增 UI** |
| p2 | ＋ 接入新模型（对话/音频） | `openModelForm(kind)` | 展开模型接入表单（读取当前设置选项） | 无（表单数据来自 `GET /api/settings`：settings.py:59） | 无 |
| p2 | 👁 显隐 Key | `toggleKey()` | 切换 DashScope Key 输入框明文/密文 | 无 | 无 |
| p2 | 保存并设为当前 | `saveModel()` | 保存模型/Key 并设为当前处理模型 | `PUT /api/settings`（settings.py:64） | 失败 toast；供应商预置选项不可删除 |
| p2 | 取消（模型表单） | `cancelModelForm()` | 收起模型表单 | 无 | 无 |

## 四、p3 知识库

| 页面 | 按钮 / 交互 | 触发函数 | 业务功能（人话） | 真实后端接口（method + path · router:行） | 边界 / 降级 |
|------|------------|----------|------------------|--------------------------------------------|--------------|
| p3 | 状态筛选 chips（待处理/在库/待补件/异常/已归档） | `setKbState(k)` | 按 `library_state` 筛选作品 | `GET /api/library/works?library_state=`（library.py:341） | 空态显示对应占位 |
| p3 | 标签 pills（行业洞察/内容运营/失效视频） | `toggleKbPill()` | 前端按作品 `tags` 二次过滤 | 无（无独立接口） | 无 |
| p3 | 分类 tabs（collections） | `setKbCat(id)` | 按知识库分类筛选作品 | `GET /api/library/collections`（library.py:121）+ `GET /api/library/works?collection_id=`（library.py:341） | 无分类=全部 |
| p3 | 重试 | `retryWork(id)` | 重新解析/抽取异常或待处理作品 | `POST /api/library/works/{id}/retry`（library.py:682） | 仅 issues/pending 态显示 |
| p3 | 补件 | `supplementWork(id)` | 上传补充素材（封面/字幕等） | `POST /api/library/works/{id}/supplement`（library.py:738） | 仅 supplement 态显示；文件上传 |
| p3 | 删除 | `removeWork(id)` | 删除作品 | `DELETE /api/library/works/{id}`（library.py:799） | 二次确认；非归档态可删 |
| p3 | 恢复 | `restoreWork(id)` | 恢复已归档作品 | `POST /api/library/works/{id}/restore`（library.py:782） | 仅 archived 态显示 |

## 五、差异与待确认（影响「按钮要不要这么对应」）

| # | 差异点 | 代码现状 | 你的示例预期 | 建议 |
|---|--------|----------|--------------|------|
| 1 | **抖音/B站/小红书「连接 / 登录」** | 「连接」= `POST /api/settings/integration-token` 生成**共享令牌**（外部工具 f2/doubaosave 推收藏/喜欢）；「握手登录」= `POST /api/platforms/{pf}/handoff` **真实保存登录态 Cookie**（复用 zhicui `douyin-login.js` 契约，存为 SecretRecord）。**后端无浏览器型 OAuth 模块**（服务端无法拉起扫码） | 「点击抖音登录按钮拉起抖音登录模块」 | ✅ **已确认：两者都做且已落地**——「连接」走真实令牌；「握手登录」现已实现为 Cookie 握手（非占位）；真实 OAuth 授权模块仍属后续（需第三方应用凭据） |
| 2 | **p2「取消批次 / 暂停任务」** | 函数 `cancelBatch()`（→ `POST /api/import-batches/{id}/cancel`，imports.py:197）**已定义但未绑定任何 UI 按钮** | 应有暂停入口 | ✅ **已确认：补进 UI**——p2 新增「取消批次」按钮（红色，二次确认） |
| 3 | **p2「视频分类下拉」** | 接口 `PATCH /api/import-items/{id}`（imports.py:150，body `{target_collection_id}`）**存在但原型无 UI 下拉** | 解析时可改分类 | ✅ **已确认：补进 UI**——每条目新增「归类到知识库分类」下拉 |
| 4 | **导入失败任务「重试」** | 原 `jobs.py` 仅 `POST /{job_id}/cancel`（jobs.py:30）；**已新增** `POST /api/jobs/retry`（jobs.py:22）→ `retry_failed_imports()` 一键重排失败导入；原型 p1 已加「🔁 重试失败导入」按钮 | 应可一键重试 | ✅ **已确认：实现 retry 端点**——推翻原「保持现状」，由 `retry_failed_imports()` 重排 `processing_state=failed` 的作品，复用 `enqueue_ingest_job` 的锁/去重 |
| 5 | **抖音采用 F2 方案** | TokBrain 原用 `f2.apps.douyin.crawler.DouyinCrawler`（`app/services/f2_links.py`）做抖音发现/解析；本次**复用 zhicui 桌面端 `platform-account.js` / `douyin-login.js`**，新增 `app/services/platforms/`（handoff + `collect_douyin` 递归归一化 + 三端 `pull`），抖音登录态改存 `douyin_cookie` | 弃用 F2，三端统一走 zhicui | ✅ **已确认：弃用 F2 发现路径**——抖音发现/接入已迁移至 `platforms`（zhicui 方案）；`f2_links.py` 仅保留 F2 媒体下载老路径（待后续替换为签名媒体下载器），媒体下载暂不变 |
| 6 | **各渠道「登录」按钮行为（网页端拉起）** | 原型 v1（`prototypes/tokbrain-prototype-v1.html`）的账号卡「登录」按钮已改为：点击在**新标签页**打开平台网页端登录页；登录态获取方式**待实测**——cookie 直连拉取收藏可行则自动关页 + 按钮「连接中」；仅能模拟操作获取则按钮「接管中」。v2 当前仍走「连接令牌 + 握手粘贴 Cookie」方案，二者为不同登录范式 | 实测后定登录按钮最终态与标签页行为 | ⏳ **待实测验证**：已登记待办（Task #1~#3）——先验证 cookie 能否直连拉取收藏列表，再据此选「连接中」或「接管中」分支；原型 `loginNet()` 内已加 TODO 注释。**⚠️ 备注：该行为为新增路径，无实际后端代码接口对应，仅前端原型占位，未回溯到任何 `app/routers_v2/*` 接口。** |
| 7 | **侧栏飞书状态栏 / 原型文件落点** | 状态栏在 `prototypes/tokbrain-prototype-v2.html` 中已实现；当年另有一份托管版 `frontend/public/tokbrain-prototype-v2.html` 未同步 | 状态栏应出现在评审原型中 | ✅ **已闭环**：托管版已删除，不再需要移植；生产侧状态栏已实装（读 `/api/sync/overview`），并带扫码续期入口 |

## 六、侧栏飞书状态栏（新增原型路径）

> 位于左下角 `sidebar-foot`，全局常驻两行状态。当年标注的"尚无后端接口对应"已过时：生产侧已由 `GET /api/sync/overview` 提供连接态与最近查询信息，授权过期时走 `POST /api/sync/feishu/relogin` 扫码续期（见[操作说明书](../操作说明书.md)第 11 节）。

| 页面 | 按钮 / 交互 | 触发函数 | 业务功能（人话） | 真实后端接口（method + path · router:行） | 边界 / 降级 |
|------|------------|----------|------------------|--------------------------------------------|--------------|
| 全局（侧栏底） | 飞书内容已同步（常亮信号） | — | 侧栏底部第一行：表示飞书内容已与本地知识库同步；信号点常亮（cyan），下方小字占位「最近同步 · 刚刚」 | ⚠️ **新增路径，无实际后端代码接口对应**；仅前端原型状态占位 | 后续应接飞书同步状态接口刷新「最近同步」时间 |
| 全局（侧栏底） | 飞书写入轮询中（脉冲信号） | — | 侧栏底部第二行：表示正按设定间隔轮询飞书写入；信号点做呼吸脉冲动画表达"进行中" | ⚠️ **新增路径，无实际后端代码接口对应**；仅前端原型动画占位 | — |
| 全局（侧栏底） | 轮询间隔下拉框（默认 30 秒） | `setFeishuPoll(sec)` | 下拉框**框内显示当前轮询间隔**，展开可设 10 秒 / 30 秒 / 1 分钟 / 5 分钟 / 10 分钟 / 30 分钟；选择后换算为"X 秒 / X 分钟"并 toast 提示"飞书轮询间隔已设为 …" | ⚠️ **新增路径，无实际后端代码接口对应**；仅前端原型 `setFeishuPoll()` 占位（`feishuPollSec` 仅存内存） | 当前不真正触发后端计时器；待后端飞书同步/写入端点确认后接入 |

## 七、预期对应端口

> 原型为单文件静态页（`prototypes/tokbrain-prototype-v2.html`），不再由 Next.js 托管，也**没有**部署版地址。下表仅为"这类交互在生产环境会落在哪个端口"的示意：

| 维度 | 端口 | 说明 |
|------|------|------|
| 生产前端 | `3001` | Next.js 应用本体 `http://127.0.0.1:3001`（导入/解析/知识库/设置四页签），**不含**原型页 |
| 真后端 | `8001` | `uvicorn :8001`。飞书同步端点已落地，不再是待立项：`GET /api/sync/overview`、`POST /api/sync/feishu/relogin`、`POST /api/integrations/v1/inbox/items` |
| 飞书轮询（预期） | `:8001` 轮询端点 | 「飞书写入轮询中」下拉设定的间隔，**预期**驱动对 `:8001` 飞书写入状态轮询端点的定时请求；当前原型未实现真实轮询计时器 |

---
*本表严格依据 `prototypes/tokbrain-prototype-v2.html`（处理函数）+ `app/routers_v2/{imports,library,settings,jobs,integrations}.py`（接口定义）整理，所有「真实后端接口」均可回溯到代码行号。后端此后新增了 `/api/platforms`、`/api/model-profiles`、`/api/sync`、`/api/system-health` 等路由，本表未覆盖。*
