# TokBrain 控制面（8011）运行手册

> 对应用 v1.0.0 / 控制面 v1.0.0 / API 契约 7。
> 目标：让 NAS 上的 Hermes（大肥鱼）能**实时**驱动本机 TokBrain 的全链路，
> 而不是只能读一份手动 deploy 的快照。

---

## 1. 它解决什么问题

在此之前，外部 agent 只有两条通道，都是**只读**：

| 通道 | 位置 | 数据源 | 能力 |
|---|---|---|---|
| stdio MCP（`tokbrain_*`） | NAS | 手动 deploy 的 SQLite 快照 | 查历史，不能写 |
| 8010 MCP（`kb_*`） | 本机回环 | 本机库 | 查历史，**Hermes 根本连不上**（回环 + 无暴露） |

结果：想导入一条视频、想重试一个失败任务、想问"现在处理到哪了"——都做不到。

控制面补的就是这块：**新增 8011 端口 + 33 个 `tb_*` 工具**，全部转发到本机 8001 的既有
REST。它不是新的一套业务逻辑，是给既有 REST 装了一个远程可控的壳。

---

## 2. 拓扑

```
Hermes (NAS, systemd)
  ├─ stdio ──> mcp_server/tokbrain_hermes_stdio.py   只读快照，离线兜底（不动）
  └─ HTTP 192.168.10.100:8011/mcp  (Bearer)          控制面（新增）
                    │
                    ▼
        mcp_server/mcp_control.py      薄代理，跑在 .venv-mcp
                    │ import
                    ▼
        tokbrain_control/              纯 stdlib 内核
          ├─ client.py                 所有 REST 调用
          ├─ service.py                进程生命周期
          ├─ commands.py               33 个工具实现
          ├─ search.py                 检索（唯一可选依赖）
          └─ cli.py                    tbctl 命令行
                    │ HTTP 127.0.0.1:8001（不带 Origin 头）
                    ▼
        app/main.py                    唯一的写库方（不改）
```

**三条不可破的边界**

1. 写操作只能经 HTTP 打到 8001。控制面任何代码都不许可写打开 `data/douyin_rag.db`。
2. 不改后端的信任边界：`APP_HOST` 仍是 `Literal["127.0.0.1","localhost"]`，
   `TrustedHostMiddleware` 不动。
3. 终止进程只经 `scripts/stop.ps1`（按 `runtime.json` 身份校验）。代码里没有
   `taskkill` / `Stop-Process`。

---

## 3. 快速开始（本机）

```bat
:: 0) 一次性准备
copy mcp_server\control.env.example mcp_server\control.env
.venv-mcp\Scripts\python.exe -c "import secrets;print(secrets.token_hex(16))"
::    把输出的 32 位填进 control.env 的 TOKBRAIN_CONTROL_TOKEN
::    HOST 填 192.168.10.100（要让 Hermes 连）或 127.0.0.1（只本机自测）

:: 1) 命令行自检（零依赖，任何 Python 3.10+ 都能跑）
tbctl.cmd doctor
tbctl.cmd service status
tbctl.cmd service start

:: 2) 启动控制 MCP（独立窗口，别在 IDE 终端里跑，见 §8 坑 3）
::    三种等价方式，选一个：
mcp_server\启动控制MCP.cmd
TokBrain.cmd control
TokBrain.cmd            :: 然后在菜单里选 [8]

:: 3) 探针验证
.venv-mcp\Scripts\python.exe scripts\probe_control_mcp.py

:: 4) 放行防火墙（需要管理员，双击即可，会自己提权）
mcp_server\放行控制端口.cmd
```

**飞书同步桥已并入统一入口**：`TokBrain.cmd start` / `start-bg` / `restart` 在服务起来后
会自动拉起飞书桥（`start.ps1` 里的 `Start-FeishuBridge`），`stop` 会先停桥再停服务。
单独控制：`TokBrain.cmd bridge`（看状态）、`bridge-start` / `bridge-stop`。
不想让启动脚本碰飞书（比如纯调试），设 `TOKBRAIN_AUTOSTART_BRIDGE=0`。

