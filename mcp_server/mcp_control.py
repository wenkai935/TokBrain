"""TokBrain 控制 MCP（薄代理）。

把 :mod:`tokbrain_control.commands` 的 33 个工具暴露给 Hermes 等 MCP 客户端。
本文件**不含业务逻辑**：每个工具都是一行 ``await asyncio.to_thread(...)``，
真正的实现在 ``tokbrain_control/`` 里，与 CLI 共用同一份代码。

启动
----
    mcp_server/启动控制MCP.cmd
    # 或
    .venv-mcp/Scripts/python.exe mcp_server/mcp_control.py

环境变量（见 control.env.example）
----------------------------------
TOKBRAIN_CONTROL_ENABLE            必须为 1，否则拒绝启动（默认关闭）
TOKBRAIN_CONTROL_HOST              绑定地址，默认 127.0.0.1；填局域网 IP 才对外
TOKBRAIN_CONTROL_PORT              端口，默认 8011
TOKBRAIN_CONTROL_TOKEN             令牌；非回环监听必填，且至少 32 位
TOKBRAIN_CONTROL_ALLOWED_HOSTS     允许的 Host 头（逗号分隔）
TOKBRAIN_CONTROL_API_BASE          后端地址，默认 http://127.0.0.1:8001
TOKBRAIN_CONTROL_ALLOW_DESTRUCTIVE 0 可一键禁掉删除/停机/重启
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:  # 作为包的一部分运行
    from mcp_server._http_guard import (
        BearerGuard,
        build_transport_security,
        describe_hermes_snippet,
        preflight_port,
        resolve_bind,
    )
except ImportError:  # 直接以脚本运行（python mcp_server/mcp_control.py）
    from _http_guard import (  # type: ignore[no-redef]
        BearerGuard,
        build_transport_security,
        describe_hermes_snippet,
        preflight_port,
        resolve_bind,
    )

from loguru import logger  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402

from tokbrain_control import __version__ as CONTROL_VERSION  # noqa: E402
from tokbrain_control import commands  # noqa: E402

ENV_PREFIX = "TOKBRAIN_CONTROL_"
DEFAULT_PORT = 8011

# ---------------------------------------------------------------- annotations
# 逐工具不同，不用"最大公约数"——Hermes 的审批与风险感知都看这几个 hint。

READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
)
WRITE_IDEMPOTENT = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
WRITE_OPEN_WORLD = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
)
DESTRUCTIVE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False
)
DESTRUCTIVE_IDEMPOTENT = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
)

INSTRUCTIONS = """\
这是 TokBrain 的**实时控制面**（本机 Windows 上的 8001 后端）。

## 与另一套工具的分工

- `tokbrain_*`（另一个 server）= NAS 上的**离线只读快照**，只能查历史收藏，快照要手动
  deploy 才更新。PC 关机时仍可用。
- `tb_*`（本 server）= **实时**，能导入、能触发解析、能改知识库、能启停服务。

规则：凡涉及"现在的状态/帮我做点什么"，一律用 `tb_*`。如果 `tb_*` 返回
`backend_offline`，明确告诉用户"本机 TokBrain 没开机"，**不要**拿快照的结果冒充操作结果。

## 意图 → 工具

| 用户意图 | 工具 |
|---|---|
| 现在什么情况 / 有没有在跑 | `tb_status`、`tb_service_status` |
| 把这几条链接存进知识库 | `tb_import_links` → `tb_batch_get` → `tb_batch_confirm` → `tb_library_ingest` |
| 把本地/NAS 上的视频存进来 | `tb_fs_roots` → `tb_fs_probe` → `tb_import_files` |
| 进度到哪了 | `tb_job_wait`、`tb_job_list`、`tb_work_list` |
| 我收藏里讲过什么 | `tb_search`（拿到证据自己回答，标注标题+时间戳+链接） |
| 某个视频具体说了什么 | `tb_work_get(with_transcript=true)` |
| 失败了重试 | `tb_work_retry` |
| 开机 / 关掉 / 重启软件 | `tb_service_start` / `tb_service_stop` / `tb_service_restart` |
| 飞书表里新加的视频处理了吗 | `tb_sync_status` → `tb_sync_pending` |
| 让飞书表的新视频进知识库 | `tb_sync_status` → `tb_sync_start` → `tb_sync_pending` → 问用户 → `tb_sync_confirm` |

