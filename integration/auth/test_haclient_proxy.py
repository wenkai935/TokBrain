#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""haclient 反代开关的单测（不改真实 HA，用假 HA + 假 auth）。

覆盖：
  [1] 默认（未开开关）：直连 S.ha_url，带 HA_TOKEN
  [2] 开 XIAOQI_HA_VIA_PROXY=1：请求打到反代地址，带的是票据不是 HA_TOKEN
  [3] 写调用按域取对应 scope（light→ha:light:write）
  [4] fail-closed：auth 不可达时抛错，不回退直连

做法：把 haclient 的 base 指向假 HA 端口，_ticket 走真实 auth_server 进程内实例。
避坑：haclient import config.S，这里用最小 stub 覆盖，不碰生产 config.py。
"""
import json
import os
import sys
import threading
import time
import types
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        print(f"  ❌ {name} {detail}")


# ── 假 config 模块（haclient 依赖它的一堆常量）──────────────────────
def install_stub_config(ha_url):
    cfg = types.ModuleType("config")
    cfg.S = types.SimpleNamespace(ha_url=ha_url, ha_token="REAL_HA_TOKEN_SENTINEL")
    cfg.LIGHTS = {"客厅灯": "light.living"}
    cfg.CLIMATE = {"主卧空调": "climate.master"}
    cfg.ICE_MAKERS = {"餐厅制冰机": "switch.ice"}
    cfg.ICE_SIZE = {"餐厅制冰机": {"entity_id": "select.ice_size", "options": {"大冰块": "big", "小冰块": "small"}}}
    cfg.INFRARED_CLIMATE = {}
    cfg.ALLOWED_SERVICES = {"light": {"turn_on", "turn_off"}, "switch": {"turn_on", "turn_off"},
                            "climate": {"set_temperature"}, "notify": {"send_message"},
                            "media_player": {"volume_mute"}}
    cfg.BANNED_PREFIXES = ()
    cfg.ALLOWED_NOTIFY = "notify.kai"
    cfg.ALLOWED_MEDIA = "media_player.kai"
    cfg.TEMP_MIN, cfg.TEMP_MAX = 16, 30
    cfg.entity_of = lambda alias: {"客厅灯": "light.living", "主卧空调": "climate.master",
                                   "餐厅制冰机": "switch.ice"}.get(alias)
    cfg.device_alias_table = lambda: {}
    cfg.ACTIVITY_MAX_HOURS = 24
    cfg.ACTIVITY_DEVICE_ALIASES = {}
    cfg.ACTIVITY_BLOCKED_DOMAINS = ()
    sys.modules["config"] = cfg
    return cfg


# ── 假上游：记录收到的 Authorization 与路径 ──────────────────────────
seen = []


class FakeUpstream(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _r(self):
        seen.append({"auth": self.headers.get("Authorization"), "path": self.path})
        # /api/states 要返回列表（haclient.states 会遍历），其余返回对象
        payload = [{"entity_id": "light.living", "state": "on", "attributes": {}}] \
            if self.path.startswith("/api/states") else {"ok": True, "path": self.path}
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._r()

    def do_POST(self):
        self._r()


def main():
    import auth_server

    # 1) 起假 auth（签发/验票）
    cfg_path = os.path.join(HERE, "_hc_test_config.json")
    audit_path = os.path.join(HERE, "_hc_test_audit.jsonl")
    json.dump({"issuer": "xiaoqi-auth-test", "token_ttl_seconds": 900, "audit_path": audit_path,
               "clients": {"hermes-home": {"secret": "s3cret-hc",
                                           "scopes": ["ha:read", "ha:light:write", "ha:notify:write"],
                                           "auds": ["ha-proxy"]}}},
              open(cfg_path, "w"), ensure_ascii=False)
    auth_server.Handler.service = auth_server.AuthService(cfg_path)
    AUTH_PORT = 8921
    srv_auth = ThreadingHTTPServer(("127.0.0.1", AUTH_PORT), auth_server.Handler)
    threading.Thread(target=srv_auth.serve_forever, daemon=True).start()

    # 2) 起假 HA（直连目标）与假反代（也指向同一上游，只是记录是哪个端口来的）
    HA_PORT, PROXY_PORT = 8922, 8923
    for port in (HA_PORT, PROXY_PORT):
        srv = ThreadingHTTPServer(("127.0.0.1", port), FakeUpstream)
        threading.Thread(target=srv.serve_forever, daemon=True).start()

    os.environ["XIAOQI_AUTH_URL"] = f"http://127.0.0.1:{AUTH_PORT}"
    os.environ["XIAOQI_AUTH_CLIENT"] = "hermes-home"
    os.environ["XIAOQI_AUTH_SECRET"] = "s3cret-hc"

    # ── 场景1：默认直连 ──
    print("\n[1] 默认直连（未开反代）")
    os.environ.pop("XIAOQI_HA_VIA_PROXY", None)
    install_stub_config(f"http://127.0.0.1:{HA_PORT}")
    sys.modules.pop("haclient", None)
    import haclient as hc1
    c1 = hc1.HAClient()
    seen.clear()
    c1.states()
    check("打到 HA 直连端口", seen and "8922" not in seen[0]["path"], seen[:1])
    check("带的是 HA_TOKEN", seen[0]["auth"] == "Bearer REAL_HA_TOKEN_SENTINEL", seen[0]["auth"])

    # ── 场景2：开反代 ──
    print("\n[2] 反代模式（XIAOQI_HA_VIA_PROXY=1）")
    os.environ["XIAOQI_HA_VIA_PROXY"] = "1"
    os.environ["HA_PROXY_URL"] = f"http://127.0.0.1:{PROXY_PORT}"
    install_stub_config(f"http://127.0.0.1:{HA_PORT}")  # 直连地址仍在，但应被忽略
    sys.modules.pop("haclient", None)
    import haclient as hc2
    c2 = hc2.HAClient()
    check("base 切到反代地址", str(PROXY_PORT) in c2.base, c2.base)
    seen.clear()
    c2.states()
    check("请求确实走反代", len(seen) == 1, seen)
    tok = seen[0]["auth"].replace("Bearer ", "")
    check("带的是票据不是 HA_TOKEN", tok != "REAL_HA_TOKEN_SENTINEL" and tok.count(".") == 2, tok[:16])

    # ── 场景3：写调用按域取 scope ──
    print("\n[3] 写调用的 scope 映射")
    check("light 映射 ha:light:write", c2._WRITE_SCOPE.get("light") == "ha:light:write")
    check("notify 映射 ha:notify:write", c2._WRITE_SCOPE.get("notify") == "ha:notify:write")
    seen.clear()
    c2._call("light", "turn_on", {"entity_id": "light.living"})
    check("写调用走反代并成功", len(seen) == 1, seen)

    # ── 场景4：fail-closed ──
    print("\n[4] fail-closed：auth 不可达")
    os.environ["XIAOQI_AUTH_URL"] = "http://127.0.0.1:59999"  # 无人监听
    sys.modules.pop("haclient", None)
    import haclient as hc3
    c3 = hc3.HAClient()
    try:
        c3.states()
        check("auth 挂了应抛错", False, "没有抛错，可能静默回退了直连")
    except Exception as ex:
        check("auth 挂了抛错（不回退直连）", True)
        check("错误信息含取票失败", "取票失败" in str(ex) or "connect" in str(ex).lower(), str(ex)[:80])

    print(f"\n{'='*40}\n通过 {passed}，失败 {failed}")
    for f in (cfg_path, audit_path):
        try:
            os.remove(f)
        except OSError:
            pass
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