---

## 4. CLI 速查

`tbctl.cmd`（根目录）等价于 `python -m tokbrain_control`。优先用 `.venv` 的解释器，
所以 `search` 也能用。

> 控制面已经是项目的正式入口之一：`TokBrain.cmd status` 会显示「控制面配置 control.env」
> 与「控制端口 8011」两行，`TokBrain.cmd control` 或菜单 `[8]` 直接拉起它。
> 但它**不受** `TokBrain.cmd stop` 管辖——关掉 8011 不影响 TokBrain 本身，反之亦然。

```bash
tbctl status --include-jobs 5
tbctl doctor                        # 解释器/后端/8011/防火墙/runtime.json/盘符
tbctl service status|start|stop|restart

# 链接导入全流程
tbctl import links --text "https://v.douyin.com/xxxx/"
tbctl batch get b_8f3a1c --wait 120
tbctl batch confirm b_8f3a1c --items 1041 --collection 3
tbctl ingest --works 501 --wait 1500

# 失败处理
tbctl work list --state issues --collection 3
tbctl work retry --works 501
tbctl work delete 501 --confirm-title "完整标题"     # 会比对标题，防手滑
tbctl work restore 501

# 检索（只给证据，不生成答案）
tbctl search "视频里讲了哪三个要点" --top-k 5

# 本地文件（NAS 需先映射盘符）
tbctl fs probe "Z:\videos" --list
tbctl import files --path "Z:\videos\lesson1.mp4" --collection 3

# 审计
tbctl audit tail --n 20
```

**退出码**：`0` 成功 / `1` 业务失败 / `2` 参数错 / `3` 后端不在线。
`--json` 输出统一信封：

```json
{"ok": true, "tool": "tb_status", "audit_id": "…", "elapsed_ms": 12,
 "data": {…}, "error": null}
```

---

## 5. 工具清单（35）

`RO`=只读 `D`=破坏性 `I`=幂等 `O`=出网

### 只读（15）

| 工具 | 说明 | 标注 |
|---|---|---|
| `tb_status` | 后端在线/契约/协调器/库计数/最近任务 | RO I |
| `tb_service_status` | 进程四态 running/stopped/orphan/stale（不依赖后端） | RO I |
| `tb_job_list` | 最近任务，可按 state 过滤 | RO I |
| `tb_job_get` | 单任务详情 | RO I |
| `tb_job_wait` | 等任务到终态，**最多 90 秒**，`done=false` 要再调 | RO I |
| `tb_batch_get` | 导入批次与每条 item 状态 | RO I |
| `tb_work_list` | 按 library_state 列作品 | RO I |
| `tb_work_get` | 作品摘要，可带时间轴片段 | RO I |
| `tb_collection_list` | 合集列表 | RO I |
| `tb_search` | 检索证据片段（不生成答案） | RO I |
| `tb_fs_roots` | **媒体路径约定表**（NAS ⇄ Windows，含在线状态与样片） | RO I |
| `tb_fs_probe` | 检查路径在本机是否可见（按约定翻译） | RO I |
| `tb_sync_status` | 飞书同步桥：进程/队列/最近失败/连哪张表/抖音是否已连 | RO I |
| `tb_sync_wait` | 等队列跑完（≤80s），返回 delta 与最近入库作品 | RO I |
| `tb_sync_pending` | **待确认列表**：预检完、等放行的飞书行（带标题/作者） | RO I |

### 写入（20）

