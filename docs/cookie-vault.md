# 统一 Cookie 库（cookie-vault）

## 为什么要做

以前 Cookie 散在三处，换号或过期时必然漏改一处：

| 位置 | 消费者 | 形态 |
|---|---|---|
| `SecretRecord(f2_cookie)` | F2 链接解析 | DPAPI 加密字符串 |
| `SecretRecord(douyin_cookie)` | 平台采集 / 握手 | DPAPI 加密字符串 |
| `D:/Temp/downloads/douyin_cookies.txt` | 外部下载脚本 | Netscape 文件 |

同一份登录态贴三遍，而且失败现象各不相同：F2 报"未登录"，下载器直接下到无音轨分片。

## 现在的约定

**`data/cookies/<platform>.txt` 是唯一事实来源**（可用 `TOKBRAIN_COOKIE_DIR` 覆盖根目录）。
设置页是唯一上传入口，其余模块只读不写第二份。

```
                 ┌──────────────────────────────┐
  设置页上传 ───▶ │  data/cookies/               │
  （全量或单平台）  │    douyin.txt                │
                 │    bilibili.txt              │
                 │    xiaohongshu.txt           │
                 └───────┬──────────────────────┘
                         │ 按需解析（cookie_store.py）
        ┌────────────────┼──────────────────┬─────────────────┐
        ▼                ▼                  ▼                 ▼
  cookie_header()   cookie_dict()     cookie_file()      status()
  F2 / 采集 / 爬虫   requests/httpx    yt-dlp --cookies   设置页/排查
```

## 消费者接线表

| 消费者 | 代码位置 | 读法 |
|---|---|---|
| F2 链接解析 | `app/services/import_queue.py` | `await cookie_header_for(session, "f2")` → 别名到 douyin |
| 平台采集（抖音/B站/小红书） | `app/routers_v2/platforms.py::pull` | `await cookie_header_for(session, platform)` |
| 握手回调 | `platforms.py::handoff` | 存 Secret 的同时 `save_text()` 落文件 |
| 设置页旧粘贴框 | `app/routers_v2/settings.py::put_settings` | 文本同样 `save_text()` |
| 外部下载脚本 | `D:/Temp/downloads/bridge_douyin.py::cookie_source()` | `data/cookies/douyin.txt`，旧路径兜底 |
| 后续新平台 | — | 只在 `PLATFORMS` 注册一行即自动可用 |

## 上传格式

三种都认，落盘统一写成 Netscape：

1. 浏览器/扩展导出的 `cookies.txt`（**Netscape**）；
2. Cookie-Editor / EditThisCookie 导出的 **JSON**；
3. 浏览器里复制的 `a=1; b=2` **请求头字符串**。

归一化规则（`parse_cookie_text`）：

- `#HttpOnly_` 开头的行是**有效 Cookie**，不是注释——按 `#` 跳过会静默丢登录态；
- 域名按后缀**严格匹配**：`evil-douyin.com` 不会命中 `douyin.com`；
- 同名 Cookie 只留一条：优先主域 → 路径更长 → 过期更晚（`.douyin.com` 与 `.iesdouyin.com` 常重名）；
- 无域名的（Header 串）补平台主域，否则下游按域取用时拿不到；
- `httpOnly` 在写回时还原成 `#HttpOnly_` 前缀，否则属性被洗掉。

## 新增一个平台

```python
# app/services/cookie_store.py
PLATFORMS["kuaishou"] = CookiePlatform(
    key="kuaishou", label="快手", filename="kuaishou.txt",
    domains=("kuaishou.com",), auth_names=frozenset({"kuaishou.web.cp.api_st"}),
    note="登录 kuaishou.com 后导出",
)
```

不用改路由、不用改前端——`/api/cookies` 会带上它，上传页自动多一行。

## 接口

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/cookies` | 全部平台状态 + 存放目录 |
| POST | `/api/cookies/upload` | 一份全量导出，**按域自动拆**到各平台 |
| POST | `/api/cookies/{platform}/upload` | 单平台整份覆盖 |
| POST | `/api/cookies/{platform}` | 粘贴文本保存 |
| DELETE | `/api/cookies/{platform}` | 删除该平台文件 |
| GET | `/api/cookies/{platform}/download` | 下载实际生效的 Netscape 文件 |
| POST | `/api/cookies/migrate-legacy` | 把旧 DPAPI Secret 迁成文件 |

**路由顺序**：`/upload`、`/migrate-legacy` 必须声明在 `/{platform}` 之前，否则会被当成平台名。

## 兼容与迁移

- 启动时 `_migrate_legacy_cookie_secrets()` 把旧 Secret 一次性写文件；文件已存在则跳过。
- 读取优先级：**文件 → 旧 Secret**。上传过文件后，旧 Secret 不再被读到。
- 旧 Secret **解不开**（换 Windows 用户/重装系统）时抛 `SecretUnavailableError`，由
  `_process_item` 转成 `f2_cookie_unreadable`——**不能静默降级成匿名请求**，否则用户只会
  看到误导性的"登录态无效"。
- 设置页"清除 Cookie"会连文件一起删，否则会出现"显示已清除、实际还在用"。

## 安全约束

- 平台名走白名单，文件名由注册表派生，**不接受外部路径**（杜绝路径穿越）；
- 目录 `0700`、文件 `0600`，`os.replace` 原子替换，不会写出半截文件；
- 单文件 ≤ 1 MiB、条目 ≤ 4000；
- 所有接口**只回名字与条数，不回 Cookie 值**；
- 测试通过 `TOKBRAIN_COOKIE_DIR` 指向临时目录，永不读写真实登录态。

## 验收

```bash
cd D:/code/TokBrain-main
./.venv/Scripts/python.exe -m pytest tests/test_cookie_store.py -q   # 14 passed
```

关键用例：`#HttpOnly_` 保留、外域过滤、同名去重方向、JSON/Header 解析、
`http.cookiejar` 能加载、旧 Secret 回退与迁移、`/upload` 不被 `/{platform}` 吞掉。
