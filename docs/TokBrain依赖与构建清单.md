# TokBrain 依赖与构建清单

> 核对基准：2026-10-31，仓库 `D:\code\TokBrain-main`。
> 本文不靠记忆，逐条读自：`requirements*.txt`、`frontend/package.json`、`cli/package.json`、`scripts/setup.ps1`、`start.ps1`、`dev.cmd`、`mcp_server/启动MCP服务.cmd`、`.env` / `.env.example`。

## 0. 一句话总览

| 层 | 内容 | 数量 |
|---|---|---|
| Python 运行时 | **Python 3.12.x 64-bit（硬要求，其他版本不可替代）** | 1 |
| Node 运行时 | **Node ≥22 + npm ≥10** | 1 套 |
| Python 环境 | `.venv`（后端）· `.venv-collect`（抖音采集）· `.venv-mcp`（MCP 只读服务） | 3 个 |
| Node 项目 | `frontend/`（主前端）· `cli/`（飞书 CLI） | 2 个 |
| Python 包 | `requirements.txt` 31 个 + f2 1 个 + billing 2 个 + dev 4 个 + mcp 14 个 | 52 |
| Node 包 | 前端 11 运行时 + 9 开发 · cli 1 个 | 21 |
| 外部二进制 | **ffmpeg + ffprobe**（唯一必需的本地二进制） | 1 组 |
| 浏览器 | Chrome / Edge（复用本机，免下 chromium） | 可选 |
| 外部服务 | 阿里云百炼 / 阿里云 BSS / 飞书 / Obsidian / 抖音 | 5 |

---

## 1. 必须自己装的前置（OS 级，装不了就起不来）

| 组件 | 版本要求 | 校验位置 | 缺失后果 |
|---|---|---|---|
| **Python** | **3.12.x，64 位，唯一允许版本** | `scripts/setup.ps1:213-220` | **安装直接中止**，提示去 python.org 装 3.12.x (64-bit) 并勾选 Add to PATH |
| **Node.js** | **≥ 22** | `setup.ps1:245` | 中止；需 https://nodejs.org/en/download |
| **npm** | **≥ 10** | `setup.ps1:253` | 中止；`package.json` 另声明 `packageManager: npm@10.9.4` |
| **ffmpeg + ffprobe** | 无版本要求，需在 PATH、`tools\ffmpeg\bin`、或由 `FFMPEG_BIN` 指定 | `setup.ps1`（安装末尾探测）、`start.ps1:406-452`、`app/main.py:_ffmpeg_bin_candidates` | **仅警告不中止**；后果是视频无法进入完整处理流水线（抽帧/转码/ASR 前置全废） |
| Chrome / Edge | 任意较新版本 | `doubaosave Skill/.../main.py:95` | 采集脚本回退到 playwright 自带 chromium，届时才需要 `playwright install chromium` |

> ✅ ffmpeg 不再硬编码到某台机器。探测顺序：环境变量 `FFMPEG_BIN` / `TOKBRAIN_FFMPEG_BIN` / `FFMPEG_HOME` → `.env` 里的 `FFMPEG_BIN`（后端读）→ 已在 PATH → `<项目>\tools\ffmpeg\bin`（推荐把便携版放这里）→ `C:\ffmpeg*\bin`、`D:\ffmpeg*\bin`、`D:\code\ffmpeg*\bin`、`D:\tools\ffmpeg*\bin` 等通配目录（同组取最新）。`start.ps1` 与后端启动时各探测一次，命中的目录会导出为 `FFMPEG_BIN` 给子进程；全都找不到只警告、不阻断启动。
>
> ⚠️ 写进 `.env` 时必须用**正斜杠**（`D:/tools/ffmpeg/bin`）：`.env` 按转义规则解析，`D:\tools\ffmpeg\bin` 中的 `\t` `\f` `\b` `\n` 会被当成控制字符，路径直接写坏。

---

## 2. 后端 Python 依赖（`requirements.txt`，31 个）

安装方式：`python -m pip install -r requirements.txt`（setup.ps1 会先钉 `pip==25.2`）。

