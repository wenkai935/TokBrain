# 二次开发声明 / Fork Notice

本文件说明本项目与上游项目的关系、二次开发的边界，以及本次新增内容。所有内容依据
git 提交记录与文件实测得出，基线可复现。

---

## 一、上游项目

| 项目 | 内容 |
|---|---|
| 项目名称 | TokBrain |
| 上游仓库 | https://github.com/fyfsxkh/TokBrain |
| 作者与版权所有者 | TokBrain contributors（见 `./LICENSE` 版权行） |
| 上游许可证 | MIT License |
| 上游许可证文件 | `./LICENSE` —— **原文保留，未作任何修改** |
| 第三方声明 | `./THIRD_PARTY_NOTICES.md` —— **原文保留** |
| 本项目基线 | 上游 `v1.0.0`，commit `e022d41b`（2026-08-12，`release: TokBrain v1.0.0 (#26)`），API contract 7 |
| 基线之后的上游版本 | `c7615b8`（2026-10-29，API contract 8，自定义主题与抖音链接解析）—— **本项目尚未合入** |

## 二、本项目性质

本项目是**基于 TokBrain 的非官方二次开发版本**，由个人维护，与原作者及上游组织无隶属、
赞助或合作关系。

- 「TokBrain」这一名称及相关商标、标识的权利归原作者所有。本项目使用该名称仅为**说明
  来源与继承关系**，不表示本项目为官方版本或获得官方背书。
- 本项目对外分发时同样保留上游的 MIT 许可证副本。
- 本次二次开发产生的改动同样以 MIT 许可发布。

## 三、许可与溯源合规

1. **保留上游许可证**：`./LICENSE` 未修改、未替换、未删除。MIT 要求的版权声明与许可
   文本完整保留。
2. **保留第三方声明**：`./THIRD_PARTY_NOTICES.md` 未修改。该文件声明 F2 等第三方组件的
   使用边界，对合规审计仍然有效。
3. **保留上游溯源**：本项目保留了上游完整的 git 提交历史（上游提交作者显示为
   `fyfsxkh` / `FYFS`），未做 rebase 抹除或历史重写。任何人执行
   `git log` 都能追溯到上游提交。
4. **修改归属清晰**：本次二次开发的改动以独立提交记录在本地分支上，与上游提交可区分。

## 四、二次开发新增内容

以下为相对上游基线 `e022d41b` **新增**的能力，均为本项目原创实现。

### 4.1 多平台内容解析

将原本单一平台的导入扩展为多平台并行体系：

| 文件 | 作用 |
|---|---|
| `app/services/platforms/douyin.py` | 抖音链接解析与签名请求 |
| `app/services/platforms/bilibili.py` | 哔哩哔哩平台解析 |
| `app/services/platforms/xiaohongshu.py` | 小红书平台解析 |
| `app/services/platforms/cookies.py` | 各平台登录态接入 |
| `app/services/platforms/schemas.py` | 平台统一数据契约 |
| `app/services/platforms/errors.py` | 平台异常分类 |
| `app/services/browser_resolver.py` | 浏览器实例解析与复用 |
| `app/routers_v2/platforms.py` | 平台交接、拉取、状态、DOM 采集接口 |

### 4.2 Cookie 保险库

| 文件 | 作用 |
|---|---|
| `app/services/cookie_store.py` | 统一目录存放各平台登录态，含旧版 DPAPI 记录的一次性迁移 |
| `app/routers_v2/cookies.py` | 上传、按平台读取、迁移、删除接口 |
| `docs/cookie-vault.md` | 设计说明 |

### 4.3 飞书集成与收件箱

| 文件 | 作用 |
|---|---|
| `app/services/feishu_auth.py` | 飞书鉴权与扫码续期 |
| `app/services/discovery.py` | 本机可发现资源探测 |
| `app/services/dispatch_store.py` | 分发状态存储 |
| `app/services/sync_status.py` | 同步状态汇总 |
| `app/routers_v2/inbox.py` | 飞书收件箱入库接口 |
| `app/routers_v2/sync.py` | 同步概览、发现、确认、重放、重登 |
| `bridge_feishu_obsidian.py` | 飞书多维表格 → TokBrain → Obsidian 常驻桥接进程 |

### 4.4 Agent 控制层与 MCP 通道

本项目新增了一套面向自动化 Agent 的工具层，与上游「网页端问答」的路线不同：

