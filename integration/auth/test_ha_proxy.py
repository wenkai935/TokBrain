#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HA 反代端到端测试：鉴权门禁 + scope 映射 + HA token 注入 + 审计。"""
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, "D:/agent/xiaoqi-auth")
import auth_server

CFG = "D:/agent/xiaoqi-auth/_test_config.json"
AUDIT = "D:/agent/xiaoqi-auth/_ha_test_audit.jsonl"
AUTH_PORT, HA_PORT, PROXY_PORT = 8904, 8905, 8906
AUTH = f"http://127.0.0.1:{AUTH_PORT}"
PROXY = f"http://127.0.0.1:{PROXY_PORT}"
HA_TOKEN = "FAKE_HA_TOKEN_FOR_TEST"

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
        try:
            return ex.code, json.loads(ex.read().decode())
        except Exception:
            return ex.code, {}


def main():
    import secrets
    json.dump({"issuer": "xiaoqi-auth-test", "token_ttl_seconds": 900,
               "audit_path": AUDIT,
               "clients": {
                   "hermes-home": {"secret": secrets.token_hex(16),
                                   "scopes": ["ha:light:write", "ha:switch:write", "ha:climate:write", "ha:read",
                                              "ha:notify:write", "ha:media:write"],
                                   "auds": ["ha-proxy"]},
                   "readonly": {"secret": secrets.token_hex(16),
                                "scopes": ["ha:read"], "auds": ["ha-proxy"]}}},
              open(CFG, "w"), ensure_ascii=False)

    # auth 服务
    auth_server.Handler.service = auth_server.AuthService(CFG)
    s1 = ThreadingHTTPServer(("127.0.0.1", AUTH_PORT), auth_server.Handler)
    threading.Thread(target=s1.serve_forever, daemon=True).start()

    # 假 HA：检查转发来的 Authorization 必须是 HA_TOKEN
    seen = {}
    class FakeHA(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def _r(self):
            seen["auth"] = self.headers.get("Authorization")
            body = json.dumps({"ok": True, "entity": self.path}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)
        def do_GET(self): self._r()
        def do_POST(self): self._r()
    s2 = ThreadingHTTPServer(("127.0.0.1", HA_PORT), FakeHA)
    threading.Thread(target=s2.serve_forever, daemon=True).start()

    # 反代（注入环境变量）
    os.environ.update({"HA_URL": f"http://127.0.0.1:{HA_PORT}", "HA_TOKEN": HA_TOKEN,
                       "AUTH_URL": AUTH, "AUDIT_PATH": AUDIT, "PORT": str(PROXY_PORT)})
    import importlib.util, shutil
    shutil.copy("D:/agent/xiaoqi-auth/ha_proxy.py", "D:/agent/xiaoqi-auth/_ha_proxy_test.py")
    spec = importlib.util.spec_from_file_location("ha_proxy", "D:/agent/xiaoqi-auth/_ha_proxy_test.py")
    hp = importlib.util.module_from_spec(spec); sys.modules["ha_proxy"] = hp; spec.loader.exec_module(hp)
    s3 = ThreadingHTTPServer(("127.0.0.1", PROXY_PORT), hp.Handler)
    threading.Thread(target=s3.serve_forever, daemon=True).start()

    cfg = json.load(open(CFG, encoding="utf-8"))
    sec = cfg["clients"]["hermes-home"]["secret"]
    ro_sec = cfg["clients"]["readonly"]["secret"]

    print("\n[1] 换票")
    st, tok = req(AUTH + "/token", "POST", {"client_id": "hermes-home", "client_secret": sec, "aud": "ha-proxy"})
    check("换 HA 票成功", st == 200 and "access_token" in tok, tok)
    write_token = tok.get("access_token", "")
    st, tok2 = req(AUTH + "/token", "POST", {"client_id": "readonly", "client_secret": ro_sec, "aud": "ha-proxy"})
    read_token = tok2.get("access_token", "")
    check("换只读票成功", st == 200)
    W = {"Authorization": "Bearer " + write_token}
    R = {"Authorization": "Bearer " + read_token}

    print("\n[2] 无票/错路径拒绝")
    st, b = req(PROXY + "/api/states", "GET")
    check("无票 GET 拒绝 401", st == 401, (st, b))
    st, b = req(PROXY + "/api/config", "GET", headers=W)
    check("非白名单路径 403", st == 403, (st, b))
    st, b = req(PROXY + "/api/services/media_player/play_media", "POST", {}, W)
    check("非白名单服务 403", st == 403, (st, b))

    print("\n[3] scope 门禁")
    st, b = req(PROXY + "/api/states", "GET", headers=R)
    check("只读票可 GET states", st == 200, (st, b))
    st, b = req(PROXY + "/api/services/light/turn_on", "POST", {"entity_id": "light.x"}, R)
    check("只读票不能开灯 401", st == 401, (st, b))
    st, b = req(PROXY + "/api/services/light/turn_on", "POST", {"entity_id": "light.x"}, W)
    check("写票可开灯 200", st == 200, (st, b))
    st, b = req(PROXY + "/api/services/climate/set_temperature", "POST", {"entity_id": "climate.x"}, W)
    check("写票可调空调 200", st == 200, (st, b))

    print("\n[3b] 播报/解静音 scope")
    st, b = req(PROXY + "/api/services/notify/send_message", "POST",
                {"entity_id": "notify.x", "message": "hi"}, W)
    check("写票可播报 notify 200", st == 200, (st, b))
    st, b = req(PROXY + "/api/services/notify/send_message", "POST",
                {"entity_id": "notify.x", "message": "hi"}, R)
    check("只读票不能播报 401", st == 401, (st, b))
    st, b = req(PROXY + "/api/services/media_player/volume_mute", "POST",
                {"entity_id": "media_player.x", "is_volume_muted": False}, W)
    check("写票可解静音 200", st == 200, (st, b))

    print("\n[4] HA token 注入")
    check("下游收到的是 HA_TOKEN 而非业务票", seen.get("auth") == "Bearer " + HA_TOKEN, seen.get("auth"))

    print("\n[5] 审计")
    s1.shutdown(); s2.shutdown(); s3.shutdown(); threading.Event().wait(0.3)
    rows = [json.loads(x) for x in open(AUDIT, encoding="utf-8").read().splitlines()]
    proxy_rows = [r for r in rows if r.get("proxy") == "ha-proxy"]
    check("反代有审计", len(proxy_rows) >= 5, f"反代 {len(proxy_rows)} 条 / 总 {len(rows)} 条")
    check("含 ok 与 deny", {"ok", "deny"} <= {r["result"] for r in proxy_rows})
    check("审计含 cid/method/path", all(k in proxy_rows[0] for k in ("cid", "method", "path", "result")), proxy_rows[0])

    print(f"\n{'='*40}\n通过 {passed}，失败 {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