### 2.1 Web / API 框架
| 包 | 版本 | 用途 |
|---|---|---|
| fastapi | 0.140.7 | HTTP 框架（全部 `/api/*` 路由） |
| starlette | 1.3.1 | ASGI 基座 |
| uvicorn | 0.30.6 | ASGI 服务器（跑 8001） |
| python-multipart | 0.0.32 | 文件上传（本地视频/数据包/补件） |
| httpx | 0.27.2 | 出站 HTTP（平台请求、AI 调用） |
| loguru | 0.7.2 | 日志 |

### 2.2 持久化
| 包 | 版本 | 用途 |
|---|---|---|
| sqlalchemy | 2.0.35 | ORM（2.0 异步风格） |
| aiosqlite | 0.20.0 | SQLite 异步驱动 |
| pydantic | 2.9.2 | 数据模型 / schema 校验 |
| pydantic-settings | 2.5.2 | `.env` 配置加载（`app/config.py`） |
| tzdata | 2025.2 | 时区数据（上海日切限额） |

### 2.3 AI / 媒体
| 包 | 版本 | 用途 |
|---|---|---|
| openai | 1.59.7 | OpenAI 兼容客户端（百炼 compatible-mode 也走它） |
| dashscope | 1.20.0 | 阿里云百炼原生 SDK（OCR/ASR/摘要/embedding/QA） |
| Pillow | 12.3.0 | 关键帧图像处理 |
| sqlite-vec | 0.1.9 | **SQLite 向量索引扩展**（知识库检索） |

### 2.4 F2 兼容运行时（历史遗留，链接解析 legacy 兜底）
> F2 包元数据钉了过期且含漏洞的依赖，所以 f2 本体用 `--no-deps --target .vendor` 隔离安装，下面这些是**审计过的兼容运行时版本**，单独声明在此。

| 包 | 版本 | | 包 | 版本 |
|---|---|---|---|---|
| aiofiles | 24.1.0 | | m3u8 | 3.6.0 |
| babel | 2.13.0 | | protobuf | 5.29.6 |
| browser-cookie3 | 0.20.1 | | PyExecJS | 1.5.1 |
| click | 8.4.2 | | PyYAML | 6.0.2 |
| cryptography | 48.0.1 | | qrcode | 8.0 |
| gmssl | 3.2.2 | | rich | 13.9.3 |
| importlib-resources | 6.4.5 | | websockets | 12.0 |
| jsonpath-ng | 1.6.1 | | websockets-proxy | 0.1.2 |

---

## 3. 需要「特殊安装方式」的依赖（最容易踩坑）

| 清单文件 | 包 | 安装方式 | 说明 |
|---|---|---|---|
| `requirements-f2.txt` | `f2==0.0.1.7` | **`pip install --upgrade --no-deps --target .vendor -r requirements-f2.txt`** | 绝不能用普通 install（会拉进过期的旧版依赖）。装完还要校验 `f2.__file__` 确实落在 `.vendor` 内，否则安装器回滚 |
| `requirements-billing.txt` | `alibabacloud-bssopenapi20171214==5.1.1`、`alibabacloud-tea-openapi==0.4.5` | 普通 install，**setup 默认装** | 只服务「账单核对」页；不用该页可 `setup.ps1 -WithoutBilling` 跳过，省依赖 |
| `requirements-dev.txt` | `pytest==9.1.1`、`pytest-asyncio==1.4.0`、`coverage==7.10.6`、`pip-audit==2.10.1` | 普通 install，**默认不装** | 只有 `setup.ps1 -WithDev` 才装；终端用户不需要 |
| `requirements-mcp.txt` | `mcp==2.3.0` + 13 个配套包 | **`pip install -r requirements-mcp.txt`**，必须装进**独立 venv `.venv-mcp`** | `mcp 2.3.x` 要求 pydantic≥2.12 / uvicorn≥0.31，与主 venv 的 pin 冲突，**不能合并进 `.venv`**。安装器用 `setup.ps1 -WithMcp` 自动建环境；详见 §10 |

---

## 4. 前端 Node 依赖（`frontend/package.json`）

