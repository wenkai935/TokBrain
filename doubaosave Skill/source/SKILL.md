---
name: doubaosave
description: "抖音个人数据采集与飞书多维表格导入工具。采集自用抖音账户的喜欢、收藏（含收藏夹层级结构与各层视频链接、收藏夹ID/收藏夹路径字段）、观看历史及每个视频的分享链接、标题、作者、发布时间等，并自动导入到飞书多维表格（obsidian知识库收件箱）。当用户要求采集抖音收藏/喜欢/观看历史、获取收藏夹层级结构、备份抖音数据、将抖音视频导入飞书表格、或提到 obsidian知识库收件箱 时使用。"
---

# doubaosave — 抖音数据采集与飞书导入

> **时效声明（2026-10-30 校订）**：本 Skill 仍被 TokBrain 生产链路调用——`app/services/doubaosave_bridge.py` 以子进程方式执行本目录的 `scripts/main.py`（`--only favor|like|history|post`），读取 `output/douyin_data_*.json`，由 `/api/platforms/douyin/dom-collect` 触发。请勿移动或重命名本目录（路径含空格，改动会破坏桥接）。

## 概述

两步完成抖音个人数据备份：
1. **采集**：用 Playwright 登录抖音网页端，拦截 API 响应获取喜欢、收藏（含收藏夹层级树，登录后自动采集）、观看历史的结构化数据
2. **导入**：将采集结果批量写入飞书多维表格，自动去重、分类标记

## 前置条件

执行前确认环境已就绪：

```bash
# Python 依赖
pip install playwright
playwright install chromium

# 飞书 CLI（导入步骤必需）
which lark-cli   # 确认已安装并完成用户授权
```

如 `lark-cli` 未安装或未授权，导入步骤会失败；采集步骤不依赖 lark-cli。

## 工作流程

### 第一步：采集抖音数据

脚本路径：`scripts/main.py`

```bash
# 首次运行（弹出浏览器，需手动扫码/密码登录）
python scripts/main.py

# 后续运行（登录态已保存，可无头运行）
python scripts/main.py --headless

# 只采集某个分类
python scripts/main.py --only like      # 喜欢
python scripts/main.py --only favor     # 收藏（默认采集收藏夹层级树）
python scripts/main.py --only history   # 观看历史

# 收藏只采指定收藏夹（可逗号分隔多个），其余夹自动跳过（9-06 起推荐）
python scripts/main.py --only favor --folder 家装
python scripts/main.py --only favor --folder 家装,露营

# 收藏改回旧扁平 DOM 方式采集（v2 收藏夹路线异常时的兜底，一般用不到）
python scripts/main.py --flat-favorites

# 调整滚动深度（数据量大时调大，默认100）
python scripts/main.py --max-scroll 200
```

**首次运行**：浏览器弹出后完成抖音登录，脚本检测到登录按钮消失后自动继续。登录态保存在 `browser_profile/` 目录，后续无需重新登录。

**收藏夹层级树采集**：登录后自动执行。通过 `/aweme/v1/web/collects/list/` 解析收藏夹列表（名字/ID 真字段为 `collects_name`/`collects_id`，多夹分页滚动收全），再逐夹确定性进入（浏览页 LI 精确点击，失败兜底 `goto /collection/{id}`），滚动加载以 API 驱动（`collects/video` 的 `has_more` 判定，勿逐轮全量扫 DOM）。整夹全量耗时较长，可用 `--folder 夹名` 定向（详见「收藏夹采集实战」章节）。API 拦截异常时才回退 DOM 方式（`--flat-favorites`）。

**输出**：`output/` 目录下生成：
- `douyin_data_YYYYMMDD_HHMMSS.json` — 完整数据（含所有分类）
- `收藏夹结构_YYYYMMDD_HHMMSS.json` — 收藏夹层级树（每节点含 collects_id、collects_name、videos）
- `喜欢_*.csv`、`收藏_父夹_子夹_*.csv`（层级路径命名）、`观看历史_*.csv` — 各分类 CSV

### 第二步：导入飞书多维表格

脚本路径：`scripts/import_to_bitable.py`

目标表格：obsidian知识库收件箱 → 视频收藏表

