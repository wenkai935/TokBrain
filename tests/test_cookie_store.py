"""统一 Cookie 库的契约测试。

这里钉住四件容易回归的事：

1. **三种上传格式都认**——Netscape cookies.txt、Cookie-Editor 的 JSON、
   浏览器里复制的 ``a=1; b=2``；
2. **``#HttpOnly_`` 行是有效 Cookie 而非注释**，直接跳过会静默丢登录态；
3. **域名按后缀严格匹配**，``evil-douyin.com`` 不能混进抖音那份；
4. **``POST /api/cookies/upload`` 不会被 ``/{platform}`` 抢先匹配**——
   固定路径必须声明在参数路径之前，否则上传会莫名其妙 422。

全部用 ``TOKBRAIN_COOKIE_DIR`` 指到临时目录，绝不碰真实的 ``data/cookies``。
"""

from __future__ import annotations

import http.cookiejar
import json
import os

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.routers_v2.cookies import router as cookie_router
from app.services import cookie_store
from app.services.cookie_store import CookieStoreError

NETSCAPE_EXPORT = (
    "# Netscape HTTP Cookie File\n"
    "# 这是注释行\n"
    "\n"
    "#HttpOnly_.douyin.com\tTRUE\t/\tTRUE\t1893456000\tsessionid\tabc123\n"
    ".douyin.com\tTRUE\t/\tFALSE\t1893456000\tttwid\tttwid-primary\n"
    ".iesdouyin.com\tTRUE\t/\tFALSE\t1893456000\tttwid\tttwid-shadow\n"
    ".evil-douyin.com\tTRUE\t/\tFALSE\t1893456000\tmalicious\tnope\n"
)

JSON_EXPORT = json.dumps(
    [
        {
            "domain": ".douyin.com",
            "name": "sessionid",
            "value": "json-session",
            "path": "/",
            "secure": True,
            "httpOnly": True,
            "expirationDate": 1893456000.5,
        },
        {
            "domain": ".douyin.com",
            "name": "ttwid",
            "value": "json-ttwid",
            "path": "/",
            "secure": False,
            "httpOnly": False,
            "expirationDate": 1893456000,
        },
    ]
)


@pytest.fixture(autouse=True)
def isolated_cookie_dir(tmp_path, monkeypatch):
    """每个用例一个独立 Cookie 目录，互不污染真实数据。"""

    monkeypatch.setenv(cookie_store.COOKIE_DIR_ENV, str(tmp_path))
    return tmp_path


def test_netscape_keeps_httponly_and_filters_foreign_domains(isolated_cookie_dir):
    cookie_store.save_text("douyin", NETSCAPE_EXPORT)

    records = cookie_store.load_cookies("douyin")
    names = [record.name for record in records]

    # #HttpOnly_ 行被保留；evil-douyin.com 被严格的域名后缀匹配挡掉
    assert names == ["sessionid", "ttwid"]
    assert "malicious" not in names
    assert records[0].http_only is True


def test_duplicate_names_prefer_the_primary_domain(isolated_cookie_dir):
    cookie_store.save_text("douyin", NETSCAPE_EXPORT)

    values = cookie_store.cookie_dict("douyin")

    # .douyin.com 与 .iesdouyin.com 同名 ttwid，必须取主域那条，而不是后写的副域
    assert values["ttwid"] == "ttwid-primary"


def test_json_export_is_accepted_and_normalized(isolated_cookie_dir):
    cookie_store.save_text("douyin", JSON_EXPORT)

    values = cookie_store.cookie_dict("douyin")
    assert values == {"sessionid": "json-session", "ttwid": "json-ttwid"}

    # 落盘统一成 Netscape，yt-dlp/curl 能直接用
    stored = cookie_store.cookie_file("douyin").read_text(encoding="utf-8")
    assert stored.startswith("# Netscape HTTP Cookie File")
    assert "sessionid\tjson-session" in stored


def test_header_string_is_accepted(isolated_cookie_dir):
    cookie_store.save_text("douyin", "sessionid=paste-me; ttwid=paste-ttwid")

    assert cookie_store.cookie_header("douyin") == "sessionid=paste-me; ttwid=paste-ttwid"
    status = cookie_store.status("douyin")
    assert status["cookie_count"] == 2
    assert status["auth_ready"] is True


def test_saved_file_loads_in_http_cookiejar(isolated_cookie_dir):
    """yt-dlp 用的是标准 cookiejar，这里等价验证"脚本能直接吃"。"""

    cookie_store.save_text("douyin", NETSCAPE_EXPORT)

    jar = http.cookiejar.MozillaCookieJar(str(cookie_store.cookie_file("douyin")))
    jar.load(ignore_discard=True, ignore_expires=True)

    assert {cookie.name for cookie in jar} == {"sessionid", "ttwid"}


def test_status_reports_login_state_and_deletion(isolated_cookie_dir):
    empty = cookie_store.status("douyin")
    assert empty["exists"] is False
    assert empty["auth_ready"] is False

    saved = cookie_store.save_text("douyin", "ttwid=only-ttwid")
    # 只有 ttwid 没有 sessionid：文件在，但登录态不完整
    assert saved["exists"] is True
    assert saved["auth_ready"] is False
    assert saved["missing_auth"] == ["sessionid", "sessionid_ss"]

    removed = cookie_store.delete_cookies("douyin")
    assert removed["removed"] is True
    assert removed["exists"] is False


