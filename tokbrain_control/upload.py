"""multipart 上传（手写，流式，不依赖 requests）。

后端两个上传口要的方向相反：

* ``PUT /api/local-import-batches/{id}/items/{iid}/video`` —— **不能**带 ``Origin``
* ``PUT /api/package-import-batches/{id}/files/{fid}``     —— **必须**带白名单 ``Origin``

所以 ``origin`` 作为显式参数，由调用方决定，默认不带。
"""

from __future__ import annotations

import http.client
import mimetypes
import os
import socket
import uuid
from pathlib import Path
from typing import Any, Mapping

from .errors import (
    CODE_FILE_TOO_LARGE,
    CODE_IS_DIRECTORY,
    CODE_NO_READ_PERMISSION,
    CODE_PATH_NOT_VISIBLE,
    ControlError,
    offline_error,
    timeout_error,
)
from . import paths
from .http import Transport, _decode_json, check_status

CHUNK_SIZE = 1024 * 1024  # 1 MiB
MAX_UPLOAD_BYTES = 1024 * 1024 * 1024  # 后端 schemas.py 的 le=1024**3


def probe_path(path: str | os.PathLike[str]) -> dict[str, Any]:
    """检查一个路径在**当前进程**（也就是 Windows 本机）是否可见。

    这是「NAS 文件能不能导入」的唯一判据。Hermes 在 NAS 上，它看到的路径和这台
    Windows 看到的不是一回事，所以这里先按 ``paths`` 里的约定把 NAS 路径翻译成
    本机路径，再逐个 stat，第一个成功的胜出。
    """
    raw = str(path)
    attempts: list[dict[str, Any]] = []

    for candidate in paths.candidates(raw):
        target = Path(candidate)
        entry: dict[str, Any] = {"path": candidate}
        try:
            stat = target.stat()
        except FileNotFoundError:
            entry["reason"] = "not_found"
            attempts.append(entry)
            continue
        except PermissionError as exc:
            entry["reason"] = f"无权限：{exc}"
            attempts.append(entry)
            continue
        except OSError as exc:
            entry["reason"] = f"读取失败：{exc}"
            attempts.append(entry)
            continue

        info: dict[str, Any] = {
            "path": str(target),
            "requested": raw,
            "exists": True,
            "is_dir": target.is_dir(),
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "name": target.name,
        }
        if candidate != raw:
            info["translated_from"] = raw
        return info

    info = {
        "path": raw,
        "requested": raw,
        "exists": False,
        "is_dir": False,
        "reason": "路径不存在或本机不可见",
    }
    if attempts:
        info["attempts"] = attempts
    if paths.specs():
        info["hint"] = (
            "媒体根目录已配置，但没有一个能命中。先调 tb_fs_roots 看哪些 root "
            "当前可用（NAS 映射盘在 NAS 重启后可能掉线，重新双击"
            "mcp_server/挂载NAS媒体盘.cmd）。"
        )
    else:
        info["hint"] = (
            "还没配置媒体根目录约定。要么在 control.env 里设 "
            "TOKBRAIN_CONTROL_MEDIA_ROOTS，要么先映射 NAS 盘符，"
            "或改用 tb_import_links 走链接导入。"
        )
    return info


def require_readable_file(path: str | os.PathLike[str]) -> Path:
    """把「路径不可用」收敛成三种固定错误码，方便模型决定下一步。"""
    info = probe_path(path)
    target = Path(info["path"])

    if not info["exists"]:
        raise ControlError(
            CODE_PATH_NOT_VISIBLE,
            f"本机看不到这个路径：{info['path']}",
            kind="local",
            hint="先调 tb_fs_probe 确认；NAS 文件需要先映射盘符，或改用链接导入。",
            detail=info,
        )
    if info["is_dir"]:
        raise ControlError(
            CODE_IS_DIRECTORY,
            f"这是一个目录，不是文件：{info['path']}",
            kind="usage",
            hint="导入目录请用 tb_import_package（folder 模式）。",
            detail=info,
        )
    if info["size"] <= 0:
        raise ControlError(
            CODE_NO_READ_PERMISSION,
            f"文件为空或不可读：{info['path']}",
            kind="local",
            detail=info,
        )
    if info["size"] > MAX_UPLOAD_BYTES:
        raise ControlError(
            CODE_FILE_TOO_LARGE,
            f"文件 {info['size']} 字节，超过后端 1GB 上限",
            kind="usage",
            hint="先截短/转码，或改用数据包导入。",
            detail=info,
        )
    try:
        with target.open("rb") as handle:
            handle.read(1)
    except PermissionError as exc:
        raise ControlError(
            CODE_NO_READ_PERMISSION,
            f"无读取权限：{info['path']}",
            kind="local",
            detail=repr(exc),
        ) from exc
    return target


def _part_header(boundary: str, field_name: str, filename: str, mime: str) -> bytes:
    safe_name = filename.replace('"', "%22").replace("\r", "").replace("\n", "")
    return (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field_name}"; filename="{safe_name}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode("utf-8")


def _field_part(boundary: str, name: str, value: str) -> bytes:
    return (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
        f"{value}\r\n"
    ).encode("utf-8")


def upload_file(
    transport: Transport,
    method: str,
    path: str,
    file_path: str | os.PathLike[str],
    *,
    field_name: str = "file",
    fields: Mapping[str, str] | None = None,
    query: Mapping[str, Any] | None = None,
    origin: str | None = None,
    upload_field: str | None = None,
    read_timeout: float | None = None,
) -> Any:
    """把单个文件以 multipart 流式上传，返回解析后的 JSON。

    ``upload_field`` 是补充字段（后端用 ``files`` 命名时传 ``"files"``），
    与 ``field_name`` 同义，只是让调用处读起来更贴近后端签名。
    """
    target = require_readable_file(file_path)
    size = target.stat().st_size
    mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    boundary = f"----TokBrainControl{uuid.uuid4().hex}"
    name = upload_field or field_name

    head = b"".join(_field_part(boundary, k, str(v)) for k, v in (fields or {}).items())
    head += _part_header(boundary, name, target.name, mime)
    tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
    total = len(head) + size + len(tail)

    full_path = transport._build_path(path, query, transport.base_path)
    timeout = read_timeout or max(transport.read_timeout, 120.0)

    headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(total),
        "Accept": "application/json",
    }
    if origin:
        headers["Origin"] = origin

    conn = transport._connect(timeout)
    try:
        conn.putrequest(method.upper(), full_path)
        for key, value in headers.items():
            conn.putheader(key, value)
        conn.endheaders()
        conn.send(head)
        remaining = size
        with target.open("rb") as handle:
            while remaining > 0:
                chunk = handle.read(min(CHUNK_SIZE, remaining))
                if not chunk:
                    break
                conn.send(chunk)
                remaining -= len(chunk)
        conn.send(tail)

        response = conn.getresponse()
        raw = response.read()
        status = response.status
    except (ConnectionRefusedError, ConnectionResetError, ConnectionAbortedError) as exc:
        raise offline_error(exc, base=transport.base) from exc
    except (socket.timeout, TimeoutError) as exc:
        raise timeout_error(exc, path=path) from exc
    except http.client.HTTPException as exc:
        raise offline_error(exc, base=transport.base) from exc
    finally:
        conn.close()

    payload = _decode_json(raw)
    check_status(status, payload)
    return payload