安装方式：`npm ci --cache <可写缓存目录>`（setup.ps1 强制指定缓存目录，绕开机器上失效的 npm cache 配置）。

### 4.1 运行时依赖（11）
| 包 | 版本 | 用途 |
|---|---|---|
| next | ^16.2.12（实装 16.2.12） | App Router 框架 |
| react / react-dom | 19.2.3 / 19.2.3 | UI |
| react-markdown | ^10.1.0 | 作品总结 Markdown 渲染 |
| remark-gfm | ^4.0.1 | GFM 表格/删除线 |
| remark-math | ^6.0.0 | 数学公式语法 |
| rehype-katex | ^7.0.1 | 公式渲染 |
| katex | ^0.18.1 | KaTeX 本体 + 字体 CSS |
| remark-parse | ^11.0.0 | Markdown 解析 |
| unified | ^11.0.5 | 解析管线 |
| mdast-util-to-string | ^4.0.0 | 取纯文本（标题/摘要截断） |

### 4.2 开发依赖（9）
| 包 | 版本 | 用途 |
|---|---|---|
| typescript | ^5 | 类型检查（`npm run typecheck`） |
| tailwindcss | ^4（实装 4.1.18） | 原子化样式（v4，无 tailwind.config 走 CSS 配置） |
| @tailwindcss/postcss | ^4 | PostCSS 插件 |
| eslint | ^9.39.5 | Lint |
| eslint-config-next | ^16.2.12 | Next 规则集 |
| @types/node | ^20 | Node 类型 |
| @types/react / @types/react-dom | ^19 | React 类型 |

### 4.3 `overrides`（安全钉版，不可省）
`brace-expansion 5.0.9` · `minimatch 10.2.6` · `postcss 8.5.23` · `sharp 0.35.3`
—— 用来压住传递依赖的已知漏洞版本。删掉会让 `npm audit` 重新报警。

### 4.4 Node 边界
`engines.node >= 22`；`packageManager: npm@10.9.4`。

---

## 5. 另外两套运行环境（不在 setup 流程里，需单独准备）

| 环境 | 路径 | 内容 | 谁用它 | 怎么装 |
|---|---|---|---|---|
| **`.venv-collect`** | 项目根 | `playwright==1.64.1` + `greenlet 3.5.6` + `pyee 14.0.0` + `typing_extensions 4.16.0` | 抖音「采集」子进程（`doubaosave_bridge.py`） | `python -m venv .venv-collect` → `.venv-collect\Scripts\python.exe -m pip install playwright`。**不要装 chromium**（本机 Chrome/Edge 足够）；可用 `DOUBAOSAVE_PYTHON` 覆盖解释器 |
| **`.venv-mcp`** | 项目根 | `mcp` 包（版本未在任何清单中锁定）+ 后端依赖 | `mcp_server/启动MCP服务.cmd`，:8010 | ⚠️ 无 requirements，见 §10 |
| **`cli/`**（Node 子项目） | `cli/` | `@larksuite/cli ^1.0.98` | 飞书同步桥接（`bridge_feishu_obsidian.py` 调外部 CLI） | `cd cli && npm install` |

---

## 6. 外部服务（不需要安装，但缺了对应功能不可用）

| 服务 | 用在哪 | 凭据放哪 |
|---|---|---|
| **阿里云百炼 DashScope** | OCR / ASR / 摘要 / embedding / QA（默认 `provider_type=dashscope`，`https://dashscope.aliyuncs.com/compatible-mode/v1`） | **存在 UI 里，用 Windows DPAPI 加密**；`.env` 明确禁止放 key |
| OpenAI 兼容端点（可选） | 切 `provider_type=openai` + `provider_base_url` 后可接任意兼容服务 | 同上 |
| 阿里云 BSS 账单 | 「账单核对」页（需 `requirements-billing.txt`） | UI 内配置 |
| 飞书开放平台 | 飞书→Obsidian 桥接（`bridge_feishu_obsidian.py` + `cli/`） | 扫码登录态存本地 |
| Obsidian | 知识库导出目标（写到本地 vault 目录） | 本地路径配置 |
| 抖音 | 平台采集（Playwright）/ 链接解析（自研签名 + F2 legacy） | 浏览器登录态存在 `doubaosave Skill/source/scripts/browser_profile/` |

