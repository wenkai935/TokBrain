# TokBrain 产品需求文档（PRD）· 原型 v1 交互规格

> **时效声明（2026-10-30 校订）**：本文是**历史原型规格**，不是当前页面需求。三处需注意：① 原型 `prototypes/tokbrain-prototype-v1.html` 仍在，但已不是设计基线；② 文中"对话页/问答/流式回答"**从未在生产实现**，当前网页端只有「导入 / 解析 / 知识库 / 设置」四页签，检索只经 MCP；③ 文中引用的 `frontend/public/tokbrain-prototype-v2.html` 已不存在，原型统一在 `prototypes/`。

> 配套原型：原 `prototypes/tokbrain-prototype-v1.html`（v1 原型已不作为设计基线）。本 PRD 现仅覆盖导入 p1 / 解析 p2 / 知识库 p3 三个页面。
> 文档定位：基于**当前已生成产品原型**整理的需求基线，逐按钮说明「功能 / 触发逻辑 / 边界条件」，用于研发实现与评审对齐。
> 编写日期：2026-09-01 ｜ 状态：草稿（原型驱动）｜ 版本：v1

---

## 1. 产品概述

### 1.1 背景与目标
TokBrain 是面向「视频 → 知识」工作流的本地优先产品：把抖音 / 小红书 / B站 / 本地视频导入后，经过解析（ASR 音频转写、模型总结、标签分类）产出**知识切片**，沉淀进个人知识库，并支撑后续的问答与克隆训练。

原型目标：以低保真、纯前端、可交互的方式快速验证**主流程交互与信息架构**，重点打磨「导入 → 解析 → 知识库」三段链路。

### 1.2 范围
- **范围内**：3 个页面（导入 p1 / 解析 p2 / 知识库 p3）的全部可见交互、按钮、状态与边界。
- **不在本次范围内（原型仅为占位）**：克隆训练页（脑图规划中的独立模块，尚未在本原型落地）、真实后端服务、账号 OAuth 真实登录、计费与萃点。

### 1.3 原型与产品的关键差异（实现前须知）
| 维度 | 原型当前表现 | 产品预期 |
|------|--------------|----------|
| 数据持久化 | `localStorage` 仅存「文案编辑」，其余为内存常量，刷新复位 | 服务端/本地库持久化 |
| 任务执行 | `runTask` 用 `setInterval` 模拟进度，90% 成功 / 10% 失败随机 | 真实解析流水线（预检→音视分离→模型总结→标签分类） |
| 网络同步 | 点击即「演示数据已预置」，不真实拉取 | 调用各平台授权接口读取收藏/喜欢等 |
| 模型调用 | 仅保存配置，不真正请求 | 按供应商路由真实推理 |
| 编辑模式 | 仅改文案，不可改结构 | — |

---

## 2. 信息架构与页面地图

```
TokBrain
├─ 顶部工具栏（全局）：导入/解析/知识库 切换 · 编辑模式 · 导出改动 · 打印 · 重置
├─ 左侧导航（全局）：📥 导入 · ⚙️ 解析 · 📚 知识库
├─ p1 导入
│   ├─ 数据统计（4 卡：处理量 / AI 用量 / 已入库 / 待入库）
│   ├─ 本地导入（拖拽上传 → 确认上传）
│   ├─ 网络视频（抖音/B站/小红书 三端 Tab · 登录 · 范围标签 · 子分类 · 视频选择 · 同步）
│   └─ 导入进度（逐条任务 · 状态说明 · 刷新）
├─ p2 解析
│   ├─ 解析进度（统计 6 卡 · 预检结果卡 · 分页 · 全选/确认）
│   └─ 模型配置（左：对话模型 · 右：音频模型，各自接入/列表）
├─ p3 知识库
│   ├─ 状态筛选（待处理/在库/待补件/异常/已归档）
│   ├─ 标签 pills（行业洞察/内容运营/失效视频）
│   └─ 分类 tabs（行业洞察/内容运营/+4）+ 空态/列表
```

---

## 3. 全局机制（影响所有按钮的边界）

### 3.1 编辑模式（`.editing`）
- **触发**：点击「✎ 编辑模式」。进入后 `body` 加类 `editing`，所有带 `data-k` 的元素 `contentEditable=true`，可改文案。
- **隔离规则（核心边界）**：原型内**几乎所有业务交互**的 `onclick/onchange` 都以 `if(document.body.classList.contains('editing'))return;` 开头——编辑模式下点击组件**不触发任何逻辑**，避免「改文案」与「点按钮」冲突。
- **编辑模式下仍可用**（未加守卫）：页面切换 `go`、编辑模式开关本身 `toggleEdit`、导出改动 `exportJSON`、重置 `resetAll`、打印 `window.print`、p1 的「状态说明 / 刷新」图标按钮。
- **编辑模式下专用**（平时 `display:none`，仅 `body.editing` 显示）：各 `del/add` 按钮——本原型中原仅「设置页」使用此类按钮，设置页移除后，剩余页面无编辑模式专属按钮。
- **粘贴约束**：编辑模式下粘贴只允许纯文本（`paste` 事件拦截富文本/图片）；`drop` 图片被拦截。
- **退出编辑**：再次点击「✎ 编辑模式」，文案写回 `localStorage`，组件恢复可交互。

### 3.2 文案本地持久化
- 所有 `data-k` 元素的文本变更 → 存入 `localStorage` 键 `tkb_proto_edits_v1`；刷新后 `applyEdits()` 还原。
- 「导出改动」下载该 JSON；「重置」清空该键并刷新。

### 3.3 提示（toast）
- 轻量浮层，2 秒内自动消失，用于操作反馈（非阻断）。

