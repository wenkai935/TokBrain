#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""xiaoqi-auth 端到端测试（进程内起服务，无需外部依赖）。"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auth_server
import auth_client

CFG = "D:/agent/xiaoqi-auth/_test_config.json"
PORT = 8901
BASE = f"http://127.0.0.1:{PORT}"
AUDIT = "D:/agent/xiaoqi-auth/_test_audit.jsonl"

passed = 0
failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        print(f"  ❌ {name} {detail}")


def post(path, data):
    req = urllib.request.Request(BASE + path, data=json.dumps(data).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as ex:
        return ex.code, json.loads(ex.read().decode())


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=5) as r:
        return r.status, json.loads(r.read().decode())


def main():
    for p in (AUDIT,):
        if os.path.exists(p):
            os.remove(p)
    # 测试配置自包含：用随机 secret 生成，不依赖预置文件
    import secrets
    json.dump({"issuer": "xiaoqi-auth-test", "token_ttl_seconds": 900,
               "audit_path": AUDIT,
               "clients": {
                   "hermes-home": {"secret": secrets.token_hex(16),
                                   "scopes": ["music:play", "music:control", "music:read"],
                                   "auds": ["music-bridge"]},
                   "full-client": {"secret": secrets.token_hex(16),
                                   "scopes": ["music:play", "ha:light:write", "ha:read"],
                                   "auds": ["music-bridge", "ha-proxy"]},
                   "bad-client": {"secret": secrets.token_hex(16),
                                  "scopes": ["ha:read"], "auds": ["ha-proxy"]},
                   "hermes-knowledge": {"secret": secrets.token_hex(16),
                                        "scopes": ["kb:read", "kb:ingest"],
                                        "auds": ["tokbrain-control"]}}},
              open(CFG, "w"), ensure_ascii=False)
    auth_server.Handler.service = auth_server.AuthService(CFG)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), auth_server.Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    cfg = json.load(open(CFG, encoding="utf-8"))
    secret = cfg["clients"]["hermes-home"]["secret"]

    print("\n[1] 健康检查 / JWKS")
    st, body = get("/health")
    check("health 200", st == 200 and body["status"] == "ok", body)
    st, body = get("/jwks")
    check("jwks 有 key", st == 200 and len(body["keys"]) >= 3, body)

    print("\n[2] 签发 token")
    st, tok = post("/token", {"grant_type": "client_credentials", "client_id": "hermes-home",
                              "client_secret": secret, "aud": "music-bridge"})
    check("签发成功 200", st == 200 and "access_token" in tok, tok)
    token = tok.get("access_token", "")
    check("expires_in=900", tok.get("expires_in") == 900, tok)
    check("scope 正确", set(tok.get("scope", "").split()) == {"music:play", "music:control", "music:read"}, tok)
    payload = json.loads(auth_server.b64url_decode(token.split(".")[1]))
    check("payload 含 aud/cid/exp/jti", all(k in payload for k in ("aud", "cid", "exp", "jti")), payload)

    print("\n[2b] 全局模型：按 aud 收窄 scope")
    fsec = cfg["clients"]["full-client"]["secret"]
    st, t_music = post("/token", {"client_id": "full-client", "client_secret": fsec, "aud": "music-bridge"})
    check("full-client 可换音乐桥票", st == 200)
    check("音乐桥票只含 music scope", set(t_music.get("scope", "").split()) == {"music:play"}, t_music)
    st, t_ha = post("/token", {"client_id": "full-client", "client_secret": fsec, "aud": "ha-proxy"})
    check("HA 票只含 ha scope", set(t_ha.get("scope", "").split()) == {"ha:light:write", "ha:read"}, t_ha)
    st, v = post("/verify", {"token": t_music.get("access_token"), "aud": "ha-proxy", "scope": "ha:read"})
    check("音乐桥票不能当 HA 票用 401", st == 401, v)

    print("\n[2c] 框架整合：知识库纳入统一账本")
    ksec = cfg["clients"]["hermes-knowledge"]["secret"]
    st, t_kb = post("/token", {"client_id": "hermes-knowledge", "client_secret": ksec,
                               "aud": "tokbrain-control"})
    check("hermes-knowledge 可换知识库票", st == 200 and "access_token" in t_kb, t_kb)
    check("知识库票只含 kb scope", set(t_kb.get("scope", "").split()) == {"kb:read", "kb:ingest"}, t_kb)
    kb_claims = json.loads(auth_server.b64url_decode(t_kb.get("access_token", "e30.").split(".")[1])
                           if t_kb.get("access_token") else "e30=")
    check("知识库票 aud=tokbrain-control", kb_claims.get("aud") == "tokbrain-control", kb_claims)
    st, v = post("/verify", {"token": t_kb.get("access_token"), "aud": "tokbrain-control", "scope": "kb:read"})
    check("知识库票 kb:read 校验通过", st == 200 and v.get("valid") is True, v)
    st, v = post("/verify", {"token": t_kb.get("access_token"), "aud": "tokbrain-control", "scope": "kb:ingest"})
    check("知识库票 kb:ingest 校验通过", st == 200 and v.get("valid") is True, v)
    st, v = post("/verify", {"token": t_kb.get("access_token"), "aud": "music-bridge"})
    check("知识库票不能当音乐桥票 401", st == 401 and v.get("valid") is False, v)
    st, v = post("/token", {"client_id": "hermes-knowledge", "client_secret": ksec, "aud": "music-bridge"})
    check("知识库 client 无权取音乐桥票 401", st == 401, v)
    st, v = post("/token", {"client_id": "full-client", "client_secret": fsec, "aud": "tokbrain-control"})
    check("家居全权 client 无权取知识库票 401", st == 401, v)

    print("\n[3] 验签通过")
    st, v = post("/verify", {"token": token, "aud": "music-bridge", "scope": "music:play"})
    check("正确 aud+scope 通过", st == 200 and v.get("valid") is True, v)

    print("\n[4] 拒绝场景")
    st, v = post("/verify", {"token": token, "aud": "ha-proxy"})
    check("错误 aud 拒绝 401", st == 401 and v.get("valid") is False, v)
    st, v = post("/verify", {"token": token, "scope": "ha:switch:write"})
    check("scope 不足拒绝 401", st == 401 and "scope" in v.get("error", ""), v)
    tampered = token[:-3] + "xxx"
    st, v = post("/verify", {"token": tampered})
    check("篡改 token 拒绝 401", st == 401, v)
    st, v = post("/token", {"client_id": "hermes-home", "client_secret": "wrong-secret", "aud": "music-bridge"})
    check("错误 secret 拒绝 401", st == 401, v)
    st, v = post("/token", {"client_id": "bad-client",
                            "client_secret": cfg["clients"]["bad-client"]["secret"], "aud": "music-bridge"})
    check("越权 aud 拒绝 401", st == 401, v)

    print("\n[5] 过期 token")
    old = auth_server.jws_sign({"alg": "HS256"},
        {"iss": "xiaoqi-auth-test", "cid": "hermes-home", "aud": "music-bridge",
         "scope": "music:play", "iat": int(time.time()) - 2000, "exp": int(time.time()) - 1000},
        secret.encode())
    st, v = post("/verify", {"token": old})
    check("过期 token 拒绝 401", st == 401 and "过期" in v.get("error", ""), v)

    print("\n[6] client 缓存续期")
    c = auth_client.AuthClient(BASE, "hermes-home", secret, aud="music-bridge")
    t1 = c.get_token()
    t2 = c.get_token()
    check("缓存命中（两次同 token）", t1 == t2)
    check("auth_header 格式", c.auth_header().startswith("Bearer "))
    ok, claims = auth_client.verify_via_auth(BASE, t1, aud="music-bridge", scope="music:control")
    check("client 端 verify 通过", ok, claims)

    print("\n[7] 审计落盘")
    srv.shutdown()
    time.sleep(0.3)
    rows = [json.loads(x) for x in open(AUDIT, encoding="utf-8").read().splitlines()]
    check("审计有记录", len(rows) >= 10, f"共 {len(rows)} 条")
    check("含 issue 与 verify", {"issue", "verify"} <= {r["action"] for r in rows})
    check("含失败记录", any(r["result"] == "fail" for r in rows))
    check("审计字段完整", all(k in rows[0] for k in ("ts", "action", "cid", "aud", "result")), rows[0])

    print(f"\n{'='*40}\n通过 {passed}，失败 {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
