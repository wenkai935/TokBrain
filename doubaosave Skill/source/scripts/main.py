#!/usr/bin/env python3
"""
抖音个人数据采集工具
====================
采集自用账户的：喜欢、收藏（含收藏夹层级结构与各层视频链接）、观看历史，
以及各视频的分享链接。

技术方案：Playwright 持久化浏览器 + 拦截 API 响应获取结构化数据。
登录后自动采集收藏夹层级树：通过 /aweme/v1/web/collects/list/ 拿到收藏夹列表
（若平台返回 children 则递归成多级树），再对每个收藏夹进入
/aweme/v1/web/collects/video/list/ 滚动加载并提取视频链接。

收藏子分类字段：每条来自收藏夹的视频记录会额外携带
  - "收藏夹ID"：所属收藏夹 id
  - "收藏夹路径"：层级路径（如 "夹A/子夹B"，父夹/子夹），用于导入端拆分
    成独立的"收藏夹/子收藏夹"字段。

已验证的技术路径（沉淀自知萃桌面端 platform-account.js）：
  - 优先复用本机 Chrome/Edge（channel="chrome"/"msedge"）替代自带 chromium
  - 用 sessionid / sessionid_ss 认证 cookie 判断登录态
  - 精确文本匹配（exact=True）切换收藏夹/tab，避免误点大 tab
  - API 拦截 + DOM 可见卡片双通道采集

首次运行会打开浏览器窗口，请手动登录抖音，登录状态会自动保存。

用法：
  python main.py                  # 采集全部（喜欢+收藏树+历史）
  python main.py --only like      # 只采集喜欢
  python main.py --only favor     # 只采集收藏（默认采集收藏夹层级树）
  python main.py --only history   # 只采集观看历史
  python main.py --only post      # 只采集我的作品（已发布）
  python main.py --flat-favorites # 收藏用旧的扁平 DOM 方式采集（回退）
  python main.py --headless       # 无头模式（需已登录）
  python main.py --max-scroll 50  # 每个分类最多滚动次数（默认100）
  python main.py --only favor --folder 家装,露营   # 收藏只采指定夹
"""

import json
import csv
import re
import time
import sys
import argparse
from contextlib import suppress
from pathlib import Path
from datetime import datetime

try:
    from playwright.sync_api import sync_playwright, Response
except ImportError:
    print("错误：未安装 playwright，请先运行：pip install playwright && playwright install chromium")
    sys.exit(1)

# ============ 配置 ============
BASE_DIR = Path(__file__).parent
USER_DATA_DIR = BASE_DIR / "browser_profile"   # 持久化浏览器数据（保存登录态）
OUTPUT_DIR = BASE_DIR / "output"                # 输出目录
SCROLL_PAUSE_MS = 2500                           # 每次滚动后等待毫秒数
MAX_SCROLLS = 100                                # 每个分类最大滚动次数
NAV_TIMEOUT_MS = 30000                           # 页面导航超时
# ==============================


