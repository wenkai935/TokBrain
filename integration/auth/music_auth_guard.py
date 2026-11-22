#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""音乐桥写端点的薄校验中间件（阶段1闭环示范）。

设计：
  - GET（status/search/artists/track）全部放行：音箱侧 xqmusic 拉流无法带 JWT，
    且这些是只读端点、风险低。
  - POST（command/play/control/rescan）要验 Bearer token：aud=music-bridge，
    scope 必须是对应端点要求的一个。委托 xiaoqi-auth 的 /verify 验签（下游零 JWT 依赖）。
  - auth 中心不可达时 fail-closed：拒绝请求，不让"认证挂了"变成"谁都能点歌"。

放置：与 server.py 同目录（/app/xiaozhi/services/music/）。
配置：环境变量 XIAOQI_AUTH_URL（默认 http://127.0.0.1:8900）、
      XIAOQI_MUSIC_AUTH_ENABLED（默认 1；设 0 可在出问题时紧急关闭，不推荐长期关）。
"""
import json
import os
import urllib.error
import urllib.request

AUTH_URL = os.environ.get("XIAOQI_AUTH_URL", "http://127.0.0.1:8900").rstrip("/")
ENABLED = os.environ.get("XIAOQI_MUSIC_AUTH_ENABLED", "1") != "0"
AUD = "music-bridge"
# 端点 -> 需要的 scope（满足其一即可）
REQUIRED = {
    "/music/command": "music:play",
    "/music/play": "music:play",
    "/music/control": "music:control",
    "/music/rescan": "music:control",
}


def _extract_bearer(header):
    if not header:
        return None
    parts = header.strip().split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def _verify(token, scope):
    """委托 auth 中心验签。返回 (ok, reason)。"""
    data = json.dumps({"token": token, "aud": AUD, "scope": scope}).encode()
    req = urllib.request.Request(AUTH_URL + "/verify", data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            result = json.loads(r.read().decode("utf-8"))
        return bool(result.get("valid")), result.get("error", "")
    except urllib.error.HTTPError as ex:
        try:
            body = json.loads(ex.read().decode("utf-8"))
            return False, body.get("error", f"HTTP {ex.code}")
        except Exception:
            return False, f"HTTP {ex.code}"
    except Exception as ex:
        return False, f"auth 中心不可达: {type(ex).__name__}"


def check(path, headers) -> tuple:
    """返回 (allowed: bool, error_msg: str)。GET 或无保护端点直接放行。"""
    if not ENABLED:
        return True, ""
    scope = REQUIRED.get(path)
    if not scope:
        return True, ""
    token = _extract_bearer(headers.get("Authorization"))
    if not token:
        return False, "缺少 Bearer token"
    ok, reason = _verify(token, scope)
    if not ok:
        return False, reason or "校验失败"
    return True, ""