## 飞书表的活交给同步桥，别自己读表

飞书表（多维表格）的读取能力在**同步桥**里，它是独立常驻进程，不在主服务生命周期内。
`tb_sync_status` 能告诉你它是否在跑、队列积压多少、连的是哪张表、当前是什么导入模式
（`mode.mode` 为 `metadata_only` 或 `download`）。

**不要自己调 `tb_import_links` 去读飞书表的链接。** 你读不到表内容（没有任何工具能读），
而同步桥已经在做这件事，两边同时写会破坏「处理状态」回写、同一行可能被导入两次。

### 指令 A：「飞书表里有什么新东西」

1. `tb_sync_status` —— `feishu.connected=false` 就先让用户在网页点「一键续期」扫码。
2. `tb_service_status` —— **后端必须在线**，否则桥拿到链接也导入不了（502）。
3. `tb_sync_start`（默认 `download=false`）—— 没在跑就拉起来，每轮约 60 秒。
4. `tb_sync_pending` —— 桥预检完的链接都在这里，**带着标题问用户要不要解析**。

### 指令 B：「**下载**我加到飞书表的视频并**解析入库**」

桥默认**不自动入库**：新链接先由 f2 补齐标题/作者/封面，然后停在 `awaiting_confirm`
等放行（`FS_AUTO_CONFIRM=0`）。所以这是一条两段式流程，不要跳步。

**第一段：先让它把信息补齐**

0. `tb_sync_status` —— **先看 `credentials.f2_cookie`**。为 false 就立刻停下，告诉用户
   「f2_cookie 不可用，飞书表里的抖音链接解析不了，得去网页设置重新保存」——
   这一步 agent 代劳不了，硬走下去只会白等几轮再失败。
   **但要分清**：这只挡「链接」；导入**本地视频文件**完全不需要 f2_cookie。
1. 看 `mode.mode`。要真下载就得是 `download`；是 `metadata_only` 就 `tb_sync_stop` →
   `tb_sync_start(download=true)`（换模式必须重启进程）。
2. 目标条目如果是 `failed`，用 `tb_sync_replay(limit=1~5, newest_first=true)` 重投。
3. `tb_sync_wait` —— 反复调直到 `idle=true` 或 `queue.awaiting_confirm > 0`。

**第二段：问人再放行**

4. `tb_sync_pending` —— 拿到 `record_id` 和 `preview[].title`。
   **把标题念给用户听**，问「这几条要解析吗」。
5. 用户说解析 → `tb_sync_confirm(record_ids="recA,recB")`。
   用户说不 → 什么都别做，让它留在表里（飞书「处理状态」显示「待确认」）。
6. `tb_job_wait` / `tb_work_list` 跟进。一个视频的下载 + ASR + OCR 要好几分钟，
   返回 `done=false` 是正常进度，**不要**重新导入、不要换方案。
7. 看到 `work_id` 后，用 `tb_work_get` 确认内容或 `tb_search` 验证。

**为什么有这道闸**：手机随手往飞书里丢链接是高频动作，自动解析等于自动烧额度、
自动撞平台风控。所以要停下来问一次。用户明确说「以后都别问我」时，让他在本机
把 `FS_AUTO_CONFIRM=1` 配进启动环境，而不是你替他跳过这一步。

**必须先说清代价**：`download=true` 会真的抓视频、调 ASR/OCR（花钱），而且可能撞平台
风控（423/429 会熔断 30 分钟）。用户没明确说「下载」时，用默认的 `download=false`。

桥不跑的时候，飞书表的新行就是没人管——这是事实，直接告诉用户，不要拿快照或别的东西冒充。

## 本地文件的路径约定

你在 NAS 上，控制面在 Windows 上，**两边看到的路径不一样**。约定表由
`tb_fs_roots` 返回，形如：NAS 前缀 `/vol1/1000/media/videos` ↔ Windows `Z:\videos`。

- 传 **Windows 路径**（`Z:\videos\a.mp4`）或 **NAS 路径**
  （`/vol1/1000/media/videos/a.mp4`）都可以，NAS 路径会被自动翻译。
- 传**裸文件名**（`a.mp4`）会在约定根目录下找。
- 不要在参数里塞文件内容或 base64，只能是路径。

标准动作：先在 NAS 上把视频下到约定目录 → `tb_fs_roots` 确认该目录在线 →
`tb_import_files(paths=[...])`（默认自动确认 + 提交入库）→ `tb_job_wait` 等完成。