---

## 7. 数据与存储（无独立安装步骤，但要知道在哪）

| 项 | 位置 | 说明 |
|---|---|---|
| 主库 | `data/douyin_rag.db` | SQLite（`sqlite+aiosqlite:///`），存作品/切片/任务/设置 |
| 桥接队列库 | `data/dispatch.db` | 飞书 / 本地 inbox / HTTP 入站三路共享 dispatch 队列 |
| 向量索引 | 主库内，靠 `sqlite-vec` 扩展 | 扩展是 **pip 包**，不是单独服务 |
| 媒体与日志 | `data/`（含 `logs/`、`backups/`）、`temp/` | 由启动脚本自动创建 |
| 采集产物 | `doubaosave Skill/source/scripts/output/douyin_data_*.json` | 采集脚本写出，桥接读入 |

---

## 8. 构建方式（三条路径，别混用）

### 8.0 单入口：`TokBrain.cmd`

`TokBrain.cmd` 是唯一需要记的入口，本身只是 `scripts\menu.ps1` 的薄壳（`chcp 65001` + `-File` + `%*` 透传）。

| 用法 | 作用 |
|---|---|
| `TokBrain.cmd`（双击） | 交互菜单，顶部先打印环境与服务状态 |
| `TokBrain.cmd status` | 只打印状态：`.venv` / `node_modules` / `.next` / `.venv-mcp` / ffmpeg / 8001 / 3001 / 8010 |
| `TokBrain.cmd install [-WithMcp] [-WithDev] [-WithoutBilling]` | 调 `scripts\setup.ps1` |
| `TokBrain.cmd start` / `start-bg` / `stop` / `restart` | 前台启动 / 隐藏窗口启动 / 停止 / 重启 |
| `TokBrain.cmd dev` | `dev.cmd`：后端 + 前端热更新两窗口 |
| `TokBrain.cmd mcp` | `mcp_server\启动MCP服务.cmd`（缺 `.venv-mcp` 时给安装提示） |
| `TokBrain.cmd open` | 打开 `http://127.0.0.1:3001` |

旧的 `安装.cmd` / `setup.cmd` / `启动.cmd` / `start.cmd` / `停止.cmd` / `重启.cmd` / `dev.cmd` 全部保留且行为不变，只是不再是必须记住的入口。

### 8.1 生产构建（推荐：双击即全自动）

```
双击 TokBrain.cmd → 选 1（或 TokBrain.cmd install）
  → scripts\menu.ps1 → powershell -File scripts\setup.ps1
```

等价旧路径：`安装.cmd → setup.cmd → setup.ps1`。

`setup.ps1` 做的事，按顺序：

| 步 | 动作 | 失败处理 |
|---|---|---|
| 1 | 校验 64 位 Python 3.12（`py -3.12` → `py -0p` 全 tag 遍历 → `python`，兼容 uv 安装） | 中止并给下载链接 |
| 2 | 校验 Node ≥22、npm ≥10 | 中止并给下载链接 |
| 3 | 检查运行时状态文件，若服务仍在跑则要求先「停止.cmd」 | 中止 |
| 4 | 在 **staging 目录**新建干净 `.venv` | 中止 |
| 5 | `pip install --upgrade pip==25.2` | 中止 |
| 6 | `pip install -r requirements.txt` | 中止 |
| 7 | `pip install -r requirements-billing.txt`（除非 `-WithoutBilling`） | 中止（提示可跳过） |
| 8 | `pip install -r requirements-dev.txt`（仅 `-WithDev`） | 中止 |
| 9 | `pip install --upgrade --no-deps --target .vendor -r requirements-f2.txt` | 中止 |
| 10 | `pip check` + 校验 `f2` 来源在 `.vendor` | 中止并保留旧环境回滚 |
| 11 | 前端：`npm ci --cache <可写缓存>` | 中止（提示 EPERM/杀软） |
| 12 | 前端：`npm run build`，注入 `NEXT_PUBLIC_API_URL=http://127.0.0.1:8001` | 中止并保留旧环境 |
| 13 | 写 `frontend/.next/tokbrain-build.json`（记录 backend_url / node 版本 / 构建时间） | — |
| 14 | **原子替换** `.venv` 与 `.vendor`（失败自动回滚到 `*-replaced-*`） | 回滚 |
| 15 | （仅 `-WithMcp`）建 `.venv-mcp` → `pip install -r requirements-mcp.txt` → `pip check` → 导入冒烟 | 中止（主服务已可用，MCP 单独失败） |
| 16 | 探测 ffmpeg/ffprobe（PATH → `tools\ffmpeg\bin` → `C:\ffmpeg*\bin` / `D:\code\ffmpeg*\bin` 等） | **仅警告** |