### 3.4 路由
- `go(id)` 切换页面并显示，同步 `location.hash`（`#p1`~`#p3`）；`initRoute()` 在加载时按 hash 或 `?embed=1`/`#embed` 定位页面 / 进入嵌入模式（`body.embed` 隐藏工具栏与侧栏，仅供 Axure iframe 内联）。

---

## 4. 全局工具栏 & 侧边导航按钮规格

| 按钮 | 位置 | 触发逻辑 | 功能 | 边界条件 |
|------|------|----------|------|----------|
| 导入 / 解析 / 知识库 | 顶部 Tabs + 左侧 Nav | `go(id,this)` | 切换对应页面（p1~p3），高亮当前 Tab/Nav，同步 hash，滚动到顶 | 编辑模式下**仍可切换**（无守卫）；点击当前页无副作用 |
| ✎ 编辑模式 | 顶部 | `toggleEdit()` | 开启/关闭编辑模式（见 3.1） | 无守卫；反复点击在开/关间切换 |
| 导出改动 | 顶部 | `exportJSON()` | 下载 `tokbrain-prototype-edits.json`（localStorage 文案改动） | 无论是否编辑模式均可点；无改动时导出空对象 `{}` |
| 打印/PDF | 顶部 | `window.print()` | 调用浏览器打印（CSS `@media print` 隐藏工具栏/侧栏/删除按钮） | 无守卫 |
| 重置 | 顶部 | `resetAll()` | 二次确认后清除 localStorage 文案键并刷新页面 | 有 `confirm()` 拦截；取消则无操作 |

---

## 5. p1 导入页 · 按钮功能规格

### 5.1 本地导入

| 按钮/元素 | 触发逻辑 | 功能 | 边界条件 |
|-----------|----------|------|----------|
| 拖拽/点击上传区域（`.dropzone`） | 点击 → `p1-file-input.click()`；`dragover/drop` → `handleFiles` | 选择或拖入视频/音频/压缩包 | 编辑模式下点击与拖放**均被拦截**（dropzone `onclick` 与 `bindDrop` drop 均加守卫）；`accept` 限定 video/audio/压缩包 |
| 文件预览「×」(`removePreview`) | `onclick` | 移除单个预览项，并 `URL.revokeObjectURL` 释放内存 | 仅预览阶段可删；已提交进入队列后不可单独撤 |
| 确认上传 | `confirmUpload()` | 校验预览非空 → 把每个文件 `enqueueTask` 进导入队列 → toast | **空文件时** toast「请先拖入或选择文件」并 return；若有空态占位 `.task-empty` 一并移除 |

**任务入队与执行（`enqueueTask` / `runTask`）**
- 入队：生成一行任务（状态 `queued`），700ms 后转 `running` 开始模拟进度。
- 进度：每 500ms 随机 +8~30%，到 100% 时 **90% →「已入库」**（同时 `bumpStats()` 让「已入库」计数 +1），**10% →「失败」**（显示「重试」按钮）。
- 失败重试：`retryTask` 隐藏重试按钮并重新 `runTask`。
- 边界：进度条到 100% 即停（不会超 100%）；「已入库」计数取自 `p1-s3v` 文本解析数字再 +1，初始为 0。

### 5.2 网络视频（抖音 / B站 / 小红书）

| 按钮/元素 | 触发逻辑 | 功能 | 边界条件 |
|-----------|----------|------|----------|
| 平台 Tab（抖音/B站/小红书） | `switchNetTab(tab,this)` | 切换三端面板显隐；首次打开某端且视频网格为空则按当前 scope 渲染一次 | 编辑模式下拦截；当前 Tab 加 `.on` |
| 登录（抖音/小红书） | `loginNet('dy'/'xhs')` | 把账号行按钮置「已连接」、禁用、转 primary；描述改为「自动读取当前账号最近的喜欢或收藏」；toast | 仅抖音/小红书有登录按钮；**B站默认「已连接」**（静态状态点），无登录按钮。编辑模式拦截 |
| 范围标签（收藏/喜欢/我的作品/观看历史/读取失败） | `switchScopeTag(platform,scope,this)` | 切换 scope，更新「当前范围」标题与同步按钮文案，重渲该范围内容 | 编辑模式拦截；「收藏」同步按钮文案追加「当前收藏夹」，其余不追加；`CUR_SCOPE` 记忆每端各自范围 |
| 子分类（左侧列表项） | `selectSubcat(platform,idx,this)` | 切换左侧子分类，右侧视频网格刷新为该分类内容 | 编辑模式拦截；仅「有子分类」的范围（收藏/喜欢/我的作品/观看历史）显示左侧列表 |
| 视频卡片（`.vg-card`） | `toggleVg(card)` | 单选/取消该视频（高亮 + 右上勾选框）；联动更新全选态与「已选 n/total」 | 编辑模式拦截；点击只切换选中，不跳转 |
| 全选（`.chk-all`） | `toggleSelectAll(platform)` | 三态切换：全选→取消→全选；按 total/sel 决定 | 编辑模式拦截；无视频时三态均无效（total=0） |
| 同步按钮（「同步当前收藏夹 50 条」） | `startNetSync(source)` | 禁用按钮 2.2s 显示「正在准备文案…」，随后恢复并 `enqueueTask` 把「平台+范围 同步」任务加入导入队列 → toast | 禁用期间防重复点击；编辑模式拦截；同步条数文案为固定演示值 50 |

**范围渲染规则（`renderScope` / `renderVideos`）边界**
- 「读取失败」范围数据为**扁平结构**（无 `cat` 字段）→ 自动**隐藏左侧子分类列表**、视频**全宽**展示，并标「缺少文稿·需重试 / 已导入」状态。
- 其他范围有子分类 → 左侧列表 + 右侧 3 列视频网格。
- 某范围无预置演示数据 → 显示「「范围名」演示数据未预置，登录并同步后展示」空态。

