#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""音乐桥鉴权 guard 端到端测试：真实 auth 服务 + 真实 guard + 假音乐后端。"""
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, "D:/agent/xiaoqi-auth")
import auth_server
import music_auth_guard

CFG = "D:/agent/xiaoqi-auth/_test_config.json"
AUTH_PORT = 8902
MUSIC_PORT = 8903
AUTH = f"http://127.0.0.1:{AUTH_PORT}"
MUSIC = f"http://127.0.0.1:{MUSIC_PORT}"

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1; print(f"  ✅ {name}")
    else:
        failed += 1; print(f"  ❌ {name} {detail}")


def req(url, method="GET", data=None, headers=None):
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    r = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                               headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as ex:
        return ex.code, json.loads(ex.read().decode())


def main():
    # 测试配置自包含（test_auth 可能没先跑）
    import secrets
    if not os.path.exists(CFG):
        json.dump({"issuer": "xiaoqi-auth-test", "token_ttl_seconds": 900,
                   "audit_path": "D:/agent/xiaoqi-auth/_test_audit.jsonl",
                   "clients": {
                       "hermes-home": {"secret": secrets.token_hex(16),
                                       "scopes": ["music:play", "music:control", "music:read"],
                                       "auds": ["music-bridge"]},
                       "bad-client": {"secret": secrets.token_hex(16),
                                      "scopes": ["ha:read"], "auds": ["ha-proxy"]}}},
                  open(CFG, "w"), ensure_ascii=False)
    # 起 auth 服务
    auth_server.Handler.service = auth_server.AuthService(CFG)
    s1 = ThreadingHTTPServer(("127.0.0.1", AUTH_PORT), auth_server.Handler)
    threading.Thread(target=s1.serve_forever, daemon=True).start()

    # 让 guard 指向测试 auth，并起假音乐桥（用鉴权版 handler）
    music_auth_guard.AUTH_URL = AUTH
    music_auth_guard.ENABLED = True
    # 动态加载鉴权版 server.py，注入假 service
    import importlib.util, shutil
    shutil.copy("D:/agent/xiaoqi-auth/music_server.py.auth-fix", "D:/agent/xiaoqi-auth/_music_server_test.py")
    spec = importlib.util.spec_from_file_location("music_server", "D:/agent/xiaoqi-auth/_music_server_test.py")
    ms = importlib.util.module_from_spec(spec)
    sys.modules["music_server"] = ms
    spec.loader.exec_module(ms)

    class FakeService:
        def status(self): return {"playing": False, "library_tracks": 2}
        def command(self, text): return {"handled": True, "say": f"fake:{text}"}
        def play_query(self, q): return {"handled": True, "say": f"play:{q}", "status": self.status()}
        def control(self, a): return {"handled": True, "action": a}
        def rescan(self): return {"tracks": 2}
        def search(self, q): return {"kind": "none"}
        def artists(self): return {"artists": []}

    # 用鉴权版的 Handler，但 music_auth_guard 已指向测试 auth
    s2 = ThreadingHTTPServer(("127.0.0.1", MUSIC_PORT), ms.MusicRequestHandler)
    s2.service = FakeService()
    threading.Thread(target=s2.serve_forever, daemon=True).start()

    cfg = json.load(open(CFG, encoding="utf-8"))
    secret = cfg["clients"]["hermes-home"]["secret"]

    print("\n[1] 签发合法 token")
    st, tok = req(AUTH + "/token", "POST", {"grant_type": "client_credentials", "client_id": "hermes-home",
                                            "client_secret": secret, "aud": "music-bridge"})
    check("签发成功", st == 200 and "access_token" in tok, tok)
    token = tok.get("access_token", "")
    auth_h = {"Authorization": "Bearer " + token}

    print("\n[2] GET 端点放行（音箱拉流/查询）")
    st, b = req(MUSIC + "/music/status")
    check("status 无 token 放行", st == 200 and b.get("library_tracks") == 2, (st, b))
    st, b = req(MUSIC + "/music/search?q=x")
    check("search 无 token 放行", st == 200, (st, b))

    print("\n[3] POST 无 token 拒绝")
    st, b = req(MUSIC + "/music/command", "POST", {"text": "播放晚风作酒"})
    check("无 token 拒绝 401", st == 401 and b.get("error") == "unauthorized", (st, b))

    print("\n[4] POST 带 token 通过")
    st, b = req(MUSIC + "/music/command", "POST", {"text": "播放晚风作酒"}, auth_h)
    check("合法 token 通过 200", st == 200 and b.get("handled") is True, (st, b))
    st, b = req(MUSIC + "/music/control", "POST", {"action": "stop"}, auth_h)
    check("control 合法 token 通过", st == 200 and b.get("action") == "stop", (st, b))

    print("\n[5] scope 不足拒绝")
    # 签一个只有 music:read 的 token（手工构造）
    payload = {"iss": "xiaoqi-auth-test", "cid": "hermes-home", "aud": "music-bridge",
               "scope": "music:read", "iat": int(__import__('time').time()),
               "exp": int(__import__('time').time()) + 900, "jti": "abc"}
    weak = auth_server.jws_sign({"alg": "HS256"}, payload, secret.encode())
    st, b = req(MUSIC + "/music/command", "POST", {"text": "x"}, {"Authorization": "Bearer " + weak})
    check("music:read 不能点歌 401", st == 401, (st, b))

    print("\n[6] fail-closed：auth 不可达")
    music_auth_guard.AUTH_URL = "http://127.0.0.1:8999"  # 不存在的端口
    st, b = req(MUSIC + "/music/command", "POST", {"text": "x"}, auth_h)
    check("auth 挂了则拒绝写 401", st == 401, (st, b))

    music_auth_guard.AUTH_URL = AUTH
    s1.shutdown(); s2.shutdown()
    print(f"\n{'='*40}\n通过 {passed}，失败 {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