> 关键点：**前端构建时把后端地址烧进产物**（`NEXT_PUBLIC_API_URL`）。改了 `APP_HOST/APP_PORT` 必须重跑安装，否则 `start.ps1:475` 会直接抛错「APP_HOST/APP_PORT 已在安装后改变」。

### 8.2 生产启动

```
双击 TokBrain.cmd → 选 2（前台）/ 3（隐藏窗口）/ 4（停止）/ 5（重启）
或 TokBrain.cmd start / start-bg / stop / restart
```

等价旧路径（仍可用）：`启动.cmd`（隐藏，日志进 `data\logs\startup-console.log`）、`start.cmd`（前台，方便看报错）、`停止.cmd`、`重启.cmd`。

`start.ps1` 做的事：

| 步 | 动作 |
|---|---|
| 1 | 注入 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`、`NODE_ENV=production` |
| 2 | 探测 ffmpeg bin 目录（`FFMPEG_BIN` → PATH → `tools\ffmpeg\bin` → 常见安装目录）并前置到 PATH，同时导出 `FFMPEG_BIN` |
| 3 | 校验 `frontend/node_modules` 存在、`.next/BUILD_ID` 存在、`tokbrain-build.json` 的 backend_url 与当前一致 |
| 4 | 校验 8001 / 3001 未被其他进程占用（**只杀身份匹配的 TokBrain 实例，不动别的软件**） |
| 5 | `.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001`（隐藏窗口） |
| 6 | `frontend/` 下 `npm start` → `next start --hostname 127.0.0.1 --port 3001` |
| 7 | 起 watchdog 守护 + 打开浏览器 `http://127.0.0.1:3001` |

### 8.3 开发模式

```
双击 dev.cmd
```
= 两个窗口：`.venv\Scripts\python.exe -m uvicorn app.main:app --port 8001`（**无 `--reload`，改后端代码必须手动重启**）+ `cd frontend && npm run dev`（Next 热更新，改 `.tsx` 自动刷）。

### 8.4 单独命令速查

| 目的 | 命令 | 工作目录 |
|---|---|---|
| 前端生产构建 | `npm run build` | `frontend/` |
| 前端开发服务 | `npm run dev`（3001） | `frontend/` |
| 前端生产服务 | `npm start`（3001） | `frontend/` |
| 类型检查 | `npm run typecheck`（`tsc --noEmit`） | `frontend/` |
| Lint | `npm run lint` | `frontend/` |
| 前端测试 | `npm run test`（`node --test tests/*.test.mjs`，**不需要 jest**） | `frontend/` |
| 后端测试 | `.venv\Scripts\python.exe -m pytest`（配置在 `pytest.ini`，需 `-WithDev` 装过 pytest） | 项目根 |
| MCP 只读服务 | 双击 `mcp_server\启动MCP服务.cmd`（:8010，读 `mcp.env`） | — |
| 飞书同步 | 双击 `启动飞书同步.cmd` | — |

---

## 9. 本机实况核对（2026-10-31）

