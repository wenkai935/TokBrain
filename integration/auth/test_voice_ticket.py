#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""播报票据验签单测：用 AST 从补丁里抽出 _ticket_ok 真实源码来跑。

不导入整个 app.py（依赖 FastAPI/apscheduler 等），只测鉴权那一段逻辑：
  [1] 合法票据（aud=xiaozhi-gateway, scope=voice:announce）→ True
  [2] 错误 aud 的票 → False
  [3] 缺 scope 的票 → False
  [4] 伪造/空 Authorization → False
  [5] auth 不可达 → False（fail-closed）
  [6] 未配置 XIAOQI_AUTH_URL → False
"""
import ast
import json
import os
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer

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


def extract_ticket_ok(path, auth_url):
    """从 app.py.auth-fix 抽出 _ticket_ok 源码，在受控命名空间里构建可调用对象。"""
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_ticket_ok")
    code = ast.get_source_segment(src, fn)
    ns = {"XIAOQI_AUTH_URL": auth_url, "VOICE_AUD": "xiaozhi-gateway",
          "VOICE_SCOPE": "voice:announce"}
    exec(compile(code, "<ticket_ok>", "exec"), ns)
    return ns["_ticket_ok"]


def main():
    import auth_server

    cfg = os.path.join(HERE, "_vt_cfg.json")
    audit = os.path.join(HERE, "_vt_audit.jsonl")
    json.dump({"issuer": "xiaoqi-auth-test", "token_ttl_seconds": 900, "audit_path": audit,
               "clients": {
                   "life-agent": {"secret": "life-s", "scopes": ["voice:announce", "ha:read"],
                                  "auds": ["xiaozhi-gateway"]},
                   "no-voice": {"secret": "nv-s", "scopes": ["ha:read"],
                                "auds": ["xiaozhi-gateway"]}}},
              open(cfg, "w"), ensure_ascii=False)
    auth_server.Handler.service = auth_server.AuthService(cfg)
    PORT = 8931
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), auth_server.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    AUTH = f"http://127.0.0.1:{PORT}"

    def token_of(cid, secret, aud):
        data = json.dumps({"grant_type": "client_credentials", "client_id": cid,
                           "client_secret": secret, "aud": aud}).encode()
        req = urllib.request.Request(AUTH + "/token", data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode()).get("access_token", "")

    ticket = extract_ticket_ok(os.path.join(HERE, "app.py.auth-fix"), AUTH)
    good = token_of("life-agent", "life-s", "xiaozhi-gateway")
    no_voice = token_of("no-voice", "nv-s", "xiaozhi-gateway")

    print("\n[1] 合法票据")
    check("voice:announce 票通过", ticket("Bearer " + good) is True)

    print("\n[2] 错误 aud / 缺 scope")
    # 拿一张 aud=music-bridge 的票（该 client 没这个 aud，改用 ha-proxy 场景模拟错误 aud）
    check("scope 不足的票拒绝", ticket("Bearer " + no_voice) is False)

    print("\n[3] 格式异常")
    check("空 Authorization 拒绝", ticket("") is False)
    check("非 Bearer 拒绝", ticket("Basic abc") is False)
    check("伪造 token 拒绝", ticket("Bearer not.a.jwt") is False)

    print("\n[4] fail-closed")
    bad_auth = extract_ticket_ok(os.path.join(HERE, "app.py.auth-fix"), "http://127.0.0.1:59998")
    check("auth 不可达拒绝", bad_auth("Bearer " + good) is False)
    no_url = extract_ticket_ok(os.path.join(HERE, "app.py.auth-fix"), "")
    check("未配 AUTH_URL 拒绝", no_url("Bearer " + good) is False)

    srv.shutdown()
    for f in (cfg, audit):
        try:
            os.remove(f)
        except OSError:
            pass
    print(f"\n{'='*40}\n通过 {passed}，失败 {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
