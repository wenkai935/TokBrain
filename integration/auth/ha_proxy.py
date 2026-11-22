#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HA 反向代理示范：验 JWT + scope 后才带 HA token 转发。

业务 server（home_rules/home_history/hermes-home）不再持有 HA token——它只拿
xiaoqi-auth 签发的短期票，本代理持 HA token 并做最后一道门禁。

门禁规则（最小权限）：
  - GET  /api/states... /api/history... → 需要 ha:read
  - POST /api/services/light/turn_*      → 需要 ha:light:write
  - POST /api/services/switch/turn_*     → 需要 ha:switch:write
  - POST /api/services/climate/*         → 需要 ha:climate:write
  - 其它一律拒绝（fail-closed）

配置：HA_URL（默认 http://192.168.10.107:8123）、HA_TOKEN（必填)、
      AUTH_URL（默认 http://127.0.0.1:8900）、PORT（默认 8124）。
"""
import json
import os
import re
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

HA_URL = os.environ.get("HA_URL", "http://192.168.10.107:8123").rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN", "")
AUTH_URL = os.environ.get("AUTH_URL", "http://127.0.0.1:8900").rstrip("/")
PORT = int(os.environ.get("PORT", "8124"))
AUD = "ha-proxy"
AUDIT_PATH = os.environ.get("AUDIT_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit.jsonl"))


def _audit(cid, method, path, result, detail=""):
    from datetime import datetime, timezone
    row = {"ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
           "proxy": "ha-proxy", "cid": cid, "method": method, "path": path,
           "result": result, "detail": detail[:160]}
    try:
        with open(AUDIT_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _verify(token, scope):
    data = json.dumps({"token": token, "aud": AUD, "scope": scope}).encode()
    req = urllib.request.Request(AUTH_URL + "/verify", data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as ex:
        try:
            return json.loads(ex.read().decode("utf-8"))
        except Exception:
            return {"valid": False, "error": f"HTTP {ex.code}"}
    except Exception as ex:
        return {"valid": False, "error": f"auth 不可达: {type(ex).__name__}: {ex}"}


def required_scope(method, path):
    """按请求判定需要的 scope；返回 None 表示此路径不允许经代理访问。"""
    if method == "GET" and (path.startswith("/api/states") or path.startswith("/api/history")
                            or path == "/api/services" or path.startswith("/api/logbook")):
        return "ha:read"
    m = re.match(r"^/api/services/(light|switch|climate)/(.+)$", path)
    if method == "POST" and m:
        domain, service = m.group(1), m.group(2)
        if domain == "light" and service.startswith("turn_"):
            return "ha:light:write"
        if domain == "switch" and service.startswith("turn_"):
            return "ha:switch:write"
        if domain == "climate":
            return "ha:climate:write"
    # 播报与解静音（haclient 的 notify_say / unmute 走这两条）
    if method == "POST" and path == "/api/services/notify/send_message":
        return "ha:notify:write"
    if method == "POST" and path in ("/api/services/media_player/volume_mute",
                                     "/api/services/media_player/volume_set"):
        return "ha:media:write"
    return None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self, method):
        path = urlsplit(self.path).path
        # 1) 路径是否允许
        scope = required_scope(method, path)
        if not scope:
            _audit("", method, path, "deny", "路径不在白名单")
            return self._json({"error": "path not allowed"}, 403)
        # 2) 验票
        authz = self.headers.get("Authorization", "")
        parts = authz.split(None, 1)
        token = parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else ""
        if not token:
            _audit("", method, path, "deny", "缺少 token")
            return self._json({"error": "unauthorized"}, 401)
        result = _verify(token, scope)
        if not result.get("valid"):
            cid = result.get("claims", {}).get("cid", "")
            _audit(cid, method, path, "deny", result.get("error", "verify fail"))
            return self._json({"error": "unauthorized", "reason": result.get("error")}, 401)
        cid = result.get("claims", {}).get("cid", "")
        # 3) 带 HA token 转发
        if not HA_TOKEN:
            _audit(cid, method, path, "deny", "HA_TOKEN 未配置")
            return self._json({"error": "proxy misconfigured"}, 500)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        headers = {"Authorization": "Bearer " + HA_TOKEN}
        if self.headers.get("Content-Type"):
            headers["Content-Type"] = self.headers["Content-Type"]
        req = urllib.request.Request(HA_URL + self.path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                raw = r.read()
                ctype = r.headers.get("Content-Type", "application/json")
                _audit(cid, method, path, "ok")
                self.send_response(r.status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        except urllib.error.HTTPError as ex:
            raw = ex.read()
            _audit(cid, method, path, "upstream_error", f"HA {ex.code}")
            self.send_response(ex.code)
            self.send_header("Content-Type", ex.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except Exception as ex:
            _audit(cid, method, path, "upstream_error", f"{type(ex).__name__}: {ex}")
            self._json({"error": "HA unreachable"}, 502)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")


def main():
    if not HA_TOKEN:
        print("⚠️ HA_TOKEN 未设置，代理将无法转发（请以环境变量注入，勿写死在代码里）")
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"HA 反代启动 :{PORT} -> {HA_URL}  aud={AUD}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n停止")


if __name__ == "__main__":
    main()
