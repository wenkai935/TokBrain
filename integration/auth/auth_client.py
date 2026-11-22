#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""xiaoqi-auth 调用方工具：取 token（带缓存与提前续期）+ 校验 token。

零外部依赖，供下游服务（音乐桥、HA 反代、电视 helper）import 使用。

用法（取 token 的一方）：
    c = AuthClient("http://127.0.0.1:8900", "hermes-home", "<secret>", aud="music-bridge")
    token = c.get_token()          # 自动缓存，过期前 60 秒续期
    headers = {"Authorization": "Bearer " + token}

用法（验 token 的一方，委托 auth 中心验签）：
    ok, claims = verify_via_auth("http://127.0.0.1:8900", token, aud="music-bridge", scope="music:play")
"""
import json
import threading
import time
import urllib.parse
import urllib.request


class AuthClient:
    def __init__(self, auth_url, client_id, client_secret, aud=None, skew=60):
        self.url = auth_url.rstrip("/")
        self.cid = client_id
        self.secret = client_secret
        self.aud = aud
        self.skew = skew
        self._token = None
        self._exp = 0
        self._lock = threading.Lock()

    def _fetch(self):
        data = json.dumps({"grant_type": "client_credentials", "client_id": self.cid,
                           "client_secret": self.secret, "aud": self.aud}).encode()
        req = urllib.request.Request(self.url + "/token", data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            result = json.loads(r.read().decode("utf-8"))
        return result["access_token"], int(result.get("expires_in", 900))

    def get_token(self):
        now = time.time()
        if self._token and now < self._exp - self.skew:
            return self._token
        with self._lock:
            if self._token and now < self._exp - self.skew:
                return self._token
            token, ttl = self._fetch()
            self._token = token
            self._exp = now + ttl
            return token

    def auth_header(self):
        return "Bearer " + self.get_token()


def verify_via_auth(auth_url, token, aud=None, scope=None, timeout=3):
    """下游调用 /verify 委托验签。返回 (valid: bool, claims_or_error: dict)。"""
    data = json.dumps({"token": token, "aud": aud, "scope": scope}).encode()
    req = urllib.request.Request(auth_url.rstrip("/") + "/verify", data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            result = json.loads(r.read().decode("utf-8"))
        return bool(result.get("valid")), result
    except urllib.error.HTTPError as ex:
        try:
            return False, json.loads(ex.read().decode("utf-8"))
        except Exception:
            return False, {"error": f"HTTP {ex.code}"}
    except Exception as ex:
        return False, {"error": f"{type(ex).__name__}: {ex}"}


def extract_bearer(header):
    """从 Authorization 头取 token。"""
    if not header:
        return None
    parts = header.strip().split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None