| 工具 | 说明 | 标注 |
|---|---|---|
| `tb_import_links` | 链接建批（只预检，不花钱） | O |
| `tb_import_files` | 本地文件导入（≤10，可自动确认+入库） | |
| `tb_import_package` | 目录数据包导入（≤200 文件） | |
| `tb_batch_confirm` | 确认条目 —— **不触发入库** | |
| `tb_batch_cancel` | 取消预检批次 | I |
| `tb_library_ingest` | 触发解析入库（真正花钱的一步） | |
| `tb_job_cancel` | 安全边界式取消 | I |
| `tb_work_retry` | 重试异常作品 | |
| `tb_work_delete` | 永久删除，需 `confirm=true` | **D** |
| `tb_work_restore` | 归档恢复 | I |
| `tb_work_supplement` | 补件（单文件） | |
| `tb_collection_assign` | 加入合集，不存在则建 | |
| `tb_platform_pull` | 平台采集 | O |
| `tb_service_start` | 启动（不弹浏览器） | I |
| `tb_service_stop` | 停止 | **D** I |
| `tb_service_restart` | 重启 | **D** |
| `tb_sync_start` | 启动飞书同步桥（默认不下载视频） | I |
| `tb_sync_stop` | 停止同步桥（只停 bridge 进程） | I |
| `tb_sync_replay` | 分批重投 failed（单次 ≤20） | |
| `tb_sync_confirm` | 对待确认行执行「确认 → 送入库」（真的下载并花钱） | |

### 为什么是扁平工具而不是一个 `tb_do(action=...)`

annotations 必须逐工具不同（`tb_search` 只读、`tb_work_delete` 破坏性、
`tb_import_links` 出网）。聚合工具只能给最大公约数，会污染 Hermes 的审批判断。
代价是 schema 约 3k tokens，靠 `tools.include` 白名单控制在 18 个。

### 媒体路径约定：大肥鱼怎么把 NAS 上的视频导进来

**这块必须约定死，不能靠猜。** 大肥鱼在 NAS 上，控制面在 Windows 上：
同一个文件在两边路径不同，甚至一边能看见、另一边看不见。

约定写在 `mcp_server/control.env`：

```
TOKBRAIN_CONTROL_MEDIA_ROOTS=Z:\videos|/vol1/1000/media/videos;D:\本地视频
```

格式 `<Windows 根目录>|<NAS 前缀>`，分号分隔多组。左边是 `net use` 映射出来的
盘符路径，右边是 NAS 文件管理器里看到的原始路径。

**三种传参都能用**（`tb_fs_roots` 会把这套规则连同每个目录的在线状态一起返回）：

| 传入 | 处理 | 例 |
|---|---|---|
| Windows 绝对路径 | 原样使用 | `Z:\videos\a.mp4` |
| NAS 路径 | 按前缀翻译成 Windows 路径 | `/vol1/1000/media/videos/a.mp4` → `Z:\videos\a.mp4` |
| 裸文件名 / 相对路径 | 在约定根目录下找 | `a.mp4` → `<root>\a.mp4` |

含 `..` 的路径直接拒绝，不参与翻译。

**完整操作序列**（大肥鱼视角，5 步）：

```
1. 下载        ← 不归 TokBrain 管，下到 NAS 的约定目录（如 /vol1/1000/media/videos）
2. tb_fs_roots                 # 确认约定目录在线；一个都不可用 → 告诉用户映射盘掉了
3. tb_fs_probe(path)           # 可选，逐个复核大小/时长
4. tb_import_files(paths=[...]) # 建批→上传→确认→提交入库，一次调用走完
5. tb_job_wait(job_id)         # 最多 90s，done=false 按 continue_with 再调
```

第 4 步默认 `auto_confirm=true, auto_ingest=true`，所以**不需要**再单独调
`tb_batch_confirm` / `tb_library_ingest`。要让用户先过目就传 `auto_confirm=false`。

**能力边界**（写清楚，别让模型瞎试）：

- 单批 ≤ 10 个文件；超过要分批。目录整体导入用 `tb_import_package`，上限 200 个文件。
- 单文件上限 1 GiB（后端 `schemas.py` 的 `le=1024**3`）。
- 上传路径是 **NAS → Windows** 双跳：大肥鱼读 NAS 上的文件，控制面从映射盘流式读走再
  发给 8001。千兆内网够用，但别指望比本地导入快。