def test_path_traversal_is_rejected_by_whitelist(isolated_cookie_dir):
    with pytest.raises(CookieStoreError) as excinfo:
        cookie_store.save_text("../../evil", "sessionid=x")

    assert excinfo.value.code == "unknown_platform"
    assert excinfo.value.status_code == 404
    # 目录里不应留下任何文件
    assert list(isolated_cookie_dir.iterdir()) == []


def test_empty_upload_is_rejected(isolated_cookie_dir):
    with pytest.raises(CookieStoreError) as excinfo:
        cookie_store.save_text("douyin", "# 只有注释，没有一条 Cookie\n")

    assert excinfo.value.code == "cookie_empty"
    assert not cookie_store.cookie_file("douyin").exists()


def test_cookie_file_mode_is_private(isolated_cookie_dir):
    cookie_store.save_text("douyin", NETSCAPE_EXPORT)
    path = cookie_store.cookie_file("douyin")

    if os.name == "nt":  # Windows 上 chmod 只体现只读位，不做严格断言
        assert path.exists()
    else:
        assert path.stat().st_mode & 0o777 == 0o600


async def test_header_falls_back_to_legacy_secret(isolated_cookie_dir, monkeypatch):
    async def fake_get_secret(_session, name):
        return "sessionid=legacy; ttwid=legacy" if name == "f2_cookie" else None

    monkeypatch.setattr("app.services.secrets.get_secret", fake_get_secret)

    # 没有文件 → 回退旧 DPAPI SecretRecord，老用户升级后不用重新上传
    assert "sessionid=legacy" in await cookie_store.cookie_header_for(None, "f2")

    # 一旦上传过文件，文件优先，旧 Secret 不再被读取
    cookie_store.save_text("douyin", "sessionid=from-file; ttwid=t")
    header = await cookie_store.cookie_header_for(None, "f2")
    assert "sessionid=from-file" in header
    assert "legacy" not in header


async def test_migrate_legacy_secrets_runs_once(isolated_cookie_dir, monkeypatch):
    async def fake_get_secret(_session, name):
        return "sessionid=migrated; ttwid=t" if name == "douyin_cookie" else None

    monkeypatch.setattr("app.services.secrets.get_secret", fake_get_secret)

    assert await cookie_store.migrate_legacy_secrets(None) == ["douyin"]
    # 第二次不应覆盖已有文件
    assert await cookie_store.migrate_legacy_secrets(None) == []
    assert cookie_store.cookie_dict("douyin")["sessionid"] == "migrated"


@pytest.fixture
async def api_client():
    """只挂 cookies 路由的最小应用：不碰数据库、不启后台协调器。"""

    application = FastAPI()
    application.include_router(cookie_router)
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://testserver"
    ) as client:
        yield client


async def test_upload_endpoint_splits_one_export_per_platform(
    isolated_cookie_dir, api_client
):
    """一份全量导出 → 按域拆成各平台文件；同时验证 /upload 没被 /{platform} 吞掉。"""

    export = NETSCAPE_EXPORT + (
        ".bilibili.com\tTRUE\t/\tFALSE\t1893456000\tSESSDATA\tbili-sess\n"
        ".bilibili.com\tTRUE\t/\tFALSE\t1893456000\tDedeUserID\t42\n"
    )
    response = await api_client.post(
        "/api/cookies/upload",
        files={"file": ("cookies.txt", export.encode("utf-8"), "text/plain")},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert {item["platform"] for item in payload["platforms"]} == {"douyin", "bilibili"}
    assert payload["skipped"] == ["xiaohongshu"]
    # 响应只回名字，不回值
    assert "abc123" not in response.text
    assert cookie_store.cookie_dict("bilibili")["SESSDATA"] == "bili-sess"


async def test_upload_endpoint_rejects_unrelated_file(isolated_cookie_dir, api_client):
    response = await api_client.post(
        "/api/cookies/upload",
        files={"file": ("cookies.txt", b".example.com\tTRUE\t/\tFALSE\t0\tx\ty\n")},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "cookie_empty"


async def test_upload_endpoint_rejects_wrong_file_type(isolated_cookie_dir, api_client):
    """后缀白名单不能写成含空串——``"x.zip".endswith("")`` 恒为真，校验会失效。"""

    response = await api_client.post(
        "/api/cookies/douyin/upload",
        files={"file": ("cookies.zip", NETSCAPE_EXPORT.encode("utf-8"), "application/zip")},
    )

    assert response.status_code == 415
    assert response.json()["detail"]["code"] == "cookie_file_type"


async def test_platform_upload_and_download_round_trip(isolated_cookie_dir, api_client):
    uploaded = await api_client.post(
        "/api/cookies/douyin/upload",
        files={"file": ("cookies.txt", NETSCAPE_EXPORT.encode("utf-8"), "text/plain")},
    )
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["auth_ready"] is True

    downloaded = await api_client.get("/api/cookies/douyin/download")
    assert downloaded.status_code == 200
    assert "sessionid\tabc123" in downloaded.text
