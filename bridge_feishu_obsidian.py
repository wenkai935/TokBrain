#!/usr/bin/env python3
"""
bridge_feishu_obsidian.py
=========================
把「飞书收藏视频收件箱」与「TokBrain 本地知识库」打通，并把处理结果导出到 Obsidian。

端到端流水线：
  飞书多维表格(手机分享的链接+标签)
      -> 本脚本读取新行
      -> 推送 TokBrain 做深度处理(转写/抽帧/OCR/总结)
      -> 处理完成后调 obsidian/manifest 拿到 markdown + 关键帧
      -> 写入 Obsidian Vault(笔记 + 图片 + 关系图谱)
      -> 回写飞书「同步状态=已同步」

两类来源都支持：
  1) 抖音/公开视频链接  -> TokBrain 链接导入 API
  2) 已下载到本地的视频  -> TokBrain v1 外部批次导入 API(需 integration token)

飞书尚未配置时，可用本地 inbox.json 兜底测试(见 INBOX_PATH)。

运行前提：
  - TokBrain 后端在 127.0.0.1:8001 正常运行(务必在干净环境启动，避免被 safe-delete 拦截)
  - 已通过 启动.cmd 或本脚本同机启动；integration token 放在 TOKBRAIN_TOKEN 或 data/integration_token.txt
  - requests 库可用(用 TokBrain 的 venv: .venv/Scripts/python.exe 运行本脚本即可)

依赖：pip install requests   （TokBrain 的 venv 已含 requests）
"""

import os
import re
import sys
import time
import json
import uuid
import hashlib
import shutil
import sqlite3
import urllib.parse
import subprocess
import tempfile
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("缺少 requests，请先: .venv/Scripts/pip install requests")

# 共享的 dispatch 队列 / 链接规范化（入站 API 与 bridge 共用同一份，避免漂移）
from app.services.dispatch_store import DispatchStore

# ---------------------------------------------------------------------------
# 配置（可用环境变量覆盖）
# ---------------------------------------------------------------------------
HERE = Path(__file__).resolve().parent

TOKBRAIN_BASE = os.environ.get("TOKBRAIN_BASE", "http://127.0.0.1:8001")
# token 优先用环境变量，其次读 data/integration_token.txt
_TOKEN_FILE = HERE / "data" / "integration_token.txt"
TOKBRAIN_TOKEN = os.environ.get("TOKBRAIN_TOKEN") or (
    _TOKEN_FILE.read_text(encoding="utf-8").strip() if _TOKEN_FILE.exists() else ""
)

# Obsidian Vault 根目录（改成你自己的 Vault 路径）
VAULT_PATH = Path(os.environ.get("VAULT_PATH", r"D:\ObsidianVault"))
# Vault 内放关键帧图片的附件目录
VAULT_ATTACHMENTS = os.environ.get("VAULT_ATTACHMENTS", "99附件")
# 关键帧硬链接镜像目录：与用户自有附件物理隔开，便于整体管理与排查。
# 这里每个文件都是 data/keyframes 下实体的硬链接，不额外占用磁盘空间。
VAULT_ATTACH_KEYFRAMES = os.environ.get("VAULT_ATTACH_KEYFRAMES", "99附件/keyframes")
# Vault 内放视频收藏笔记的目录；按你的规则「视频收藏只进入 00收件箱/视频收藏」，
# 实际落盘会再按标签分子目录。可用环境变量 VAULT_NOTES 覆盖。
VAULT_NOTES = os.environ.get("VAULT_NOTES", "00收件箱/视频收藏")

# ---- 飞书配置（不配则走本地 inbox.json）----
# 重要：本脚本通过 lark-cli 的「用户身份」访问多维表格，而不是应用身份(tenant token)。
# 原因：自建应用 cli_aa1b01b781389cbb 未开通应用身份的 bitable:app 等 scope，
#       直接用 tenant_access_token 调 OpenAPI 会返回 99991672 Access denied；
#       而 lark-cli 持有的用户 token 已包含 base:table:read / base:record:read / base:record:update。
FEISHU_BITABLE_APP_TOKEN = os.environ.get("FEISHU_BITABLE_APP_TOKEN", "")
FEISHU_BITABLE_TABLE_ID = os.environ.get("FEISHU_BITABLE_TABLE_ID", "")
# lark-cli 所在目录（会被加入 PATH）
# 历史坑：lark-cli 曾装在 WorkBuddy 托管的 node 目录里，该目录会随 node 升级被清理
# （22.22.2 -> 22.22.2-2 -> 22.22.2-6）。2026-10-30 起固定装在项目内 cli/ 目录
# （npm install --prefix cli @larksuite/cli），与托管运行时彻底解耦。
# 探测顺序：LARK_CLI_DIR 环境变量 -> 项目内 cli/ -> versions/current 指针 ->
# versions/* 下任一含 @larksuite/cli 的目录 -> 全局 npm 目录。
_NODE_VERSIONS_DIR = Path.home() / ".workbuddy" / "binaries" / "node" / "versions"


def _has_lark_cli_exe(base: Path) -> bool:
    return (
        base / "node_modules" / "@larksuite" / "cli" / "bin" / "lark-cli.exe"
    ).exists()


def _resolve_lark_cli_dir() -> str:
    override = os.environ.get("LARK_CLI_DIR", "").strip()
    if override:
        return override

    # 优先用项目内置的 cli/：不随 WorkBuddy 的 node 升级被清理。
    bundled = HERE / "cli"
    if _has_lark_cli_exe(bundled):
        return str(bundled)

    candidates = []
    try:
        pointer = (_NODE_VERSIONS_DIR / "current").read_text(encoding="utf-8").strip()
    except OSError:
        pointer = ""
    if pointer:
        candidates.append(_NODE_VERSIONS_DIR / pointer)
    try:
        candidates.extend(sorted(_NODE_VERSIONS_DIR.glob("*"), reverse=True))
    except OSError:
        pass

    for cand in candidates:
        if _has_lark_cli_exe(cand):
            return str(cand)

    npm_root = os.environ.get("APPDATA", "")
    if npm_root:
        npm_dir = Path(npm_root) / "npm"
        if _has_lark_cli_exe(npm_dir):
            return str(npm_dir)

    # 全都没找到时返回 current 指针目录，让错误信息指向期望位置。
    return str(_NODE_VERSIONS_DIR / "current")


LARK_CLI_DIR = _resolve_lark_cli_dir()
# Windows 下 .cmd 会经 cmd.exe 转发，命令行长度受 8191 上限约束——批量回写
# 120+ 条记录(JSON 约 1 万字符)会直接失败(返回码非 0)。优先用原生 .exe
# (node_modules/@larksuite/cli/bin/lark-cli.exe，不走 cmd.exe 无长度限制)；
# 找不到再回退 .cmd / sh 版本。
_EXE_CANDIDATES = [
    os.environ.get("LARK_CLI_EXE", ""),
    str(Path(LARK_CLI_DIR) / "node_modules" / "@larksuite" / "cli" / "bin" / "lark-cli.exe"),
]
LARK_CLI = next((p for p in _EXE_CANDIDATES if p and Path(p).exists()), None)
if LARK_CLI is None:
    LARK_CLI = os.environ.get("LARK_CLI", "lark-cli.cmd")
    # 若 .cmd 不存在则回退到无扩展名的 sh 版本
    if LARK_CLI == "lark-cli.cmd" and not (Path(LARK_CLI_DIR) / "lark-cli.cmd").exists():
        LARK_CLI = "lark-cli"

# 列名：与你飞书表的实际结构一致。
# 收藏夹分层：用「父级 / 子级」两列组合成层级路径（如 收藏/未分类），决定 Obsidian 目录层级；
# FS_TAG_COLS 为附加标签列（兼容旧 标签/标签2..标签5），读取时去重合并。
FS_COL_LINK = os.environ.get("FS_COL_LINK", "链接")
FS_PARENT_COL = os.environ.get("FS_PARENT_COL", "父级")
FS_CHILD_COL = os.environ.get("FS_CHILD_COL", "子级")
# 是否允许 bridge 自动创建缺失的 TokBrain 收藏夹。
# 方案 B：收藏夹名 = 首标签的完整层级路径（如 "收藏/未分类"），与 Obsidian 目录一致。
# 设为 0 则只在收藏夹已存在时关联，缺失时跳过并告警。
FS_AUTO_CREATE_COLLECTION = os.environ.get("FS_AUTO_CREATE_COLLECTION", "1") != "0"
# 收藏夹名来源层级：2（默认）= 仅认明确的层级路径（父级/子级，含 "/"）；
# 1 = 放宽到首标签（仅父级也认）。为 2 时可避免把话题类标签（如「技术分享」）误建成收藏夹。
FS_COLLECTION_MIN_LEVEL = int(os.environ.get("FS_COLLECTION_MIN_LEVEL", "2") or 2)
FS_TAG_COLS = [
    c.strip()
    for c in os.environ.get("FS_TAG_COLS", "标签,标签2,标签3,标签4,标签5").split(",")
    if c.strip()
]
# 流水线写入飞书表的三列（可加列时启用；不存在会自动创建）
FS_COL_STATUS = os.environ.get("FS_COL_STATUS", "处理状态")
FS_COL_WORK_ID = os.environ.get("FS_COL_WORK_ID", "work_id")
FS_COL_MODIFIED = os.environ.get("FS_COL_MODIFIED", "最后修改时间")
# 元数据回写列：处理完成后把作品标题/作者/平台来源同步回飞书，
# 避免飞书行长期为空（仅在 works 表确有值时写入，缺失不覆盖）。
FS_COL_TITLE = os.environ.get("FS_COL_TITLE", "标题")
FS_COL_AUTHOR = os.environ.get("FS_COL_AUTHOR", "作者")
FS_COL_SOURCE = os.environ.get("FS_COL_SOURCE", "来源")
# LID（数据唯一标识）：飞书表新增的文本列，作为该条数据在落表之后
# 贯穿飞书 / dispatch / Obsidian 全链路的业务主键，随每一条数据携带。
FS_COL_LID = os.environ.get("FS_COL_LID", "LID")
# 平台标识 -> 可读来源名（写入飞书「来源」列）
PLATFORM_NAMES = {
    "douyin": "抖音",
    "bilibili": "B站",
    "xhs": "小红书",
    "xiaohongshu": "小红书",
    "kuaishou": "快手",
}