| 组件 | 要求 | 本机实际 | 状态 |
|---|---|---|---|
| Python（后端） | 3.12.x 64bit | `.venv` = **3.12.14**（基于 uv 的 CPython 3.12.14 创建） | ✅ |
| Python（采集） | 任意 3.x + playwright | `.venv-collect` = **3.13.14**，playwright **1.64.1** | ✅（版本与后端不同，但采集脚本只用标准库+playwright，无碍） |
| Node | ≥22 | **22.22.2** | ✅ |
| npm | ≥10 | **10.9.7** | ✅ |
| Next | ^16.2.12 | **16.2.12** | ✅ |
| React | 19.2.3 | **19.2.3** | ✅ |
| Tailwind | ^4 | **4.1.18** | ✅ |
| 后端 venv 包数 | 31+ | **94 条**（含传递依赖） | ✅ |
| ffmpeg / ffprobe | 必需 | **不在 PATH**；通配探测命中 `D:\code\ffmpeg-2026-08-27-...\bin` | ✅ 后端 `app/main.py` 启动时自行探测并导出 `FFMPEG_BIN`，直接跑 uvicorn 也不再丢 ffmpeg |
| 前端构建产物 | `.next` + BUILD_ID | `frontend/.next` **232MB**，BUILD_ID 存在 | ✅ |
| `.vendor`（f2） | 必须存在 | 存在（`f2-0.0.1.7.dist-info`） | ✅ |
| Chrome / Edge | 采集用 | Chrome **156.0.8078.74** / Edge **156.0.4314.50**，均可被 playwright 直接驱动 | ✅ |

---

## 10. 环境隔离与注意事项

### 10.1 MCP 服务为什么必须单独一套 venv

| 约束 | 主服务 `.venv`（`requirements.txt`） | MCP `.venv-mcp`（`requirements-mcp.txt`） |
|---|---|---|
| pydantic | `==2.9.2` | `==2.14.1`（mcp 2.3 要求 ≥2.12） |
| uvicorn | `==0.30.6` | `==0.54.0`（mcp 2.3 要求 ≥0.31.1） |
| starlette | `==1.3.1` | `==1.10.0` |
| mcp | 不装 | `==2.3.0` |

两套 pin 无法在同一环境共存，`pip check` 必然报冲突，所以拆成两个进程两个环境。MCP 只读数据库、独立端口 8010，与主服务不共享内存也不共享依赖树。

- 安装：`.\scripts\setup.ps1 -WithMcp`（幂等，可重复跑；缺 pip 的环境会先 `ensurepip`）
- 手工安装：见 `requirements-mcp.txt` 头部注释
- 启动：`mcp_server\启动MCP服务.cmd`
- 清单里的 14 个版本与 2026-10-31 本机实测可运行的 `.venv-mcp` 逐条一致，且 `import mcp_server.tokbrain_mcp` 已跑通

### 10.2 其余注意事项

1. **改端口必须重装**：`NEXT_PUBLIC_API_URL` 在 `npm run build` 时烧进前端产物，`start.ps1` 会校验不一致并拒绝启动。
2. **`pip install` 只能装在项目内 venv**：往项目目录之外的 Python 环境（如 `C:\Users\...\.workbuddy\...`）装包会被沙箱直接 SIGTERM、零输出。装依赖一律用 `D:\code\TokBrain-main\.venv*`。
3. **采集不需要 chromium**：`main.py` 优先 `channel="chrome"/"msedge"` 复用本机浏览器，两个通道都已实测可用；只有本机彻底没有浏览器时才需要 `playwright install chromium`。
4. **后端 dev 模式无热重载**：`dev.cmd` 里 uvicorn 没带 `--reload`，改后端 `.py` 后必须重启进程。
5. **ffmpeg 便携版放哪儿**：推荐解压到 `<项目>\tools\ffmpeg\bin`（优先级仅次于环境变量），这样整个目录可以整体打包搬机器，不依赖 C:/D: 的绝对路径。
6. **`.env.example` 已与当前实现对齐**：`APP_PORT=8001`、`FRONTEND_ORIGINS` 含 3000+3001、`IMPORT_BATCH_LIMIT` / `IMPORT_DAILY_LIMIT` / `INBOX_BATCH_LIMIT` 三个限额键都在，且注明「设置页改过之后以数据库持久值为准，此文件只是初始值」。可直接复制为 `.env`。
