#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""xiaoqi-auth —— 小七家庭服务间认证中心（阶段1，零外部依赖）。

只做四件事：
  1. 服务注册表（clients.json）：每个调用方一个 client_id + 允许的 scope + 允许的 aud。
  2. /token：client_credentials 换短期 JWT（HS256，含 aud/scope/cid/exp/iat/jti）。
  3. /verify：下游把 token 交回来验签 + 校验 aud/scope（方便不装 JWT 库的下游）。
  4. /audit：审计查询；每次签发/校验都落一条 JSONL。

设计取舍（对齐 2026-11-19 方案）：
  - 用 HS256 对称密钥而非 RS256：单家庭、服务都在同一可信 NAS 上，
    对称密钥够用且无需 pyjwt/cryptography。若将来上 mTLS 或跨网段，再换 RS256 + JWKS。
  - 短期 token（默认 15 分钟），过期必须重新换取；jti 便于审计与吊销。
  - 不碰语音"认人"——这里只管机器身份。

运行：python3 auth_server.py --config config.json
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def jws_sign(header: dict, payload: dict, secret: bytes) -> str:
    h = b64url(json.dumps(header, separators=(",", ":"), sort_keys=True).encode())
    p = b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signing_input = f"{h}.{p}".encode("ascii")
    sig = b64url(hmac.new(secret, signing_input, hashlib.sha256).digest())
    return f"{h}.{p}.{sig}"