> **v2 平台连接器（复用 zhicui-desktop 代码，2026-09-02 接入）**
> - **登录 = Cookie 握手**（非浏览器 OAuth）：zhicui 的登录是启动本机真实 Chrome/Edge 让用户扫码，服务端无法复现；故 TokBrain 复用 zhicui `douyin-login.js` 的握手契约——`POST /api/platforms/{pf}/handoff` 接收粘贴的登录态 Cookie，校验后存为密文 SecretRecord（抖音 `douyin_cookie`（原 `f2_cookie` 已弃用）/ B站 `bilibili_cookie` / 小红书 `xhs_cookie`）。
> - **拉取 = 移植 zhicui `platform-account.js` 纯逻辑**：B站走官方 Web API（`collect_bilibili`）、抖音走 zhicui 递归 aweme 遍历归一化（`collect_douyin`，**弃用 F2 发现路径**）、小红书走 URL 提取（`collect_xiaohongshu`）。`POST /api/platforms/{pf}/pull` 返回拉取结果；**三端均 `enqueue=true`** 直接走 platforms 导入管线（`create_platform_import_batch`→`enqueue_platform_batch`，`source_type=platform_pull`），不再走 F2 解析队列。
> - 详见《TokBrain按钮业务映射表_v2.md》p1「握手登录」「拉取视频」两行。

### 5.3 导入进度（补充板块）

> **展示形态（v1 修订 · 2026-10-30）**：该板块默认**折叠**，仅在存在「进行中」任务（`activeCount > 0`，即 `job.state` 不属于 `TERMINAL_JOB_STATES`）时自动展开；折叠态标题栏显示 `▸ 导入进度 · N 进行中 · 补充`，点击标题栏可展开/收起。空闲（无任务）时仅保留标题栏，不占用任务列表 / 空态高度。面板功能与交互逻辑不变（见下表）。

| 按钮 | 触发逻辑 | 功能 | 边界条件 |
|------|----------|------|----------|
| 状态说明（info 图标） | `toggleProgressHelp()` | 点击在图标**上方**弹出浮层，展示 4 项状态图例（排队中 `#93C5FD` / 解析中 `#6EE7B7` / 已完成 `#4be3a0` / 失败 `#F87171`） | 再次点击图标或点击页面其它位置收起；**编辑模式仍可打开**（无守卫） |
| 刷新（refresh 图标） | `refreshProgress()` | 图标 0.6s 旋转后重渲任务列表与状态分布 | **编辑模式仍可刷新**（无守卫） |
| 任务「重试」 | `retryTask(this)` | 重新执行失败任务 | 仅失败任务显示；重跑后隐藏重试按钮 |

> **任务颗粒度与待补件状态（v1 修订 · 2026-10-30）**：
> - **颗粒度 = 单条视频**：导入进度面板的每个 `job` 对应**一条视频**的导入/解析流程（非批次聚合）。`jobLabel(job)` 已调整为单条视频语义（视频预检 / 视频入库 / 视频精华整理 / 视频导入）。
> - **待补件状态**：新增 `await_fix` 任务状态，文案「待补件」（`jobStateLabel` 映射、圆点色 `#F59E0B`，与解析页 `await` 对齐）。**终态**——计入 `TERMINAL_JOB_STATES`，与已完成/失败同属收尾状态：计入"清除已完成"计数、显示"×"移除、不再因它强制展开面板；卡片仅展示状态文案，不提供"去补件"按钮（补件在解析页处理）。
> - ⚠️ 原型演示数据中 `job.message` 当前仍为批次统计风格（如「预检完成: 可确认 1…」），需数据源调整为单条视频视角（如「视频《xxx》· 预检通过」），与颗粒度定义对齐。

> **任务卡片布局与删除交互（v1 修订 · 2026-10-30）**：
> - **列优先排列**：任务卡片网格（`.task-list`）采用**列优先（column-major）**填充——左列先自上而下排满（先上后下），再排右列（先上后下）；两列网格下视觉顺序为 左列 a1/a2/a3、右列 a4/a5/a6。行数随条目数动态计算 `ceil(n/2)`，奇数条末行右列留空。
> - **删除防误判过渡**：点击单条「×」删除时，被点卡片先在原位以 `opacity→0 / scale(.96)` 淡出 0.25s（`.task.removing`），确认是本条本人在离开，之后才从 `dismissed` 集合移除、其余卡片回填。此过渡消除"删除瞬间两列网格回填导致误以为删了下一条"的视觉歧义。

### 5.4 飞书发现批次提示（v1 修订 · 2026-11-02）

手机往飞书表丢链接 → 本地 bridge 自动发现 → **静默补齐**（f2 预检拿标题/作者/封面）→ 补齐结束后**一次性弹出**，由用户决定是否开始下载与知识化。

> **核心约束：补齐期间界面必须完全静默。** 用户只看一次数字，不允许在补齐过程中看到它从 1 跳到 3。实现上不依赖"轮次表"：只要 `dispatch` 里还有**非终态**记录（`queued`/`processing` 等），后端一律返回 `state=filling` 且清单为空，前端不渲染任何东西；全部落终态后才给出固定数字。

