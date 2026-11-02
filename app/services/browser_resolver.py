"""用 playwright-cli 替代 f2 解析抖音作品。

## 为什么要换

f2 0.0.1.7（PyPI 最后版本，2024-12-31）在 2026-11 已完全失效：它拼的
``a_bogus`` 签名 + 随机 ``webid`` 会被抖音 Argus 拦掉，返回

    403 Blocked by ArgusSecurityPlugin Uifid Not Found

升级无解——没有更新版本；而项目里 zhicui 的采集路径 ``_abogus_class()`` 也是
``from f2.utils.abogus import ABogus``，共用同一套签名，换路径同样被拦。

## 为什么这样能通

真实浏览器里的 fingerprint、``webid``、``UIFID`` 是抖音自己维护的，天然自洽，
所以页面能正常渲染。我们只**读已渲染的 DOM**，不再自己拼签名发请求。

实测（2026-11-02）短链 ``v.douyin.com/AUTwGFr94Qs/``：正常跳转、无验证码，
拿得到标题、作者、时长、发布时间、视频流地址。

## 边界

- 只读 DOM，不请求任何接口。页面自己的 XHR 失败不影响我们。
- 走系统 Chrome（``--browser=chrome``），不下载 Chromium。
- 每次解析使用独立 session，结束即 close，避免并发互相踩。
- 任何一步超时/失败都抛 ``PublicLinkError``，由上层决定熔断——不在这里重试。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import uuid
from typing import Any

from app.services.f2_links import PublicLinkError, PublicWork

#: 单次解析总预算。打开 5s + 提取 3s + 收尾，留足余量。
TIMEOUT_SECONDS = 90

#: 页面渲染等待：抖音详情区是客户端补渲染的，等太短会拿不到作者。
RENDER_WAIT_MS = 5000

#: playwright-cli 的可执行名（Windows 上是 .cmd，由 shutil.which 解析）。
CLI_NAME = "playwright-cli"

#: 允许用环境变量改用系统其它浏览器（chrome / msedge）。
BROWSER = os.environ.get("TOKBRAIN_BROWSER_FOR_RESOLVE", "chrome")

_EXTRACT_JS = """
async page => {
  await page.waitForTimeout(%d);
  return await page.evaluate(() => {
    const href = location.href;
    const m = href.match(/\\/(?:video|note)\\/(\\d+)/);
    const detail = [...document.querySelectorAll('[data-e2e]')]
      .find(e => e.getAttribute('data-e2e') === 'video-detail');
    const body = detail ? detail.innerText : '';
    const video = document.querySelector('video');
    const mediaUrl = video ? (video.currentSrc || video.src || '') : '';

    const userLinks = [...document.querySelectorAll('a')].filter(a => {
      const h = String(a.getAttribute('href') || '');
      return h.includes('/user/') && !h.includes('/user/self');
    });
    const authorLink = userLinks.find(a => String(a.innerText).trim());
    const secMatch = authorLink
      ? String(authorLink.getAttribute('href')).match(/\\/user\\/([A-Za-z0-9_\\-]+)/)
      : null;

    const dur = body.match(/(\\d{2}):(\\d{2})\\s*\\/\\s*(\\d{2}):(\\d{2})/);
    const published = body.match(/发布时间：([\\d\\-]+\\s+[\\d:]+)/);

    return JSON.stringify({
      awemeId: m ? m[1] : '',
      kind: href.includes('/note/') ? 'image' : 'video',
      title: (document.title || '').replace(/\\s*-\\s*抖音\\s*$/, '').trim(),
      author: authorLink ? String(authorLink.innerText).replace(/\\n/g, ' ').trim().slice(0, 60) : '',
      authorId: secMatch ? secMatch[1] : '',
      duration: dur ? Number(dur[3]) * 60 + Number(dur[4]) : 0,
      mediaUrl: mediaUrl,
      published: published ? published[1] : '',
      bodyLen: body.length
    });
  });
}
""" % RENDER_WAIT_MS


class BrowserUnavailableError(PublicLinkError):
    """本机没有可用的 playwright-cli（未安装 / 不在 PATH）。"""


def _node_install_roots() -> list:
    """npm 全局安装的兜底目录。

    后端由 ``start.ps1`` 拉起时，PATH 里通常既没有 ``%APPDATA%\\npm``，也没有
    WorkBuddy 托管的 node 目录，此时 ``shutil.which`` 一律返回 ``None``。
    不兜底的话通道会被判成「未安装」，所有解析都挂在 ``browser_unavailable``
    上——症状和「抖音拒绝」几乎一样，排查代价极高。
    """
    from pathlib import Path

    roots = []
    for key in ("APPDATA", "LOCALAPPDATA"):
        base = os.environ.get(key, "")
        if base:
            roots.append(Path(base) / "npm")
    managed = Path.home() / ".workbuddy" / "binaries" / "node" / "versions"
    try:
        roots.extend(sorted((p for p in managed.iterdir() if p.is_dir()), reverse=True))
    except OSError:
        pass
    return roots


def _project_frontend_dir():
    """项目自带的 frontend 目录（其下有 npm 装出来的 node_modules）。

    ``npm ci`` 把 ``@playwright/cli`` 装在 ``frontend/node_modules`` 里——那是项目
    局部安装，不会进 PATH。只扫全局安装目录的话，clone 用户明明装好了，解析通道
    仍然会抛 ``browser_unavailable``；症状和「抖音拒绝」几乎一样，排查代价极高。

    环境变量 ``TOKBRAIN_FRONTEND_DIR`` 可覆盖；默认按本文件位置推导项目根。
    """
    from pathlib import Path

    env_dir = os.environ.get("TOKBRAIN_FRONTEND_DIR", "").strip()
    if env_dir:
        return Path(env_dir)
    # app/services/browser_resolver.py -> 上溯两级是项目根
    return Path(__file__).resolve().parents[2] / "frontend"


def _cli_script_candidates() -> list:
    """找出 ``playwright-cli.js`` 的候选位置。

    Windows 上 ``playwright-cli`` 是 ``.cmd``/``sh`` 包装脚本，
    ``create_subprocess_exec`` 无法直接执行它（WinError 2）。所以直接定位
    真正的 node 入口 ``node_modules/@playwright/cli/playwright-cli.js``，
    用 node 显式执行——顺带完全避开 shell 引号问题。
    """
    from pathlib import Path

    bases = []
    env_script = os.environ.get("TOKBRAIN_PLAYWRIGHT_CLI_JS", "").strip()
    if env_script:
        bases.append(Path(env_script))
    # 项目自带的 node_modules：npm ci 装出来的 @playwright/cli 就在这里，它不在
    # PATH 上，必须显式纳入。放在全局安装之前，是为了让所有机器都用 lock
    # 锁定的同一个版本，而不是各自 PATH 里那个来路不明的。
    bases.append(_project_frontend_dir())
    wrapper = shutil.which(CLI_NAME)
    if wrapper:
        bases.append(Path(wrapper).resolve().parent)
    node = shutil.which("node")
    if node:
        bases.append(Path(node).resolve().parent)
    bases.extend(_node_install_roots())
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        bases.append(Path(local) / "Programs" / "nodejs")

    candidates = []
    for base in bases:
        candidates.append(base / "node_modules" / "@playwright" / "cli" / "playwright-cli.js")
        candidates.append(
            base / "node_modules" / "playwright" / "cli" / "playwright-cli.js"
        )
        candidates.append(base.parent / "node_modules" / "@playwright" / "cli" / "playwright-cli.js")
    return candidates


def _launcher() -> tuple[str, str]:
    """返回 (node 可执行文件, playwright-cli.js 路径)。找不到就抛错。"""
    from pathlib import Path

    script = next((c for c in _cli_script_candidates() if c.is_file()), None)
    if script is None:
        raise BrowserUnavailableError(
            "browser_unavailable",
            "本机没有 playwright-cli，无法用浏览器解析链接",
        )
    node = os.environ.get("TOKBRAIN_NODE_EXE", "").strip()
    if not node or not Path(node).is_file():
        # 托管 node 的布局是 <versions>/<ver>/node_modules/@playwright/cli/xxx.js，
        # 往上第四层就是 node.exe 所在目录；兜底再扫一遍安装根目录。
        roots = [Path(script).parent.parent.parent.parent, *_node_install_roots()]
        for root in roots:
            candidate = root / "node.exe"
            if candidate.is_file():
                node = str(candidate)
                break
    if not node:
        which_node = shutil.which("node")
        if which_node and Path(which_node).is_file():
            node = which_node
    if not node:
        raise BrowserUnavailableError(
            "browser_unavailable", "找不到 node.exe，无法启动 playwright-cli"
        )
    return node, str(script)


def cli_available() -> bool:
    try:
        _launcher()
    except BrowserUnavailableError:
        return False
    return True


def _env() -> dict[str, str]:
    env = dict(os.environ)
    # npm 全局 bin 不一定在 PATH 里（WorkBuddy 的 node 是托管安装）
    extra = [
        os.path.join(os.environ.get("APPDATA", ""), "npm"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "npm"),
    ]
    path = env.get("PATH", "")
    for entry in extra:
        if entry and entry not in path:
            path = f"{path}{os.pathsep}{entry}"
    env["PATH"] = path
    return env


async def _run(args: list[str], *, timeout: float) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_env(),
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise PublicLinkError(
            "network_timeout", "浏览器解析超时，抖音页面未在预期时间内就绪"
        ) from None
    return (
        proc.returncode or 0,
        out.decode("utf-8", "replace"),
        err.decode("utf-8", "replace"),
    )


def _parse_result(stdout: str) -> dict[str, Any]:
    """playwright-cli 的 eval 结果形如 ``### Result\\n"<json string>"``。"""
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith('"'):
            continue
        try:
            inner = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(inner, str):
            continue
        try:
            return json.loads(inner)
        except json.JSONDecodeError:
            continue
    raise PublicLinkError("f2_response_invalid", "浏览器未返回可解析的作品数据")


