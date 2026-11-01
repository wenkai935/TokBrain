# 安全策略 / Security policy

## 中文

### 支持版本

安全修复只应用于默认分支的最新版本。旧快照和派生仓库不在支持范围内。

### 报告安全漏洞

对于可能导致下列信息或能力暴露的问题，请使用 GitHub 私有漏洞报告功能：

- API Key、F2 Cookie、由 DPAPI 保护的值或本地数据库内容；
- 任意本地文件、内部网络地址或不安全重定向；
- 属于其他用户的已下载媒体；
- 远程代码执行、命令注入或不受限制的文件上传；
- 绕过下载权限、速率限制或风险熔断控制。

请勿在公开 Issue 中提交真实密钥、Cookie、私密视频、数据库、日志压缩包或创作者
内容。请使用人工构造的数据，并遮盖 URL 和标识符。维护者应在公开仓库之前启用
**Settings → Security → Private vulnerability reporting**。

### 本地安全模型

TokBrain 的设计目标是在一台 Windows 电脑上由一名可信用户本地使用。后端：

- 只绑定 `127.0.0.1`/`localhost`；
- 限制 Host、Origin 和 CORS 值；
- 不实现多用户身份认证或权限控制；
- 在本地保存可变数据，并使用 Windows DPAPI 保护已配置的秘密。DPAPI 只在 Windows
  上可用，非 Windows 环境下密钥存储不可用，因此当前不支持跨平台部署。

不要通过局域网、反向代理、隧道、容器宿主机或公网暴露 3001、8001 端口。托管或
多用户部署需要重新进行安全设计，并补充身份认证、权限控制、CSRF 防护、租户隔离、
审计日志、数据保留控制和法律审查。

### MCP 与外部接入的攻击面

项目同时存在三个 MCP，安全边界不同，需要分别对待——**前两个只读，第三个可写**：

| 组件 | 默认绑定 | 鉴权 | 能力 |
|---|---|---|---|
| 本机网络 MCP（`mcp_server/tokbrain_mcp.py`，工具名 `kb_*`） | `127.0.0.1:8010` | 绑定非回环地址时**必须**设置 `TOKBRAIN_MCP_TOKEN`，否则进程拒绝启动；带 Bearer 校验 | 只读 |
| Hermes stdio 副本（`mcp_server/tokbrain_hermes_stdio.py`，工具名 `tokbrain_*`） | 不监听端口 | 由 Hermes 以 stdio 拉起，仅本机父进程可访问 | 只读 |
| 控制面（`mcp_server/mcp_control.py`，工具名 `tb_*`） | 需要显式开启；`TOKBRAIN_CONTROL_HOST` 决定是否对外 | `TOKBRAIN_CONTROL_ENABLE=1` 才启动；非回环**必须**配 `TOKBRAIN_CONTROL_TOKEN` 且至少 32 位；Bearer 常量时间比较 | **可写**：导入、触发入库、重试、永久删除、启停服务 |

控制面是本项目唯一对外可写的入口，它的护栏是分层的：

- **默认关闭**：不设 `TOKBRAIN_CONTROL_ENABLE=1` 直接拒绝启动，避免装完就暴露。
- **令牌三分离**：8010 令牌、8011 令牌、外部导入令牌必须互不相同；启动脚本会自检并告警。
- **不直接写库**：所有写操作转发到 `127.0.0.1:8001`，与网页同一条路径，不存在绕过业务规则的第二套写通道。
- **不留强杀能力**：停止服务只经 `scripts/stop.ps1`，按 `data/runtime.json` 的进程身份复核，控制面代码里没有 `taskkill`。
- **可一键关闸**：`TOKBRAIN_CONTROL_ALLOW_DESTRUCTIVE=0` 会禁用永久删除、停机、重启。
- **全量审计**：写操作记入 `data/logs/control-audit-*.jsonl`，不记原文，只记参数摘要与结果。
- **防火墙收敛**：配套脚本只放行 8011，来源限 `192.168.10.0/24`，且限私有/域网络配置。

仍然不要把任何 MCP 端口暴露到公网；对外提供知识检索应通过反向代理加认证，而不是直接把 MCP 挂上去。

### 平台凭据出站约束

- 保存的平台 Cookie 只会发往对应平台的 HTTPS 官方域名，请求前会做公网 DNS 校验，
  并逐跳校验重定向目标；不会把 Cookie 带到第三方域名或明文 HTTP。
- 可选 F2 Cookie 属于链接解析的 legacy 兜底路径，日常导入主路径不再依赖 F2。
- 外部批量导入 API 需要 Bearer Token、权利声明和 `Idempotency-Key`，服务仍只监听回环地址。