**接口**

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/sync/discovery` | 返回 `state`（`idle`/`filling`/`ready`）、`total`、`resolvable`、`unresolved`、`items`、`truncated`。`filling` 时计数全 0、`items` 为空 |
| POST | `/api/sync/discovery/confirm` | body `{item_ids:[...]}`，逐批 confirm + 建 ingest job，把 dispatch 推进到 `ingested` |

**四种状态**

| 状态 | 触发 | 表现 |
|------|------|------|
| 静默 | `state=filling` | 什么都不渲染（连灰条都不给） |
| 收起态（默认） | `state=ready` 且未点「稍后」 | 顶部横幅：琥珀左竖条 + `飞书发现 N 条新链接，已补齐信息` + 可解析/未取到信息分述 |
| 展开清单 | 点「查看清单」 | 卡内展开，可逐条勾选；可解析项在前、未取到信息的在后并按原因说明 |
| 已稍后 | 点「稍后」 | 横幅收起，**topbar 右侧留琥珀 chip `飞书 N 条待解析`**，点击可重新展开 |

**按钮规格**

| 按钮 | 功能 | 边界条件 |
|------|------|----------|
| 解析这 N 条 | 提交选中项，`N` 为已勾选数而非总数 | 选中为空时禁用；提交中显示「提交中…」并禁用；成功后刷新卡片与任务队列 |
| 查看清单 | 展开/收起清单 | **`total < 3` 时禁用**（少于 3 条不值得展开，直接解析） |
| 稍后 | 记下批次签名到 `localStorage['tokbrain.discovery.dismissed']`，降级为 chip | 签名只取 `record_id` 拼接，标题变化不应让"已稍后"失效；有新链接（签名变）自动重新弹出 |

**字段与状态约定**

- 文案不写"已补齐"就完事：**必须同时报出未取到的条数**。原句「已补齐信息，是否解析？」在部分失败时不成立，会让用户点下去才发现解析不了。
- 清单条目 `state`：`ready`（可勾选） / `unresolved`（置灰不可选，展示失败原因）。`unresolved` 只统计**最近 30 分钟**的失败，避免把历史 429 积压（当前库中 121 条）算进"本轮刚失败"。
- 清单最多列 10 条，多余部分以 `truncated` 数量提示。
- 确认只提交勾选项；未勾选的留在 `awaiting_confirm`，下次还能再点，不会被静默吞掉。
- 一个飞书行 = 一个 import batch（bridge `process_link` 逐行建批），确认时按 batch 分组逐批 confirm。

**dispatch 状态机补充（本轮新增 `ingested`）**

```
pending → queued → processing → awaiting_confirm → ingested → processed
                                        ↑                ↑
                              等用户点「解析」    已提交解析，等回填笔记