def _make_lid(link: str) -> str:
    """根据链接生成稳定唯一标识 LID。

    规则：``lid_`` + sha256(去掉 markdown 包裹后的链接) 前 16 位。
    同一链接无论从哪个入口进来（飞书直发 / doubaosave 采集 / 入站 API）
    都会得到同一 LID，全局一致、幂等可重算，适合作为跨系统唯一标识。
    """
    raw = _strip_markdown_link(link or "").strip()
    return "lid_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
# 飞书「处理状态」列的全部选项。必须覆盖 DISPATCH_TO_FEISHU_STATUS 的所有取值，
# 否则回写时会撞「选项不存在」而失败（awaiting_confirm / ingested 曾漏在这里）。
STATUS_OPTIONS = ["待处理", "处理中", "待确认", "解析中", "已归档", "需补件", "失败"]
# dispatch 内部状态 -> 飞书「处理状态」列选项的映射
DISPATCH_TO_FEISHU_STATUS = {
    "pending": "待处理",
    "queued": "处理中",
    "processing": "处理中",
    "awaiting_confirm": "待确认",
    "ingested": "解析中",
    "processed": "已归档",
    "needs_supplement": "需补件",
    "failed": "失败",
}
# 飞书侧与本机时钟可能有偏差，游标回退一段时间作为安全边界，避免漏行
CURSOR_SKEW_SECONDS = int(os.environ.get("CURSOR_SKEW_SECONDS", "300"))

# 占位标题前缀：仅链接入库（metadata_only）或本地补件的作品标题是占位符，
# 回写飞书 / Obsidian 标题时跳过，避免覆盖收藏导入已有的真实标题。
_PLACEHOLDER_TITLE_PREFIXES = ("待补件 · ", "待补件·", "本地补件 ", "本地补件·")

# 同步状态：飞书表没有「同步状态」列，改用本地状态文件记录已同步的 record_id，
# 避免改动你的表结构。可用环境变量 SYNC_STATE_PATH 覆盖。
SYNC_STATE_PATH = Path(
    os.environ.get(
        "SYNC_STATE_PATH",
        str(VAULT_PATH / "99 系统" / "飞书增量同步" / "同步状态.json"),
    )
)

# 流水线队列：记录每条收件记录的处理状态，实现「占位笔记」与「回填」解耦。
# 用 SQLite 而非 JSON，天然支持并发访问与事务。DispatchStore / DISPATCH_DB
# 定义在 app/services/dispatch_store.py（与入站 API 共用）。

# 本地兜底收件箱（飞书未配置时使用）
INBOX_PATH = HERE / "inbox.json"

POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "10"))
POLL_TIMEOUT = int(os.environ.get("POLL_TIMEOUT", "600"))  # 单任务最多等 10 分钟
# 常驻轮询的周期（秒）：分钟级实时性要求下 60s 足够，且不会给飞书接口造成压力
POLL_CYCLE = int(os.environ.get("POLL_CYCLE", "60"))
# 命令行传 --watch 进入常驻轮询模式
WATCH_MODE = "--watch" in sys.argv
CLI_DOWNLOAD = "--download" in sys.argv
CLI_AUTO_CONFIRM = "--auto-confirm" in sys.argv

# 是否放弃抖音解析（默认开启）：抖音风控(429)会熔断 30min，飞书同步不再把链接
# 丢给平台解析，改为「长链确认 + 元数据 Work 入库」，Obsidian 笔记保留链接；
# 后续可在 TokBrain 网页为对应 work 补件。设 0 可回退到原解析路径。
FS_NO_DOUYIN = False if CLI_DOWNLOAD else os.environ.get("FS_NO_DOUYIN", "1") != "0"

# 预检完成后是否直接 confirm + ingest 到底。
#   默认 0（停下等确认）：链接先由 f2 补上标题/作者/封面，回写飞书记「待确认」，
#   等人或 agent 说「解析吧」再入库。设计意图：手机随手存的链接不该自动烧额度。
#   设 1 回到旧行为：预检完直接确认并送入库队列，一路跑到作品出来。
FS_AUTO_CONFIRM = CLI_AUTO_CONFIRM or os.environ.get("FS_AUTO_CONFIRM", "0") == "1"

# 是否在导出笔记时，把该作品的关键帧图片也拷进 Vault 并嵌入（默认开启）。
# 注意：官方 obsidian/manifest 默认不带关键帧(asset_ids 为空)，
# 这里额外读取 TokBrain 内部的 data/keyframes 与 keyframes 表来补齐"图片"价值。
INCLUDE_KEYFRAMES = os.environ.get("INCLUDE_KEYFRAMES", "1") != "0"
# 关键帧入选阈值（selection_score），低于此分的不导出，避免图片过多
KEYFRAME_MIN_SCORE = float(os.environ.get("KEYFRAME_MIN_SCORE", "0.0"))
# TokBrain 数据库与关键帧目录（脚本位于 TokBrain 项目内，按相对路径定位）
TOKBRAIN_DB = HERE / "data" / "douyin_rag.db"
TOKBRAIN_KEYFRAMES_DIR = HERE / "data" / "keyframes"


