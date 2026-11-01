"""HTTP 传输层（纯标准库）。

只做三件事：拼请求、解 JSON、把失败翻译成 :class:`ControlError`。

刻意不做的事：

* **不发 ``Origin`` 头**。后端 ``app/main.py`` 的 ``_enforce_local_origin`` 只在
  请求带 ``Origin`` 时才校验；控制面是本机可信进程，不带即直通。唯一例外是数据包
  导入接口（它反过来要求必须带），由 :mod:`tokbrain_control.upload` 显式处理。
* **不复用连接**。回环几十微秒的握手，换来的确定性远比那点开销值钱。
"""

from __future__ import annotations

import http.client
import json
import socket
from typing import Any, Mapping
from urllib.parse import urlencode, urlsplit

from .errors import (
    CODE_BACKEND_CONTRACT_MISMATCH,
    CODE_HOST_REJECTED,
    CODE_NOT_FOUND,
    CODE_REJECTED,
    ControlError,
    offline_error,
    timeout_error,
)

DEFAULT_BASE = "http://127.0.0.1:8001"
DEFAULT_CONNECT_TIMEOUT = 3.0
DEFAULT_READ_TIMEOUT = 30.0

#: 后端 API 契约版本（``app/main.py``）。不一致时不静默继续。
EXPECTED_API_CONTRACT = 7


def _decode_json(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"_raw": raw.decode("utf-8", "replace")[:2000]}


def _normalize_detail(payload: Any, status: int) -> tuple[str, str, bool, Any]:
    """从后端的 ``detail`` 里提炼出 (code, message, retryable, detail)。

    后端有三种形态，都要认：

    1. ``{"detail": {"code": "x", "message": "...", "retryable": false}}`` —— 结构化错误
    2. ``{"detail": "人话"}`` —— ``HTTPException(detail="...")``
    3. ``{"detail": [{"loc": [...], "msg": "..."}]}`` —— pydantic 422
    """
    detail = payload.get("detail") if isinstance(payload, dict) else None

    if isinstance(detail, dict):
        code = str(detail.get("code") or f"http_{status}")
        message = str(
            detail.get("message") or detail.get("msg") or f"后端返回 HTTP {status}"
        )
        retryable = bool(detail.get("retryable", False))
        return code, message, retryable, detail

    if isinstance(detail, str) and detail.strip():
        return f"http_{status}", detail.strip(), False, None

    if isinstance(detail, list) and detail:
        parts: list[str] = []
        for entry in detail[:6]:
            if isinstance(entry, dict):
                loc = ".".join(str(x) for x in entry.get("loc", []) if x != "body")
                msg = str(entry.get("msg") or "")
                parts.append(f"{loc}: {msg}" if loc else msg)
            else:
                parts.append(str(entry))
        return f"http_{status}", "；".join(parts), False, detail

    if isinstance(payload, dict) and "_raw" in payload:
        return f"http_{status}", str(payload["_raw"])[:400], False, None

    return f"http_{status}", f"后端返回 HTTP {status}", False, None


def check_status(status: int, payload: Any) -> None:
    """把非 2xx 响应翻译成 :class:`ControlError`。上传路径也复用它。"""
    if status < 400:
        return

    code, message, retryable, detail = _normalize_detail(payload, status)

    if status in (401, 403):
        raise ControlError(
            CODE_REJECTED,
            message,
            kind="business",
            http_status=status,
            detail=detail or payload,
            hint="控制面不带 Origin 头；若报 CSRF/Origin，说明该接口强制同源。",
        )
    if status == 404:
        raise ControlError(
            CODE_NOT_FOUND,
            message,
            kind="business",
            http_status=404,
            detail=detail or payload,
        )
    if status >= 500:
        raise ControlError(
            code,
            message,
            kind="server",
            retryable=retryable or status in (502, 503, 504),
            http_status=status,
            detail=detail or payload,
        )
    raise ControlError(
        code,
        message,
        kind="business",
        retryable=retryable,
        http_status=status,
        detail=detail or payload,
    )