```

- `awaiting_confirm`：预检已完成，球在用户侧。属**终态**，慢轨不会重复导入。
- `ingested`：网页/控制面已确认并提交解析，只剩 bridge 回填 Obsidian。同样属**终态**——若当非终态会被 `process_queue` 重新 `process_link` 一次。
- bridge 新增 `resume_ingested()`：扫 `ingested` 记录，按飞书标签建收藏夹并挂作品 → 回填笔记 → 推进 `processed`；作品还没解析完时跳过、留到下轮再看，避免写出一篇空笔记。
- 飞书「处理状态」列选项必须覆盖全部映射值：本次补上「待确认」「解析中」（此前 `STATUS_OPTIONS` 缺这两项，回写会撞"选项不存在"而失败）。

**边界场景**

| 场景 | 表现 |
|------|------|
| bridge 未运行 | 库中无 `awaiting_confirm` → `state=idle`，不弹 |
| 用户不点任何按钮直接关页面 | 下次打开仍在（`awaiting_confirm` 是持久态），不会丢 |
| 补齐中途页面刷新 | 后端仍返回 `filling`，界面保持静默，补齐完再弹 |
| 本轮 3 条全部预检失败 | `resolvable=0`，「解析这 0 条」禁用，清单只列失败原因 |

---

## 6. p2 解析页 · 按钮功能规格

### 6.1 解析进度（第一板块）

| 按钮/元素 | 触发逻辑 | 功能 | 边界条件 |
|-----------|----------|------|----------|
| 选择全部可确认项（链接） | `toggleParseSelectAll()` | 在全部 `confirm` 态条目间切换全选 | 若无任何 `confirm` 条目 → 直接 return（无 toast）；否则按「当前是否全选」反转 |
| 确认加入待入库（n） | `confirmParseQueue()` | 把已勾选条目移出预检队列、写入待入库，更新统计 | **未勾选任何项** → toast「请先选择要加入待入库的作品」并 return；勾选项仅限 `confirm` 态（其它态不渲染复选框）；成功后 `stats.confirm -=n`、`stats.left=max(0,left-n)`、总数重算 |
| 分页 `<` `/` `>`/页码 | `changeParsePage(idx)` | 翻页（每页 5 条） | 越界 `idx<0` 或 `>=页数` 直接 return；首页 `<` 禁用、末页 `>` 禁用（按钮 `disabled`） |
| 单条复选框 | `toggleParseItem(el)` | 勾选/取消该预检结果，联动确认按钮数字 | **`parsing`（解析中）态不渲染复选框**，无法勾选 |
| 删除预检结果（链接） | `removeParseItem(id)` | `confirm()` 后删除该条，对应状态计数 -1，总数=剩余条数 | 删除后若翻页越界自动回退到末页；解析中态显示「暂停该任务」而非删除 |
| 暂停该任务 | `pauseParseItem(id)` | **原型仅 toast「已暂停该任务：标题」**，无真实暂停逻辑 | 仅 `parsing` 态显示；产品需补真实暂停/恢复 |
| 模型标签「×」 | `removeModelTag(id,tag)` | 从 `modelTags` 移除该标签并重渲 | 仅 `confirm` 态卡片显示标签区；标签数组为空时显示「暂无模型标签」 |
| 视频标签下拉（一级~四级） | `onchange→updateParseLabel(sel)` | 写入该条 `labels[key]` | 选项来自知识库层级 `KB_L1~KB_L4`；仅 `confirm` 态可编辑 |

**单条卡片状态机（解析页 `itemStatus`，2026-10-30 校正）**
- 激活态（显示旋转 spinner + 中文态名，时间轴高亮当前节点）：`queued` 排队中（青）· `resolving` 解析中（青）· `pending` **等待中**（青，2026-10-30 新增——平台拉取导入的初始状态）。
- 终态/收尾态（无 spinner，显示小胶囊标签）：`ready` 可以确认（绿）· `needs_local_file` 需要补件（黄）· `confirmed` 已加入待入库（绿）· `failed` 解析失败（粉）· `blocked` 因风控停止（粉）· `cancelled` 已中断（灰）· `duplicate` 已在知识库/重复（灰）。
- 时间轴 4 节点（预检 / 音视分离 / 模型总结 / 标签分类）按 `stageDone(status)` 填充：`pending`/`queued`→0（全空心）、`resolving`→1（第 2 节点高亮）、`needs_local_file`→2、`ready`/`confirmed`→4（全 ✓）。
- 顶部 6 统计卡顺序固定：重复 / 中断 / 失败 / 需补件 / 待分类 / 可确认。

> ⚠️ **文档漂移提醒**：上一段（`PARSE_STATE_LABELS` 的 `confirm/await/dup/fail/stop/left/parsing` 英文名）为早期实现，与当前 `ParseWorkspace.tsx` 实际 `itemStatus` 不一致（真实态名见上）。`pending` 与 `queued` 此前在文档里完全缺失。本段为 2026-10-30 校正后的权威定义，6.1 整节待统一刷新。

> **数据源备注（2026-10-30）**：`pending` 是 `import_integrations.py` 平台拉取导入创建条目时的**初始状态**（line 686）。`enqueue_platform_batch` 将其入队，但协调器 worker（`import_queue.py:1085`）只处理 `status=="queued"` 的条目，遇到 `pending` 直接跳过、永不提升——导致大量 `pending` 条目卡死（实测 365 条，占 `import_items` 总数 419 的 87%）。前端已对 `pending` 做优雅兜底（显示「等待中」+ spinner）；根因修复需后端把 `pending→queued` 的提升补上（详见对话结论，待产品/研发拍板是否改后端）。

### 6.2 模型配置（第二板块，左对话模型 / 右音频模型）

> 左右两栏逻辑**完全对称**，下表以「对话模型」为例，音频模型函数前缀为 `Audio`（行为一致）。

| 按钮/元素 | 触发逻辑 | 功能 | 边界条件 |
|-----------|----------|------|----------|
| ＋ 接入新模型 | `toggleModelForm()` | 展开接入表单并隐藏「＋」按钮；再次点击（或点取消）收起 | 表单展开时「＋」按钮 `display:none`；收起即调用 `cancelModelForm` |
| 取消 | `cancelModelForm()` | 隐藏表单、恢复「＋」按钮、清空 5 个输入、启用开关复位 `.on` | — |
| API Key 眼睛 👁 | `toggleModelKeyVisibility()` | 切换输入框 `password`↔`text` 显隐 | 仅改 type，不持久改变量 |
| 启用开关 | `toggleModelEnable()` | 切换 `MODEL_FORM_ENABLED`（开/关） | 默认开；保存时作为该模型 `enabled` 值 |
| 保存并设为当前 | `saveModel()` | 校验 5 字段非空 → 清空其它模型 `current`、新增模型 `current:true` → 收起表单、重渲列表、toast | **任一字段为空** → toast「请填写完整模型信息」并 return（不保存）；保存后新模型即「当前模型」 |
| 模型列表「×」删除 | `deleteModel(id)` | `confirm()` 后从列表移除并重渲 | 二次确认；不限制删除「当前模型」（产品需决定删除当前模型后的回退策略） |

---

## 7. p3 知识库页 · 按钮功能规格

| 按钮/元素 | 触发逻辑 | 功能 | 边界条件 |
|-----------|----------|------|----------|
| 状态筛选（待处理/在库/待补件/异常/已归档） | `filterKb(state,this)` | 切换筛选态，更新「N个结果」与空态/列表显隐 | 计数为**硬编码** `{pending:0,inlib:0,await:0,error:1,archive:0}`（原型演示，非数据驱动）；`count===0` 显空态、隐藏列表，否则反之；初始默认 `pending`（0 个 → 显示空态） |
| 标签 pills（行业洞察/内容运营/失效视频） | `toggleKbPill(pill)` | 切换 pill 选中态（多选筛选） | 编辑模式拦截；「失效视频」为 warn 样式 |
| 分类 tabs（行业洞察/内容运营/+4） | `selectKbCat(btn)` | 单选切换当前分类（高亮） | 编辑模式拦截；「+4」为占位，无真实子分类数据 |

> 说明：p3 列表/空态在原型中为静态占位（`#p3-list` 默认 `display:none`，`#p3-empty` 默认显示）。**产品需实现**：按筛选+标签+分类拉取真实知识条目，渲染 `kb-item`（缩略图/标题/副标题/标签）。

---


## 8. 状态清单 / 边界场景

> 时效说明：本节依据 `frontend/app/page.tsx`、`frontend/components/ImportWorkspace.tsx`、`ParseWorkspace.tsx` 当前实现整理（2026-10-30）。原型存在三套并行状态机：批次（batch）/ 任务（job）/ 条目（item），另含解析页卡片状态机 `PARSE_STATE_LABELS`（见 6.1）。

### 8.1 状态模型总览

| 层级 | 状态字段 | 文案映射 | 用途 |
|------|----------|----------|------|
| 批次 | `batch.state` | `stateLabel()` | 顶部栏 / 导入进度整体状态 |
| 任务 | `job.state` | `jobStateLabel()` | 导入进度逐条任务 |
| 条目 | `item.status` | 集合判定 | 批次内单条素材处理态 |
| 解析卡片 | `PARSE_STATE_LABELS` | — | p2 解析页单条卡片（见 6.1） |