| 文件 | 作用 |
|---|---|
| `tokbrain_control/`（14 个模块） | 控制层实现，暴露 33 个工具命令，CLI 与 MCP 共用同一份代码 |
| `mcp_server/mcp_control.py` | 控制 MCP 服务，默认 `127.0.0.1:8011`，非回环监听强制令牌 |
| `mcp_server/tokbrain_mcp.py` | 只读知识检索 MCP，默认 `127.0.0.1:8010`，工具前缀 `kb_*` |
| `mcp_server/tokbrain_hermes_stdio.py` | NAS 上 Hermes 的只读 stdio 适配器，直接读只读快照 |
| `mcp_server/_http_guard.py` | HTTP 守卫（Host 校验、令牌校验） |
| `app/routers_v2/model_profiles.py` | 多套模型档案的切换与激活 |
| `app/services/model_profiles.py` | 模型档案存储与解析 |

### 4.5 向量存储与 Provider 工厂

| 文件 | 作用 |
|---|---|
| `app/services/vector_store.py` | sqlite-vec 的 `vec0` 扩展集成；扩展缺失时优雅降级为内存扫描，功能不中断 |
| `app/services/providers/base.py` | Provider 工厂抽象，屏蔽不同厂商调用差异 |

### 4.6 第三方桥接

| 文件 | 作用 |
|---|---|
| `app/services/doubaosave_bridge.py` | 与 doubaosave 存档工具的对接 |
| `doubaosave Skill/` | 该工具配套的浏览器自动化资源 |

### 4.7 启动器与本地化运维

| 文件 | 作用 |
|---|---|
| `TokBrain.cmd` / `tbctl.cmd` | 单一入口命令行，转发到脚本菜单 |
| `scripts/menu.ps1` | 带菜单的安装 / 启动 / 停止 / 重启 / 打开 |
| `启动.cmd` `停止.cmd` `重启.cmd` `安装.cmd` `启动飞书同步.cmd` | 中文操作入口 |
| `操作说明书.md` | 面向最终用户的中文操作手册 |
| `scripts/*.py`（10 个） | Hermes/MCP 探测、批量重试、Cookie 设置等运维脚本 |

### 4.8 文档与测试

- `docs/` 新增 **37 份**文档：产品需求文档、功能层级结构图、按钮与前后端流程映射、
  流水线 v2 改造任务清单、表结构说明、状态同步方案、控制面与 Hermes 接入说明等。
- `tests/` 新增 **20 个**测试文件，覆盖控制层、Cookie 库、平台导入流程、MCP 安全、
  sqlite-vec 检索、飞书收件箱等新增模块。

## 五、对上游文件的修改

相对基线 `e022d41b`，共有 **58 个上游文件**被修改。其中影响面最大的是：

| 文件 | 修改内容 |
|---|---|
| `app/main.py` | 新增 5 个 router（`cookies` `inbox` `model_profiles` `platforms` `sync`）；**移除 `chat.router`**（网页端问答入口改由 MCP 承担）；新增 ffmpeg/ffprobe 路径自动探测与 `PATHEXT` 修正；新增旧版 Cookie 记录迁移；`_stop_coordinator` 由 `getattr` 改为直接取属性 |
| `app/config.py` | 数据目录结构调整、模型档案与平台相关配置项 |
| `app/models.py` | 新增表以承载 Cookie、模型档案、同步与分发状态 |
| `app/services/knowledge.py` | 检索链路改为复用 `vector_store` 的向量索引 |
| `README.md` / `操作说明书.md` | 能力描述同步更新为本项目实际形态 |

## 六、差异规模一览

| 类别 | 数量 |
|---|---|
| 新增后端代码（`app/**/*.py`） | 23 个文件 |
| 新增控制层 / MCP / CLI | 28 个文件 |
| 新增前端代码（`.ts` / `.tsx`） | 10 个文件 |
| 新增运维脚本（`scripts/*.py|ps1`） | 10 个文件 |
| 新增测试 | 20 个文件 |
| 新增文档（`docs/`） | 37 个文件 |
| 新增中文启动器（`.cmd`） | 15 个文件 |
| 修改的上游文件 | 58 个文件 |

## 七、给下游使用者与二次贡献者

1. **不要直接覆盖上游 LICENSE**。MIT 要求分发时附带许可证副本，本项目已保留。
2. **不要删除 `THIRD_PARTY_NOTICES.md`**。F2 等组件的使用边界依赖该声明。
3. **提交信息区分来源**：合入上游时用 `chore(upstream): sync ...`，本地改动用
   `feat(...)` / `fix(...)`，便于日后回溯。
4. **若在本项目之上继续开发**，请在 `FORK_NOTICE.md` 追加你的修改段落，保留上游到你的
   完整链路。

---

*声明生成依据：上游仓库 `fyfsxkh/TokBrain` 的提交记录与本地文件实测对比（基线
`e022d41b`）。*