class Transport:
    """极简同步 HTTP 客户端。    >>> t = Transport("http://127.0.0.1:8001")
    >>> t.get("/health")["api_contract"]
    7
    """

    def __init__(
        self,
        base: str = DEFAULT_BASE,
        *,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
    ) -> None:
        parsed = urlsplit(base.strip() or DEFAULT_BASE)
        if parsed.scheme not in {"http", "https"}:
            raise ControlError(
                "invalid_base_url",
                f"后端地址必须带 http:// 或 https://：{base!r}",
                kind="usage",
                hint="默认 http://127.0.0.1:8001",
            )
        self.base = f"{parsed.scheme}://{parsed.netloc}"
        self.base_path = parsed.path.rstrip("/")
        self.host = parsed.hostname or "127.0.0.1"
        self.port = parsed.port or (443 if parsed.scheme == "https" else 80)
        self.scheme = parsed.scheme
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout

    # -- 内部 ---------------------------------------------------------

    def _connect(self, timeout: float) -> http.client.HTTPConnection:
        cls = (
            http.client.HTTPSConnection
            if self.scheme == "https"
            else http.client.HTTPConnection
        )
        return cls(self.host, self.port, timeout=timeout)

    @staticmethod
    def _build_path(path: str, query: Mapping[str, Any] | None, base_path: str) -> str:
        full = f"{base_path}{path}"
        if query:
            pairs = [(k, v) for k, v in query.items() if v is not None]
            if pairs:
                full = f"{full}?{urlencode(pairs, doseq=True)}"
        return full

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        json_body: Any = None,
        headers: Mapping[str, str] | None = None,
        read_timeout: float | None = None,
        expect_json: bool = True,
    ) -> Any:
        """发一个请求并返回解析后的 JSON（失败抛 :class:`ControlError`）。"""
        timeout = read_timeout or self.read_timeout
        full_path = self._build_path(path, query, self.base_path)

        body: bytes | None = None
        hdrs: dict[str, str] = {"Accept": "application/json"}
        if json_body is not None:
            body = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            hdrs["Content-Type"] = "application/json; charset=utf-8"
        if headers:
            hdrs.update(headers)

        conn = self._connect(timeout)
        try:
            conn.request(method.upper(), full_path, body=body, headers=hdrs)
            response = conn.getresponse()
            raw = response.read()
            status = response.status
        except (ConnectionRefusedError, ConnectionResetError, ConnectionAbortedError) as exc:
            raise offline_error(exc, base=self.base) from exc
        except (socket.timeout, TimeoutError) as exc:
            raise timeout_error(exc, path=path) from exc
        except socket.gaierror as exc:
            raise ControlError(
                CODE_HOST_REJECTED,
                f"无法解析后端地址 {self.host}",
                kind="offline",
                hint="检查 TOKBRAIN_CONTROL_API_BASE 配置",
                detail=repr(exc),
            ) from exc
        except http.client.HTTPException as exc:
            raise offline_error(exc, base=self.base) from exc
        finally:
            conn.close()

        payload = _decode_json(raw) if expect_json else raw
        check_status(status, payload)
        return payload

    # -- 便捷方法 -----------------------------------------------------

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> Any:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> Any:
        return self.request("PUT", path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> Any:
        return self.request("PATCH", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Any:
        return self.request("DELETE", path, **kwargs)

    # -- 健康检查 -----------------------------------------------------

    def health(self, *, read_timeout: float = 5.0) -> dict[str, Any]:
        """``GET /health``。返回体可能只有 ``status/api_contract``。"""
        payload = self.get("/health", read_timeout=read_timeout)
        return payload if isinstance(payload, dict) else {}

    def assert_contract(self) -> dict[str, Any]:
        """确认后端在线且契约版本一致；不一致时明确报错而不是继续猜字段。"""
        health = self.health()
        contract = health.get("api_contract")
        if contract is not None and int(contract) != EXPECTED_API_CONTRACT:
            raise ControlError(
                CODE_BACKEND_CONTRACT_MISMATCH,
                f"后端 API 契约版本是 {contract}，控制面按 {EXPECTED_API_CONTRACT} 编写",
                kind="server",
                hint="本机升级了 TokBrain，控制面需要同步；先不要执行写操作。",
                detail=health,
            )
        return health