### 敏感文件

绝不要提交 `.env`、`data/`、`logs/`、`backups/`、SQLite 文件、已下载媒体、
字幕、关键帧、导出笔记或浏览器/会话凭据。每次公开发布前都应运行
`scripts/prepublish_check.ps1`。

---

## English

### Supported version

Security fixes are applied to the latest revision of the default branch. Older
snapshots and forks are not supported.

### Reporting a vulnerability

Please use GitHub Private Vulnerability Reporting for issues that could expose:

- API keys, F2 cookies, DPAPI-protected values, or local database contents;
- arbitrary local files, internal network addresses, or unsafe redirects;
- downloaded media belonging to another user;
- remote code execution, command injection, or unrestricted file upload;
- a bypass of the download-permission, rate-limit, or risk-circuit controls.

Do not include a real secret, Cookie, private video, database, log archive, or
creator content in a public issue. Use synthetic data and redact URLs and
identifiers. Maintainers should enable **Settings → Security → Private
vulnerability reporting** before making the repository public.

### Local security model

TokBrain is designed for one trusted user on one Windows machine. The backend:

- binds to `127.0.0.1`/`localhost`;
- restricts Host, Origin, and CORS values;
- does not implement multi-user authentication or authorization;
- stores mutable data locally and protects configured secrets with Windows
  DPAPI. DPAPI is Windows-only, so secrets cannot be stored on other
  platforms and cross-platform hosting is not supported today.

Do not expose ports 3001 or 8001 to a LAN, reverse proxy, tunnel, container
host, or the public Internet. A hosted or multi-user deployment requires a
separate security design, authentication, authorization, CSRF protection,
tenant isolation, audit logging, retention controls, and legal review.

### MCP and integration attack surface

Three MCP servers exist with different boundaries — **the first two are read-only,
the third is writable**:

| Component | Default bind | Auth | Capability |
|---|---|---|---|
| Local network MCP (`mcp_server/tokbrain_mcp.py`, tools `kb_*`) | `127.0.0.1:8010` | Binding a non-loopback address **requires** `TOKBRAIN_MCP_TOKEN` or startup fails; Bearer checked per request | read-only |
| Hermes stdio replica (`mcp_server/tokbrain_hermes_stdio.py`, tools `tokbrain_*`) | no listening port | started by Hermes over stdio, reachable only by the local parent process | read-only |
| Control plane (`mcp_server/mcp_control.py`, tools `tb_*`) | disabled unless explicitly enabled; `TOKBRAIN_CONTROL_HOST` decides exposure | requires `TOKBRAIN_CONTROL_ENABLE=1`; non-loopback binding **requires** `TOKBRAIN_CONTROL_TOKEN` of at least 32 chars; constant-time Bearer comparison | **writable**: import, trigger ingest, retry, permanent delete, service start/stop |

The control plane is the only writable entry point in this project. Its guards:

- **Off by default**: without `TOKBRAIN_CONTROL_ENABLE=1` the process refuses to start.
- **Three separate tokens**: the 8010 token, the 8011 token, and the external-import
  token must all differ; the launcher warns when they collide.
- **No direct database writes**: every mutation is forwarded to `127.0.0.1:8001`,
  the same path the web UI uses, so there is no second write channel bypassing
  business rules.
- **No force-kill capability**: stopping the service goes through `scripts/stop.ps1`,
  which re-verifies process identity from `data/runtime.json`. The control plane
  contains no `taskkill` path.
- **Single kill switch**: `TOKBRAIN_CONTROL_ALLOW_DESTRUCTIVE=0` disables permanent
  delete, stop, and restart.
- **Full audit**: mutations land in `data/logs/control-audit-*.jsonl` with argument
  digests rather than raw content.
- **Narrow firewall rule**: the helper script allows only port 8011, only from
  `192.168.10.0/24`, and only on private/domain profiles.

Never publish any MCP port to the public Internet; front external access with an
authenticated reverse proxy.

### Platform credential egress

- Stored platform cookies are only sent to that platform's official HTTPS
  domains, after a public-DNS check and per-hop redirect validation.
- The optional F2 cookie is a legacy fallback for link parsing; the main import
  path no longer depends on F2.

### Sensitive files

Never commit `.env`, `data/`, `logs/`, `backups/`, SQLite files, downloaded
media, subtitles, keyframes, exported notes, or browser/session credentials.
Run `scripts/prepublish_check.ps1` before every public release.