### 8.2 批次状态（batch.state → 文案）

`stateLabel(state)`：`queued`→等待中、`running`→进行中、`automating`→自动确认中、`cancelling`→安全停止中、`cancelled`→已中断、`succeeded`→已完成、`partial`→部分完成、`failed`→失败。

终端态集合 `TERMINAL_BATCH_STATES = {succeeded, partial, failed, cancelled}`。

**「解析中」覆盖规则（重点）**：当批次自身已终态（`batchIsTerminal` 为真）但仍有活跃条目（`batchHasActiveItems` 为真，即任一 `item.status ∈ {queued, resolving, pending}`）时，顶部状态视为"解析中"——语义为"批次总体已收尾，但仍有条目在排队/解析/待处理中"，刻意不显示终态以免误导。

> **实现决策（2026-10-30）**：原顶部栏批次徽标（显示"解析中"的 `<span class="pill">`）已**移除**，该状态不再以顶部徽标呈现；状态穿透改由「导入进度」任务列表与汇总计数体现（见 8.5）。

### 8.3 任务状态（job.state → 文案）

`jobStateLabel(state)`：`queued`→排队中、`running`→**解析中**、`automating`→自动确认中、`cancelling`→安全停止中、`cancelled`→已中断、`succeeded`→已完成、`partial`→部分完成、`failed`→失败、`await_fix`→**待补件**（终态，黄 `#F59E0B`）。

> 注意：任务级 `running` 即显示"解析中"，与 8.2 的覆盖标签**同名不同义**——前者是单任务在运行，后者是"批次终态 + 残留活跃条目"的聚合态。

### 8.4 条目状态（item.status）

- 活跃态 `ACTIVE_ITEM_STATES = {queued, resolving, pending}`：参与"解析中"聚合判定。
- 保留展示态 `RETAINED_IMPORT_STATUSES = {ready, needs_local_file}`：导入进度保留显示。
- 其它（成功/失败等）随批次终态收口。

### 8.5 边界场景

- **B1 批次终态 + 残留活跃条目**：批次 `succeeded/partial/failed/cancelled` 下，只要仍有 `queued/resolving/pending` 条目，历史上顶部显示"解析中"覆盖标签；现已移除顶部徽标。
- **B2 批次汇总计数**：`lastBatchSummary` 中「解析中 N = pending + resolving + queued 条目数」，N>0 时显示"解析中 N"。
- **B3 状态说明弹层**（导入进度 info 图标，4 态色板）：排队中 `#93C5FD` / 解析中 `#6EE7B7` / 已完成 `#4be3a0` / 失败 `#F87171`。
- **B4 解析页独立计数**：`ParseWorkspace` 自有 `resolving` 态与"解析中 N"计数（p2 解析进度，见 6.1）。
- **B5 同名多义**：全站"解析中"出现 4 处（顶部徽标[已移除] / 任务行 / 批次汇总 / 解析页），含义不同，UI 文案与埋点需区分。
- **B6 任务卡片删除误判**：原"点 × 删成下一条"实为**两列网格（`1fr 1fr`）删除后瞬间回填**的视觉错位——`dismissJob(job.id)` 按唯一 `job.id` 隐藏、数据库 id 全唯一、无 key 冲突，删除逻辑本身正确。已通过**列优先排列 + 删除淡出过渡（0.25s）**消除歧义（见 5.3）。

### 8.6 待产品确认

- 顶部徽标移除后，"批次终态但残留活跃条目"是否需在其它入口（如导入进度标题）提示？当前依赖任务列表体现。
- "解析中"四义是否统一/拆分（建议区分"批次收尾中"与"任务运行中"）。

---

## 9. 边界条件汇总（研发必读）

### 9.1 编辑模式隔离边界
- ✅ 编辑模式下**禁用**：平台切换、登录、范围标签、子分类、视频选择、全选、同步、解析页全部操作、模型增删改、知识库筛选/标签/分类。
- ✅ 编辑模式下**仍可用**：页面导航、编辑开关、导出、重置、打印、p1 帮助/刷新。
- ⚠️ 例外风险：`confirmUpload` 与 `handleFiles` **未加编辑守卫**，但上传区点击/拖放在编辑模式下已被拦截，故实际无法触发；若后续放开上传区需同步加守卫。

### 9.2 空/无数据边界
- 本地导入无文件 → 确认上传拦截并提示。
- 网络某范围无预置数据 → 显示「演示数据未预置」空态。
- 「读取失败」范围 → 自动全宽、无左侧子分类。
- 解析预检结果为空 → 解析进度区显示「暂无预检结果」并清空分页/确认数字。
- 模型列表为空 → 显示「暂无自定义模型，点击右上角接入」。
- 知识库筛选 count=0 → 显示空态插画。

### 9.3 计数与状态一致性边界
- `confirmParseQueue` 仅处理 `confirm` 态勾选项；成功后递减 `confirm` 与 `left` 计数，总数=剩余条数（`done=total`）。
- `removeParseItem` 删除时按该条 `state` 递减对应统计；删除后翻页越界自动回退。
- p1「已入库」计数只在模拟任务成功时 +1，其它统计卡（处理量/AI 用量/待入库）原型内不联动。
- `runTask` 失败概率固定 10%，仅为演示，真实产品应由后端返回状态。

### 9.4 防重复 / 防错边界
- 同步按钮点击后 2.2s 内 `disabled`，防重复提交。
- 删除类操作（模型/预检结果/重置）均 `confirm()` 二次确认。
- 分页首尾箭头 `disabled` 防越界；`changeParsePage` 越界 return。

---