```bash
# 导入全部采集数据
python scripts/import_to_bitable.py output/douyin_data_YYYYMMDD_HHMMSS.json

# 预览模式（不写入，仅展示将导入的内容）
python scripts/import_to_bitable.py output/xxx.json --dry-run

# 只导入某个分类
python scripts/import_to_bitable.py output/xxx.json --only like
python scripts/import_to_bitable.py output/xxx.json --only favor
python scripts/import_to_bitable.py output/xxx.json --only history

# 关闭去重（可能产生重复记录，不建议）
python scripts/import_to_bitable.py output/xxx.json --no-dedup
```

**导入特性**：
- 自动去重：按视频链接判断，已存在的记录跳过
- 批量写入：每批最多 200 条，自动分批
- 分类标记：来源（喜欢/收藏-夹名/观看历史）和标签自动填入；收藏夹层级树会递归展平，标签带完整层级路径（如 `收藏-夹A/子夹B`）
- 收藏子分类字段：每条收藏记录自动拆分「收藏夹」（父夹）与「子收藏夹」（子级路径）两个独立字段，方便按夹筛选；旧数据无字段时从标签回退拆分
- 交互确认：导入前展示预览，需输入 `y` 确认

## 字段映射

> 实际目标表格字段可能与早期版本不同，运行前请用 `lark-cli base +field-list` 核对。以下映射为当前飞书「视频收藏」表真实字段。

| 抖音数据 | 多维表格字段 | 说明 |
|---------|------------|------|
| 分享链接 | 链接 | 抖音短链优先，回退视频详情页链接 |
| 标题 | 标题 | 视频描述 |
| 作者 | 作者 | 作者昵称 |
| 来源分类 | 来源 | 喜欢 / 收藏-夹名（层级路径）/ 观看历史 |
| 发布时间 | 发布时间 | 视频发布时间 |
| 收藏夹路径 | 标签3 | 完整层级路径，如 `收藏夹/家装` |
| 收藏夹路径（父夹） | 父级 | 父收藏夹（如 `收藏`） |
| 收藏夹路径（子级） | 子级 | 子收藏夹/层级路径（如 `家装`） |
| 分享链接 | LID | `lid_` + sha256(链接) 前 16 位，跨系统去重 |
| 收藏夹ID | — | 采集端字段，供去重/溯源，不导入表格 |
| — | 标签2、标签4、标签5 | 留空，供后续手动标注 |

## 已验证技术路径参考

以下路径来自知萃桌面端（zhicui-desktop v1.0.9，`resources/app.asar` 内 `dist/platform-account.js` / `dist/douyin-login.js` 逆向分析）已实际运行验证，本 skill 的脚本已采纳其中关键点；如遇抖音风控或页面改版，优先按此路径调整。

| 技术点 | 知萃已验证做法 | 本 skill 采纳情况 |
|--------|--------------|------------------|
| 浏览器启动 | `chromium.launchPersistentContext` 优先 `channel='chrome'`/`'msedge'` 复用本机浏览器，无需自带 chromium；失败回退 | ✅ 已采纳（`_launch_context`） |
| 登录态检测 | 轮询认证 cookie：抖音 `sessionid`/`sessionid_ss`、B站 `SESSDATA`/`DedeUserID`、小红书 `web_session`；比页面元素判断更稳 | ✅ 已采纳（`_has_douyin_session`） |
| Tab/收藏夹切换 | 精确文本匹配 `getByText(label, {exact:true})`，遍历可见候选逐个点击，避免误点同名元素 | ✅ 已采纳（`_scrape_one_collection`、`_select_collection`） |
| 收藏夹子分类 | 拦截 `collects/list` 拿收藏夹列表（含 `children` 递归），逐夹进入 `/collection/{id}` 采集；每条视频标注收藏夹 ID 与层级路径 | ✅ 已采纳（收藏夹树 + `收藏夹ID/收藏夹路径` 字段） |
| 数据采集双通道 | ① 拦截 `aweme|favorite|collection|post|feed|detail|like` 相关响应递归解析 JSON；② 抓取可见卡片 `a[href*='/video/']` 的 href/封面/文案 兜底，按 videoId 合并去重 | ⚠️ 部分采纳：本 skill 以 API 拦截为主，DOM 仅收集 ID；知萃的"质量评分合并"可作后续增强 |
| 登录超时/前台引导 | 后台等 15s 无果 → 置 `needs-action` 状态并把浏览器带到前台，给用户 2 分钟手动完成验证/切换 | 可参考：本 skill 登录等待登录按钮消失（5 分钟） |
| 采集节流 | 滚动间隔 0.85–0.9s，连续 5 轮无新增停止；B站 API 单页上限 50（like）/20（收藏）自动分页 | ⚠️ 部分一致：本 skill 默认 2.5s 间隔、100 轮上限 |