# ---------------------------------------------------------------------------
# TokBrain 客户端
# ---------------------------------------------------------------------------
class TokBrain:
    def __init__(self, base=TOKBRAIN_BASE, token=TOKBRAIN_TOKEN):
        self.base = base.rstrip("/")
        self.token = token
        self.s = requests.Session()
        self.s.headers.update({"Accept": "application/json"})
        self._collection_cache = {}  # {title.lower(): collection_id}

    # ---- 收藏夹（collections）：飞书标签 → TokBrain 收藏夹 ----
    def list_collections(self):
        """返回 {title.lower(): collection_id}。注意 collections 挂在 /api/library 前缀下。"""
        r = self.s.get(f"{self.base}/api/library/collections", timeout=30)
        r.raise_for_status()
        data = r.json() or {}
        items = data.get("items") or []
        result = {}
        for it in items:
            title = str(it.get("title") or "").strip()
            cid = it.get("id")
            if title and cid is not None:
                result[title.lower()] = cid
        return result

    def ensure_collection(self, title):
        """按标题查/建收藏夹，返回 collection_id；无标题或未开启自动创建时返回 None。

        幂等：先查列表；缺失时创建；创建撞 409（重名，并发或缓存失效）则重新拉取列表再取。
        任何异常都只记日志并返回 None，**不影响主流程**。
        """
        if not title:
            return None
        key = title.lower()
        if key in self._collection_cache:
            return self._collection_cache[key]
        try:
            found = self.list_collections()
        except Exception as e:
            print(f"  [warn] 读取 TokBrain 收藏夹列表失败: {e}")
            found = {}
        if key in found:
            self._collection_cache[key] = found[key]
            return found[key]
        if not FS_AUTO_CREATE_COLLECTION:
            print(f"  [warn] 收藏夹「{title}」不存在且未开启自动创建，跳过关联")
            return None
        try:
            r = self.s.post(
                f"{self.base}/api/library/collections", json={"title": title}, timeout=30)
            if r.status_code == 409:
                # 重名：可能被并发创建，重新拉取一次
                found = self.list_collections()
                cid = found.get(key)
                if cid is not None:
                    self._collection_cache[key] = cid
                return cid
            r.raise_for_status()
            cid = (r.json() or {}).get("id")
            if cid is not None:
                self._collection_cache[key] = cid
                print(f"  [collection] 已创建收藏夹「{title}」(id={cid})")
            return cid
        except Exception as e:
            print(f"  [warn] 创建收藏夹「{title}」失败，跳过关联: {e}")
            return None

    def add_works_to_collection(self, collection_id, work_ids):
        """把已入库作品补挂到收藏夹（本地导入等无 confirm 环节的场景）。"""
        if not collection_id or not work_ids:
            return None
        r = self.s.post(
            f"{self.base}/api/library/collections/{collection_id}/works",
            json={"work_ids": list(work_ids)},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    # ---- 链接导入：抖音/公开视频 ----
    def import_link(self, text, *, metadata_only=False, title=None, author=None):
        """创建链接导入批次，返回 batch_id。

        metadata_only=True 时不访问平台（放弃抖音解析，长链确认入库），后端
        直接用长链 + 可选元数据建 Work，避免风控熔断；预检项随即 ready。
        """
        body = {"text": text}
        if metadata_only:
            body["metadata_only"] = True
            if title:
                body["title"] = title
            if author:
                body["author"] = author
        r = self.s.post(f"{self.base}/api/import-batches", json=body, timeout=30)
        r.raise_for_status()
        return r.json()["batch_id"]

    def get_batch(self, batch_id):
        r = self.s.get(f"{self.base}/api/import-batches/{batch_id}", timeout=30)
        r.raise_for_status()
        return r.json()

    def confirm_batch(self, batch_id, item_ids, collection_id=None):
        items = [{"item_id": i, "collection_id": collection_id} for i in item_ids] if collection_id else []
        r = self.s.post(
            f"{self.base}/api/import-batches/{batch_id}/confirm",
            json={"item_ids": item_ids, "items": items},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def ingest(self, work_ids):
        r = self.s.post(f"{self.base}/api/library/ingest/jobs", json={"work_ids": work_ids}, timeout=30)
        r.raise_for_status()
        return r.json()

    # ---- 本地文件导入：v1 外部批次（需 token）----
    def create_external_batch(self, client_item_ids, rights_attested=True):
        headers = {"Idempotency-Key": str(uuid.uuid4()), "Authorization": f"Bearer {self.token}"}
        items = [{"client_item_id": cid} for cid in client_item_ids]
        r = self.s.post(
            f"{self.base}/api/integrations/v1/import-batches",
            headers=headers,
            json={"rights_attested": rights_attested, "items": items},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["batch_id"]

    def upload_external_asset(self, batch_id, client_item_id, file_path):
        headers = {"Authorization": f"Bearer {self.token}"}
        with open(file_path, "rb") as f:
            r = self.s.put(
                f"{self.base}/api/integrations/v1/import-batches/{batch_id}/items/{client_item_id}/asset",
                headers=headers,
                files={"file": (Path(file_path).name, f, "application/octet-stream")},
                timeout=300,
            )
        r.raise_for_status()
        return r.json()

    def commit_external_batch(self, batch_id, start_processing=True):
        headers = {"Authorization": f"Bearer {self.token}"}
        r = self.s.post(
            f"{self.base}/api/integrations/v1/import-batches/{batch_id}/commit",
            headers=headers,
            json={"start_processing": start_processing},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def get_external_batch(self, batch_id):
        headers = {"Authorization": f"Bearer {self.token}"}
        r = self.s.get(f"{self.base}/api/integrations/v1/import-batches/{batch_id}", headers=headers, timeout=30)
        r.raise_for_status()
        return r.json()

    # ---- 任务轮询 ----
    def get_job(self, job_id):
        r = self.s.get(f"{self.base}/api/jobs/{job_id}", timeout=30)
        r.raise_for_status()
        return r.json()

    # TokBrain 的任务终态：partial 表示「跑完了但有条目失败」，
    # 例如作品需要本地补件时 ingest 就是 partial，不再是中间态。
    _JOB_TERMINAL = {"succeeded", "failed", "partial", "cancelled"}

    def wait_job(self, job_id):
        """轮询任务直到进入终态，返回最终 job 字典。"""
        deadline = time.time() + POLL_TIMEOUT
        while time.time() < deadline:
            job = self.get_job(job_id)
            state = str(job.get("state") or "").lower()
            if state in self._JOB_TERMINAL:
                return job
            time.sleep(POLL_INTERVAL)
        raise TimeoutError(f"job {job_id} 超时未完成")

    # ---- Obsidian 导出 ----
    def obsidian_manifest(self, work_ids):
        r = self.s.post(f"{self.base}/api/library/obsidian/manifest", json={"work_ids": work_ids}, timeout=60)
        r.raise_for_status()
        return r.json()["items"]

    def download_asset(self, work_id, asset_name, dest: Path):
        url = f"{self.base}/api/library/works/{work_id}/assets/{urllib.parse.quote(asset_name)}"
        r = self.s.get(url, timeout=120)
        r.raise_for_status()
        dest.write_bytes(r.content)


# ---------------------------------------------------------------------------
# 飞书客户端（经 lark-cli，用户身份）
# ---------------------------------------------------------------------------
def _strip_markdown_link(value):
    """剥掉飞书粘贴进单元格的 markdown 链接标记：[text](url) → url。

    用户从飞书分享粘贴时单元格值常为 "[https://...](https://...)" 形式，
    直接当链接会导致导入失败，这里统一还原成裸 URL。
    """
    s = str(value or "").strip()
    m = re.match(r"^\[[^\]]*\]\(\s*(\S+)\s*\)$", s)
    return m.group(1) if m else s


def _decode_cli_output(data: bytes) -> str:
    """解码 lark-cli 子进程输出：优先 UTF-8，失败回退 GBK/CP936。

    Windows 控制台默认代码页常为 GBK，lark-cli 错误信息按本地代码页输出；
    用单一编码硬解码会抛 UnicodeDecodeError（8/29-9/6 期间多次把飞书回写整段炸掉）。
    """
    if not data:
        return ""
    for enc in ("utf-8", "gbk", "cp936", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


class Feishu:
    """通过 lark-cli 以「用户身份」读取多维表格。

    不使用应用身份(tenant_access_token)：自建应用未开通应用身份的 bitable scope，
    直接调 OpenAPI 会返回 99991672 Access denied。lark-cli 的用户 token 已具备
    base:table:read / base:record:read / base:record:update 等 scope，因此走用户身份。
    """

    def __init__(self, app_token=FEISHU_BITABLE_APP_TOKEN, table_id=FEISHU_BITABLE_TABLE_ID):
        self.app_token = app_token
        self.table_id = table_id

    def _run(self, args, timeout=180, cwd=None):
        env = os.environ.copy()
        env["PATH"] = LARK_CLI_DIR + os.pathsep + env.get("PATH", "")
        # Windows 控制台下 lark-cli 输出可能为 GBK/CP936（尤其错误信息），直接 utf-8
        # 硬解码会抛 UnicodeDecodeError 把整次调用炸掉；先按字节捕获再回退解码。
        proc = subprocess.run(
            [LARK_CLI] + args,
            capture_output=True,
            env=env,
            timeout=timeout,
            cwd=cwd,
        )
        if proc.returncode != 0:
            err = _decode_cli_output(proc.stderr or proc.stdout)
            raise RuntimeError(f"lark-cli 调用失败: {' '.join(args)}\n{err}")
        return _decode_cli_output(proc.stdout)

    @staticmethod
    def _read_ndjson(path: Path):
        """解析 lark-cli 的 ndjson 输出为 [{'record_id','fields'}]。"""
        if not path.exists():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            rid = obj.get("_record_id") or obj.get("record_id")
            if not rid:
                continue
            rows.append({"record_id": rid, "fields": obj})
        return rows

    def list_records(self):
        """返回 [{'record_id':..., 'fields':{...}}]。用 ndjson 输出，避免解析 markdown 表格。"""
        # lark-cli 的 --output 只允许「当前工作目录下的相对路径」，
        # 因此把 cwd 切到临时目录，再用相对文件名输出。
        with tempfile.TemporaryDirectory() as td:
            rel = "records.ndjson"
            self._run(
                [
                    "base", "+record-list",
                    "--base-token", self.app_token,
                    "--table-id", self.table_id,
                    "--format", "ndjson",
                    "--output", rel,
                    "--as", "user",
                ],
                cwd=td,
            )
            return self._read_ndjson(Path(td) / rel)

    def _list_records_raw(self, filter_json=None):
        """调用 base +record-list（可选 filter-json 增量过滤），解析 ndjson 为记录列表。

        用 record-list 而非 record-search：后者强制要求 keyword + search_fields，
        是「关键词搜字段」接口，不能按修改时间做增量过滤；record-list 支持
        --filter-json（与视图筛选同构），才是增量拉取的正确工具。
        """
        args = [
            "base", "+record-list",
            "--base-token", self.app_token,
            "--table-id", self.table_id,
            "--format", "ndjson",
            "--output", "records.ndjson",
            "--as", "user",
        ]
        if filter_json:
            args += ["--filter-json", json.dumps(filter_json, ensure_ascii=False)]
        with tempfile.TemporaryDirectory() as td:
            self._run(args, cwd=td)
            return self._read_ndjson(Path(td) / "records.ndjson")

    def list_fields(self):
        """返回表中已有字段名的集合。解析失败返回空集合（按未知处理）。"""
        out = self._run(
            ["base", "+field-list",
             "--base-token", self.app_token,
             "--table-id", self.table_id,
             "--as", "user"]
        )
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            return set()
        # 飞书 +field-list 返回结构：{"ok":true,"data":{"fields":[{name,id,type...}]}}
        items = (
            data.get("items")
            or (data.get("data") or {}).get("fields")
            or data.get("fields")
            or []
        )
        names = set()
        for i in items:
            if isinstance(i, dict):
                names.add(i.get("name") or i.get("field_name") or "")
        names.discard("")
        return names

    def ensure_columns(self):
        """确保流水线需要的三列存在，缺失则创建。返回新建的列名列表。"""
        existing = self.list_fields()
        to_create = []
        if FS_COL_STATUS not in existing:
            to_create.append({
                "name": FS_COL_STATUS, "type": "select", "multiple": False,
                "options": [{"name": s} for s in STATUS_OPTIONS],
            })
        if FS_COL_WORK_ID not in existing:
            to_create.append({"name": FS_COL_WORK_ID, "type": "number"})
        if FS_COL_MODIFIED not in existing:
            # 飞书「最后修改时间」字段：lark-cli +field-create 用字符串判别符
            # "updated_at"（= 平台内部 1002 类型，由平台自动维护），不接受数字 1002
            # （实测报 800010701 Invalid discriminator value）。
            to_create.append({"name": FS_COL_MODIFIED, "type": "updated_at"})
        to_create.extend(
            {"name": col, "type": "text"}
            for col in (FS_COL_TITLE, FS_COL_AUTHOR, FS_COL_SOURCE)
            if col not in existing
        )
        if FS_COL_LID not in existing:
            to_create.append({"name": FS_COL_LID, "type": "text"})
        if not to_create:
            return []
        self._run(
            ["base", "+field-create",
             "--base-token", self.app_token,
             "--table-id", self.table_id,
             "--json", json.dumps(to_create, ensure_ascii=False),
             "--as", "user"]
        )
        return [f["name"] for f in to_create]

    def get_updated_rows(self, cursor=""):
        """按「最后修改时间」增量拉取，返回 [(record_id, link, tags, modified)]。

        优先用 record-list 的 filter-json 按最后修改时间增量；该列不存在、
        或过滤语法不被接受时，自动退回全量拉取。run_once 还会用 synced
        按 record_id 二次去重，因此即便增量退化为全量也不影响正确性。
        """
        if cursor:
            filter_json = {
                "logic": "and",
                "conditions": [[FS_COL_MODIFIED, "isGreater", cursor]],
            }
            try:
                rows = self._list_records_raw(filter_json)
                print(f"  [feishu] 增量拉取命中 {len(rows)} 行（游标 {cursor}）")
                return [self._row_tuple(r) for r in rows]
            except RuntimeError as e:
                print(f"  [warn] 增量过滤失败，退回全量拉取: {e}")
        rows = self._list_records_raw()
        return [self._row_tuple(r) for r in rows]

    def _collect_tags(self, fields):
        """收集标签：父级/子级组合成层级路径（作为首标签，决定 Obsidian 目录），
        其余标签列（FS_TAG_COLS）去重追加。

        例如 父级=收藏、子级=未分类 → 首标签 "收藏/未分类"，
        占位笔记落 `视频收藏/收藏/未分类/{record_id}.md`。
        """
        tags = []
        parent = self._text(fields.get(FS_PARENT_COL)).strip()
        child = self._text(fields.get(FS_CHILD_COL)).strip()
        if parent:
            tags.append(f"{parent}/{child}" if child else parent)
        for col in FS_TAG_COLS:
            if col in (FS_PARENT_COL, FS_CHILD_COL):
                continue
            v = self._text(fields.get(col)).strip()
            if v:
                tags.append(v)
        return tags

    def _row_tuple(self, row):
        """把一行记录整理成 (record_id, lid, link, tags, modified)。

        lid 取飞书「LID」列的值；空则返回空串，由快轨在入队前生成并回填。
        """
        fields = row["fields"]
        lid = self._text(fields.get(FS_COL_LID)).strip()
        tags = self._collect_tags(fields)
        modified = self._text(fields.get(FS_COL_MODIFIED)).strip()
        return (
            row["record_id"],
            lid,
            self._text(fields.get(FS_COL_LINK)).strip(),
            " ".join(dict.fromkeys(tags)),
            modified,
        )

    @staticmethod
    def _text(value):
        """把单元格值规整成纯文本（支持 list / dict / None），并剥掉 markdown 链接标记。"""
        if value is None:
            return ""
        if isinstance(value, list):
            parts = []
            for v in value:
                if isinstance(v, dict):
                    parts.append(_strip_markdown_link(v.get("text") or v.get("name") or ""))
                else:
                    parts.append(_strip_markdown_link(v))
            return " ".join(p for p in parts if p.strip())
        if isinstance(value, dict):
            return _strip_markdown_link(value.get("text") or value.get("name") or "")
        return _strip_markdown_link(value)

    def get_new_rows(self, synced_ids):
        """返回 [(record_id, link, tags)]，仅取未同步且有链接的行。
        标签从父级/子级组合 + 标签列合并、去重，只使用表中明确提供的值。"""
        rows = self.list_records()
        result = []
        for row in rows:
            rid = row["record_id"]
            if rid in synced_ids:
                continue
            fields = row["fields"]
            link = self._text(fields.get(FS_COL_LINK)).strip()
            if not link:
                continue
            tags = self._collect_tags(fields)
            result.append((rid, link, " ".join(dict.fromkeys(tags))))
        return result

    def update_records(self, updates: dict) -> None:
        """批量回写记录状态。updates = {record_id: {列名: CellValue}}。

        使用 +record-batch-update：select 列值为 ["选项"]，number 列值为裸数字。
        调用方（writeback_feishu_status）负责只在状态变化时传入，保证幂等、
        不浪费接口额度，且失败不影响主流程。
        """
        if not updates:
            return
        body = {"update_records": updates}
        self._run(
            [
                "base", "+record-batch-update",
                "--base-token", self.app_token,
                "--table-id", self.table_id,
                "--json", json.dumps(body, ensure_ascii=False),
                "--as", "user",
            ]
        )

    def backfill_lids(self) -> list:
        """存量行 LID 回填：对「链接有值、LID 为空」的行生成并写回，幂等。

        覆盖历史数据以及用户直接分享进表（没走 doubaosave 采集）的行，
        确保飞书表每一条有链接的数据都具备 LID。返回本次回填的 record_id。
        """
        updates = {}
        for row in self.list_records():
            fields = row["fields"]
            link = self._text(fields.get(FS_COL_LINK)).strip()
            if not link:
                continue
            if self._text(fields.get(FS_COL_LID)).strip():
                continue
            updates[row["record_id"]] = {FS_COL_LID: _make_lid(link)}
        if not updates:
            return []
        self.update_records(updates)
        return list(updates)


# ---------------------------------------------------------------------------
# 同步状态（本地文件：飞书表无「同步状态」列）
# ---------------------------------------------------------------------------
def load_sync_state():
    if SYNC_STATE_PATH.exists():
        try:
            return json.loads(SYNC_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_sync": None, "synced_record_ids": [], "pending_record_ids": []}


def save_sync_state(state):
    SYNC_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SYNC_STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# 流水线队列：把「收件归档」与「深度处理」解耦
# DispatchStore / DISPATCH_DB 已迁移到 app/services/dispatch_store.py，
# 与入站 API（/api/integrations/v1/inbox/items）共用同一份队列与链接规范化
# 逻辑，避免两份实现漂移。本文件顶部已 `from app.services.dispatch_store
# import DispatchStore, DISPATCH_DB`，此处不再本地定义。
# ---------------------------------------------------------------------------


def local_inbox_rows():
    """本地兜底：从 inbox.json 读取待处理行。每条: {"link":..., "tags":..., "local_path":...}"""
    if not INBOX_PATH.exists():
        return []
    data = json.loads(INBOX_PATH.read_text(encoding="utf-8"))
    out = []
    for i, e in enumerate(data):
        link = e.get("link", "")
        # 本地兜底没有飞书 LID 列，入队时用同一规则即时生成
        out.append((f"local-{i}", _make_lid(link), link, e.get("tags", ""), e.get("local_path", "")))
    return out


# ---------------------------------------------------------------------------
# Obsidian 导出
# ---------------------------------------------------------------------------
def work_platform_id(work_id):
    """取作品的 platform_work_id，用作附件命名的幂等键。查不到返回空串。"""
    if not TOKBRAIN_DB.exists():
        return ""
    try:
        import sqlite3
        con = sqlite3.connect(str(TOKBRAIN_DB))
        row = con.execute("SELECT platform_work_id FROM works WHERE id=?", (work_id,)).fetchone()
        con.close()
        return (row[0] or "") if row else ""
    except Exception:
        return ""


def hardlink_into_vault(src: Path, dest: Path) -> str:
    """把源文件接入 Vault，优先硬链接（不额外占磁盘），失败依次降级为符号链接、实体复制。

    返回实际使用的方式：hardlink / symlink / copy；失败返回空串。
    跨卷或非 NTFS 文件系统下硬链接会失败，此时降级保证功能不中断。
    """
    if dest.exists():
        try:
            if dest.stat().st_ino == src.stat().st_ino:
                return "hardlink"  # 已指向同一实体，幂等跳过
        except OSError:
            pass
        try:
            dest.unlink()
        except OSError as e:
            print(f"  [warn] 无法替换已存在文件 {dest.name}: {e}")
            return ""
    try:
        os.link(src, dest)
        return "hardlink"
    except OSError as e:
        print(f"  [warn] 硬链接失败({e})，尝试符号链接: {src.name}")
    try:
        dest.symlink_to(src)
        return "symlink"
    except OSError as e:
        print(f"  [warn] 符号链接失败({e})，降级为实体复制: {src.name}")
    shutil.copy2(src, dest)
    return "copy"


def gather_keyframes(work_id, base_name):
    """从 TokBrain 内部 keyframes 表/目录，把入选关键帧以硬链接方式接入 Vault，
    并返回要嵌入笔记的 Markdown 片段（含时间戳与说明）。失败返回空串。

    附件名以 platform_work_id 为前缀，重复导出时路径固定，天然幂等。
    """
    if not INCLUDE_KEYFRAMES or not TOKBRAIN_DB.exists():
        return ""
    try:
        import sqlite3
        con = sqlite3.connect(str(TOKBRAIN_DB))
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT path, timestamp_seconds, selection_score, visual_description, ocr_text "
            "FROM keyframes WHERE work_id=? ORDER BY timestamp_seconds",
            (work_id,),
        ).fetchall()
        con.close()
    except Exception as e:
        print(f"  [warn] 读取关键帧失败: {e}")
        return ""

    attach_dir = VAULT_PATH / VAULT_ATTACH_KEYFRAMES
    attach_dir.mkdir(parents=True, exist_ok=True)
    # 用 platform_work_id 做前缀，保证重复导出不会产出 -1.jpg 之类副本
    prefix = work_platform_id(work_id) or base_name

    snippet_lines = ["", "## 关键帧", ""]
    linked = 0
    modes = set()
    for idx, r in enumerate(rows, 1):
        score = float(r["selection_score"] or 0)
        if score < KEYFRAME_MIN_SCORE:
            continue
        src = Path(r["path"])
        if not src.exists():
            continue
        ts = float(r["timestamp_seconds"] or 0)
        ext = src.suffix or ".jpg"
        dest_name = f"{prefix}-kf{idx:02d}{ext}"
        mode = hardlink_into_vault(src, attach_dir / dest_name)
        if not mode:
            continue
        modes.add(mode)
        caption = (r["visual_description"] or "").strip().replace("\n", " ")
        ocr = (r["ocr_text"] or "").strip().replace("\n", " ")
        note = caption or ocr
        snippet_lines.append(f"![[{dest_name}]]")
        snippet_lines.append(f"> 时间 {ts:.1f}s" + (f" ｜ {note}" if note else ""))
        snippet_lines.append("")
        linked += 1
    if linked == 0:
        return ""
    if modes == {"hardlink"}:
        hint = "硬链接，不额外占用空间"
    else:
        hint = f"链接方式 {'/'.join(sorted(modes))}，已降级"
    print(f"  [obsidian] 关键帧 {linked} 张已接入 Vault（{hint}）")
    return "\n".join(snippet_lines)


def prune_orphan_keyframe_links():
    """清理已删除作品在 Vault 侧残留的关键帧链接，避免「幽灵占用」。

    作品在 TokBrain 中被永久删除后，Vault 侧硬链接仍在，磁盘空间不会释放
    （硬链接双方平权，只删一边空间不回收）。本函数扫描镜像目录，对比数据库，
    删除数据库中已不存在作品的链接。返回清理数量。
    """
    attach_dir = VAULT_PATH / VAULT_ATTACH_KEYFRAMES
    if not attach_dir.exists() or not TOKBRAIN_DB.exists():
        return 0
    try:
        import sqlite3
        con = sqlite3.connect(str(TOKBRAIN_DB))
        alive = {r[0] for r in con.execute("SELECT platform_work_id FROM works")}
        con.close()
    except Exception as e:
        print(f"  [warn] 读取作品列表失败，跳过清理: {e}")
        return 0

    removed = 0
    for f in attach_dir.iterdir():
        if not f.is_file():
            continue
        # 文件名格式：{platform_work_id}-kf{nn}.ext
        stem = f.stem
        marker = stem.rfind("-kf")
        if marker <= 0:
            continue
        pid = stem[:marker]
        if pid not in alive:
            try:
                f.unlink()
                removed += 1
            except OSError as e:
                print(f"  [warn] 清理残留链接失败 {f.name}: {e}")
    if removed:
        print(f"  [cleanup] 已清理 {removed} 个已删除作品的残留关键帧链接")
    return removed


def work_processing_state(work_id):
    """直读 TokBrain 数据库，返回作品的 processing_state（兜底轮询用）。"""
    if not TOKBRAIN_DB.exists():
        return None
    try:
        import sqlite3
        con = sqlite3.connect(str(TOKBRAIN_DB))
        row = con.execute("SELECT processing_state FROM works WHERE id=?", (work_id,)).fetchone()
        con.close()
        return row[0] if row else None
    except Exception:
        return None


def _tag_to_dir(value):
    """把（子级）标签值归一化为安全的多级目录相对路径。

    规则：
      - 子级标签以 '/'（兼容 '\\'）分隔层级，如 "夹A/子夹B" → "夹A/子夹B"
      - 逐段清理文件系统非法字符与首尾空白，空段丢弃
      - 返回形如 "夹A/子夹B" 的相对路径，供 mkdir(parents=True) 自动逐级创建
    """
    parts = []
    for segment in re.split(r"[/\\]+", str(value or "").strip()):
        seg = re.sub(r'[<>:"|?*\x00-\x1f]+', " ", segment).strip(" .")
        seg = re.sub(r"\s+", " ", seg).strip()
        if seg:
            parts.append(seg)
    return "/".join(parts)


def resolve_collection_title(tags):
    """方案 B：用层级路径作为 TokBrain 收藏夹名，与 Obsidian 目录一一对应。

    只认「父级/子级」这种明确含层级的路径，避免把话题类标签误建成收藏夹：
      tags="收藏/未分类 干货" → "收藏/未分类"   （建/关联）
      tags="技术分享 干货"    → ""              （首标签无层级，不关联）
      tags="家装"            → "家装"（仅当 FS_COLLECTION_MIN_LEVEL=1）
      tags=""               → ""              （不关联）
    """
    tag_list = [t for t in (str(tags or "").split()) if t]
    if not tag_list:
        return ""
    first = _tag_to_dir(tag_list[0])
    if not first:
        return ""
    if "/" in first or FS_COLLECTION_MIN_LEVEL <= 1:
        return first
    return ""


def write_placeholder_note(record_id, lid, link, tags) -> Path:
    """快轨：收件即写占位笔记，不等 TokBrain 处理。

    笔记文件名用 record_id 固定，保证后续回填时路径不变、天然幂等。
    目录按标签层级展开：首标签含子级（如 "夹A/子夹B"）时，在收藏目录下
    以子级标签值为文件夹名逐级创建（缺失自动新建），例如
    "00收件箱/视频收藏/夹A/子夹B/{record_id}.md"。
    frontmatter 携带 lid（数据唯一标识）与 feishu_record_id，贯穿全链路。
    """
    tag_list = [t for t in (tags or "").split() if t]
    first_dir = _tag_to_dir(tag_list[0]) if tag_list else ""
    note_dir = VAULT_PATH / VAULT_NOTES / (first_dir or "_未分类")
    # parents=True：父夹/子夹任一缺失都会自动新建
    note_dir.mkdir(parents=True, exist_ok=True)
    note_path = note_dir / f"{record_id}.md"
    if note_path.exists():
        return note_path  # 已存在则原样返回，保证重复运行不覆盖

    text = (
        "---\n"
        f'title: "待处理 · {record_id}"\n'
        f"lid: {lid}\n"
        f"source: {_platform_from_link(link)}\n"
        f"url: {link}\n"
        f"feishu_record_id: {record_id}\n"
        f"tags: [{', '.join(tag_list)}]\n"
        "topics: []\n"
        "projects: []\n"
        "status: pending\n"
        "work_id: null\n"
        f"captured_at: {time.strftime('%Y-%m-%d %H:%M')}\n"
        "tokbrain_synced_at: null\n"
        "---\n\n"
        "> ⏳ TokBrain 正在处理这条视频，转写、关键帧与总结完成后会自动回填到本笔记。\n\n"
        f"原文链接：<{link}>\n"
    )
    note_path.write_text(text, encoding="utf-8")
    return note_path


def merge_frontmatter(md_text: str, fields: dict) -> str:
    """把顶层字段写入 Markdown 的 YAML frontmatter，保留原有行顺序与结构。

    已存在的键就地替换，不存在的追加到 frontmatter 末尾；没有 frontmatter 则创建。
    """
    if md_text.startswith("---"):
        end = md_text.find("\n---", 3)
        if end != -1:
            head, body = md_text[4:end], md_text[end + 4:]
            out, seen = [], set()
            for line in head.splitlines():
                key = line.split(":", 1)[0].strip() if ":" in line else ""
                if key in fields:
                    out.append(f"{key}: {fields[key]}")
                    seen.add(key)
                else:
                    out.append(line)
            for k, v in fields.items():
                if k not in seen:
                    out.append(f"{k}: {v}")
            return "---\n" + "\n".join(out) + "\n---" + body
    head = "\n".join(f"{k}: {v}" for k, v in fields.items())
    return f"---\n{head}\n---\n\n{md_text}"


def _merge_tag_list(md_text: str, extra_tags):
    """把 extra_tags 合并进 markdown frontmatter 的 tags 数组。

    兜底：manifest 的 tags 是 TokBrain AI 关键词，会覆盖占位笔记里的飞书标签。
    这里把飞书标签前置合并（去重），保证来源标签在回填后仍然存在。
    """
    m = re.search(r"^tags:\s*\[(.*?)\]\s*$", md_text, re.M)
    existing = []
    if m and m.group(1).strip():
        existing = [
            p.strip().strip("'\"")
            for p in m.group(1).split(",")
            if p.strip()
        ]
    merged = list(dict.fromkeys([*extra_tags, *existing]))
    return "[" + ", ".join(merged) + "]"


def _sync_obsidian_title(note_path: str, title: str) -> None:
    """把真实标题同步进 Obsidian 笔记的 frontmatter `title` 字段。

    快轨占位笔记的 title 是写死的「待处理 · {record_id}」，与飞书/作品无关；
    作品处理完、works 表有真实标题后，由 writeback_feishu_status 在同一次回写
    中调用本函数，保证飞书与 Obsidian 两侧标题一致。
    """
    p = Path(note_path)
    if not p.exists():
        return
    try:
        md = p.read_text(encoding="utf-8")
        # 用 JSON 双引号包裹，避免标题内含冒号/引号破坏 YAML frontmatter
        md = merge_frontmatter(md, {"title": json.dumps(title, ensure_ascii=False)})
        p.write_text(md, encoding="utf-8")
    except Exception as e:
        print(f"  [warn] 同步 title 到 Obsidian 失败 {p.name}: {e}")


def _rename_note_to_concept(note_path: Path, filename: str, work_id) -> Path:
    """用 manifest 的核心概念改名，并保留同目录下的唯一性。"""
    stem = Path(str(filename or "")).stem
    stem = re.sub(r"__[^_]+-[^_]+$", "", stem).strip()
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" ._") or f"未命名作品-{work_id}"
    target = note_path.with_name(f"{stem}.md")
    if target == note_path:
        return note_path
    if target.exists():
        target = note_path.with_name(f"{stem}__{work_id}.md")
    try:
        note_path.replace(target)
        return target
    except OSError as e:
        print(f"  [warn] 核心概念改名失败 {note_path.name}: {e}")
        return note_path


def _backfill_note_metadata_only(work_id, item) -> Path:
    """仅链接入库（无媒体）的轻量回填：不动媒体 manifest，只更新 frontmatter。

    占位笔记已含原文链接与标签；此处补上 work_id / lid / 状态 / 溯源字段，
    并把正文末尾追加一行提示，说明媒体待补（避免用户误以为已含视频内容）。
    """
    note_path = Path(item["note_path"])
    note_path.parent.mkdir(parents=True, exist_ok=True)
    if not note_path.exists():
        md = ""
    else:
        md = note_path.read_text(encoding="utf-8")
    _lid = str(item.get("lid") or "").strip() or _make_lid(str(item.get("link") or ""))
    feishu_tags = [t for t in str(item.get("tags") or "").split() if t]
    _fields = {
        "feishu_record_id": item["record_id"],
        "lid": _lid,
        "url": item["link"],
        "status": "processed",
        "work_id": str(work_id),
        "tokbrain_synced_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tags": _merge_tag_list(md, feishu_tags),
        "source": _platform_from_link(str(item.get("link") or "")),
    }
    meta = _work_meta(work_id)
    if meta.get("platform"):
        _fields["source"] = meta["platform"]
    if meta.get("author_name"):
        _fields["author"] = meta["author_name"]
    if meta.get("platform_work_id"):
        _fields["platform_work_id"] = meta["platform_work_id"]
    md = merge_frontmatter(md, _fields)
    hint = "\n> 已按原文链接确认入库（未下载媒体，避免平台风控）。在 TokBrain 网页为该作品补传视频后，本笔记会自动补全内容。\n"
    if hint.strip() not in md:
        md = md.rstrip() + "\n" + hint
    note_path.write_text(md, encoding="utf-8")
    meta_title = _work_meta(work_id).get("title")
    if meta_title:
        note_path = _rename_note_to_concept(note_path, meta_title, work_id)
    item["note_path"] = str(note_path)
    return note_path


def backfill_note(tb: TokBrain, work_id, item) -> Path:
    """慢轨：处理完成后把正文与关键帧回填到占位笔记，并推进 frontmatter 状态。

    覆盖的是快轨阶段就已确定的同一路径，因此不会新建第二个文件。
    仅链接入库的 work（无媒体）走轻量 frontmatter 回填，不依赖媒体 manifest。
    """
    if work_id in _METADATA_ONLY_WORK_IDS:
        return _backfill_note_metadata_only(work_id, item)
    note_path = Path(item["note_path"])
    note_path.parent.mkdir(parents=True, exist_ok=True)
    items = tb.obsidian_manifest([work_id])
    if not items:
        raise RuntimeError("Obsidian manifest 返回空，无法回填")

    it = items[0]
    kf = gather_keyframes(work_id, note_path.stem)
    md = it["markdown"]
    if kf:
        md = md.rstrip() + "\n" + kf + "\n"
    feishu_tags = [t for t in str(item.get("tags") or "").split() if t]
    _lid = str(item.get("lid") or "").strip() or _make_lid(str(item.get("link") or ""))
    meta = _work_meta(work_id)
    _fields = {
        "feishu_record_id": item["record_id"],
        "lid": _lid,
        "url": item["link"],
        "status": "processed",
        "work_id": str(work_id),
        "tokbrain_synced_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        # 兜底：飞书标签前置合并，避免被 manifest 的 AI 关键词覆盖
        "tags": _merge_tag_list(md, feishu_tags),
    }
    # 作品真实溯源信息：source 由快轨推断值覆盖为 works.platform；
    # author / platform_work_id 为新增字段，补全作品来源。
    if meta.get("platform"):
        _fields["source"] = meta["platform"]
    if meta.get("author_name"):
        _fields["author"] = meta["author_name"]
    if meta.get("platform_work_id"):
        _fields["platform_work_id"] = meta["platform_work_id"]
    md = merge_frontmatter(md, _fields)
    note_path.write_text(md, encoding="utf-8")
    note_path = _rename_note_to_concept(note_path, it.get("filename"), work_id)
    item["note_path"] = str(note_path)
    return note_path


def mark_note_status(item, status, message=""):
    """把处理结果写回占位笔记的 frontmatter，让状态在 Obsidian 里真实可见。

    失败/需补件时尤其重要：否则笔记一直显示 pending，用户会以为还在处理。
    """
    note_path = Path(item["note_path"])
    if not note_path.exists():
        return
    fields = {"status": status}
    if message:
        fields["error"] = " ".join(str(message).split())[:500]
    try:
        md = merge_frontmatter(note_path.read_text(encoding="utf-8"), fields)
        note_path.write_text(md, encoding="utf-8")
    except OSError as e:
        print(f"  [warn] 更新笔记状态失败 {note_path.name}: {e}")


def export_to_obsidian(tb: TokBrain, work_ids, tags=""):
    """把已处理的作品导出为 Obsidian 笔记 + 关键帧图片。返回导出的 md 路径列表。"""
    items = tb.obsidian_manifest(work_ids)
    notes_dir = VAULT_PATH / VAULT_NOTES
    attach_dir = VAULT_PATH / VAULT_ATTACHMENTS
    notes_dir.mkdir(parents=True, exist_ok=True)
    attach_dir.mkdir(parents=True, exist_ok=True)

    written = []
    for it in items:
        # 按标签在笔记目录下再分子目录（含子级标签时逐级创建；无标签则用 _未分类）
        first_tag = _tag_to_dir(tags.split()[0]) if tags.split() else ""
        tag_dir = notes_dir / (first_tag or "_未分类")
        tag_dir.mkdir(parents=True, exist_ok=True)

        # it['filename'] 已含 .md 后缀（由 TokBrain 的 safe_filename 生成）
        fname = it["filename"] if it["filename"].lower().endswith(".md") else it["filename"] + ".md"
        md_path = tag_dir / fname
        # 下载官方 manifest 自带资产（如有）
        for asset in it.get("assets", []):
            dest = attach_dir / asset["export_name"]
            if not dest.exists():
                tb.download_asset(it["work_id"], asset["name"], dest)
        # 追加关键帧（官方 manifest 默认不带，这里补齐；以硬链接接入，不重复占空间）
        base_name = Path(fname).stem
        kf = gather_keyframes(it["work_id"], base_name)
        md_text = it["markdown"]
        if kf:
            md_text = md_text.rstrip() + "\n" + kf + "\n"
        md_path.write_text(md_text, encoding="utf-8")
        written.append(str(md_path))
        n_assets = len(it.get("assets", []))
        print(f"  [obsidian] 已写出: {md_path}  (官方资产 {n_assets} 张)")
    return written


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
class NeedsSupplement(Exception):
    """作品需要本地补件：作者未开放下载，TokBrain 未取得视频/音频/字幕。

    这是一个等待人工处理的终态，不是失败——不应自动重投。
    """


class AwaitingConfirm(Exception):
    """预检已完成，但按 FS_AUTO_CONFIRM=0 停下来等确认。

    预检已经把标题/作者/封面补齐，批次里的条目是 ready 状态。此时不 confirm、
    不 ingest，避免手机随手存进来的链接自动消耗解析额度。
    确认入口有两个：TokBrain 网页「解析」页勾选确认，或控制面 tb_sync_confirm。
    """

    def __init__(self, batch_id: str, item_ids: list, message: str = "") -> None:
        self.batch_id = batch_id
        self.item_ids = item_ids
        super().__init__(
            message
            or f"预检完成（批次 {batch_id}，{len(item_ids)} 条就绪），等待确认是否解析"
        )


# 预检进行中的状态：需要继续轮询
_PRECHECK_PENDING = {"", None, "pending", "queued", "prechecking", "processing", "reserved"}
# 预检结束的状态
_PRECHECK_DONE = {"ready", "needs_local_file", "failed", "rejected", "cancelled", "deduplicated"}
# 仅链接入库（无媒体）的 work：回填时跳过媒体 manifest，改用轻量 frontmatter 更新。
# 由 process_link 的 metadata_only 分支登记，backfill_note 据此分流。
_METADATA_ONLY_WORK_IDS: set = set()


def wait_precheck(tb: TokBrain, batch_id):
    """轮询直到所有条目预检结束，返回 items 列表。"""
    deadline = time.time() + POLL_TIMEOUT
    items = []
    while time.time() < deadline:
        view = tb.get_batch(batch_id)
        items = view.get("items", []) or []
        if items and all(str(i.get("status") or "").lower() in _PRECHECK_DONE for i in items):
            return items
        time.sleep(POLL_INTERVAL)
    return items


def process_link(tb: TokBrain, link, tags):
    """链接导入全流程，返回 work_ids。"""
    if FS_NO_DOUYIN:
        # 放弃抖音解析：长链确认 + 元数据 Work 入库（默认，见 process_link_metadata_only）
        return process_link_metadata_only(tb, link, tags)
    print(f"[import-link] {link}")
    batch_id = tb.import_link(link)
    # 等预检真正结束（之前只要拿到 item id 就 confirm，结果在预检失败时会撞 409）
    items = wait_precheck(tb, batch_id)
    if not items:
        raise RuntimeError("链接预检超时，可能链接无效或被风控(423/429)")

    confirmable = [
        i for i in items if str(i.get("status") or "").lower() in ("ready", "needs_local_file")
    ]
    failed = [i for i in items if str(i.get("status") or "").lower() == "failed"]
    if not confirmable:
        detail = "；".join(
            f"{i.get('error_code') or 'error'}: {i.get('error_message') or '未知原因'}"
            for i in failed
        ) or "无可用条目"
        raise RuntimeError(f"预检失败，无可确认条目 → {detail}")
    if failed:
        print(f"  [warn] {len(failed)} 条预检失败，将跳过")

    if not FS_AUTO_CONFIRM:
        # 预检已完成：标题/作者/封面/分类都在 items 里了。停在这里等确认，
        # 不 confirm、不 ingest —— 手机随手存的链接不该自动烧解析额度。
        # 谁去问：网页「解析」页能看到这些 ready 条目；agent 走 tb_sync_pending。
        raise AwaitingConfirm(batch_id, [i["id"] for i in confirmable])

    # 飞书标签 → TokBrain 收藏夹（方案 B：完整层级路径，如 "收藏/未分类"）
    title = resolve_collection_title(tags)
    collection_id = tb.ensure_collection(title) if title else None
    if title and not collection_id:
        print(f"  [warn] 未取得收藏夹「{title}」的 id，本次不关联收藏夹")
    tb.confirm_batch(batch_id, [i["id"] for i in confirmable], collection_id=collection_id)
    # confirm 后从 batch 视图取 existing_work_id
    view = tb.get_batch(batch_id)
    work_ids = [i["existing_work_id"] for i in view.get("items", []) if i.get("existing_work_id")]
    if not work_ids:
        # 退路：直接 ingest 可能还需要 work_id；若没有则抛错提示手动处理
        raise RuntimeError("无法从批次取得 work_id，请到网页确认入库")
    # 去重场景：作品可能在入库前已存在，confirm 环节的关联不生效，这里补挂一次
    if collection_id:
        try:
            tb.add_works_to_collection(collection_id, work_ids)
        except Exception as e:
            print(f"  [warn] 补挂收藏夹失败（不影响处理）: {e}")
    job = tb.ingest(work_ids)
    tb.wait_job(job["id"])

    # 处理完检查是否需要「本地补件」：作者关闭下载时 TokBrain 拿不到视频流，
    # 此时作品停在 needs_supplement，导出也只会是一篇空笔记。
    need_sup = [w for w in work_ids if work_processing_state(w) == "needs_supplement"]
    if need_sup:
        raise NeedsSupplement(
            f"作品 {need_sup} 需要本地补件（作者未开放下载，TokBrain 未取得视频/音频/字幕）。"
            "请在 TokBrain 网页手动上传完整视频后重试，或换一条允许下载的视频。"
        )
    return work_ids


def process_link_metadata_only(tb: TokBrain, link, tags):
    """长链确认 + 元数据 Work 入库（不访问抖音）。

    抖音风控(429)会熔断 30 分钟，飞书同步默认不再请求平台解析：仅用长链建
    一条无媒体 Work（confirm 直接产出），Obsidian 笔记保留占位 + 链接，后续
    在 TokBrain 网页为该 work 补件后由现有补件/回填链路接管富化。

    返回 work_ids。无媒体是预期态，不抛 NeedsSupplement，也不触发 ingest。
    """
    print(f"[import-link:metadata-only] {link}")
    batch_id = tb.import_link(link, metadata_only=True)
    items = wait_precheck(tb, batch_id)
    confirmable = [
        i for i in (items or [])
        if str(i.get("status") or "").lower() in ("ready", "needs_local_file")
    ]
    if not confirmable:
        detail = "；".join(
            f"{i.get('error_code') or 'error'}: {i.get('error_message') or '未知原因'}"
            for i in (items or [])
        ) or "无可用条目"
        raise RuntimeError(f"仅链接入库未生成可确认条目 → {detail}")

    # 飞书标签 → TokBrain 收藏夹（方案 B：完整层级路径）
    title = resolve_collection_title(tags)
    collection_id = tb.ensure_collection(title) if title else None
    if title and not collection_id:
        print(f"  [warn] 未取得收藏夹「{title}」的 id，本次不关联收藏夹")
    tb.confirm_batch(batch_id, [i["id"] for i in confirmable], collection_id=collection_id)
    view = tb.get_batch(batch_id)
    work_ids = [i["existing_work_id"] for i in view.get("items", []) if i.get("existing_work_id")]
    if not work_ids:
        raise RuntimeError("无法从批次取得 work_id（仅链接入库）")
    if collection_id:
        try:
            tb.add_works_to_collection(collection_id, work_ids)
        except Exception as e:
            print(f"  [warn] 补挂收藏夹失败（不影响处理）: {e}")
    for wid in work_ids:
        _METADATA_ONLY_WORK_IDS.add(wid)
    print(f"  [metadata-only] work {work_ids} 已确认入库（无媒体，可在 TokBrain 网页补件）")
    return work_ids


def process_local(tb: TokBrain, local_path, tags):
    """本地文件导入(v1 外部批次)，返回 work_ids。"""
    print(f"[import-local] {local_path}")
    cid = "item-1"
    batch_id = tb.create_external_batch([cid])
    tb.upload_external_asset(batch_id, cid, local_path)
    resp = tb.commit_external_batch(batch_id, start_processing=True)
    work_ids = resp.get("work_ids") or []
    job = resp.get("job")
    if job and job.get("job_id"):
        tb.wait_job(job["job_id"])
    else:
        # 没有回传 job 时，直读数据库等待处理完成
        deadline = time.time() + POLL_TIMEOUT
        while time.time() < deadline:
            states = [work_processing_state(w) for w in work_ids]
            if all(s in ("processed", "failed") for s in states):
                break
            time.sleep(POLL_INTERVAL)
    if not work_ids:
        raise RuntimeError("v1 批次未产生 work_id，可能文件无效或已存在重复")
    # 本地导入没有 confirm 环节，入库后补挂收藏夹（方案 B 同名规则）
    title = resolve_collection_title(tags)
    if title:
        collection_id = tb.ensure_collection(title)
        if collection_id:
            try:
                tb.add_works_to_collection(collection_id, work_ids)
                print(f"  [collection] 已把 {len(work_ids)} 个作品关联到「{title}」")
            except Exception as e:
                print(f"  [warn] 关联收藏夹「{title}」失败（不影响处理）: {e}")
    return work_ids


def _work_id_for_lid(lid):
    """按 LID 反查 works.id（works.lid 与飞书 LID 同源，work_id 缺失/失败时兜底）。"""
    if not lid or not TOKBRAIN_DB.exists():
        return None
    try:
        con = sqlite3.connect(str(TOKBRAIN_DB))
        row = con.execute("SELECT id FROM works WHERE lid=? LIMIT 1", (lid,)).fetchone()
        con.close()
        return row[0] if row else None
    except Exception:
        return None


def _work_id_by_source_url(url):
    """按链接反查 works.id（最后兜底：dispatch.link 与 works.source_url 精确匹配）。"""
    if not url or not TOKBRAIN_DB.exists():
        return None
    try:
        con = sqlite3.connect(str(TOKBRAIN_DB))
        row = con.execute(
            "SELECT id FROM works WHERE source_url=? LIMIT 1", (url,)
        ).fetchone()
        con.close()
        return row[0] if row else None
    except Exception:
        return None


def _platform_from_link(link):
    """从链接域名推断平台标识（用于快轨占位笔记的 source 字段）。

    快轨时作品尚未入库，无法读取 works.platform；这里仅做轻量推断，
    回填阶段（backfill_note）会用 works.platform 真实值覆盖。
    """
    s = (link or "").lower()
    if "douyin.com" in s or "v.douyin" in s or "iesdouyin" in s:
        return "douyin"
    if "bilibili.com" in s or "b23.tv" in s:
        return "bilibili"
    if "xiaohongshu.com" in s or "xhslink.com" in s:
        return "xiaohongshu"
    if "kuaishou.com" in s or "kuaishou" in s:
        return "kuaishou"
    return "unknown"


def _work_meta(work_id):
    """取作品平台/作者/平台作品ID，用于回填 Obsidian frontmatter（source/author/platform_work_id）。

    快轨占位笔记 source 是链接推断值，回填时以 works 真实值覆盖；
    author / platform_work_id 是新增字段，补全作品溯源信息。查不到返回空 dict。
    """
    if not work_id or not TOKBRAIN_DB.exists():
        return {}
    try:
        import sqlite3 as _sql
        con = _sql.connect(str(TOKBRAIN_DB))
        con.row_factory = _sql.Row
        row = con.execute(
            "SELECT title, platform, author_name, platform_work_id FROM works WHERE id=?",
            (work_id,),
        ).fetchone()
        con.close()
        if not row:
            return {}
        return {
            "title": (row["title"] or "").strip(),
            "platform": (row["platform"] or "").strip(),
            "author_name": (row["author_name"] or "").strip(),
            "platform_work_id": (row["platform_work_id"] or "").strip(),
        }
    except Exception:
        return {}


def rescan_supplements(tb: TokBrain, store: DispatchStore) -> int:
    """补件回扫：用户在 TokBrain 网页手动补件完成后，自动发现并闭环。

    触发器 = watch 轮询的每一轮（run_once 内，process_queue 之后调用）。
    定位作品优先级：dispatch.work_id → LID（works.lid 与飞书 LID 同源）→
    dispatch.link 匹配 works.source_url。作品已 processed 则回填 Obsidian
    笔记、把状态推进为 processed；下一轮 writeback 自动回写飞书「已归档」。
    """
    con = store._connect()
    rows = con.execute(
        "SELECT * FROM dispatch WHERE status='needs_supplement'"
    ).fetchall()
    con.close()
    resumed = 0
    for r in rows:
        rid = r["record_id"]
        item = dict(r)
        work_id = r["work_id"]
        if work_id is None:
            work_id = _work_id_for_lid(str(item.get("lid") or "").strip())
        if work_id is None and (item.get("link") or "").strip():
            work_id = _work_id_by_source_url(str(item["link"]).strip())
        if work_id is None:
            continue
        if work_processing_state(work_id) != "processed":
            continue
        try:
            note_path = backfill_note(tb, work_id, item)
            store.update(rid, status="processed", work_id=work_id,
                         note_path=str(note_path), error_code="", error_message="")
            mark_note_status(item, "processed")
            print(f"[rescan] {rid} 补件完成（work {work_id} 已 processed），已回填并推进")
            resumed += 1
        except Exception as e:
            print(f"[warn] {rid} 补件回填失败，下轮重试: {e}")
    if resumed:
        print(f"[rescan] 本轮恢复 {resumed} 条待补件记录")
    return resumed


def resume_ingested(tb: TokBrain, store: DispatchStore) -> int:
    """网页/控制面已确认解析的条目：补收藏夹 + 回填笔记，然后推进 processed。

    确认动作（confirm + 建 ingest job）发生在 API 侧，bridge 不重复导入，只补
    它独有的两件事：按飞书标签建收藏夹并挂上作品、把结果写回 Obsidian 笔记。

    作品还没解析完时直接跳过、状态留在 ingested，下一轮再看——这样解析快慢
    都不会让回填写出一篇空笔记。
    """
    record_ids = store.ids_by_status("ingested")
    if not record_ids:
        return 0
    done = 0
    for rid in record_ids:
        item = store.get(rid)
        if not item:
            continue
        work_id = item["work_id"]
        if work_id is None:
            work_id = _work_id_for_lid(str(item.get("lid") or "").strip())
        if work_id is None and (item.get("link") or "").strip():
            work_id = _work_id_by_source_url(str(item["link"]).strip())
        if work_id is None:
            print(f"[warn] {rid} 已确认解析但找不到对应作品，留在队列等人工处理")
            continue
        if work_processing_state(work_id) != "processed":
            continue
        try:
            title = resolve_collection_title(item.get("tags") or "")
            if title:
                try:
                    collection_id = tb.ensure_collection(title)
                    if collection_id:
                        tb.add_works_to_collection(collection_id, [work_id])
                except Exception as e:
                    print(f"  [warn] 补挂收藏夹失败（不影响回填）: {e}")
            note_path = backfill_note(tb, work_id, item)
            store.update(
                rid, status="processed", work_id=work_id, note_path=str(note_path),
                error_code="", error_message=""
            )
            print(f"[ingest] {rid} 已回填（work {work_id}），推进 processed")
            done += 1
        except Exception as e:
            print(f"[warn] {rid} 已确认记录回填失败，下轮重试: {e}")
    if done:
        print(f"[ingest] 本轮回填 {done} 条已确认记录")
    return done


def process_queue(tb: TokBrain, store: DispatchStore) -> None:
    """慢轨：消费队列，处理完成后回填笔记。

    非终态条目（上次跑到一半的）会被自动接管续跑；failed 为终态，需人工重投。
    """
    items = store.pending_items()
    if not items:
        print("[slow] 队列为空，无需处理。")
        return
    print(f"[slow] 待处理 {len(items)} 条，开始回填链路")

    for item in items:
        rid, link = item["record_id"], item["link"]
        try:
            store.update(rid, status="queued")
            local_path = item["local_path"] if os.path.isfile(item["local_path"] or "") else ""
            if local_path:
                work_ids = process_local(tb, local_path, item["tags"])
            else:
                work_ids = process_link(tb, link, item["tags"])
            store.update(rid, status="processing", work_id=work_ids[0] if work_ids else None)
            note_path = backfill_note(tb, work_ids[0], item)
            store.update(rid, status="processed", work_id=work_ids[0],
                         note_path=str(note_path), error_code="", error_message="")
            print(f"[ok] {link or local_path} -> 已回填")
        except NeedsSupplement as e:
            store.update(rid, status="needs_supplement", error_code="needs_supplement",
                         error_message=str(e)[:500])
            mark_note_status(item, "needs_supplement", str(e))
            print(f"[wait] {link} -> 需要本地补件，等待人工上传视频")
        except AwaitingConfirm as e:
            # 记下 batch_id：确认入口（网页 / tb_sync_confirm）靠它找回批次。
            store.update(rid, status="awaiting_confirm", batch_id=e.batch_id,
                         error_code="", error_message="")
            mark_note_status(item, "awaiting_confirm", str(e))
            print(f"[wait] {link} -> 预检完成，待确认解析（批次 {e.batch_id}）")
        except Exception as e:
            store.update(rid, status="failed", error_code="exception",
                         error_message=str(e)[:500])
            mark_note_status(item, "failed", str(e))
            print(f"[fail] {link}: {e}")


def writeback_feishu_status(store: DispatchStore, state: dict) -> None:
    """把 dispatch 队列状态回写到飞书表，让处理进度在表格里可见。

    - 仅回写真实飞书 record_id（local-* 本地导入跳过，TC-P3-10）。
    - 只在状态变化时回写，幂等且不浪费接口额度（TC-P3-07）。
    - 回写失败只记日志、不阻塞主流程，下轮重试（不更新 last 映射，TC-P3-06）。
    - 失败/需补件不自动重投（需人工 --retry），但状态仍如实回写为「失败/需补件」。
    """
    if not (FEISHU_BITABLE_APP_TOKEN and FEISHU_BITABLE_TABLE_ID):
        return
    con = store._connect()
    rows = con.execute(
        "SELECT record_id, status, work_id, note_path, link, lid FROM dispatch"
    ).fetchall()
    con.close()

    # 预取 works 元数据（title/author/platform），按 work_id 索引一次，避免逐行查库。
    # 仅在作品确有值时回写，缺失不覆盖飞书既有内容。
    work_meta = {}
    wids = [r["work_id"] for r in rows if r["work_id"] is not None]
    if wids and TOKBRAIN_DB.exists():
        try:
            import sqlite3 as _sql
            wc = _sql.connect(str(TOKBRAIN_DB))
            wc.row_factory = _sql.Row
            ph = ",".join("?" * len(wids))
            for w in wc.execute(
                f"SELECT id, title, author_name, platform FROM works WHERE id IN ({ph})",
                wids,
            ):
                work_meta[w["id"]] = {
                    "title": (w["title"] or "").strip(),
                    "author": (w["author_name"] or "").strip(),
                    "platform": (w["platform"] or "").strip(),
                }
            wc.close()
        except Exception as e:
            print(f"  [warn] 读取 works 元数据失败（不影响状态回写）: {e}")

    last = state.setdefault("feishu_last_status", {})
    # 已成功回写过标题的行集合：标题只需写一次，避免每轮重复消耗接口额度。
    meta_written = set(state.get("feishu_meta_written", []))
    updates = {}
    intended = {}
    lid_backfill = {}  # dispatch 中缺失 lid 的行：生成后回填本地队列
    for r in rows:
        rid = r["record_id"]
        # 只回写真实飞书 record_id：local-*（本地兜底收件箱）与 inbox-*
        # （P4 入站 API）都没有飞书行，跳过（TC-P3-10 + P4 回归）。
        if not rid or str(rid).startswith(("local-", "inbox-")):
            continue
        st = r["status"]
        status_changed = last.get(rid) != st
        wid = r["work_id"]
        meta = work_meta.get(wid) if wid is not None else None
        # 元数据仅在作品有真实标题、且本行尚未成功写过时才补写。
        # metadata_only 作品的标题是占位符（待补件 · xxx / 本地补件 xxx），
        # 不回写飞书「标题」列，避免覆盖收藏导入时已有的真实标题（TC-P3-11）。
        meta_title = (meta or {}).get("title") or ""
        if meta_title.startswith(_PLACEHOLDER_TITLE_PREFIXES):
            meta_title = ""
        need_meta = bool(meta and meta_title and rid not in meta_written)
        if not status_changed and not need_meta:
            continue
        opt = DISPATCH_TO_FEISHU_STATUS.get(st, "待处理")
        fields = {FS_COL_STATUS: [opt]}
        # LID：随行携带（数据唯一标识）。本地队列缺失时即时生成并回填，保证
        # dispatch / Obsidian / 飞书三侧始终能对上同一条数据。
        _lid = str(r["lid"] or "").strip()
        if not _lid and (r["link"] or "").strip():
            _lid = _make_lid(r["link"])
            lid_backfill[rid] = _lid
        if _lid:
            fields[FS_COL_LID] = _lid
        if wid is not None:
            fields[FS_COL_WORK_ID] = wid
        if meta:
            if meta_title:
                fields[FS_COL_TITLE] = meta_title
            if meta["author"]:
                fields[FS_COL_AUTHOR] = meta["author"]
            if meta["platform"]:
                fields[FS_COL_SOURCE] = PLATFORM_NAMES.get(meta["platform"], meta["platform"])
            # 新增3：同一时机把真实标题同步进 Obsidian 笔记的 title 字段，
            # 让快轨占位笔记「待处理 · xxx」被替换为作品真实标题，飞书/obsidian 两侧一致。
            if meta_title and r["note_path"]:
                _sync_obsidian_title(r["note_path"], meta_title)
        updates[rid] = fields
        intended[rid] = st

    if not updates:
        return
    try:
        Feishu().update_records(updates)
        for rid, st in intended.items():
            last[rid] = st
        # 仅对确实写入了标题的行标记“已写过元数据”，下轮不再重复回写
        for rid, flds in updates.items():
            if FS_COL_TITLE in flds:
                meta_written.add(rid)
        state["feishu_meta_written"] = list(meta_written)
        save_sync_state(state)
        print(f"[feishu] 已回写 {len(updates)} 条（含标题/作者/来源/LID）到多维表格")
    except Exception as e:
        # 不阻塞主流程，下轮重试（last 不更新，因此会再次尝试）
        print(f"[warn] 飞书状态回写失败，下轮重试: {e}")
    # 回写成功后，把新生成的 lid 存回 dispatch，保证本地队列也带 LID
    for rid, lid in lid_backfill.items():
        store.update(rid, lid=lid)


_RUNNING = False  # 重入保护：上一轮未结束时跳过本轮


def run_once(tb: TokBrain, store: DispatchStore, state: dict) -> dict:
    """执行一轮：快轨收件归档（秒级）+ 慢轨处理回填（分钟级）。"""
    global _RUNNING
    if _RUNNING:
        print("[skip] 上一轮尚未结束，跳过本轮，避免并发重入。")
        return state
    _RUNNING = True
    try:
        use_feishu = bool(FEISHU_BITABLE_APP_TOKEN and FEISHU_BITABLE_TABLE_ID)
        rows = []
        if use_feishu:
            print("[source] 飞书多维表格（lark-cli 用户身份）")
            fs = Feishu()
            synced = set(state.get("synced_record_ids", []))
            cursor = state.get("last_modified_cursor", "")
            fetched = fs.get_updated_rows(cursor)
            # 游标与已同步列表双保险：任一命中都不会重复处理
            rows = [
                (rid, lid, link, tags, "")
                for rid, lid, link, tags, _mod in fetched
                if rid not in synced
            ]
            mods = [m for _r, _lid, _l, _t, m in fetched if m]
            if mods:
                new_cursor = max(mods)
                if new_cursor != cursor:
                    state["last_modified_cursor"] = new_cursor
                    save_sync_state(state)
                    print(f"[feishu] 游标推进 -> {new_cursor}")
        else:
            print(f"[source] 本地 inbox.json ({INBOX_PATH})")
            rows = local_inbox_rows()

        # ---- 快轨：收件即归档，不等 TokBrain ----
        new_count = 0
        lid_fill = {}  # record_id -> LID，待统一回填飞书
        for record_id, lid, link, tags, extra in rows:
            if not link and not os.path.isfile(str(extra or "")):
                continue  # 无链接且无本地文件，跳过空行
            # LID 为空的行（飞书直发 / 历史数据）即时生成，稍后回填飞书列
            if not lid:
                lid = _make_lid(link)
                lid_fill[record_id] = lid
            note_path = write_placeholder_note(record_id, lid, link, tags)
            if store.enqueue(record_id, link, tags, extra, str(note_path), lid=lid):
                new_count += 1
        if new_count:
            print(f"[fast] 新增 {new_count} 条占位笔记，已立即在 Obsidian 可见")
        if use_feishu and lid_fill:
            try:
                Feishu().update_records(
                    {rid: {FS_COL_LID: lid} for rid, lid in lid_fill.items()}
                )
                print(f"[feishu] 已回填 {len(lid_fill)} 条 LID 到多维表格")
            except Exception as e:
                print(f"[warn] LID 回填失败（下轮重试）: {e}")

        # ---- 为尚未建占位笔记的待处理条目补建（含 P4 入站 API 提交）----
        # 入站 API 直接写 dispatch 队列、不经过上面的快轨，因此这里统一兜底：
        # 任何 status=pending 且 note_path 为空的条目都要先有占位笔记，慢轨
        # 回填才安全（TC-P4-12 双轨一致性）。已建笔记的条目会被此步跳过。
        for item in store.pending_items():
            if not item.get("note_path"):
                note = write_placeholder_note(
                    item["record_id"],
                    str(item.get("lid") or "").strip() or _make_lid(item.get("link") or ""),
                    item["link"], item["tags"],
                )
                store.update(item["record_id"], note_path=str(note))

        # ---- 慢轨：处理并回填（失败不影响已归档的占位笔记）----
        process_queue(tb, store)

        # 网页/控制面已确认解析的条目：补收藏夹 + 回填笔记（解析没跑完会自动跳过）
        resume_ingested(tb, store)

        # 补件回扫：用户已在 TokBrain 网页补件完成的作品，自动回填并推进状态
        rescan_supplements(tb, store)

        # 清理已删除作品残留的关键帧链接，回收磁盘空间（硬链接只删一边不释放）
        prune_orphan_keyframe_links()

        # ---- 飞书状态回写（失败不阻塞主流程，下轮重试）----
        writeback_feishu_status(store, state)

        counts = store.counts()
        if counts:
            print(f"[state] 队列状态: {counts}")

        if use_feishu:
            finished = store.ids_by_status("processed")
            synced_list = state.setdefault("synced_record_ids", [])
            added = [r for r in finished if r not in synced_list]
            if added:
                synced_list.extend(added)
                state["last_sync"] = time.strftime("%Y-%m-%d %H:%M:%S")
                save_sync_state(state)
                print(f"[state] 已记录 {len(added)} 条 record_id 到 {SYNC_STATE_PATH}")
        return state
    finally:
        _RUNNING = False


def main():
    tb = TokBrain()
    store = DispatchStore()
    state = load_sync_state()

    # 人工重投：把失败/需补件条目重置为 queued，重新进入慢轨（不自动重投）
    if "--retry" in sys.argv:
        try:
            rid = sys.argv[sys.argv.index("--retry") + 1]
        except IndexError:
            print("[retry] 用法: bridge_feishu_obsidian.py --retry <record_id>")
            return
        item = store.get(rid)
        if not item:
            print(f"[retry] 未找到 record_id={rid}")
            return
        store.update(rid, status="queued", error_code="", error_message="")
        mark_note_status(item, "pending")
        print(f"[retry] 已将 {rid} 重置为 queued，准备重投")

    if bool(FEISHU_BITABLE_APP_TOKEN and FEISHU_BITABLE_TABLE_ID):
        created = Feishu().ensure_columns()
        if created:
            print(f"[feishu] 已创建缺失列: {created}")
        # 存量行 LID 回填：链接有值、LID 为空的历史行补生成，确保全表数据带唯一标识
        try:
            lid_rows = Feishu().backfill_lids()
            if lid_rows:
                print(f"[feishu] 已回填 {len(lid_rows)} 条历史行 LID")
        except Exception as e:
            print(f"[warn] 历史行 LID 回填失败（可下轮重试）: {e}")

    if WATCH_MODE:
        print(f"[watch] 常驻轮询模式，周期 {POLL_CYCLE}s；Ctrl+C 退出")
        while True:
            try:
                state = run_once(tb, store, state) or state
            except KeyboardInterrupt:
                print("\n[watch] 已停止。")
                return
            except Exception as e:
                # 单轮异常不应终止常驻进程
                print(f"[watch] 本轮异常，下轮重试: {e}")
            time.sleep(POLL_CYCLE)
    else:
        run_once(tb, store, state)


if __name__ == "__main__":
    if not TOKBRAIN_TOKEN:
        print("警告: 未找到 integration token（data/integration_token.txt 或 TOKBRAIN_TOKEN）。")
        print("      链接导入不受影响；本地文件(v1)导入需要 token。")
    main()