def jws_verify(token: str, secret: bytes):
    """返回 (payload, error)。校验签名 + exp/iat/nbf，不校 aud/scope（由调用方按资源定）。"""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None, "token 格式不对"
        h, p, s = parts
        header = json.loads(b64url_decode(h))
        if header.get("alg") != "HS256":
            return None, "不支持的 alg"
        expect = b64url(hmac.new(secret, f"{h}.{p}".encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(expect, s):
            return None, "签名不匹配"
        payload = json.loads(b64url_decode(p))
        now = int(time.time())
        if "exp" not in payload or now >= int(payload["exp"]):
            return None, "token 已过期"
        if "iat" in payload and now + 60 < int(payload["iat"]):
            return None, "token 签发时间异常"
        return payload, None
    except Exception as ex:
        return None, f"解析失败: {type(ex).__name__}"


class AuthService:
    def __init__(self, config_path):
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
        self.issuer = cfg.get("issuer", "xiaoqi-auth")
        self.ttl = int(cfg.get("token_ttl_seconds", 900))
        self.clients = cfg.get("clients", {})          # cid -> {secret, scopes, auds}
        self.audit_path = cfg.get("audit_path", os.path.join(HERE, "audit.jsonl"))
        os.makedirs(os.path.dirname(os.path.abspath(self.audit_path)), exist_ok=True)

    def issue(self, cid, secret, aud=None):
        c = self.clients.get(cid)
        if not c:
            return None, "未知 client_id"
        if not hmac.compare_digest(str(c.get("secret", "")), str(secret or "")):
            return None, "client_secret 错误"
        all_scopes = list(c.get("scopes", []))
        allowed_auds = list(c.get("auds", []))
        target = aud or (allowed_auds[0] if allowed_auds else None)
        if not target:
            return None, "未指定 aud"
        if allowed_auds and target not in allowed_auds:
            return None, "该 client 无权访问此 aud"
        # 按 aud 收窄 scope：一张给音乐桥的票里不该出现 ha:light:write。
        # scope 前缀 -> aud 的映射；无匹配的 scope（如 voice:announce）保留给其所属 aud。
        prefix_to_aud = {"music:": "music-bridge", "ha:": "ha-proxy",
                         "exec:": "command-exec",
                         "file:": "file-service", "voice:": "xiaozhi-gateway",
                         "kb:": "tokbrain-control"}
        scopes = [s for s in all_scopes if prefix_to_aud.get(s.split(":")[0] + ":") == target]
        if not scopes:
            scopes = []  # 该 client 在此 aud 上无可用 scope；下游 verify 会因 scope 不足拒绝
        now = int(time.time())
        payload = {
            "iss": self.issuer, "cid": cid, "aud": target,
            "scope": " ".join(scopes), "iat": now, "exp": now + self.ttl,
            "jti": secrets.token_hex(8),
        }
        token = jws_sign({"alg": "HS256", "typ": "JWT"}, payload, str(c["secret"]).encode())
        self.audit("issue", cid, target, "ok", scopes=scopes)
        return {"access_token": token, "token_type": "Bearer",
                "expires_in": self.ttl, "scope": " ".join(scopes), "aud": target}, None

    def verify(self, token, aud=None, scope=None):
        """下游代理调用：验签 + 可选 aud/scope 断言。返回 (payload, error)。"""
        # 从 token 里读 cid 找对应 secret（ unverified payload 仅用于定位密钥，签名随后校验）
        try:
            claims = json.loads(b64url_decode(token.split(".")[1]))
        except Exception:
            return None, "token 无法解析"
        cid = claims.get("cid", "")
        c = self.clients.get(cid)
        if not c:
            return None, "未知 client_id"
        payload, err = jws_verify(token, str(c["secret"]).encode())
        if err:
            self.audit("verify", cid, aud or claims.get("aud", ""), "fail", error=err)
            return None, err
        if aud and payload.get("aud") != aud:
            self.audit("verify", cid, aud, "fail", error="aud 不匹配")
            return None, "aud 不匹配"
        if scope:
            granted = set(str(payload.get("scope", "")).split())
            if scope not in granted:
                self.audit("verify", cid, aud or payload.get("aud", ""), "fail", error="scope 不足")
                return None, "scope 不足"
        self.audit("verify", cid, payload.get("aud", ""), "ok", scope=scope)
        return payload, None

    def audit(self, action, cid, aud, result, **extra):
        row = {"ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
               "action": action, "cid": cid, "aud": aud, "result": result}
        row.update(extra)
        with open(self.audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def jwks(self):
        """对称密钥不暴露 JWKS（无公钥）。给下游返回说明 + key_id 供核对共享密钥指纹。"""
        keys = []
        for cid, c in self.clients.items():
            digest = hashlib.sha256(str(c["secret"]).encode()).hexdigest()[:16]
            keys.append({"kid": digest, "kty": "oct", "alg": "HS256", "use": "sig", "owner": cid})
        return {"keys": keys, "note": "HS256 对称密钥；下游用 clients.json 中对应 secret 验签，kid 为 secret 指纹"}


class Handler(BaseHTTPRequestHandler):
    service = None  # type: AuthService

    def log_message(self, fmt, *args):
        pass

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        ctype = self.headers.get("Content-Type", "")
        if "json" in ctype:
            return json.loads(raw or b"{}"), None
        return parse_qs(raw.decode("utf-8")), None

    def do_GET(self):
        path = urlsplit(self.path).path
        if path in ("/health", "/"):
            return self._json({"status": "ok", "issuer": self.service.issuer})
        if path == "/.well-known/jwks.json" or path == "/jwks":
            return self._json(self.service.jwks())
        if path == "/audit":
            limit = 100
            try:
                lines = open(self.service.audit_path, encoding="utf-8").read().splitlines()
            except OSError:
                lines = []
            return self._json({"count": len(lines), "rows": [json.loads(x) for x in lines[-limit:]]})
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlsplit(self.path).path
        try:
            body, err = self._body()
            if err:
                return self._json({"error": err}, 400)
            if path == "/token":
                grant = body.get("grant_type", ["client_credentials"] if isinstance(body, dict) else "")
                if isinstance(grant, list):
                    grant = grant[0] if grant else ""
                if grant != "client_credentials":
                    return self._json({"error": "unsupported_grant_type"}, 400)
                g = lambda k, d="": (body.get(k, [d])[0] if isinstance(body, dict) and isinstance(body.get(k), list) else body.get(k, d))
                result, err = self.service.issue(str(g("client_id")), str(g("client_secret")), g("aud") or None)
                if err:
                    self.service.audit("issue", str(g("client_id")), str(g("aud") or ""), "fail", error=err)
                    return self._json({"error": err}, 401)
                return self._json(result)
            if path == "/verify":
                token = body.get("token", "")
                if isinstance(token, list):
                    token = token[0] if token else ""
                aud = body.get("aud")
                scope = body.get("scope")
                if isinstance(aud, list):
                    aud = aud[0] if aud else None
                if isinstance(scope, list):
                    scope = scope[0] if scope else None
                payload, err = self.service.verify(str(token), aud, scope)
                if err:
                    return self._json({"valid": False, "error": err}, 401)
                return self._json({"valid": True, "claims": payload})
            return self._json({"error": "not found"}, 404)
        except Exception as ex:
            return self._json({"error": f"{type(ex).__name__}: {ex}"}, 500)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8900)
    args = ap.parse_args()
    Handler.service = AuthService(args.config)
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"xiaoqi-auth 启动: http://{args.host}:{args.port} issuer={Handler.service.issuer}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n停止")


if __name__ == "__main__":
    main()