**知萃桌面端其余可复用路径**（未在 skill 脚本中实现，仅记录）：
- 平台会话隔离：持久化 profile 按 `sha256(用户标识)` 分目录存放，断开即删，多账号互不污染
- 输入/URL 白名单校验：`awemeId` 仅允许 `[A-Za-z0-9_-]{1,128}`，分享链接必须归一化到 `douyin.com/video/{id}` 且去重限量（1–100）
- 单实例锁 + 深链 `zhicui://douyin-login` 直达收藏库页

## 收藏夹采集实战（家装案例 · 2026-08-30 → 09-06 两代结论）

> 2026-08-30 首次用「家装」夹打通嵌套子夹采集；09-06 网页端改版后旧 DOM 路线失效，经 API 探测
> 与脚本重构（均已内建到 `scripts/main.py` 并实测）得到确定性路线。以下为最终结论，采集其他
> 收藏夹时直接复用。**旧版 `collect_jia_zhuang.py` 独立脚本已废弃，统一走 `main.py --folder`。**

### 抖音网页端「收藏夹」的真实结构（探测结论 2026-09-06）

1. 正确视图：`https://www.douyin.com/user/{sec_uid}?showTab=favor_collection`，点主 tab **「收藏」**，
   再点顶部子 tab **「收藏夹」**（URL 落 `...&showSubTab=favorite_folder`）。
2. 在此视图下会触发收藏夹列表接口 `/aweme/v1/web/collects/list/`——8-30 观察「未触发 / 只触发
   `aweme/listcollection`（视频列表）」是页面停在错误子视图的**误判**；正确子 tab 下必触发，
   响应即全部真实自建夹，多夹分页滚动追加（实测 10 + 2 = 12 个）。
3. 夹字段真名：**名字 = `collects_name`、id = `collects_id`**（读 `name/title` 会全部退化成「未命名收藏夹」）。
4. 夹卡片 DOM 是 `UL > LI > … > P` 结构，**页面没有 `a[href*='/collection/']`**——凡以抓 `<a href=/collection/id>`
   定位/点击实现的代码都注定失败。

### 成功采集路线（已内建 `scripts/main.py`，确定性导航 v2）

```bash
# 定向只采一个/多个夹（日常推荐；其余夹自动跳过，单夹约 1 分钟内）
python scripts/main.py --only favor --folder 家装
python scripts/main.py --only favor --folder 家装,露营

# 不指定 --folder 则采集全部收藏夹（12 个全量明显更久，建议配 --max-scroll）
python scripts/main.py --only favor --max-scroll 60
```

流程要点：

1. `goto user/{uid}?showTab=favor_collection` → 点「收藏」→ 点「收藏夹」→ 滚动翻页收全夹列表；
2. 每个夹都**从全新全量导航开始**——**不要用 go_back 往返浏览页**（SPA 返回实测 10/12 会崩：
   丢视图或 `ERR_ABORTED`）；
3. 进入夹：夹列表里**叶子精确文本 → 上溯 LI 点击**（`li:has-text` 定位法：`get_by_text(name, exact=True)`
   后 `ancestor::li[1]` 点击）；点击后未确认进入 → 兜底**直接 `goto https://www.douyin.com/collection/{collects_id}`**
   （历史实测 10/10 可靠）；
4. 进入后 `api_data.clear()` → 点详情页子 tab **「视频」**（避免停在音乐/合集等空 tab）；
5. 滚动加载用 **API 驱动**：看 `collects/video` 响应增长、`has_more=false` 即停；**不要每轮全量扫描页面
   视频链接**（重 DOM 下每轮 8–15s，曾卡 23 分钟无输出；API 驱动后同夹 49s 完成），每 ~10 轮打印进度；
6. 提取时补 `extra_fields`：`收藏夹ID = collects_id`、`收藏夹名称 = collects_name`、`收藏夹路径 = 层级路径`。

