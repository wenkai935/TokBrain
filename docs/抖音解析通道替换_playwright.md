# 抖音解析通道：f2 → 浏览器（playwright-cli）

> 2026-11-02 实施。替换原因、验证数据、切换方式、已知代价。

## 为什么必须换

f2 `0.0.1.7` 是 PyPI 上的**最后版本**（2024-12-31 发布，至今无更新）。它拼的 `a_bogus`
签名 + 随机 `webid` 已被抖音 Argus 拦掉：

```
GET https://www.douyin.com/aweme/v1/web/aweme/detail/  →  403
Blocked by ArgusSecurityPlugin Uifid Not Found
```

排查结论（逐条验证过，别再重复走）：

| 怀疑点 | 结论 |
|--------|------|
| Cookie 失效 | ❌ 不是。新 Cookie 63 个字段齐全，`sessionid`/`sid_guard`/`ttwid`/`UIFID` 都在 |
| f2 版本旧 | ⚠️ 是背景但不是主因。PyPI 无更新可升 |
| 签名算法过期 | ❌ 不是主因。**同一套 `ABogus` 在真实浏览器里能用** |
| webid 随机 | ✅ **主因**。`_random_webid()` 造的 19 位数字与 Cookie 里的 UIFID 不匹配 |

关键点：项目里 zhicui 采集路径的 `_abogus_class()` 也是 `from f2.utils.abogus import
ABogus`，两条路径共用同一签名 + 同样随机 webid，**换路径救不了**。

## 换成什么

`app/services/browser_resolver.py`：用系统浏览器打开作品页，**只读已渲染的 DOM**。

真实浏览器里的 fingerprint / webid / UIFID 由抖音自己维护、天然自洽，所以页面能正常
渲染。我们不再自己拼签名发请求，也就不存在"签名对不上"这回事。

- 走系统 Chrome（`--browser=chrome`），不下载 Chromium
- 每次解析独立 session，结束即 `close`
- **不读 Cookie**：登录态坏掉也不会连带解析失败（这是 `f2_cookie_unreadable` 的教训）
- 只读 DOM，不请求任何接口；页面自身 XHR 失败不影响我们

## 实测（2026-11-02）

短链 `v.douyin.com/AUTwGFr94Qs/`（正是飞书表里 f2 解析失败的一条）：

```
id        : 7667154814924557577
kind      : video
title     : Github宝藏面试救星，秋招求职必看 #github #面试 #秋招 #求职 #简历
author    : 千忆
sync_id   : MS4wLjABAAAAA7SMNSznnnkdLxSn
duration  : 42.0
media     : https://v26-web.douyinvod.com/b780256f82e6c56cba7b59e1f350ed...
published : 2026-07-27 18:36
```

f2 对同一链接是 403。全程无验证码、无滑块。

取数方式（一次 `run-code` 取全，不要一条信息跑一轮）：

| 字段 | 来源 |
|------|------|
| aweme_id | 短链跳转后的 `location.href` 正则 |
| title | `document.title` 去掉 ` - 抖音` |
| author / sec_user_id | 第一个 `text` 非空且非 `/user/self` 的 `/user/` 链接 |
| duration | `[data-e2e=video-detail]` 文本里的 `00:05 / 00:42` |
| published_at | 同上文本里的 `发布时间：…` |
| media_urls | `video` 元素的 `currentSrc`/`src` |

## 怎么切回来

```bash
TOKBRAIN_LINK_RESOLVER=f2   # 默认 browser；f2 仅用于排障对照
```

`import_queue._resolve_link()` 的行为：

- 拿到真正的 `F2WorkClient` → 按 `LINK_RESOLVER` 走通道
- 调用方注入了自定义 client（测试替身、定制实现）→ **尊重注入，不被默认策略覆盖**

## 已知代价（务必知情）

1. **慢**：每次解析要开浏览器，约 **10–15 秒**，f2 是秒级。批量解析会明显变慢。
2. **占内存**：并发 worker 各起一个 Chrome 实例（约 200MB/个）。当前 3 个 worker
   即最多 3 个实例，尚可；不要再加并发。
3. **依赖本机 GUI 环境**：需要能启动 Chrome 的桌面会话。纯 SSH / 无桌面环境不可用。
4. **DOM 结构会变**：抖音改版就要修选择器。已按「优先 `data-e2e`、退化到遍历」
   写，比硬编码 class 稳一些，但仍需维护。
5. **封面还没取**：`cover_url` 目前留空。详情区有 39 张图，暂无法可靠区分封面与头像/声明图。

## 后端服务环境（2026-11-02 修复的隐蔽坑）

第一版实现用 `shutil.which("playwright-cli")` 找 CLI。这在交互式 shell 里没问题，
但**后端是由 `start.ps1` 拉起的，它的 PATH 里没有 `%APPDATA%\npm`，也没有
WorkBuddy 托管的 node 目录**。实测：

```bash
env PATH="/c/Windows/System32:/c/Windows" python -c "cli_available()"
# 修复前 → False    修复后 → True
```

判成「未安装」后的后果不是报错那么简单——错误码当时复用 `access_forbidden`，
和「抖音拒绝解析」**长得一模一样**，排查会一路往风控方向跑偏。

修复：

1. 新增 `_node_install_roots()`，把 `%APPDATA%\npm`、`%LOCALAPPDATA%\npm`、
   `~/.workbuddy/binaries/node/versions/*` 都纳入候选（版本号会变，所以用 glob）
2. node 查找优先用 `script.parent^4 / "node.exe"`（托管布局正好命中），找不到才退 `which`
3. **错误码独立成 `browser_unavailable`**，与抖音的 `access_forbidden` 彻底分开

以后只要看到 `browser_unavailable`，就去查工具与开关，不要怀疑风控。

## 操作纪律

抖音风控敏感，调试时：

- 先用中性页面验证工具链，再上目标站
- 一次探测拿到结论就停；**遇 403/验证码/空响应立刻停，不重试、不换参数再试**
- 用一次 `run-code` 取回所有需要的字段