- **下载这一步 TokBrain 不参与**。没有"帮我去 B 站下这个视频"这种能力——那个要么用
  链接导入（`tb_import_links` 让后端自己下），要么大肥鱼用别的手段下完再走上面流程。
- NAS 映射盘掉线时，`tb_fs_roots` 的 `usable_roots` 会是 0，`hint` 会指向重新映射。

### 飞书表：不要让 agent 自己读表

「把**我刚加到飞书表里**的视频下载并解析入库」是最容易做错的一类指令，因为它看起来
应该由 agent 从头做，实际不是。

**飞书表的读取能力在同步桥里，不在控制面。** `bridge_feishu_obsidian.py` 已经有完整的
读表 → 导入 → 回写「处理状态」→ 写 Obsidian 链路，还带三层去重（游标、
`synced_record_ids`、队列主键）。如果 agent 也去读同一张表：

- 表里的链接**读不到**——没有任何 `tb_*` 工具暴露表内容；
  `GET /api/sync/overview` 虽然真的查了一次表，但只保留行数，链接全丢弃；
- 就算读到了，两边同时写会破坏「处理状态」回写，同一行可能被导入两次。

所以正确的分工是：**桥负责干活，agent 负责问人、盯着结果**。

```
1. tb_sync_status      # feishu.connected=false → 让用户去网页点「一键续期」扫码
2. tb_service_status   # 后端必须在线，否则桥拿到链接也导入不了（502）
3. tb_sync_start       # 没在跑就拉起来，默认 download=false
4. tb_sync_pending     # 预检完的链接在这里：带标题/作者，拿来问用户
5. tb_sync_confirm     # 用户说解析 → 放行；没说就什么都别做
6. tb_sync_replay(≤20) # 有 failed 且用户要求重试时才用，分批放
```

**桥默认不自动入库**（`FS_AUTO_CONFIRM=0`）。手机往飞书丢链接是高频动作，自动解析等于
自动烧额度、自动撞风控，所以链路被切成两段：

| 阶段 | 谁做 | 结果 |
|---|---|---|
| 预检 | 桥自动 | f2 补齐标题/作者/封面，回写飞书「待确认」，队列状态 `awaiting_confirm` |
| 放行 | 人或 agent | 网页「解析」页勾选确认，或 `tb_sync_confirm(record_ids)` → confirm + ingest |

想让桥一路跑到底（旧行为），启动时传 `auto_confirm=true`，或在环境里设 `FS_AUTO_CONFIRM=1`。

**三个必须讲清楚的约束：**

| 约束 | 含义 |
|---|---|
| 后端必须在线 | 桥只做「读表 + 调 `POST /api/import-batches`」。后端没开就直接 502，队列里留一条 failed |
| 默认**不下载视频** | `download=false` 走 `metadata_only`：只抓标题/作者/来源，视频要人工补件后才解析。要真下载得 `download=true`，会撞平台风控 |
| **链接要 f2_cookie，本地文件不要** | `tb_sync_status` 的 `credentials.f2_cookie` 为 false 时，飞书表里的抖音**链接**预检会报 `f2_cookie_unreadable`。**导入本地视频文件完全不需要它**（走 `platform=local`，不联网） |

「下载并解析入库」的完整顺序（`download=true` + `auto_confirm=true` 时 bridge 走的是
`import_link → wait_precheck → confirm → ingest → wait_job`，一条龙）：

```
tb_sync_status                          # 看 platforms.douyin_connected 与 mode.mode
tb_sync_stop                            # 换模式必须重启进程
tb_sync_start(download=true, auto_confirm=true)   # 这一步才会真下载并自动入库
tb_sync_replay(limit=1, newest_first=true)   # 目标条目已是 failed 时才需要
tb_sync_wait                            # 反复调直到 idle=true，一个视频要好几分钟
```