实测验收（2026-09-06）：`--only favor --folder 家装 --max-scroll 60` → **49s 采得 122 条**，
分组名 = 「家装」（不再是「未命名」），JSON 每条含 `aweme_id/标题/作者/分享链接/收藏夹ID` 等字段，
CSV 同步产出；飞书侧过滤 `标签3 == 收藏夹/家装` 校验一致（8-30 为 112 条——视频随用户增删动态变化，非脚本回退）。

### 失败路径与修复（两代踩坑汇总）

| 失败点 | 现象 | 修复 |
|--------|------|------|
| 选择器抓不到收藏夹 | 用 `a[href*='/collection/']` 定位/点卡片永远空转 | 卡片是 `UL>LI>P` 无 `<a>`；改文本上溯 LI 点击，或直接 `goto /collection/{id}` |
| 夹名全是「未命名收藏夹」 | 读 `name/title` 取不到夹名 | 真字段为 `collects_name` / `collects_id` |
| go_back 往返崩页 | 逐夹 go_back 回浏览页，10/12 崩/丢视图 | 每夹从全量 goto 导航开始，杜绝 SPA 返回 |
| 单夹采集卡死无输出 | 每轮滚动全量扫 DOM 链接，单轮 8–15s，20+ 分钟无进展 | 改 API 驱动滚动（`collects/video` 增长 + `has_more`），约快 20 倍 |
| 停在空 tab | 详情页默认 tab 非视频 | 显式点子 tab「视频」 |
| 数量差异 | 抖音显示 N 作品，实采 < N | 接口含失效/已删（`invalid_item_count`），API 只回有效视频，属正常 |
| 8-30「collects/list 未触发」 | 页面停在错误子视图 | 正确视图（收藏→收藏夹，`showSubTab=favorite_folder`）下必触发 |
| Windows lark-cli 找不到（导入侧） | `FileNotFoundError: 未找到 lark-cli` | Windows 调用 `lark-cli.cmd`，类 Unix 保持 `lark-cli` |
| 字段映射错误（导入侧） | 飞书报 `not_found` | `FIELD_MAPPING` 对齐真实表：`标签→标签3`、`收藏夹→父级`、`子收藏夹→子级` |
| Dry-run 去重误判（导入侧） | 首次 dry-run 显示「表格已有 0 条」 | `get_existing_links` 默认只取第一页 100 条，以「实际导入报告 + 过滤查询」验证 |
| record-list 输出格式（导入侧） | 默认 markdown 表格，JSON 解析失败 | 结构化分析用 `--format ndjson --output ./xxx.ndjson`（路径相对当前目录） |

### 产出与验证

- `output/douyin_data_YYYYMMDD_HHMMSS.json`：全量数据（含所有分类与收藏分组）；
- `output/收藏夹结构_YYYYMMDD_HHMMSS.json`：收藏夹树（每节点含 `collects_id`、`collects_name`、videos）；
- 实测样本：`output/douyin_data_20260906_153508.json`（家装 122 条，分组名=家装）；
- 飞书「视频收藏」表验证：过滤 `标签3 == 收藏夹/家装`，记录数与采集数一致。

## 注意事项

1. **登录安全**：`browser_profile/` 包含登录凭证，勿分享
2. **频率控制**：脚本内置 2.5 秒滚动间隔，遇验证码可调大 `SCROLL_PAUSE_MS`
3. **观看历史**：抖音网页端观看历史仅保留最近一段时间，数量有限
4. **API 变更**：抖音可能变更网页 API（收藏夹接口为 `/aweme/v1/web/collects/list/`、`/aweme/v1/web/collects/video/list/`）。如采集结果为空需更新 `_setup_api_capture` 中的关键词匹配，或改用 `--flat-favorites` 回退
5. **服务器运行**：首次需图形界面登录，之后将 `browser_profile/` 复制到服务器即可 `--headless` 运行
6. **导入前提**：目标多维表格必须已存在字段：链接、标题、作者、来源、发布时间、标签3、父级、子级、LID；同时存在 标签2/标签4/标签5 可保留但非必需。`import_to_bitable.py` 的 `FIELD_MAPPING` 须与真实字段名完全一致，否则飞书会报 `not_found`（字段不存在）。

## 脚本文件

- `scripts/main.py` — 抖音采集主程序（Playwright + API 拦截）
- `scripts/import_to_bitable.py` — 飞书多维表格导入工具（lark-cli 批量写入）
- `scripts/requirements.txt` — Python 依赖清单
