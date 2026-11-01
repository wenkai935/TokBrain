"""浏览器解析通道（替代 f2）的契约。

f2 0.0.1.7 已被抖音 Argus 拦成 403，PyPI 两年无更新；项目里 zhicui 采集路径
也复用同一套 ABogus 签名，换路径同样被拦。所以解析改走「系统浏览器 + 只读
已渲染 DOM」。这里钉住四件事：

1. playwright-cli 的 ``### Result`` 输出能正确解析成作品数据；
2. 页面没渲染出作品（失效/私密/验证页）→ 抛 work_unavailable，不返回假数据；
3. 浏览器通道不读 Cookie，登录态坏掉不连带解析失败；
4. 找不到 playwright-cli 时明确报错，而不是静默变成空结果。
"""

import json

import pytest

from app.services import browser_resolver, import_queue
from app.services.browser_resolver import (
    BrowserUnavailableError,
    _parse_result,
    resolve_via_browser,
)
from app.services.f2_links import PublicLinkError


def _cli_output(payload: dict) -> str:
    """模拟 playwright-cli eval 的真实输出格式。"""
    return "### Result\n" + json.dumps(json.dumps(payload, ensure_ascii=False)) + "\n"


def test_parse_result_reads_cli_wrapper():
    payload = {"awemeId": "7667154814924557577", "title": "示例作品"}
    assert _parse_result(_cli_output(payload)) == payload


def test_parse_result_rejects_garbage():
    with pytest.raises(PublicLinkError):
        _parse_result("### Error\nsomething went wrong")


@pytest.fixture
def fake_run(monkeypatch):
    """替换子进程调用，记录调用顺序，不真开浏览器。"""
    calls = []
    payload = {
        "awemeId": "7667154814924557577",
        "kind": "video",
        "title": "Github宝藏面试救星，秋招求职必看",
        "author": "千忆",
        "authorId": "MS4wLjABAAAAA7SMNSznnnkdLxSn",
        "duration": 42,
        "mediaUrl": "https://v26-web.douyinvod.com/abc/video/tos/x",
        "published": "2026-07-27 18:36",
    }

    async def runner(args, *, timeout):
        calls.append(args)
        if "open" in args:
            return (0, "### Browser `x` opened", "")
        if "run-code" in args:
            return (0, _cli_output(payload), "")
        return (0, "", "")

    monkeypatch.setattr(browser_resolver, "_run", runner)
    monkeypatch.setattr(browser_resolver, "cli_available", lambda: True)
    monkeypatch.setattr(
        browser_resolver, "_launcher", lambda: ("node.exe", "playwright-cli.js")
    )
    return calls, payload


async def test_resolve_maps_fields_to_public_work(fake_run):
    work = await resolve_via_browser("https://v.douyin.com/AUTwGFr94Qs/")

    assert work.platform_work_id == "7667154814924557577"
    assert work.kind == "video"
    assert work.author_name == "千忆"
    assert work.duration_seconds == 42.0
    assert work.media_urls == ["https://v26-web.douyinvod.com/abc/video/tos/x"]
    assert work.canonical_url == "https://www.douyin.com/video/7667154814924557577"
    assert work.raw_metadata["resolver"] == "playwright"
    assert work.raw_metadata["published_at"] == "2026-07-27 18:36"


async def test_resolve_closes_session_even_on_success(fake_run):
    calls, _ = fake_run
    await resolve_via_browser("https://v.douyin.com/AUTwGFr94Qs/")
    assert any("close" in call for call in calls), "必须关掉本次 session，避免残留浏览器"


async def test_resolve_raises_when_page_has_no_work(monkeypatch):
    async def runner(args, *, timeout):
        if "run-code" in args:
            return (0, _cli_output({"awemeId": "", "title": ""}), "")
        return (0, "", "")

    monkeypatch.setattr(browser_resolver, "_run", runner)
    monkeypatch.setattr(browser_resolver, "cli_available", lambda: True)
    monkeypatch.setattr(
        browser_resolver, "_launcher", lambda: ("node.exe", "playwright-cli.js")
    )

    with pytest.raises(PublicLinkError) as info:
        await resolve_via_browser("https://v.douyin.com/dead/")
    assert info.value.code == "work_unavailable"


async def test_missing_cli_raises_not_silent(monkeypatch):
    monkeypatch.setattr(browser_resolver, "cli_available", lambda: False)
    with pytest.raises(BrowserUnavailableError):
        await resolve_via_browser("https://v.douyin.com/x/")


async def test_resolve_link_uses_browser_by_default(monkeypatch):
    """默认通道是浏览器，且不读 Cookie（登录态坏掉不该连带解析失败）。"""
    seen = {}

    async def fake_browser(url):
        seen["url"] = url
        return "work"

    monkeypatch.setattr(import_queue, "LINK_RESOLVER", "browser")
    monkeypatch.setattr(browser_resolver, "resolve_via_browser", fake_browser)

    # 必须传真实的 F2WorkClient 才会走通道策略；注入替身时按替身走（见下一个测试）
    real = import_queue.F2WorkClient()
    result = await import_queue._resolve_link(real, "https://v.douyin.com/a/", "")
    assert result == "work"
    assert seen["url"] == "https://v.douyin.com/a/"


async def test_injected_client_is_respected(monkeypatch):
    """调用方显式注入 client 时不能被默认通道覆盖，否则既有预检测试全失真。"""
    monkeypatch.setattr(import_queue, "LINK_RESOLVER", "browser")

    class Client:
        async def resolve(self, url, *, cookie=""):
            return f"injected:{url}"

    assert await import_queue._resolve_link(Client(), "u", "") == "injected:u"


async def test_resolve_link_honours_f2_switch(monkeypatch):
    monkeypatch.setattr(import_queue, "LINK_RESOLVER", "f2")

    class Client:
        async def resolve(self, url, *, cookie=""):
            return f"f2:{cookie}"

    assert await import_queue._resolve_link(Client(), "u", "abc") == "f2:abc"


async def test_browser_channel_ignores_broken_cookie(monkeypatch):
    """浏览器通道必须完全不碰 Cookie。

    历史教训：Cookie 因 DPAPI 换用户而解不开时，旧 f2 通道会把每一条链接
    都标成 f2_cookie_unreadable，整条链路瘫痪。浏览器通道不需要登录态，
    这里钉死它不会去读——cookie 读取函数一被调用就抛错。
    """
    from app.services import secrets

    def boom(*_args, **_kwargs):
        raise AssertionError("浏览器通道不应读取 Cookie")

    monkeypatch.setattr(import_queue, "cookie_header_for", boom)
    monkeypatch.setattr(secrets, "get_secret", boom)
    monkeypatch.setattr(import_queue, "LINK_RESOLVER", "browser")

    async def fake_browser(url):
        return "ok"

    monkeypatch.setattr(browser_resolver, "resolve_via_browser", fake_browser)

    real = import_queue.F2WorkClient()
    assert await import_queue._resolve_link(real, "https://v.douyin.com/a/", "") == "ok"