## 10. 验收标准（建议）

1. 三个页面（导入 p1 / 解析 p2 / 知识库 p3）可经顶部 Tab 与左侧 Nav 互相切换，hash 可定位（含 `?embed=1` 嵌入）。
2. 编辑模式开启后，除 3.1 所列「仍可用/专属」项外，所有业务按钮点击无副作用；文案改动刷新后保留。
3. 本地上传→确认→进入队列→进度→成功(计数+1)/失败(可重试) 全链路可演示。
4. 网络三端 Tab/范围/子分类/视频选择/全选/同步 交互符合第 5.2 节，且「读取失败」范围自动全宽。
5. 解析页统计/分页/全选/确认/删除/标签编辑 符合第 6.1 节；解析中态隐藏复选框与删除、显示暂停。
6. 模型接入表单五字段校验、保存即设为当前、删除二次确认 符合第 6.2 节。
7. 知识库筛选/标签/分类切换符合第 7 节（真实数据接入前以空态占位）。

---

## 11. 待确认问题（评审出）

- Q1：克隆训练页（脑图规划中的独立模块）在导航中应如何安放？设置页已从原型与 PRD 移除，是否需要在后续版本补回设置/偏好模块？
- Q2：知识库（p3）的筛选计数、标签、分类是否需后端实时聚合？原型为硬编码。
- Q3：模型「当前模型」被删除后的回退策略（自动选下一个 / 提示用户选择）？
- Q4：导入进度中「失败 10% 随机」是否需替换为真实状态机（含失败原因与重试策略）？
- Q5：网络同步是「按范围整体同步」还是「仅同步用户勾选的视频」？当前原型按钮为「同步当前收藏夹」，但卡片支持逐条勾选——二者关系需明确。

---

## 12. 按钮 → 业务功能映射表（v2 原型，代码溯源）

> 本节为 §4~§8 按钮规格的「代码级溯源」补充：**仅查询项目代码**，每个按钮从原型处理函数回溯到后端 `app/routers_v2/*` 真实接口（method + path + 文件:行号）。原配套原型 `frontend/public/tokbrain-prototype-v2.html` 已不存在，现为 `prototypes/tokbrain-prototype-v2.html`（静态单文件，不直连后端）。完整版见 `docs/TokBrain按钮业务映射表_v2.md`。

| 页面 | 按钮 / 交互 | 触发函数 | 业务功能（人话） | 真实后端接口（method + path · router:行） | 边界 / 降级 |
|------|------------|----------|------------------|--------------------------------------------|--------------|
| 全局 | 📥导入/⚙️解析/📚知识库 Tab | `go(id)` | 切换页面 + 同步 hash | 无 | 点击当前页无副作用 |
| 全局 | 🔄 刷新数据 | `refreshAll()` | 重拉 任务/解析/知识库/设置 | `GET /api/jobs`(jobs.py:22)+`GET /api/import-batches/{id}`(imports.py:189)+`GET /api/library/works`(library.py:341)+`GET /api/settings`(settings.py:59) | 单块失败则该块用 mock |
| 全局 | 🗺️ 按钮→业务映射 | `toggleLegend()` | 显示/隐藏映射图例 | 无 | — |
| p1 | 拖入/点击选择 | `onFiles()` | 选本地视频/音频入预览 | 无 | `accept` 过滤 |
| p1 | 预览 × 移除 | `rmPreview(id)` | 上传前移除预览 | 无 | 不影响已上传批次 |
| p1 | 确认上传 | `confirmUpload()` | 建批次 + 逐文件上传 | `POST /api/local-import-batches`(imports.py:88)+`PUT …/video`(imports.py:103) | 空列表 toast 拦截 |
| p1 | 平台 Tab | `switchNet(pf)` | 切换平台面板 | 无 | 三平台复用同一令牌 |
| p1 | 🔗 连接抖音 | `connectPlatform()` | **生成平台连接令牌**（真实接入） | `POST /api/settings/integration-token`(settings.py:105) | 失败 toast |
| p1 | 🔐 握手登录（粘贴 Cookie） | `handoffLogin()` / `submitHandoff()` | 复用 zhicui 桌面端登录契约：粘贴平台登录态 Cookie，服务端校验后存为密文 SecretRecord（抖音→`douyin_cookie`（原 `f2_cookie` 已弃用）、B站→`bilibili_cookie`、小红书→`xhs_cookie`） | `POST /api/platforms/{pf}/handoff`（platforms.py）→ `set_secret` | 校验 Cookie 名（B站 SESSDATA+DedeUserID / 抖音 sessionid+sessionid_ss / 小红书 web_session）；缺失返回 401 |
| p1 | 📥 拉取视频 | `pullPlatform()` | 用已保存 Cookie 调平台接口拉取收藏/喜欢：B站=官方 Web API、抖音=zhicui 递归 aweme 遍历归一化、小红书=URL 提取（均移植自 zhicui `platform-account.js` / `douyin-login.js`） | `POST /api/platforms/{pf}/pull`（platforms.py）→ `collect_bilibili` / `collect_douyin` / `collect_xiaohongshu` | **三端均 `enqueue=true`** 走 platforms 导入管线（zhicui 方案，弃用 F2）；抖音已弃用 F2 发现路径；Cookie 失效/风控返回干净错误（502） |
| p1 | 范围 chips | `switchScope()` | 切本地状态 | 无 | 真实数据由外部工具拉取 |
| p1 | 🔄 同步当前连接 | `syncNet()` | 校验连接 + 刷新进度 | `GET /api/settings/integration-token`(settings.py:100)+`GET /api/jobs`(jobs.py:22) | 未连接提示 |
| p1 | 状态说明 | `toggleProgressHelp()` | 弹层 5 态说明 | 无 | — |
| p1 | ↻ 刷新（进度） | `refreshJobs()` | 拉任务列表 | `GET /api/jobs`(jobs.py:22) | 失败用 mock |
| p1 | 🔁 重试失败导入 | `retryFailedImports()` | 一键重排失败导入 | `POST /api/jobs/retry`(jobs.py:22)→`retry_failed_imports()` | 无可重试提示；不可达降级演示 |
| p2 | 选择全部可确认项 | `toggleParseSelectAll()` | 选中 `done` 条目 | 无 | 无可确认项 toast |
| p2 | 确认加入待入库 | `confirmParseQueue()` | 转知识库作品 | `POST /api/import-batches/{id}/confirm`(imports.py:206) | 无批次/未选 toast |
| p2 | 分页 ‹ › | `parseGo(p)` | 前端分页 | 无 | 首尾页箭头禁用 |
| p2 | 单条复选 | `toggleParseItem(id)` | 选中条目 | 无 | 仅 `done` 可勾 |
| p2 | 删除（预检结果） | `rmParseItem(id)` | 删除条目 | `DELETE /api/import-items/{id}`(imports.py:255) | 二次确认 |
| p2 | 取消批次 | `cancelBatch()` | 取消当前批次 | `POST /api/import-batches/{id}/cancel`(imports.py:197) | 二次确认 |
| p2 | 条目分类下拉 | `setItemCollection(id)` | 改条目分类 | `PATCH /api/import-items/{id}`(imports.py:150) | 选项来自 `GET /api/library/collections`(library.py:121) |
| p2 | ＋ 接入新模型 | `openModelForm(kind)` | 展开模型表单 | 无（数据来自 `GET /api/settings`） | — |
| p2 | 👁 显隐 Key | `toggleKey()` | 切换密文 | 无 | — |
| p2 | 保存并设为当前 | `saveModel()` | 保存模型并设为当前 | `PUT /api/settings`(settings.py:64) | 失败 toast |
| p2 | 取消（模型表单） | `cancelModelForm()` | 收起表单 | 无 | — |
| p3 | 状态筛选 chips | `setKbState(k)` | 按 `library_state` 筛选 | `GET /api/library/works?library_state=`(library.py:341) | 空态占位 |
| p3 | 标签 pills | `toggleKbPill()` | 前端 tags 过滤 | 无 | — |
| p3 | 分类 tabs | `setKbCat(id)` | 按分类筛选 | `GET /api/library/collections`(library.py:121)+`works?collection_id=`(library.py:341) | 无分类=全部 |
| p3 | 重试 | `retryWork(id)` | 重新解析/抽取 | `POST /api/library/works/{id}/retry`(library.py:682) | 仅 issues/pending |
| p3 | 补件 | `supplementWork(id)` | 上传补充素材 | `POST /api/library/works/{id}/supplement`(library.py:738) | 文件上传 |
| p3 | 删除 | `removeWork(id)` | 删除作品 | `DELETE /api/library/works/{id}`(library.py:799) | 二次确认 |
| p3 | 恢复 | `restoreWork(id)` | 恢复归档 | `POST /api/library/works/{id}/restore`(library.py:782) | 仅 archived |

