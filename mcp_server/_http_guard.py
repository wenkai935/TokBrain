"""HTTP 传输层护栏：Bearer 鉴权 + 监听配置 + 端口预检。

**纯标准库**，不依赖 ``mcp`` 包——这样 ``.venv`` 里的 pytest 能直接测这段安全逻辑，
不需要 ``importorskip("mcp")``。两个 MCP 服务（8010 只读、8011 控制）共用本模块。

启动期就 ``raise``，不留到运行期：配置错了就不该把端口开出去。
"""

from __future__ import annotations

import hmac
import ipaddress
import socket
from dataclasses import dataclass, field
from typing import Any

LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1"}
#: 非回环监听时令牌的最小长度。32 位十六进制是 ``secrets.token_hex(16)`` 的自然产物。
MIN_TOKEN_LENGTH = 32


class BearerGuard:
    """极简 ASGI 中间件：校验 ``Authorization: Bearer <token>``。

    常量时间比较，避免时序侧信道。只拦截 HTTP，其它 ASGI 消息原样透传。
    """

    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self._expected = f"Bearer {token}".encode()

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        presented = headers.get(b"authorization", b"")
        if not hmac.compare_digest(presented, self._expected):
            body = b'{"error":"unauthorized"}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


def is_loopback(host: str) -> bool:
    host = (host or "").strip()
    if host.lower() in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass
class BindConfig:
    host: str
    port: int
    token: str
    loopback: bool
    allowed_hosts: list[str] = field(default_factory=list)

    @property
    def needs_token(self) -> bool:
        return not self.loopback

    def url(self) -> str:
        shown = self.host if not self.loopback else "127.0.0.1"
        return f"http://{shown}:{self.port}/mcp"


def resolve_bind(
    env: dict[str, str],
    *,
    prefix: str,
    default_port: int,
    default_host: str = "127.0.0.1",
    enable_var: str | None = None,
    service_name: str = "MCP",
) -> BindConfig:
    """从环境变量解析监听配置，配置不安全就抛 ``RuntimeError``。

    ``enable_var`` 给了就表示"默认关闭，必须显式启用"——控制面用这个开关防止
    "装完就暴露到局域网"。
    """
    if enable_var and env.get(enable_var, "").strip() != "1":
        raise RuntimeError(
            f"{service_name} 未启用：需要显式设置 {enable_var}=1。"
            "这是一个默认关闭的开关，避免安装后无意把写权限暴露到局域网。"
        )

    host = (env.get(f"{prefix}HOST", default_host) or default_host).strip()
    try:
        port = int(env.get(f"{prefix}PORT", str(default_port)) or default_port)
    except ValueError as exc:
        raise RuntimeError(f"{prefix}PORT 不是合法端口：{env.get(f'{prefix}PORT')!r}") from exc
    token = (env.get(f"{prefix}TOKEN", "") or "").strip()

    try:
        loopback = is_loopback(host)
    except ValueError:
        loopback = False

    if not loopback:
        if not token:
            raise RuntimeError(
                f"{service_name} 非回环监听（{host}）必须配置 {prefix}TOKEN，否则拒绝启动"
            )
        if len(token) < MIN_TOKEN_LENGTH:
            raise RuntimeError(
                f"{prefix}TOKEN 至少 {MIN_TOKEN_LENGTH} 位十六进制字符"
                "（用 python -c \"import secrets;print(secrets.token_hex(16))\" 生成）"
            )

    allowed = [
        item.strip()
        for item in (env.get(f"{prefix}ALLOWED_HOSTS", "") or "").split(",")
        if item.strip()
    ]
    if not loopback and not allowed:
        allowed = [f"{host}:{port}"]
    if loopback and not allowed:
        allowed = []  # 回环不启 DNS rebinding 防护，避免本机请求被误拦

    return BindConfig(
        host=host,
        port=port,
        token=token,
        loopback=loopback,
        allowed_hosts=allowed,
    )


def build_transport_security(bind: BindConfig) -> Any:
    """构造 ``TransportSecuritySettings``（DNS rebinding 防护）。"""
    from mcp.server.transport_security import TransportSecuritySettings

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=bool(bind.allowed_hosts),
        allowed_hosts=list(bind.allowed_hosts),
        allowed_origins=list(bind.allowed_hosts),
    )


def preflight_port(host: str, port: int) -> str | None:
    """启动前试着占一下端口，被占用就返回一句人话错误。

    不自动换端口：换了 Hermes 侧配置就漂了，排障成本比直接报错高得多。
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind((host if not is_loopback(host) else "127.0.0.1", port))
    except OSError as exc:
        return (
            f"端口 {host}:{port} 无法绑定：{exc}。"
            "可能已有别的服务占用；换端口需要同时改 control.env 与 Hermes 配置。"
        )
    finally:
        probe.close()
    return None


def describe_hermes_snippet(bind: BindConfig, token_present: bool) -> str:
    """生成一段可以直接贴进 Hermes ``config.yaml`` 的片段。"""
    host = bind.host if not bind.loopback else "127.0.0.1"
    auth = "Bearer ${TOKBRAIN_CONTROL_TOKEN}" if token_present else "Bearer <未配置>"
    return (
        "  tokbrain_control:\n"
        f'    url: "http://{host}:{bind.port}/mcp"\n'
        "    headers:\n"
        f'      Authorization: "{auth}"\n'
        "    enabled: true\n"
        "    timeout: 120\n"
        "    connect_timeout: 10\n"
        "    trust: untrusted\n"
        "    resources: false\n"
        "    prompts: false\n"
    )