**下载这一步不归 TokBrain 管**。如果不确定该下到哪，先调 `tb_fs_roots` 看
`usable_roots`，只有一个可用就下到那个目录；一个都不可用就告诉用户"NAS 映射盘掉线了"，
不要自己去别的地方找文件。

## 三条硬规则

1. **`tb_batch_confirm` 不触发入库**。它只建出待入库作品。要真正解析必须再调
   `tb_library_ingest`，否则东西永远停在"待入库"。
2. **长任务要连续等待**。`tb_job_wait` 最多等 90 秒（低于传输超时）。返回
   `done=false` 时按 `continue_with` 再调一次，**不要**重新提交入库，也不要换方案。
   返回里的 `note` 如果说"队列第 N 位"，那是单 worker 串行排队，正常。
3. **检索只给证据**。`tb_search` 不生成答案。回答必须基于返回片段，标注《标题》与
   时间戳；没有命中就直说没有。

## 破坏性操作

`tb_work_delete`（永久删除）、`tb_service_stop`、`tb_service_restart` 标了
`destructiveHint`。删除不可恢复；停机/重启会让本机正在看网页的人断掉。
不确定时先问用户。

`tb_sync_stop` 不删数据，所以没标破坏性——但它会让飞书表的新行暂时没人处理，
停之前说一声。
"""

mcp = MCPServer(
    name="tokbrain-control",
    title="TokBrain 控制面（实时）",
    version=CONTROL_VERSION,
    instructions=INSTRUCTIONS,
)


def _dump(payload: dict[str, Any]) -> str:
    """统一以 JSON 文本返回，避免不同客户端的 structured_content 兼容差异。"""
    return json.dumps(payload, ensure_ascii=False, indent=2)


async def _call(fn: Any, **kwargs: Any) -> str:
    """把同步的 commands 函数丢到线程里执行，不阻塞事件循环。"""
    return _dump(await asyncio.to_thread(fn, **kwargs))


# ============================================================== 只读（10）


@mcp.tool(
    name="tb_status",
    description=(
        "总览：后端是否在线、API 契约版本、后台协调器状态、知识库计数、最近任务。"
        "任何时候想知道'现在什么情况'都先调它。"
    ),
    annotations=READ_ONLY,
)
async def tb_status(include_jobs: int = 5, include_sync: bool = False) -> str:
    return await _call(
        commands.status, include_jobs=include_jobs, include_sync=include_sync
    )


@mcp.tool(
    name="tb_service_status",
    description=(
        "查 TokBrain 进程状态（不依赖后端）：running / stopped / orphan / stale，"
        "含 8001-3001 端口、runtime.json、启动日志尾部。判断'是不是没开机'用它。"
    ),
    annotations=READ_ONLY,
)
async def tb_service_status() -> str:
    return await _call(commands.service_status)


@mcp.tool(
    name="tb_job_list",
    description="最近的任务列表（最多 50 条），可按 state 过滤：queued/running/succeeded/partial/failed/cancelled。",
    annotations=READ_ONLY,
)
async def tb_job_list(limit: int = 10, state: str | None = None) -> str:
    return await _call(commands.job_list, limit=limit, state=state)


@mcp.tool(
    name="tb_job_get",
    description="按 job_id 取单个任务的完整状态与进度。",
    annotations=READ_ONLY,
)
async def tb_job_get(job_id: str) -> str:
    return await _call(commands.job_get, job_id=job_id)


@mcp.tool(
    name="tb_job_wait",
    description=(
        "等任务跑到终态，最多 90 秒。返回 done=true 表示结束；done=false 时按"
        " continue_with 再调一次。用它代替反复查 tb_job_get。"
    ),
    annotations=READ_ONLY,
)
async def tb_job_wait(
    job_id: str | None = None,
    batch_id: str | None = None,
    wait_seconds: int = 45,
    interval: float = 2.0,
) -> str:
    return await _call(
        commands.job_wait,
        job_id=job_id,
        batch_id=batch_id,
        wait_seconds=wait_seconds,
        interval=interval,
    )


@mcp.tool(
    name="tb_batch_get",
    description=(
        "查导入批次：状态、进度、每条 item 的 status/error_code。链接导入后必须先看这个，"
        "等 items.status=ready 才能确认。"
    ),
    annotations=READ_ONLY,
)
async def tb_batch_get(batch_id: str, include_items: bool = True) -> str:
    return await _call(commands.batch_get, batch_id=batch_id, include_items=include_items)


@mcp.tool(
    name="tb_work_list",
    description=(
        "列知识库作品。library_state：in_library（在库）/pending（待入库）/issues（异常）/"
        "archived/supplement。看失败原因用 issues。"
    ),
    annotations=READ_ONLY,
)
async def tb_work_list(
    library_state: str = "in_library",
    collection_id: int | None = None,
    platform: str | None = None,
    kind: str | None = None,
    author: str | None = None,
    offset: int = 0,
    limit: int = 20,
) -> str:
    return await _call(
        commands.work_list,
        library_state=library_state,
        collection_id=collection_id,
        platform=platform,
        kind=kind,
        author=author,
        offset=offset,
        limit=limit,
    )


@mcp.tool(
    name="tb_work_get",
    description=(
        "单个作品的摘要与元数据。with_transcript=true 时附时间轴片段（默认 8 段）。"
        "用户追问'具体怎么说的'才用它，不要滥用。"
    ),
    annotations=READ_ONLY,
)
async def tb_work_get(
    work_id: int, with_transcript: bool = False, transcript_limit: int = 8
) -> str:
    return await _call(
        commands.work_get,
        work_id=work_id,
        with_transcript=with_transcript,
        transcript_limit=transcript_limit,
    )


@mcp.tool(
    name="tb_collection_list",
    description="列所有合集及其作品数（总数/在库/待入库/异常）。分配合集前先看 id 与标题。",
    annotations=READ_ONLY,
)
async def tb_collection_list() -> str:
    return await _call(commands.collection_list)


@mcp.tool(
    name="tb_search",
    description=(
        "在知识库里检索**证据片段**（不生成答案）。返回标题、时间戳、链接、原文片段。"
        "回答必须基于这些片段并标注来源；没命中就直说没有。"
    ),
    annotations=READ_ONLY,
)
async def tb_search(query: str, top_k: int = 5, collection: str | None = None) -> str:
    return await _call(commands.search, query=query, top_k=top_k, collection=collection)


@mcp.tool(
    name="tb_fs_roots",
    description=(
        "返回「NAS 路径 ⇄ 本机 Windows 路径」的约定表：每个约定媒体目录的 Windows 路径、"
        "对应的 NAS 前缀、当前是否在线、里面有哪些视频文件。"
        "**第一次要导入本地文件时先调这个**，它会告诉你该把视频下到哪、该用什么路径传参。"
    ),
    annotations=READ_ONLY,
)
async def tb_fs_roots() -> str:
    return await _call(commands.fs_roots)


@mcp.tool(
    name="tb_fs_probe",
    description=(
        "检查一个路径在**本机 Windows**上是否可见（大小/是否目录/修改时间）。"
        "支持 Windows 路径、NAS 路径或裸文件名（按 tb_fs_roots 的约定翻译）。"
        "传空字符串等价于 tb_fs_roots。导入前先探一下，避免上传阶段才发现路径不对。"
    ),
    annotations=READ_ONLY,
)
async def tb_fs_probe(path: str, list_dir: bool = False) -> str:
    return await _call(commands.fs_probe, path=path, list_dir=list_dir)


# ============================================================== 写入（17）


@mcp.tool(
    name="tb_import_links",
    description=(
        "把视频链接（抖音/B站/小红书，可多行）建成导入批次。只做预检，不下载不花钱。"
        "返回 batch_id，接着用 tb_batch_get 等预检完成。"
    ),
    annotations=WRITE_OPEN_WORLD,
)
async def tb_import_links(
    text: str,
    metadata_only: bool = False,
    title: str | None = None,
    author: str | None = None,
) -> str:
    return await _call(
        commands.import_links,
        text=text,
        metadata_only=metadata_only,
        title=title,
        author=author,
    )


@mcp.tool(
    name="tb_import_files",
    description=(
        "导入视频文件并**一路做到入库**（建批→上传→确认→提交解析），单批≤10 个。"
        "路径可以是 Windows 路径或 NAS 路径（按 tb_fs_roots 的约定自动翻译），也可以是"
        "约定目录下的裸文件名。先用 tb_fs_probe 复核存在性。"
    ),
    annotations=WRITE,
)
async def tb_import_files(
    paths: list[str],
    collection_id: int | None = None,
    auto_confirm: bool = True,
    auto_ingest: bool = True,
) -> str:
    return await _call(
        commands.import_files,
        paths=paths,
        collection_id=collection_id,
        auto_confirm=auto_confirm,
        auto_ingest=auto_ingest,
    )


@mcp.tool(
    name="tb_import_package",
    description=(
        "把一整个目录当数据包导入（文件夹模式，≤200 个文件）。适合迁移历史收藏包。"
        "低频操作，文件多时很慢。"
    ),
    annotations=WRITE,
)
async def tb_import_package(
    folder_path: str,
    collection_id: int | None = None,
    auto_analyze: bool = True,
) -> str:
    return await _call(
        commands.import_package,
        folder_path=folder_path,
        collection_id=collection_id,
        auto_analyze=auto_analyze,
    )


@mcp.tool(
    name="tb_batch_confirm",
    description=(
        "确认导入批次里预检通过的条目。注意：**这一步不触发解析入库**，只建出待入库作品；"
        "之后必须调 tb_library_ingest。all_ready=true 表示确认所有 ready 的条目。"
    ),
    annotations=WRITE,
)
async def tb_batch_confirm(
    batch_id: str,
    item_ids: list[int] | None = None,
    all_ready: bool = False,
    collection_id: int | None = None,
) -> str:
    return await _call(
        commands.batch_confirm,
        batch_id=batch_id,
        item_ids=item_ids,
        all_ready=all_ready,
        collection_id=collection_id,
    )


@mcp.tool(
    name="tb_batch_cancel",
    description="取消一个还在跑的导入批次（预检阶段）。已确认的条目不受影响。",
    annotations=WRITE_IDEMPOTENT,
)
async def tb_batch_cancel(batch_id: str) -> str:
    return await _call(commands.batch_cancel, batch_id=batch_id)


@mcp.tool(
    name="tb_library_ingest",
    description=(
        "触发解析入库（下载→抽帧→ASR→OCR→摘要→切片→向量）。这是**真正花钱和耗时**的一步。"
        "返回 job_id，用 tb_job_wait 跟踪。"
    ),
    annotations=WRITE,
)
async def tb_library_ingest(work_ids: list[int]) -> str:
    return await _call(commands.library_ingest, work_ids=work_ids)


@mcp.tool(
    name="tb_job_cancel",
    description=(
        "取消运行中的任务。是安全边界式取消：当前作品处理完才停，state 会先变 cancelling。"
    ),
    annotations=WRITE_IDEMPOTENT,
)
async def tb_job_cancel(job_id: str) -> str:
    return await _call(commands.job_cancel, job_id=job_id)


@mcp.tool(
    name="tb_work_retry",
    description=(
        "重试处理失败的作品（只对 library_state=issues 有效）。可按 work_ids / error_code / "
        "collection_id 圈定范围，三者至少给一个。"
    ),
    annotations=WRITE,
)
async def tb_work_retry(
    work_ids: list[int] | None = None,
    error_code: str | None = None,
    collection_id: int | None = None,
) -> str:
    return await _call(
        commands.work_retry,
        work_ids=work_ids,
        error_code=error_code,
        collection_id=collection_id,
    )


@mcp.tool(
    name="tb_work_delete",
    description=(
        "**永久删除**一个作品：数据库记录、知识片段、本地媒体与关键帧文件都会被删。"
        "不可恢复。必须显式传 confirm=true。不确定时先问用户。"
    ),
    annotations=DESTRUCTIVE,
)
async def tb_work_delete(work_id: int, confirm: bool = False) -> str:
    return await _call(commands.work_delete, work_id=work_id, confirm=confirm)


@mcp.tool(
    name="tb_work_restore",
    description="把已归档的作品恢复到知识库（有知识片段则回到在库，否则回到待入库）。",
    annotations=WRITE_IDEMPOTENT,
)
async def tb_work_restore(work_id: int) -> str:
    return await _call(commands.work_restore, work_id=work_id)


@mcp.tool(
    name="tb_work_supplement",
    description=(
        "给一个处理失败的作品补件（只能一个文件），补完会自动重新入库。"
        "典型场景：原视频下不下来，用户本地有文件。"
    ),
    annotations=WRITE,
)
async def tb_work_supplement(work_id: int, paths: list[str]) -> str:
    return await _call(commands.work_supplement, work_id=work_id, paths=paths)


@mcp.tool(
    name="tb_collection_assign",
    description=(
        "把作品加进合集。合集不存在时按标题自动创建。title_or_id 可传标题或数字 id。"
    ),
    annotations=WRITE,
)
async def tb_collection_assign(title_or_id: str, work_ids: list[int]) -> str:
    return await _call(
        commands.collection_assign, title_or_id=title_or_id, work_ids=work_ids
    )


@mcp.tool(
    name="tb_platform_pull",
    description=(
        "用已保存的登录 cookie 从平台拉取收藏/点赞列表并可选直接入队导入。"
        "需要先在网页里完成对应平台的握手登录。"
    ),
    annotations=WRITE_OPEN_WORLD,
)
async def tb_platform_pull(
    platform: str,
    mode: str = "like",
    limit: int = 50,
    enqueue: bool = True,
    profile_url: str | None = None,
) -> str:
    return await _call(
        commands.platform_pull,
        platform=platform,
        mode=mode,
        limit=limit,
        enqueue=enqueue,
        profile_url=profile_url,
    )


@mcp.tool(
    name="tb_service_start",
    description=(
        "启动本机 TokBrain（后台，不会弹浏览器），最多等 40 秒到就绪。"
        "已经在跑时幂等返回。用户说'软件没开/打不开'时用。"
    ),
    annotations=WRITE_IDEMPOTENT,
)
async def tb_service_start(wait_seconds: int = 40) -> str:
    return await _call(commands.service_start, wait_seconds=wait_seconds)


@mcp.tool(
    name="tb_service_stop",
    description=(
        "停止本机 TokBrain。按 runtime.json 的进程身份校验后停止，不会误杀其他软件。"
        "停机后本机网页会立刻断开。"
    ),
    annotations=DESTRUCTIVE_IDEMPOTENT,
)
async def tb_service_stop(wait_seconds: int = 30) -> str:
    return await _call(commands.service_stop, wait_seconds=wait_seconds)


@mcp.tool(
    name="tb_service_restart",
    description="重启本机 TokBrain（先停再起），返回停机时长。改完配置或卡住时用。",
    annotations=DESTRUCTIVE,
)
async def tb_service_restart(wait_seconds: int = 40) -> str:
    return await _call(commands.service_restart, wait_seconds=wait_seconds)


# ============================================================== 飞书同步（4）


@mcp.tool(
    name="tb_sync_status",
    description=(
        "飞书同步桥的状态：进程在不在跑、队列里待处理/失败各多少、最近失败原因、"
        "飞书连接是否有效、连着哪张表。"
        "用户说「飞书表里新加的视频处理了吗」时**先调这个**。"
    ),
    annotations=READ_ONLY,
)
async def tb_sync_status() -> str:
    return await _call(commands.sync_status)


@mcp.tool(
    name="tb_sync_start",
    description=(
        "启动飞书同步桥（常驻，每 60 秒轮询一次飞书表）。"
        "桥发现新行后会先做预检（f2 补标题/作者/封面），然后**停在待确认**，"
        "不会自己入库——用 tb_sync_pending 看、用户同意后 tb_sync_confirm 放行。"
        "download=true 表示确认后要真下载视频（默认 false 只抓元数据、不碰视频）。"
    ),
    annotations=WRITE_IDEMPOTENT,
)
async def tb_sync_start(download: bool = False, auto_confirm: bool = False) -> str:
    return await _call(
        commands.sync_start, download=download, auto_confirm=auto_confirm
    )


@mcp.tool(
    name="tb_sync_stop",
    description=(
        "停止飞书同步桥。只停命令行里明确是 bridge_feishu_obsidian.py 的进程，"
        "不会误杀其他 python。停止期间飞书表新行不会被导入。"
    ),
    annotations=WRITE_IDEMPOTENT,
)
async def tb_sync_stop() -> str:
    return await _call(commands.sync_stop)


@mcp.tool(
    name="tb_sync_replay",
    description=(
        "把队列里 failed 的条目重投回慢轨（状态改回 queued）。"
        "**单次最多 20 条**，默认 5 条——历史积压的失败原因几乎全是 429 限流，"
        "一次放太多会立刻再撞同一面墙。放完用 tb_sync_wait 看消化情况再放下一批。"
        "用户说「我刚加的那条」时传 newest_first=true，否则会先翻出两个月前的旧链接。"
        "用户指着飞书表某几行说「把这几个补齐」时，必须传 record_ids 精确点名"
        "（按数量挑会挑到无关的旧链接）；不在 failed 的 record_id 会在 skipped 里返回。"
        "注意：是否真下载视频取决于桥的启动模式，返回里的 warning 会说清楚。"
    ),
    annotations=WRITE,
)
async def tb_sync_replay(
    limit: int = 5,
    newest_first: bool = False,
    record_ids: list[str] | None = None,
) -> str:
    return await _call(
        commands.sync_replay,
        limit=limit,
        newest_first=newest_first,
        record_ids=record_ids,
    )


@mcp.tool(
    name="tb_sync_wait",
    description=(
        "等同步桥把队列跑完（最多 80 秒），返回这段期间的 delta 与最近入库的作品。"
        "返回 idle=false 表示还在处理（单 worker 串行，一个视频要几分钟）——"
        "**再调一次即可，不要重新导入**。"
    ),
    annotations=READ_ONLY,
)
async def tb_sync_wait(timeout: int = 80) -> str:
    return await _call(commands.sync_wait, timeout=timeout)


@mcp.tool(
    name="tb_sync_pending",
    description=(
        "列出飞书表里「预检已完成、等你决定解不解析」的链接。"
        "桥默认不自动入库：新链接先由 f2 补齐标题/作者/封面，然后停在这里等放行。"
        "拿到的 `link` 和 `preview[].title` 用来问用户——**问清楚是哪一条再动**。"
        "用户同意解析 → 调 tb_sync_confirm；不同意就别调，让它留在表里。"
    ),
    annotations=READ_ONLY,
)
async def tb_sync_pending(limit: int = 20) -> str:
    return await _call(commands.sync_pending, limit=limit)


@mcp.tool(
    name="tb_sync_confirm",
    description=(
        "对指定的飞书行执行「确认 → 送入库队列」。传 record_ids（逗号分隔，来自 "
        "tb_sync_pending）。只处理 awaiting_confirm 状态的行，其他状态会跳过并说明原因。"
        "解析在后台单 worker 串行排队，用 tb_job_wait / tb_work_list 跟进。"
        "**只在用户明确说要解析时才调**——这一步会真的下载并消耗模型额度。"
    ),
    annotations=WRITE,
)
async def tb_sync_confirm(record_ids: str, collection: str = "") -> str:
    return await _call(
        commands.sync_confirm, record_ids=record_ids, collection=collection or None
    )


# ============================================================== 传输层


def build_app(bind: Any = None) -> Any:
    bind = bind or resolve_bind(
        os.environ,
        prefix=ENV_PREFIX,
        default_port=DEFAULT_PORT,
        enable_var="TOKBRAIN_CONTROL_ENABLE",
        service_name="TokBrain 控制 MCP",
    )
    security = build_transport_security(bind)
    app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        host=bind.host,
        transport_security=security,
    )
    if bind.token:
        app = BearerGuard(app, bind.token)
        logger.info("已启用 Bearer 令牌校验")
    else:
        logger.warning("控制面仅监听回环地址，未启用 Bearer 令牌")
    return app


def main() -> None:
    import uvicorn

    bind = resolve_bind(
        os.environ,
        prefix=ENV_PREFIX,
        default_port=DEFAULT_PORT,
        enable_var="TOKBRAIN_CONTROL_ENABLE",
        service_name="TokBrain 控制 MCP",
    )
    conflict = preflight_port(bind.host, bind.port)
    if conflict:
        raise SystemExit(conflict)

    logger.info(f"TokBrain 控制 MCP 启动：{bind.url()}")
    logger.info(f"后端地址：{os.environ.get('TOKBRAIN_CONTROL_API_BASE', 'http://127.0.0.1:8001')}")
    if bind.token:
        logger.info("令牌：已配置（不打印）")
    else:
        logger.warning("令牌：未配置（仅回环可用）")
    logger.info("Hermes 配置片段：\n" + describe_hermes_snippet(bind, bool(bind.token)))
    if not os.environ.get("TOKBRAIN_CONTROL_ALLOW_DESTRUCTIVE", "1").strip() in {
        "1",
        "true",
        "yes",
    }:
        logger.warning("破坏性操作已关闭（TOKBRAIN_CONTROL_ALLOW_DESTRUCTIVE）")

    uvicorn.run(build_app(bind), host=bind.host, port=bind.port, log_level="info")


if __name__ == "__main__":
    main()