async def resolve_via_browser(url: str) -> PublicWork:
    """用系统浏览器打开作品页并读 DOM，返回与 f2 同构的 ``PublicWork``。

    不重试：失败一次就抛，熔断交给上层（``import_queue`` 的 circuit）。
    """
    if not cli_available():
        raise BrowserUnavailableError(
            "access_forbidden", "本机没有 playwright-cli，无法用浏览器解析链接"
        )

    node, script = _launcher()
    session = f"tb-{uuid.uuid4().hex[:8]}"
    base = [node, script, f"-s={session}"]
    try:
        code, out, err = await _run(
            [*base, "open", f"--browser={BROWSER}", url], timeout=TIMEOUT_SECONDS
        )
        if code != 0:
            raise PublicLinkError(
                "network_error",
                f"浏览器打开链接失败：{(err or out).strip().splitlines()[-1][:120] if (err or out).strip() else code}",
            )

        code, out, err = await _run(
            [*base, "run-code", _EXTRACT_JS], timeout=TIMEOUT_SECONDS
        )
        if code != 0:
            raise PublicLinkError("f2_response_invalid", "浏览器读取页面数据失败")
        data = _parse_result(out)
    finally:
        # 关掉本次 session，避免机器上残留一堆浏览器进程
        try:
            await _run([*base, "close"], timeout=30)
        except Exception:  # noqa: BLE001 - 收尾失败不该掩盖主结果
            pass

    aweme_id = str(data.get("awemeId") or "").strip()
    title = str(data.get("title") or "").strip()
    if not aweme_id or not title:
        # 页面没渲染出作品（可能链接失效、或撞上验证页）
        raise PublicLinkError(
            "work_unavailable", "页面没有渲染出作品信息，可能已删除、私密或需要验证"
        )

    kind = "image" if data.get("kind") == "image" else "video"
    media_url = str(data.get("mediaUrl") or "").strip()
    return PublicWork(
        platform_work_id=aweme_id,
        canonical_url=f"https://www.douyin.com/{'note' if kind == 'image' else 'video'}/{aweme_id}",
        kind=kind,
        title=title[:500],
        description=title,
        author_id=str(data.get("authorId") or "").strip() or None,
        author_name=str(data.get("author") or "").strip()[:200] or None,
        duration_seconds=float(data.get("duration") or 0),
        media_urls=[media_url] if media_url else [],
        raw_metadata={
            "resolver": "playwright",
            "published_at": str(data.get("published") or "").strip(),
        },
    )