`newest_first=true` 很关键：用户说「我刚加的那条」时，按时间升序会先翻出两个月前的旧链接。

**队列语义**：`failed` 是终态，永远不会自动重试。要重试只能 `tb_sync_replay` 显式改回
`queued`，下一轮桥才会捡起。`awaiting_confirm` 也是终态——球在人或 agent 那边，
桥不会重复导入；`pending/queued/processing` 是非终态，桥重启后会自动接管续跑。

**为什么重投必须分批**：这个项目的历史数据就是最好的反例——队列里积压 120 条 failed，
失败原因**全是 429 Too Many Requests**，是某次一次性放开批量导入把后端限流打爆的结果。
所以 `tb_sync_replay` 单次硬上限 20 条，默认 5 条。

---

## 6. Hermes 接入

1. 在 NAS 上备份配置：

   ```bash
   cp -p /vol1/1000/hermes-state/config.yaml \
         /vol1/1000/hermes-state/backups/config-before-control-$(date +%Y%m%d-%H%M).yaml
   ```

2. 把 `mcp_server/hermes-control-config.example.yaml` 里的 `tokbrain_control:` 块
   合并进 `mcp_servers`（**不要**重复写顶层 `mcp_servers:`）。

3. 令牌写进 `/vol1/1000/hermes-state/.env`：

   ```
   TOKBRAIN_CONTROL_TOKEN=和 control.env 里一致的那串
   ```

   `chmod 600`。三个令牌必须互不相同：8010 / 8011 / 外部导入。

4. 重启并验收：

   ```bash
   sudo systemctl restart hermes-household
   ```

   先只放开 18 个工具（示例里已配好），跑一轮只读 + 一次导入，确认稳定后再放开
   注释掉的 9 个（含 3 个破坏性操作）。

**两个 server 的分工**（已写进 MCP instructions）

| 用户意图 | 用哪个 |
|---|---|
| 「我收藏里讲过什么」 | `tokbrain_search`（快照，离线可用） |
| 「今天导入了吗 / 帮我把这条入库」 | `tb_*` |
| 「刚导入的那个讲了什么」 | 先 `tb_search` 再 `tb_work_get` |
| `tb_*` 返回 `backend_offline` | 直说"本机没开机"，**不要**用快照冒充操作结果 |

不要删掉 stdio 那套：PC 关机/休眠时，它是唯一还能回答历史问题的通道。

---

## 7. 安全

| 层 | 措施 |
|---|---|
| 默认关闭 | `TOKBRAIN_CONTROL_ENABLE` 必须为 `1`，否则启动即拒绝 |
| 令牌 | 非回环必填，≥32 位；`hmac.compare_digest` 常量时间比较 |
| DNS rebinding | `allowed_hosts` 同时登记 LAN 与 127.0.0.1 |
| 防火墙 | 只放行 8011，来源限定 `192.168.10.0/24`，profile 限定 private/domain |
| 破坏性开关 | `TOKBRAIN_CONTROL_ALLOW_DESTRUCTIVE=0` 可一键禁掉删除/停机/重启 |
| 审计 | `data/logs/control-audit-YYYYMMDD.jsonl`，写操作全记，30 天清理 |

审计**不落原文**：链接正文、文件名、提示词只记数量和 sha256 摘要。每个返回体带
`audit_id`，出错时凭 id 回查。

关于 `trust: untrusted`：它的语义是"这个 server 的输出不可被当成指令"（防提示注入），
**不是**"每个写操作都要审批"。`tb_search` / `tb_work_get` 会返回视频字幕这种外部文本，
提升成 `trusted` 才是真正的安全退化。审批看的是 `destructiveHint`。

唯一"妥协"：数据包导入接口反向要求必须带同源 `Origin`（不带反而 403），
控制面在 `tb_import_package` 一处主动带上，并在审计里标 `origin_spoof_for_package`。
那是 CSRF 防护不是身份认证，控制面本来就有 Bearer + 回环双门槛。

---

## 8. 排障

