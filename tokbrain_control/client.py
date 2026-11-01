"""TokBrain 后端 REST 的领域封装。

所有方法最终都落到 :class:`tokbrain_control.http.Transport`，也就是说**唯一写入口
就是 8001 的 HTTP**——控制面任何地方都不碰 SQLite 写连接。
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

from .errors import (
    CODE_ALREADY_QUEUED,
    CODE_JOB_NOT_FOUND,
    ControlError,
)
from .http import DEFAULT_BASE, Transport
from .upload import probe_path, upload_file

TERMINAL_JOB_STATES = frozenset({"succeeded", "partial", "failed", "cancelled"})
ACTIVE_JOB_STATES = frozenset({"uploading", "queued", "running", "cancelling"})
LIBRARY_STATES = ("pending", "in_library", "issues", "archived", "supplement")
DEFAULT_ORIGIN = "http://127.0.0.1:3001"


def _env_base() -> str:
    return (
        os.environ.get("TOKBRAIN_CONTROL_API_BASE")
        or os.environ.get("TOKBRAIN_BASE")
        or DEFAULT_BASE
    )


def _env_origin() -> str:
    return os.environ.get("TOKBRAIN_CONTROL_ORIGIN") or DEFAULT_ORIGIN


def _as_int_list(values: Iterable[Any] | None) -> list[int]:
    out: list[int] = []
    for value in values or []:
        try:
            out.append(int(value))
        except (TypeError, ValueError):
            continue
    return out


class Client:
    """一个后端连接的薄封装。无状态，可安全跨请求复用。"""

    def __init__(
        self,
        base: str | None = None,
        *,
        transport: Transport | None = None,
        origin: str | None = None,
    ) -> None:
        self.transport = transport or Transport(base or _env_base())
        self.origin = origin or _env_origin()

    # ------------------------------------------------------------ 健康

    def health(self) -> dict[str, Any]:
        payload = self.transport.health()
        return payload if isinstance(payload, dict) else {}

    def system_health(self) -> dict[str, Any]:
        return self.transport.get("/api/system-health") or {}

    def coordinator_probe(self) -> dict[str, Any]:
        try:
            return (
                self.transport.get("/api/system-health/probes/coordinators") or {}
            )
        except ControlError:
            return {}

    # ------------------------------------------------------------ 任务

    def list_jobs(self) -> list[dict[str, Any]]:
        payload = self.transport.get("/api/jobs")
        return payload if isinstance(payload, list) else []

    def get_job(self, job_id: str) -> dict[str, Any]:
        try:
            payload = self.transport.get(f"/api/jobs/{job_id}")
        except ControlError as exc:
            if exc.http_status == 404:
                raise ControlError(
                    CODE_JOB_NOT_FOUND,
                    f"任务不存在：{job_id}",
                    kind="business",
                    hint="用 tb_status(include_jobs=5) 或 tb_job_list 拿最近的 job_id。",
                    detail=exc.detail,
                ) from exc
            raise
        return payload if isinstance(payload, dict) else {}

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        return self.transport.post(f"/api/jobs/{job_id}/cancel") or {}

    def retry_failed_jobs(self) -> dict[str, Any]:
        return self.transport.post("/api/jobs/retry") or {}

    def wait_job(
        self,
        job_id: str,
        *,
        timeout: float = 600.0,
        interval: float = 2.0,
        on_tick: Any = None,
    ) -> tuple[dict[str, Any], bool]:
        """轮询任务直到终态或超时。返回 ``(job, done)``。

        CLI 用 ``timeout=600``（人手跑不怕等），MCP 用 ``<=90``（不超过 Hermes 的
        120 秒传输超时），两边共用这一个循环。
        """
        deadline = time.monotonic() + max(1.0, timeout)
        interval = max(0.5, interval)
        job: dict[str, Any] = {}
        while True:
            job = self.get_job(job_id)
            state = str(job.get("state") or "")
            if state in TERMINAL_JOB_STATES:
                return job, True
            if on_tick is not None:
                try:
                    on_tick(job)
                except Exception:  # noqa: BLE001 - 进度回调不该打断等待
                    pass
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return job, False
            time.sleep(min(interval, remaining))

    def resolve_job_id(self, *, job_id: str | None, batch_id: str | None) -> str:
        if job_id:
            return job_id
        if batch_id:
            batch = self.get_batch(batch_id)
            resolved = batch.get("job_id")
            if not resolved:
                raise ControlError(
                    CODE_JOB_NOT_FOUND,
                    f"批次 {batch_id} 没有关联任务",
                    kind="business",
                    hint="这个批次可能已经结束或被取消。",
                    detail=batch,
                )
            return str(resolved)
        raise ControlError(
            "invalid_argument", "必须提供 job_id 或 batch_id", kind="usage"
        )

    # ------------------------------------------------------------ 导入

    def create_link_batch(
        self,
        text: str,
        *,
        metadata_only: bool = False,
        title: str | None = None,
        author: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"text": text, "metadata_only": metadata_only}
        if title:
            body["title"] = title
        if author:
            body["author"] = author
        return self.transport.post("/api/import-batches", json_body=body) or {}

    def create_local_batch(
        self,
        items: Sequence[dict[str, Any]],
        *,
        target_collection_id: int | None = None,
    ) -> dict[str, Any]:
        payload_items = []
        for item in items:
            entry = {
                "client_item_id": item["client_item_id"],
                "filename": item["filename"],
                "size_bytes": int(item["size_bytes"]),
            }
            collection_id = item.get("target_collection_id", target_collection_id)
            if collection_id:
                entry["target_collection_id"] = int(collection_id)
            payload_items.append(entry)
        body = {"rights_attested": True, "items": payload_items}
        return self.transport.post("/api/local-import-batches", json_body=body) or {}

    def upload_local_video(
        self, batch_id: str, item_id: int, path: str | Path
    ) -> dict[str, Any]:
        return upload_file(
            self.transport,
            "PUT",
            f"/api/local-import-batches/{batch_id}/items/{item_id}/video",
            path,
            field_name="file",
            # 这个接口不能带 Origin：后端只在带了 Origin 时才校验白名单。
            read_timeout=600.0,
        ) or {}

    def get_batch(self, batch_id: str) -> dict[str, Any]:
        try:
            payload = self.transport.get(f"/api/import-batches/{batch_id}")
        except ControlError as exc:
            if exc.http_status == 404:
                raise ControlError(
                    "batch_not_found",
                    f"导入批次不存在：{batch_id}",
                    kind="business",
                    hint="批次 id 形状如 b_xxxxxxxx，从 tb_import_links 的返回里取。",
                    detail=exc.detail,
                ) from exc
            raise
        return payload if isinstance(payload, dict) else {}

    def cancel_batch(self, batch_id: str) -> dict[str, Any]:
        return self.transport.post(f"/api/import-batches/{batch_id}/cancel") or {}

    def confirm_batch(
        self,
        batch_id: str,
        *,
        item_ids: Sequence[int] | None = None,
        items: Sequence[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """确认批次。

        注意：**这一步不会触发入库**。它只把 ``import_items`` 变成 confirmed 并建出
        ``works``（``library_state=pending``）。要真正解析必须再调
        :meth:`ingest`。这是产品设计的「预检 ≠ 入库」两段式，控制面不得合并。
        """
        body: dict[str, Any] = {
            "item_ids": _as_int_list(item_ids),
            "items": [
                {
                    "item_id": int(entry["item_id"]),
                    "collection_id": int(entry["collection_id"]),
                }
                for entry in (items or [])
            ],
        }
        return self.transport.post(
            f"/api/import-batches/{batch_id}/confirm", json_body=body
        ) or {}

    def patch_import_item(self, item_id: int, **fields: Any) -> dict[str, Any]:
        body = {k: v for k, v in fields.items() if v is not None}
        return self.transport.patch(
            f"/api/import-items/{item_id}", json_body=body
        ) or {}

    # ---------------------------------------------------------- 数据包

    def create_package_batch(
        self,
        files: Sequence[dict[str, Any]],
        *,
        upload_mode: str = "folder",
        target_collection_id: int | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "rights_attested": True,
            "upload_mode": upload_mode,
            "files": list(files),
        }
        if target_collection_id:
            body["target_collection_id"] = int(target_collection_id)
        # 该接口强制同源：不带 Origin 反而 403。
        return self.transport.post(
            "/api/package-import-batches",
            json_body=body,
            headers={"Origin": self.origin},
        ) or {}

    def upload_package_file(
        self, batch_id: str, file_id: str, path: str | Path
    ) -> dict[str, Any]:
        return upload_file(
            self.transport,
            "PUT",
            f"/api/package-import-batches/{batch_id}/files/{file_id}",
            path,
            field_name="file",
            origin=self.origin,
            read_timeout=600.0,
        ) or {}

    def analyze_package(self, batch_id: str) -> dict[str, Any]:
        return self.transport.post(
            f"/api/package-import-batches/{batch_id}/analyze",
            headers={"Origin": self.origin},
        ) or {}

    # ------------------------------------------------------------ 知识库

    def list_collections(self) -> dict[str, Any]:
        return self.transport.get("/api/library/collections") or {}

    def create_collection(self, title: str) -> dict[str, Any]:
        return self.transport.post(
            "/api/library/collections", json_body={"title": title}
        ) or {}

    def update_collection(self, collection_id: int, summary_prompt: str) -> dict[str, Any]:
        return self.transport.put(
            f"/api/library/collections/{collection_id}",
            json_body={"summary_prompt": summary_prompt},
        ) or {}

    def add_collection_works(
        self, collection_id: int, work_ids: Sequence[int]
    ) -> dict[str, Any]:
        return self.transport.post(
            f"/api/library/collections/{collection_id}/works",
            json_body={"work_ids": _as_int_list(work_ids)},
        ) or {}

    def find_collection(self, title_or_id: str | int) -> int | None:
        """按 id 或标题（忽略大小写/空白）找合集，找不到返回 None。"""
        collections = self.list_collections().get("items") or []
        if isinstance(title_or_id, int) or str(title_or_id).isdigit():
            wanted = int(title_or_id)
            for item in collections:
                if int(item.get("id", -1)) == wanted:
                    return wanted
            return None
        needle = " ".join(str(title_or_id).split()).lower()
        for item in collections:
            if " ".join(str(item.get("title", "")).split()).lower() == needle:
                return int(item["id"])
        return None

    def list_works(
        self,
        *,
        library_state: str = "in_library",
        collection_id: int | None = None,
        lid: str | None = None,
        platform: str | None = None,
        kind: str | None = None,
        author: str | None = None,
        source: str | None = None,
        offset: int = 0,
        limit: int = 60,
    ) -> dict[str, Any]:
        return self.transport.get(
            "/api/library/works",
            query={
                "library_state": library_state,
                "collection_id": collection_id,
                "lid": lid,
                "platform": platform,
                "kind": kind,
                "author": author,
                "source": source,
                "offset": offset,
                "limit": limit,
            },
        ) or {}

    def ingest(self, work_ids: Sequence[int]) -> dict[str, Any]:
        try:
            return self.transport.post(
                "/api/library/ingest/jobs",
                json_body={"work_ids": _as_int_list(work_ids)},
            ) or {}
        except ControlError as exc:
            if exc.http_status == 409:
                raise ControlError(
                    CODE_ALREADY_QUEUED,
                    exc.message,
                    kind="business",
                    hint="这些作品已在队列中，用 tb_job_wait 等它跑完，不要重复提交。",
                    detail=exc.detail,
                ) from exc
            raise

    def summarize(self, work_ids: Sequence[int]) -> dict[str, Any]:
        return self.transport.post(
            "/api/library/summaries/jobs",
            json_body={"work_ids": _as_int_list(work_ids)},
        ) or {}

    def retry_works(
        self,
        *,
        work_ids: Sequence[int] | None = None,
        error_code: str | None = None,
        collection_id: int | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        ids = _as_int_list(work_ids)
        if ids:
            body["work_ids"] = ids
        if error_code:
            body["error_code"] = error_code
        if collection_id is not None:
            body["collection_id"] = int(collection_id)
        return self.transport.post(
            "/api/library/works/retry-batch", json_body=body
        ) or {}

    def get_work_summary(self, work_id: int) -> dict[str, Any]:
        """作品详情。409 表示「还没有可用总结」，这是正常状态而非错误。"""
        try:
            payload = self.transport.get(f"/api/library/works/{work_id}/summary")
            return payload if isinstance(payload, dict) else {}
        except ControlError as exc:
            if exc.http_status == 409:
                return {"id": work_id, "summary_available": False, "note": exc.message}
            raise

    def retry_work(self, work_id: int) -> dict[str, Any]:
        return self.transport.post(f"/api/library/works/{work_id}/retry") or {}

    def restore_work(self, work_id: int) -> dict[str, Any]:
        return self.transport.post(f"/api/library/works/{work_id}/restore") or {}

    def delete_work(self, work_id: int) -> dict[str, Any]:
        return self.transport.delete(f"/api/library/works/{work_id}") or {}

    def supplement_work(self, work_id: int, paths: Sequence[str | Path]) -> dict[str, Any]:
        """补件：一次只能传一个文件（后端字段是 ``files`` 列表，这里串行多发会
        产生多个补件批次，容易乱；控制面固定只收单文件，需要多文件请分次调用）。"""
        return upload_file(
            self.transport,
            "POST",
            f"/api/library/works/{work_id}/supplement",
            paths[0],
            field_name="files",
            fields={"rights_attested": "true"},
            read_timeout=600.0,
        ) or {}

    def obsidian_manifest(self, work_ids: Sequence[int]) -> dict[str, Any]:
        return self.transport.post(
            "/api/library/obsidian/manifest",
            json_body={"work_ids": _as_int_list(work_ids)},
        ) or {}

    # ------------------------------------------------------------ 平台

    def get_settings(self) -> dict[str, Any]:
        """读设置页状态。

        里面的 ``has_f2_cookie`` / ``has_douyin_cookie`` 走的是 ``has_readable_secret``，
        判断的是**能不能解密**而不是有没有存过——所以能直接当可用性判据用。
        """
        return self.transport.get("/api/settings") or {}

    def platform_status(self, platform: str) -> dict[str, Any]:
        return self.transport.get(f"/api/platforms/{platform}/status") or {}
    def platform_pull(
        self,
        platform: str,
        *,
        mode: str = "like",
        limit: int = 50,
        enqueue: bool = True,
        profile_url: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"mode": mode, "limit": limit, "enqueue": enqueue}
        if profile_url:
            body["profile_url"] = profile_url
        return self.transport.post(
            f"/api/platforms/{platform}/pull", json_body=body, read_timeout=180.0
        ) or {}

    # ------------------------------------------------------------ 同步

    def sync_overview(self) -> dict[str, Any]:
        return self.transport.get("/api/sync/overview") or {}

    # ------------------------------------------------------------ 文件

    @staticmethod
    def probe_fs(path: str) -> dict[str, Any]:
        return probe_path(path)