### 12.1 五项已确认决策（2026-09-02）
1. **平台「连接 / 登录」= 两者都做且已落地**：「连接」走真实令牌（`POST /api/settings/integration-token`，外部工具 f2/doubaosave 用令牌推收藏/喜欢）；「🔐 握手登录（粘贴 Cookie）」已实现为 Cookie 握手（`POST /api/platforms/{pf}/handoff`，复用 zhicui `douyin-login.js` 契约，存为 SecretRecord），非占位；「📥 拉取视频」已实现（`POST /api/platforms/{pf}/pull` 移植自 zhicui `platform-account.js`）。真实浏览器型 OAuth 授权模块仍属后续（需第三方应用凭据）。
2. **p2 补「取消批次」按钮**（红色，二次确认）→ `POST /api/import-batches/{id}/cancel`。
3. **p2 每条目补「归类到知识库分类」下拉** → `PATCH /api/import-items/{id}` `{target_collection_id}`，选项来自 `GET /api/library/collections`。
4. **导入失败「重试」已实现 retry 端点**：`POST /api/jobs/retry`(jobs.py:22)→`retry_failed_imports()` 一键重排 `processing_state=failed` 的作品重新入库；原型 p1 已加「🔁 重试失败导入」按钮（推翻原「保持现状」决定，2026-09-02 用户授权实现）。
5. **抖音弃用 F2、三端统一走 zhicui 方案**：复用 `E:\Program Files\zhicui-desktop` 的 `platform-account.js` / `douyin-login.js` / `security.js`，新增 `app/services/platforms/`（handoff + `collect_douyin` 递归 aweme 归一化 + 三端 `pull`）；抖音登录态改存 `douyin_cookie`，**F2 发现路径（`app/services/f2_links.py`）已弃用**（仅保留 F2 媒体下载老路径，待后续替换为签名媒体下载器）。三端「拉取」均 `enqueue=true` 走 platforms 导入管线。

---

*本 PRD 依据 v1 原型（`prototypes/tokbrain-prototype-v1.html`，含其内联 JS 与 `原型设计.md`）整理，仅作设计沿革保留；设置页 p4 已从原型与本文档移除，现仅覆盖导入 p1 / 解析 p2 / 知识库 p3。所有「触发逻辑/边界条件」均可回溯到原型代码。按钮→业务映射（§12）额外依据 v2 原型 `prototypes/tokbrain-prototype-v2.html` 与 `app/routers_v2/*` 后端接口。当前生产页面为四页签结构，与本 PRD 的差异以[功能层级结构图](TokBrain功能层级结构图.md)为准。*