**先跑 `tbctl doctor`**，它会直接列出问题。

| 现象 | 判断 | 处理 |
|---|---|---|
| NAS 上 curl 8011 超时 | 防火墙没放行 | 双击 `mcp_server/放行控制端口.cmd`；确认网络配置文件是"专用" |
| curl 返回 **401** | 通了，只是令牌不对 | 对一下 NAS `.env` 与 `control.env` |
| curl 返回 connection refused | 服务没起 | `mcp_server/启动控制MCP.cmd` |
| `tb_status` 返回 `backend_offline` | TokBrain 后端没开 | `tbctl service start` |
| `tb_service_status` 是 `stale` | 启动过但进程没了 | 见下面坑 3 |
| 任务一直 `queued` | 单 worker 串行排队 | 正常，看 `queue_position`；继续 `tb_job_wait` |
| `tb_job_wait` 反复 `done=false` | 长任务还没完 | 继续调，**不要**重复 `tb_library_ingest` |
| 导入后东西停在"待入库" | 只 `confirm` 没 `ingest` | 补一次 `tb_library_ingest` |

### 三个容易踩的坑

1. **`confirm` ≠ 入库**。`tb_batch_confirm` 只建出 `library_state=pending` 的作品，
   必须再调 `tb_library_ingest`。这是产品"预检 ≠ 入库"的两段式，别合并。
2. **NAS 路径在本机不可见**。Hermes 在 NAS 上，它说 `Z:\videos\a.mp4` 之前，
   这台 Windows 得先 `net use` 映射（跑 `mcp_server/挂载NAS媒体盘.cmd`）。
   导入前先 `tb_fs_probe`。
3. **后台进程被 Job Object 回收**（2026-11-01 实测复现）。如果在 IDE/终端里调
   `tbctl service start`，`启动.cmd` 拉起的服务会随那个终端会话一起被回收。
   症状很具体：`startup-console.log` 写着「TokBrain 1.0.0 已启动」，但 8001/3001
   端口已经关了，`runtime.json` 变成孤儿文件，`tb_service_status` 报 `stale`。
   `start.ps1` 自己也会打印「无法创建作业对象（本进程可能已受其他作业约束）」。
   处理：用独立 cmd 窗口或"登录时运行"的计划任务启动控制 MCP；残留状态用
   `tbctl service stop` 清掉（实测 4 秒完成，会删掉孤儿 `runtime.json`）。
   `tbctl doctor` 会检测并提示 `in_job_object`。

### 端口占用

启动时会 `socket.bind` 预检，冲突直接报错退出，**不会自动换端口**——换了 Hermes
配置就漂了，排障成本比报错高得多。真要换：改 `control.env` 的 PORT → 改 Hermes
`url` → 重跑放行脚本。

---

## 9. 回滚

```bash
# 1) 停控制 MCP：关掉那个 cmd 窗口（8011 不影响主服务）
# 2) 撤防火墙
#    双击 mcp_server/取消放行控制端口.cmd

# 3) 撤 Hermes 配置
cp -p /vol1/1000/hermes-state/backups/config-before-control-*.yaml \
      /vol1/1000/hermes-state/config.yaml
sudo systemctl restart hermes-household
```

控制面完全独立于主服务：`scripts/stop.ps1` 不认 8011（它的 runtime.json 里只有
8001/3001），关掉控制 MCP 不会影响 TokBrain 本身，反之亦然。

---

## 10. 明确不做的

1. 不新增 `/api/chat`。`tb_search` 只回证据，答案由 Hermes 自己的模型合成。
2. 不做克隆训练工具（产品侧未落地），只留 `FUTURE_NAMESPACE = "tb_clone_"` 注释。
3. 不暴露 8001/8010 到局域网。
4. 不实现远程 taskkill、不删 `runtime.json`、不绕过 `stop.ps1` 身份校验。
5. 不在工具里做摘要/答案生成（`summaries/jobs` 只触发任务）。