class DouyinScraper:
    def __init__(self, headless=False, max_scrolls=MAX_SCROLLS, only_folders=None):
        self.headless = headless
        self.max_scrolls = max_scrolls
        self.only_folders = set(only_folders or [])  # 收藏采集定向夹名集合（空=全部）
        self.playwright = None
        self.context = None
        self.page = None
        self.api_data = []  # 存储拦截到的 API 响应数据
        self.sec_uid = None

    def _launch_context(self):
        """
        启动持久化浏览器上下文。

        技术路径参考（已在知萃桌面端验证）：
        - 优先复用本机已装的 Chrome / Edge（channel="chrome"/"msedge"），
          不额外下载浏览器，登录态与日常浏览器一致、风控识别度低；
        - 全部失败时回退到 playwright 自带的 chromium。
        """
        common = {
            "user_data_dir": str(USER_DATA_DIR),
            "headless": self.headless,
            "viewport": {"width": 1280, "height": 900},
            "locale": "zh-CN",
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--start-maximized",
            ],
        }
        for channel in ("chrome", "msedge"):
            try:  # try the next browser channel on failure.
                ctx = self.playwright.chromium.launch_persistent_context(
                    channel=channel, **common)
                print(f"[启动] 使用本机浏览器: {channel}")
                return ctx
            except Exception as e:  # noqa: PERF203
                print(f"[启动] 尝试 {channel} 失败: {e}")
        print("[启动] 回退到 playwright 自带 chromium")
        return self.playwright.chromium.launch_persistent_context(**common)

    def start(self):
        """启动浏览器（持久化上下文，自动保存登录态）"""
        USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        self.playwright = sync_playwright().start()
        self.context = self._launch_context()
        # 注入反检测脚本
        self.context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = {runtime: {}};
        """)
        self.page = self.context.new_page()
        self.page.set_default_timeout(NAV_TIMEOUT_MS)

        # 设置 API 响应拦截器
        self._setup_api_capture()
        print("[启动] 浏览器已启动")

    def _setup_api_capture(self):
        """拦截抖音视频数据相关的 API 响应，直接获取结构化 JSON"""
        def on_response(response: Response):
            url = response.url
            # 抖音网页端视频列表/详情相关 API
            # 注意收藏夹实际接口是 collects/list、collects/video/list（带 s），
            # 旧的 collection/list、collection/aweme 一并保留做兼容。
            keywords = [
                "aweme/list", "aweme/favorite", "aweme/like",
                "aweme/post", "aweme/detail", "aweme/history",
                "collects/list", "collects/video", "collection/aweme",
                "collection/list", "aweme/listcollection",
                "aweme/v1/web", "im/peek/message",
            ]
            if any(kw in url for kw in keywords):
                try:
                    data = response.json()
                    self.api_data.append({"url": url, "data": data})
                except Exception:
                    pass

        self.page.on("response", on_response)

    def _has_douyin_session(self):
        """通过认证 cookie 判断是否已登录（知萃桌面端已验证路径）。

        抖音网页端登录成功的标志 cookie：sessionid / sessionid_ss。
        比"登录按钮消失"更可靠，且不依赖页面文本。
        """
        try:
            for cookie in self.context.cookies("https://www.douyin.com"):
                if cookie.get("name") in ("sessionid", "sessionid_ss") and cookie.get("value"):
                    return True
        except Exception:
            pass
        return False

    def ensure_login(self):
        """确保已登录，未登录则等待用户手动登录"""
        print("[登录] 检查登录状态...")
        # 方式1：认证 cookie 检测（知萃已验证，速度快、不受页面结构影响）
        if self._has_douyin_session():
            print("[登录] 已登录（检测到认证 cookie）")
            return
        self.page.goto("https://www.douyin.com", wait_until="domcontentloaded")
        time.sleep(3)
        # 方式2：登录按钮状态检测（原逻辑，保留作兜底）
        login_btn = self.page.locator("text=登录").first
        if login_btn.count() > 0 and login_btn.is_visible():
            if self.headless:
                print("[错误] 无头模式下无法登录，请先在有界面模式下运行一次完成登录")
                sys.exit(1)
            print("[登录] 检测到未登录，请在弹出的浏览器窗口中完成登录...")
            print("[登录] 登录成功后脚本会自动继续（最多等待5分钟）")
            # 等待登录按钮消失
            try:
                login_btn.wait_for(state="detached", timeout=300000)
                print("[登录] 检测到登录成功！")
            except Exception:
                print("[警告] 登录等待超时，将尝试继续执行")
            time.sleep(3)
        else:
            print("[登录] 已登录")

    def get_sec_uid(self):
        """获取当前用户的 sec_uid"""
        print("[用户] 获取用户信息...")
        self.page.goto("https://www.douyin.com/user/self", wait_until="domcontentloaded")
        time.sleep(3)
        url = self.page.url
        # URL 格式: https://www.douyin.com/user/MS4wLjABAAAA...
        parts = url.rstrip("/").split("/")
        self.sec_uid = parts[-1] if "user" in parts else None
        if self.sec_uid:
            print(f"[用户] sec_uid: {self.sec_uid[:20]}...")
        else:
            print("[警告] 未能获取 sec_uid，将尝试使用页面默认数据")
        return self.sec_uid

    def _scroll_to_load_all(self, stop_when_api_done=None, pause_ms=SCROLL_PAUSE_MS):
        """滚动页面直到加载全部内容或达到最大滚动次数。

        stop_when_api_done: 指定 API 关键词（如 "collects/video"）时按 API 响应
          判断：has_more=false 即末页；连续 4 轮无新响应也停。不做整页 DOM 链接
          扫描（慢），速度快得多。未指定时按可见视频链接数判断（DOM 兜底）。
        """
        # 把光标移到视口中央，确保滚轮事件打在主内容滚动容器上（而非左侧边栏）
        try:
            self.page.mouse.move(640, 450)
        except Exception:
            pass
        collected = set()
        no_change = 0
        prev = 0

        for i in range(self.max_scrolls):
            # 分页接口已到末页则提前结束
            if stop_when_api_done and self._last_has_more(stop_when_api_done) is False:
                print(f"  [滚动] 分页接口已到末页（has_more=false），停止滚动")
                break

            if stop_when_api_done:
                # API 模式：看捕获的列表响应是否还在增长
                current = self._count_captured(stop_when_api_done)
            else:
                # DOM 模式：按可见视频链接数判断，顺便收集 ID
                links = self.page.locator("a[href*='/video/']").all()
                for link in links:
                    try:  # skip broken links without aborting the page scan.
                        href = link.get_attribute("href")
                        if href and "/video/" in href:
                            aweme_id = href.rstrip("/").split("/")[-1].split("?")[0]
                            if aweme_id and len(aweme_id) > 5:
                                collected.add(aweme_id)
                    except Exception:  # noqa: PERF203
                        pass
                current = len(collected)

            # 检测是否停止加载
            if i > 0 and current == prev:
                no_change += 1
                if no_change >= 4:
                    print(f"  [滚动] 第 {i+1} 次后无新内容，停止滚动")
                    break
            else:
                no_change = 0
            prev = current

            if (i + 1) % 10 == 0 and stop_when_api_done:
                print(f"  [滚动] 已滚 {i+1} 次，{stop_when_api_done} 响应累计 {current} 次")

            # 滚动（在收藏夹页面内触发下一页刷新请求）
            self.page.mouse.wheel(0, 4000)
            time.sleep(pause_ms / 1000)

        print(f"  [滚动] 共收集到 {len(collected)} 个视频ID")
        return collected

    # ------------------------------------------------------------------
    # 滚动 / 分页辅助
    # ------------------------------------------------------------------

    def _count_captured(self, keyword):
        """统计已拦截到的、URL 包含 keyword 的响应条数"""
        return sum(1 for it in self.api_data if keyword in it.get("url", ""))

    def _last_has_more(self, keyword):
        """返回最近一次匹配 keyword 的响应里的 has_more 标记（None=未捕获到）"""
        for it in reversed(self.api_data):
            url = it.get("url", "")
            if keyword not in url:
                continue
            data = it.get("data", {}) or {}
            payload = data.get("data", data) if isinstance(data, dict) else {}
            for cand in (payload, data):
                if isinstance(cand, dict) and "has_more" in cand:
                    return bool(cand.get("has_more"))
        return None

    def _extract_videos_from_api(self, url_contains=None, extra_fields=None):
        """
        从拦截的 API 响应中提取视频结构化数据。

        url_contains: 可选 URL 关键词列表，只提取匹配来源响应的视频；
                      为 None 时从所有拦截响应中提取。
        extra_fields: 可选 dict，注入到每条视频记录上（如收藏夹ID、收藏夹路径）。
        """
        videos = {}
        for item in self.api_data:
            url = item.get("url", "")
            if url_contains and not any(kw in url for kw in url_contains):
                continue
            data = item.get("data", {})
            # 抖音 API 响应中视频列表通常在 data.aweme_list 或 data.aweme_details
            aweme_list = (
                data.get("aweme_list")
                or data.get("aweme_details")
                or data.get("aweme_list_extra")
                or []
            )
            if not isinstance(aweme_list, list):
                continue

            for aweme in aweme_list:
                if not isinstance(aweme, dict):
                    continue
                aweme_id = aweme.get("aweme_id", "")
                if not aweme_id:
                    continue

                # 提取作者信息
                author = aweme.get("author", {}) or {}
                author_name = author.get("nickname", "")
                author_uid = author.get("uid", "")
                author_sec_uid = author.get("sec_uid", "")

                # 提取分享链接（优先使用 API 返回的 share_url，即短链）
                share_url = aweme.get("share_url", "")
                if not share_url:
                    share_info = aweme.get("share_info", {}) or {}
                    share_url = share_info.get("share_url", "")
                if not share_url:
                    share_url = f"https://www.douyin.com/video/{aweme_id}"

                # 视频描述/标题
                desc = aweme.get("desc", "")

                # 创建时间
                create_time = aweme.get("create_time", 0)
                create_time_str = ""
                if create_time:
                    with suppress(Exception):
                        create_time_str = datetime.fromtimestamp(create_time).strftime("%Y-%m-%d %H:%M:%S")

                # 统计信息
                statistics = aweme.get("statistics", {}) or {}
                digg_count = statistics.get("digg_count", 0)
                comment_count = statistics.get("comment_count", 0)
                collect_count = statistics.get("collect_count", 0)
                share_count = statistics.get("share_count", 0)

                record = {
                    "aweme_id": aweme_id,
                    "标题": desc,
                    "作者": author_name,
                    "作者UID": author_uid,
                    "作者sec_uid": author_sec_uid,
                    "分享链接": share_url,
                    "视频链接": f"https://www.douyin.com/video/{aweme_id}",
                    "发布时间": create_time_str,
                    "点赞数": digg_count,
                    "评论数": comment_count,
                    "收藏数": collect_count,
                    "分享数": share_count,
                    "采集时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                if extra_fields:
                    record.update(extra_fields)
                videos[aweme_id] = record
        return videos

    def scrape_liked(self):
        """采集喜欢的视频"""
        print("\n" + "=" * 50)
        print("[喜欢] 开始采集喜欢的视频...")
        self.api_data.clear()

        if not self.sec_uid:
            self.get_sec_uid()

        url = f"https://www.douyin.com/user/{self.sec_uid}?showTab=like"
        self.page.goto(url, wait_until="domcontentloaded")
        time.sleep(4)

        # 点击"喜欢"标签（如果URL参数未生效）；用精确匹配，避免误选页面上
        # 任何含"喜欢"字样的元素（如某个收藏夹名末段的"喜欢\nN"）。
        try:
            like_tab = self.page.get_by_text("喜欢", exact=True).first
            if like_tab.count() > 0 and like_tab.is_visible():
                like_tab.click()
                time.sleep(2)
        except Exception:
            pass

        self._scroll_to_load_all()
        videos = self._extract_videos_from_api()
        print(f"[喜欢] 采集完成，共 {len(videos)} 个视频")
        return list(videos.values())

    def scrape_favorites(self, flat=False):
        """
        采集收藏。

        默认（flat=False）：自动获取收藏夹层级树——通过 /aweme/v1/web/collects/list/
        解析收藏夹列表（平台若返回 children 则递归成多级树），再逐夹进入并滚动加载，
        提取每层的视频链接。若 API 拦截失败则回退到 DOM 方式。
        flat=True：使用旧的扁平 DOM 采集（_discover_collections 猜测收藏夹名）。
        """
        if not flat:
            tree = self.scrape_favorites_tree()
            if tree:
                return tree
            print("[收藏] API 树采集失败，回退到 DOM 方式")
        return self._scrape_favorites_dom()

    # ------------------------------------------------------------------
    # 收藏夹层级树采集（主路径：API 拦截）
    # ------------------------------------------------------------------

    def scrape_favorites_tree(self):
        """登录后自动获取收藏夹层级结构 + 每层视频链接，返回树列表"""
        print("\n" + "=" * 50)
        print("[收藏树] 开始采集收藏夹层级结构...")
        self.api_data.clear()

        if not self.sec_uid:
            self.get_sec_uid()

        # 1) 拿到收藏夹列表树
        tree = self._fetch_collection_tree()
        if not tree:
            print("[收藏树] 未能从 API 获取收藏夹列表")
            return []

        # 1.5) --folder 定向过滤（默认采全部）
        if self.only_folders:
            kept, skipped = [], []
            for n in tree:
                (kept if n.get("name") in self.only_folders else skipped).append(n)
            if skipped:
                print("[收藏树] --folder 定向，跳过:",
                      "、".join(n.get("name") for n in skipped))
            tree = kept
            if not tree:
                print("[收藏树] 没有匹配指定收藏夹，返回空")
                return []

        # 2) 逐夹采集视频链接
        self._fill_tree_videos(tree)
        total = self._count_videos(tree)
        print(f"\n[收藏树] 采集完成：{len(tree)} 个收藏夹（含子级共"
              f"{self._count_nodes(tree)} 个节点），视频总数 {total}")
        return tree

    @staticmethod
    def _count_nodes(tree):
        return sum(1 + DouyinScraper._count_nodes(n.get("children", [])) for n in tree)

    @staticmethod
    def _count_videos(tree):
        return sum(len(n.get("videos", []))
                   + DouyinScraper._count_videos(n.get("children", [])) for n in tree)

    def _fetch_collection_tree(self):
        """
        访问收藏 tab 并等待 /collects/list 响应，返回收藏夹树。
        收藏夹条目若带 children / sub_collects / collection_list 则递归成多级。
        """
        if not self.sec_uid:
            self.get_sec_uid()
        url = f"https://www.douyin.com/user/{self.sec_uid}?showTab=favor_collection"
        try:
            self.page.goto(url, wait_until="domcontentloaded")
            time.sleep(4)
        except Exception as e:
            print(f"[收藏树] 访问收藏夹页失败: {e}")

        # 显式点击主标签 "收藏"（仿截图步骤 1）。精确匹配避免误选"收藏夹"子 tab
        # 或页面上其它含"收藏"字样的元素（text=... 是子串匹配，不可靠）。
        try:
            tab = self.page.get_by_text("收藏", exact=True).first
            if tab.count() > 0 and tab.is_visible():
                tab.click()
                time.sleep(2)
        except Exception:
            pass

        # 显式点击子标签 "收藏夹"（仿截图步骤 2 —— 红字 2 的位置）。
        # URL 参数 showTab=favor_collection 在部分情况下不会触发子 tab，
        # 会保留默认"视频"而显示所有视频网格；显式点"收藏夹"才是文件夹网格。
        try:
            sub = self.page.get_by_text("收藏夹", exact=True).first
            if sub.count() > 0 and sub.is_visible():
                sub.click()
                time.sleep(2)
        except Exception:
            pass

        # 在收藏夹页面内滚动，触发更多收藏夹分页请求（collects/list 可能分页）
        self._scroll_collection_list()

        # 夹名/ID 来自 API：collects_name / collects_id（2026-09-06 探测确认，
        # 返回的就是全部真实自建夹，含分页）。页面夹卡片是 UL>LI 结构（非 <a>），
        # 逐夹进入由 _scrape_one_collection 用「精确文本→上溯 LI 点击」或 goto 完成。
        return self._parse_collection_tree()

    def _scroll_collection_list(self):
        """在收藏夹列表页滚动，触发更多收藏夹分页请求（collects/list）。

        优先把滚轮打在可能的左侧收藏夹列表容器上；连续若干轮无新请求即停止。
        """
        before = self._count_captured("collects/list")
        no_change = 0
        # 把滚轮打在可能的收藏夹列表容器（侧边栏）上，而不是主内容区
        container = self.page.locator(
            "aside, [class*='collect-list'], [class*='folder-list'], [class*='collect']"
        ).first
        if container.count() > 0:
            try:
                box = container.bounding_box()
                if box:
                    self.page.mouse.move(
                        box["x"] + min(box["width"] / 2, 200),
                        box["y"] + box["height"] / 2,
                    )
            except Exception:
                pass
        for i in range(self.max_scrolls):
            self.page.mouse.wheel(0, 2000)
            time.sleep(SCROLL_PAUSE_MS / 1000)
            now = self._count_captured("collects/list")
            if now > before:
                before = now
                no_change = 0
                print(f"  [收藏树] 第 {i+1} 次滚动触发了新的收藏夹列表请求（累计 {now} 次）")
            else:
                no_change += 1
                if no_change >= 4:
                    print(f"  [收藏树] 收藏夹列表加载完毕（滚动 {i+1} 次无新请求）")
                    break

    def _parse_collection_tree(self):
        """从所有拦截到的收藏夹列表响应中合并收藏夹条目，构建层级树。

        合并跨分页的多个 collects/list 响应，并按 id 去重，避免重复收藏夹。
        """
        entries = []
        for item in self.api_data:
            url = item.get("url", "")
            if "collects/list" not in url and "collection/list" not in url:
                continue
            data = item.get("data", {}) or {}
            payload = data.get("data", data) if isinstance(data, dict) else {}
            page = (payload.get("collects_list")
                    or payload.get("collection_list")
                    or payload.get("collects")
                    or [])
            if isinstance(page, list):
                entries.extend(page)
        if not entries:
            return []

        # 按 id 去重，避免分页重复
        uniq = {}
        for e in entries:
            if not isinstance(e, dict):
                continue
            eid = str(e.get("id") or e.get("collects_id")
                      or e.get("cid") or e.get("fid") or "")
            if eid and eid not in uniq:
                uniq[eid] = e
        return self._build_collection_tree(list(uniq.values()))

    def _build_collection_tree(self, entries):
        """递归构建收藏夹层级树"""
        tree = []
        for e in entries:
            if not isinstance(e, dict):
                continue
            node = {
                "id": str(e.get("id") or e.get("collects_id")
                          or e.get("cid") or e.get("fid") or ""),
                "name": str(e.get("collects_name") or e.get("name")
                  or e.get("title") or "未命名收藏夹"),
                "aweme_count": e.get("aweme_count") or e.get("video_count") or 0,
                "children": [],
                "videos": [],
            }
            kids = (e.get("children") or e.get("sub_collects")
                    or e.get("collection_list") or [])
            if isinstance(kids, list):
                node["children"] = self._build_collection_tree(kids)
            tree.append(node)
        return tree

    def _fill_tree_videos(self, tree, prefix=""):
        """递归遍历收藏夹树，逐节点采集视频链接，并为每条视频标注收藏夹归属。

        prefix: 当前节点在树中的层级路径（如 "夹A/子夹B"），用于给视频记录
                填充"收藏夹路径"字段（父夹/子夹）。

        单个收藏夹采集异常（页面崩溃、导航失败等）只跳过该夹并继续，
        不让整棵树采集中断。
        """
        for node in tree:
            path = f"{prefix}/{node['name']}" if prefix else node["name"]
            try:
                self._scrape_one_collection(node, path)
            except Exception as e:  # noqa: PERF203
                print(f"  [警告] 收藏夹 '{node.get('name')}' 采集出错，跳过该夹: {e}")
                node["videos"] = []
            if node.get("children"):
                try:
                    self._fill_tree_videos(node["children"], path)
                except Exception as e:  # noqa: PERF203
                    print(f"  [警告] 收藏夹 '{node.get('name')}' 子级采集出错: {e}")

    def _scrape_one_collection(self, node, path=""):
        """进入单个收藏夹并采集全部视频（确定性导航路线）。

        依据（技能「家装案例」+ 2026-09-06 实测）：
        - 夹列表是 UL>LI 结构、无 <a href=/collection/id>，故用「叶子精确文本 →
          上溯 LI 点击」进入（技能已验证 li:has-text 定位法）；
        - 收藏夹详情页可直接 goto /collection/{id}（历史实测 10/10 可靠）作回退；
        - 不用 go_back 往返浏览页（SPA 返回不可靠），每夹都从全量导航开始。
        """
        coll_id = str(node.get("id", ""))
        name = node.get("name", "")
        print(f"\n[收藏树] 正在采集收藏夹: {name} (id={coll_id})")
        if not coll_id:
            print(f"  [警告] 收藏夹 '{name}' 没有 id，跳过视频采集")
            node["videos"] = []
            return

        # 1) 确定性进入：优先文件夹列表 LI 点击（成功经验），失败回退 goto 详情页
        opened = self._open_folder_list() and self._click_folder_li(name)
        if not opened:
            try:
                self.page.goto(f"https://www.douyin.com/collection/{coll_id}",
                               wait_until="domcontentloaded")
                time.sleep(4)
                opened = f"/collection/{coll_id}" in self.page.url
            except Exception:
                opened = False
        if not opened:
            print(f"  [警告] 无法进入收藏夹 '{name}'，跳过")
            node["videos"] = []
            return

        print(f"  [收藏树] 已进入 '{name}'，URL={self.page.url[:110]}")

        # 2) 只保留本夹的视频响应（进入后、滚动前清空）
        self.api_data.clear()
        self._click_video_subtab()
        self._scroll_to_load_all(stop_when_api_done="collects/video")

        # 3) 提取
        videos = self._extract_videos_from_api(
            url_contains=["collects/video", "collects/aweme",
                          "collection/aweme", "aweme/list"],
            extra_fields={"收藏夹ID": coll_id, "收藏夹路径": path,
                          "收藏夹名称": name},
        )
        if not videos:
            videos = self._extract_videos_from_api(
                extra_fields={"收藏夹ID": coll_id, "收藏夹路径": path,
                              "收藏夹名称": name})
        node["videos"] = list(videos.values())
        print(f"  [收藏树] {name}: 采集到 {len(node['videos'])} 个视频")

    def _open_folder_list(self):
        """确保停在「收藏夹文件夹列表」视图：全量 goto + 点主/子标签。

        全量导航比 go_back 可靠（SPA 返回常丢视图或触发导航竞态 ERR_ABORTED）。
        """
        for attempt in (1, 2):
            try:
                uid = self.sec_uid or "self"
                self.page.goto(
                    f"https://www.douyin.com/user/{uid}?showTab=favor_collection",
                    wait_until="load")
                time.sleep(2)
                tab = self.page.get_by_text("收藏", exact=True).first
                if tab.count() > 0 and tab.is_visible():
                    tab.click()
                    time.sleep(1.5)
                sub = self.page.get_by_text("收藏夹", exact=True).first
                if sub.count() > 0 and sub.is_visible():
                    sub.click()
                    time.sleep(2)
                if "showSubTab=favorite_folder" in self.page.url                         or self._count_captured("collects/list"):
                    return True
            except Exception as e:
                print(f"[收藏树] 打开文件夹列表失败（第 {attempt} 次）: {e}")
                time.sleep(3)
        return False

    def _click_folder_li(self, name, timeout=8):
        """在文件夹列表视图点击目标夹：叶子精确文本 → 上溯 LI（li:has-text 定位法）。"""
        try:
            el = self.page.get_by_text(name, exact=True).first
            if el.count() == 0 or not el.is_visible():
                return False
            li = el.locator("xpath=ancestor::li[1]")
            if li.count() > 0 and li.first.is_visible():
                li.first.click(timeout=8000)
            else:
                el.click(timeout=8000)
        except Exception as e:
            print(f"  [警告] 点击收藏夹 '{name}' 失败: {e}")
            return False
        deadline = time.time() + timeout
        while time.time() < deadline:
            if (self.page.locator("a[href*='/video/']").count() > 0
                    or self._count_captured("collects/video") > 0):
                time.sleep(2)
                return True
            time.sleep(0.5)
        return True  # 未确认也继续，交由滚动+提取兜底判断

    def _click_video_subtab(self):
        """在收藏夹详情页显式点击子标签"视频"，避免停在音乐/合集/短剧等空 tab。"""
        try:
            sub = self.page.get_by_text("视频", exact=True).first
            if sub.count() > 0 and sub.is_visible():
                role = sub.get_attribute("role") or ""
                parent_class = (sub.evaluate(
                    "e => e.parentElement && e.parentElement.parentElement "
                    "? (e.parentElement.parentElement.className || '') : ''"
                ) or "").lower()
                if role == "tab" or "tab" in parent_class or "sub" in parent_class:
                    sub.click()
                    time.sleep(1)
        except Exception:
            pass
    @staticmethod
    def _flatten_tree(tree, prefix=""):
        """把收藏夹树展平为 {路径: [视频]}，路径用 '/' 分隔层级。
        即使某收藏夹没有视频也保留路径键（值为空列表），保证层级结构完整。"""
        flat = {}
        for node in tree:
            path = f"{prefix}/{node['name']}" if prefix else node["name"]
            flat[path] = node.get("videos") or []
            if node.get("children"):
                flat.update(DouyinScraper._flatten_tree(node["children"], path))
        return flat

    # ------------------------------------------------------------------
    # 旧 DOM 方式（回退路径）
    # ------------------------------------------------------------------

    def _scrape_favorites_dom(self):
        """采集收藏（含收藏夹子分类）——旧 DOM 方式"""
        print("\n" + "=" * 50)
        print("[收藏] 开始采集收藏...")
        self.api_data.clear()

        if not self.sec_uid:
            self.get_sec_uid()

        url = f"https://www.douyin.com/user/{self.sec_uid}?showTab=favor"
        self.page.goto(url, wait_until="domcontentloaded")
        time.sleep(4)

        # 点击"收藏"标签
        try:
            favor_tab = self.page.locator("text=收藏").first
            if favor_tab.count() > 0:
                favor_tab.click()
                time.sleep(2)
        except Exception:
            pass

        collections = {}

        # 尝试发现收藏夹分类
        # 收藏夹可能以下拉选择器或横向标签形式存在
        collection_names = self._discover_collections()

        if len(collection_names) <= 1:
            # 没有多个收藏夹，直接采集当前页面
            print("[收藏] 未发现多个收藏夹，采集默认收藏")
            self._scroll_to_load_all()
            videos = self._extract_videos_from_api()
            collections["默认收藏"] = list(videos.values())
            print(f"[收藏] 默认收藏: {len(videos)} 个视频")
        else:
            print(f"[收藏] 发现 {len(collection_names)} 个收藏夹: {', '.join(collection_names)}")
            for coll_name in collection_names:
                print(f"\n[收藏] 正在采集收藏夹: {coll_name}")
                self.api_data.clear()
                try:
                    self._select_collection(coll_name)
                    time.sleep(2)
                    self._scroll_to_load_all()
                    videos = self._extract_videos_from_api()
                    collections[coll_name] = list(videos.values())
                    print(f"[收藏] {coll_name}: {len(videos)} 个视频")
                except Exception as e:
                    print(f"[收藏] 采集 {coll_name} 时出错: {e}")
                    collections[coll_name] = []

        return collections

    def _discover_collections(self):
        """发现页面上的收藏夹分类名称"""
        names = ["默认收藏"]
        try:
            # 尝试多种可能的收藏夹选择器
            selectors = [
                "[class*='collection'] [class*='item']",
                "[class*='folder'] [class*='name']",
                "[class*='tab'] [class*='text']",
                "div[role='tab']",
                "[class*='select'] option",
            ]
            for sel in selectors:
                elements = self.page.locator(sel).all()
                for el in elements:
                    try:  # ignore individual element extraction failures.
                        text = el.inner_text().strip()
                        if text and text not in names and len(text) < 50:
                            names.append(text)
                    except Exception:  # noqa: PERF203
                        pass
                if len(names) > 1:
                    break
        except Exception:
            pass
        return names

    def _select_collection(self, coll_name):
        """点击选择指定的收藏夹（精确文本匹配，避免误点）"""
        try:
            el = self.page.get_by_text(coll_name, exact=True).first
            if el.count() > 0:
                el.click()
                return
        except Exception:
            pass
        # 降级：不做特殊选择，使用当前页面
        print(f"  [警告] 未能点击收藏夹 '{coll_name}'，使用当前页面")

    def scrape_history(self):
        """采集观看历史"""
        print("\n" + "=" * 50)
        print("[历史] 开始采集观看历史...")
        self.api_data.clear()

        # 抖音观看历史页面
        history_urls = [
            "https://www.douyin.com/history",
            "https://www.douyin.com/user/self?showTab=history",
        ]
        for url in history_urls:
            try:
                self.page.goto(url, wait_until="domcontentloaded")
                time.sleep(4)
                # 检查页面是否有视频内容
                video_links = self.page.locator("a[href*='/video/']").count()
                if video_links > 0:
                    print(f"[历史] 使用URL: {url}")
                    break
            except Exception as e:
                print(f"[历史] URL {url} 访问失败: {e}")

        self._scroll_to_load_all()
        videos = self._extract_videos_from_api()
        print(f"[历史] 采集完成，共 {len(videos)} 条观看历史")
        return list(videos.values())

    def scrape_work(self):
        """采集我的作品（已发布视频），导航到用户主页 ?showTab=post。"""
        print("\n" + "=" * 50)
        print("[作品] 开始采集我的作品...")
        self.api_data.clear()

        if not self.sec_uid:
            self.get_sec_uid()

        url = f"https://www.douyin.com/user/{self.sec_uid}?showTab=post"
        self.page.goto(url, wait_until="domcontentloaded")
        time.sleep(4)

        # 点击"作品"标签（若 URL 参数未生效）
        try:
            work_tab = self.page.locator("text=作品").first
            if work_tab.count() > 0:
                work_tab.click()
                time.sleep(2)
        except Exception:
            pass

        self._scroll_to_load_all()
        videos = self._extract_videos_from_api(url_contains=["aweme/post"])
        if not videos:
            videos = self._extract_videos_from_api()
        print(f"[作品] 采集完成，共 {len(videos)} 个视频")
        return list(videos.values())

    def save_data(self, liked=None, favorites=None, history=None, work=None):
        """保存数据到 JSON 和 CSV

        favorites 支持两种形态：
        - dict {收藏夹名: [视频]}：旧扁平格式，直接保存
        - list（收藏夹树节点）：额外输出 收藏夹结构_*.json 树文件，
          主 JSON 的"收藏"字段用层级路径展平为 dict，保持向后兼容。
        """
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        favorites_tree = None
        favorites_flat = favorites
        if isinstance(favorites, list):
            favorites_tree = favorites
            favorites_flat = self._flatten_tree(favorites)
            if not favorites_flat:
                favorites_flat = {"默认收藏": []}

        all_data = {
            "采集时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "喜欢": liked or [],
            "收藏": favorites_flat or {},
            "观看历史": history or [],
            "我的作品": work or [],
        }

        # 保存完整 JSON
        json_path = OUTPUT_DIR / f"douyin_data_{timestamp}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(all_data, f, ensure_ascii=False, indent=2)
        print(f"\n[保存] 完整数据已保存: {json_path}")

        # 保存收藏夹层级结构树 JSON（仅树采集时）
        if favorites_tree:
            tree_path = OUTPUT_DIR / f"收藏夹结构_{timestamp}.json"
            with open(tree_path, "w", encoding="utf-8") as f:
                json.dump({"采集时间": all_data["采集时间"],
                           "收藏夹树": favorites_tree}, f,
                          ensure_ascii=False, indent=2)
            print(f"[保存] 收藏夹层级结构已保存: {tree_path}")

        # 保存各分类 CSV
        if liked:
            self._save_csv(liked, OUTPUT_DIR / f"喜欢_{timestamp}.csv")
        if favorites_flat:
            for coll_path, videos in favorites_flat.items():
                if videos:
                    safe_name = coll_path.replace("/", "_").replace("\\", "_")
                    self._save_csv(videos, OUTPUT_DIR / f"收藏_{safe_name}_{timestamp}.csv")
        if history:
            self._save_csv(history, OUTPUT_DIR / f"观看历史_{timestamp}.csv")
        if work:
            self._save_csv(work, OUTPUT_DIR / f"我的作品_{timestamp}.csv")

        # 打印汇总
        print("\n" + "=" * 50)
        print("[汇总] 采集结果:")
        print(f"  喜欢: {len(liked or [])} 个视频")
        if favorites_flat:
            total_fav = sum(len(v) for v in favorites_flat.values())
            print(f"  收藏: {total_fav} 个视频（{len(favorites_flat)} 个收藏夹）")
            for name, vids in favorites_flat.items():
                print(f"    - {name}: {len(vids)} 个")
            if favorites_tree:
                print(f"  收藏夹层级: 共 {self._count_nodes(favorites_tree)} 个节点")
        print(f"  观看历史: {len(history or [])} 条")
        print(f"\n  输出目录: {OUTPUT_DIR.resolve()}")

    @staticmethod
    def _save_csv(videos, filepath):
        """保存视频列表为 CSV"""
        if not videos:
            return
        fieldnames = list(videos[0].keys())
        with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(videos)
        print(f"[保存] CSV: {filepath}")

    def close(self):
        """关闭浏览器"""
        if self.context:
            self.context.close()
        if self.playwright:
            self.playwright.stop()
        print("\n[结束] 浏览器已关闭")


def main():
    parser = argparse.ArgumentParser(description="抖音个人数据采集工具")
    parser.add_argument("--only", choices=["like", "favor", "history", "post"],
                        help="只采集指定类型（like=喜欢, favor=收藏, history=观看历史, post=我的作品）")
    parser.add_argument("--flat-favorites", action="store_true",
                        help="收藏使用旧扁平 DOM 方式采集（默认采集收藏夹层级树）")
    parser.add_argument("--headless", action="store_true",
                        help="无头模式运行（需已登录过）")
    parser.add_argument("--max-scroll", type=int, default=MAX_SCROLLS,
                        help=f"每个分类最大滚动次数（默认{MAX_SCROLLS}）")
    parser.add_argument("--folder", default=None,
                        help="收藏采集定向夹名，逗号分隔（如 --folder 家装,露营；默认采全部）")
    args = parser.parse_args()

    only_folders = [x.strip() for x in args.folder.split(",") if x.strip()] \
        if args.folder else None
    scraper = DouyinScraper(headless=args.headless, max_scrolls=args.max_scroll,
                            only_folders=only_folders)

    try:
        scraper.start()
        scraper.ensure_login()
        scraper.get_sec_uid()

        liked = favorites = history = work = None

        if args.only in (None, "like"):
            liked = scraper.scrape_liked()

        if args.only in (None, "favor"):
            favorites = scraper.scrape_favorites(flat=args.flat_favorites)

        if args.only in (None, "history"):
            history = scraper.scrape_history()

        if args.only in (None, "post"):
            work = scraper.scrape_work()

        scraper.save_data(liked=liked, favorites=favorites, history=history, work=work)

    except KeyboardInterrupt:
        print("\n[中断] 用户中断，正在保存已采集数据...")
    except Exception as e:
        print(f"\n[错误] 运行出错: {e}")
        import traceback
        traceback.print_exc()
    finally:
        scraper.close()


if __name__ == "__main__":
    main()
