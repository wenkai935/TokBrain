#!/usr/bin/env python3
# 浏览器桥：用真实 Chrome 借抖音自己的 JS 算出带签名的播放直链，再交给 yt-dlp 下载。
# 为什么这么绕：yt-dlp 单独跑时签名靠自己在 Python 里逆推抖音算法（易崩）；
# 本脚本让抖音亲妈 JS 在真实浏览器里算签名，把"已经放行"的直链抠出来，彻底绕开签名脆弱点。
#
# 用法:
#   python bridge_douyin.py <index 0-based>      # 单条测试
#   python bridge_douyin.py all [limit N]         # 批量全部（limit 可限制条数做小批量）
#   python bridge_douyin.py all 10               # 先跑前 10 条试水
#   python bridge_douyin.py all 0 --headless      # 0=不限制
#
# 前置:
#   - cookies.txt (Netscape 格式) 统一放在 TokBrain 的 data/cookies/douyin.txt,
#     由设置页「Cookie 库」上传；脚本也兼容旧的 D:/Temp/downloads/douyin_cookies.txt
#   - 本机已装 Chrome (脚本用 channel="chrome" 驱动, 不另下 chromium)
#   - yt-dlp + ffmpeg 已在位 (见 dl_feishu_douyin.py 同款路径)
import subprocess, sys, json, os, time, re
from pathlib import Path
from shutil import which

# 项目根：本文件位于 <root>/tools/douyin_bridge/bridge_douyin.py
_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])


def _find_python():
    # 优先用项目自身的 .venv（下载依赖已装在那里），否则退化到当前解释器。
    venv_py = os.path.join(_PROJECT_ROOT, ".venv", "Scripts", "python.exe")
    if os.path.exists(venv_py):
        return venv_py
    return sys.executable


def _find_chrome():
    env = os.environ.get("CHROME_PATH")
    if env and os.path.exists(env):
        return env
    for c in (
        r"C:/Program Files/Google/Chrome/Application/chrome.exe",
        r"C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%/Google/Chrome/Application/chrome.exe"),
    ):
        if os.path.exists(c):
            return c
    found = which("chrome") or which("chrome.exe")
    return found or r"C:/Program Files/Google/Chrome/Application/chrome.exe"


def _find_ffmpeg_dir():
    env = os.environ.get("FFMPEG_BIN")
    if env:
        d = env if os.path.isdir(env) else os.path.dirname(env)
        if os.path.exists(os.path.join(d, "ffmpeg.exe")):
            return d
    found = which("ffmpeg")
    if found:
        return os.path.dirname(found)
    for c in (
        r"D:/code/ffmpeg-2026-08-27-git-a6f573a1db-full_build/bin",
        r"C:/ffmpeg/bin",
        r"D:/ffmpeg/bin",
    ):
        if os.path.exists(os.path.join(c, "ffmpeg.exe")):
            return c
    return r"D:/code/ffmpeg-2026-08-27-git-a6f573a1db-full_build/bin"


PY = _find_python()
CHROME = _find_chrome()
_FFMPEG_DIR = _find_ffmpeg_dir()
FFMPEG = _FFMPEG_DIR
FFPROBE = os.path.join(_FFMPEG_DIR, "ffprobe.exe")
FFMPEG_EXE = os.path.join(_FFMPEG_DIR, "ffmpeg.exe")
# 统一 Cookie 库：与 TokBrain 设置页共用同一份文件，不再各存一份。
# 环境变量 TOKBRAIN_COOKIE_DIR 可覆盖默认位置。
COOKIE_DIR = os.environ.get("TOKBRAIN_COOKIE_DIR") or os.path.join(_PROJECT_ROOT, "data", "cookies")
LEGACY_COOKIES = os.path.join(os.path.expanduser("~"), "Downloads", "douyin_cookies.txt")  # 旧导出兜底（可选）


def cookie_source():
    """当前生效的 cookies.txt：统一库优先，旧导出兜底（迁移期免手工搬文件）。"""
    for path in (os.path.join(COOKIE_DIR, "douyin.txt"), LEGACY_COOKIES):
        if os.path.exists(path):
            return path
    return os.path.join(COOKIE_DIR, "douyin.txt")


COOKIES = cookie_source()
LINKS = os.path.join(_PROJECT_ROOT, "data", "douyin", "links.json")
OUTDIR = os.path.join(_PROJECT_ROOT, "data", "douyin", "downloads")
os.makedirs(OUTDIR, exist_ok=True)
# 严格的抖音媒体 CDN——只认这些, 杜绝 ibytedapm/bytescm/bytetos/amemv 等
# SDK/API 域名误判(amemv 是抖音 API 主机, 绝不能当媒体)
STRICT_MEDIA_HOSTS = ("douyinvod", "muscdn", "v3-dy", "bytecdn", "ixigua")
# 抖音的"网页"域名——这些永远不是媒体文件, 即使含 douyin 字样也要排除
PAGE_HOSTS = ("douyin.com", "iesdouyin.com", "v.douyin.com")
# 直播投递专用主机/关键字——只出现在真直播"播放"流里; 既不当 VOD 媒体, 也用于判定整页是直播
LIVE_HOSTS = ("douyinliving.com", "pull-flv", "pull-hls", "pull-lls")
# 抖音 API/SDK 主机——永远不是媒体文件(amemv/snssdk 是接口, webcast. 是直播接口子域);
# 注意: 这些只排除出媒体候选, 不能据此判"整页是直播"(VOD 页也会侧载 webcast 接口)
API_HOSTS = ("amemv", "snssdk", "webcast.")
# 伪媒体路径(海报/封面/缩略图/头像)——即使命中媒体 CDN 也排除, 绝不下载
NON_VIDEO_PATHS = ("/logo/", "/poster/", "/cover/", "/thumb", "/avatar/", "/preview/")

# 页面内捕获钩子: 复刻 aix 扩展的做法——在抖音播放器内部 patch fetch/XHR/video.src,
# 把媒体直链"自己报上来"。Playwright 的 response 事件对 SW/MSE 通道是瞎的, 这个才抓得到。
MEDIA_HOOK = r"""
(function(){
  try {
    window.__mediaCapture = window.__mediaCapture || [];
    function log(u){
      if(!u || typeof u !== 'string') return;
      var ul = u.toLowerCase();
      // 排除 logo/海报/封面/缩略图/头像等伪媒体(它们有时也在 douyinvod 上, 但非视频)
      if(/\/logo\/|\/poster\/|\/cover\/|\/thumb|\/avatar\/|\/preview\//.test(ul)) return;
      if(/m3u8|\.mp4|\.ts|douyinvod|muscdn|v3-dy|bytecdn|ixigua/i.test(ul)){
        window.__mediaCapture.push(u);
      }
    }
    var _f = window.fetch;
    if(_f){ window.fetch = function(){
      var i = arguments[0];
      var u = (typeof i === 'string') ? i : (i && i.url);
      log(u);
      return _f.apply(this, arguments);
    };}
    var _o = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function(){
      log(arguments[1]);
      return _o.apply(this, arguments);
    };
    var d = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'src');
    if(d && d.configurable && d.set){
      Object.defineProperty(HTMLMediaElement.prototype, 'src', {
        configurable: true,
        get: function(){ return d.get.call(this); },
        set: function(v){ log(v); return d.set.call(this, v); }
      });
    }
  } catch(e){}
})();
"""

def is_page_url(u):
    u = u.lower()
    return ("/share/video" in u or "douyin.com/video/" in u
            or "iesdouyin.com" in u or "v.douyin.com/" in u)

HEADLESS = "--headless" in sys.argv
if HEADLESS:
    sys.argv.remove("--headless")

# ---------- cookie 解析 (Netscape) ----------
def load_cookies(path):
    cookies = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            p = line.split("\t")
            if len(p) < 7:
                continue
            domain, _flag, path_, secure, expiry, name, value = p[:7]
            c = {"name": name, "value": value,
                 "domain": domain, "path": path_}
            if secure.lower() in ("true", "1"):
                c["secure"] = True
            if expiry and expiry not in ("0", "-1", ""):
                try:
                    c["expires"] = float(expiry)
                except ValueError:
                    pass
            cookies.append(c)
    return cookies

# ---------- 从 JSON 递归抠播放地址 ----------
def is_real_media(u):
    """严格判定: 必须是真正的媒体直链, 排除一切抖音'网页'/SDK/监控/直播域名"""
    if not isinstance(u, str) or "http" not in u:
        return False
    ul = u.lower()
    if any(p in ul for p in PAGE_HOSTS):
        return False
    # 直播投递主机: 哪怕带 .m3u8/.flv 也是直播流, 绝不当 VOD 媒体
    if any(h in ul for h in LIVE_HOSTS):
        return False
    # API/SDK 主机(amemv/snssdk/webcast.) 永远不是媒体文件
    if any(h in ul for h in API_HOSTS):
        return False
    # 伪媒体路径(海报/封面/缩略图/头像) 排除
    if any(p in ul for p in NON_VIDEO_PATHS):
        return False
    # 明确的媒体扩展, 或在严格媒体 CDN 上
    if ".m3u8" in ul or ".mp4" in ul or ".ts" in ul:
        return True
    if any(h in ul for h in STRICT_MEDIA_HOSTS):
        return True
    return False

def looks_media(u):
    return is_real_media(u)

def dig_urls(obj, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str):
                if looks_media(v):
                    out.append(v)
            elif isinstance(v, list):
                for it in v:
                    if isinstance(it, str):
                        if looks_media(it):
                            out.append(it)
                    else:
                        dig_urls(it, out)
            else:
                dig_urls(v, out)
    elif isinstance(obj, list):
        for it in obj:
            dig_urls(it, out)

def pick_best(urls, playlists):
    urls = list(dict.fromkeys(urls))  # 去重保序
    # 1) 优先 HLS 播放列表(mpegurl content-type 抓到的), yt-dlp 吃它最稳
    plset = set(playlists)
    pl = [u for u in urls if u in plset]
    if pl:
        pl.sort(key=lambda u: (0 if "manifest" in u.lower() else 1, u))
        return pl[0]
    # 2) 其次 mp4, 3) 再次严格媒体 CDN 直链, 网页一律 9 分排除
    def score(u):
        ul = u.lower()
        if any(p in ul for p in PAGE_HOSTS):
            return 9
        if any(h in ul for h in LIVE_HOSTS):   # 直播投递主机, 一律排除
            return 9
        if any(h in ul for h in API_HOSTS):    # API/SDK 主机, 一律排除
            return 9
        if any(p in ul for p in NON_VIDEO_PATHS):  # 海报/封面/缩略图, 排除
            return 9
        # 首选专用 VOD CDN(douyinvod)——已实测能稳定拉到完整真视频(224M/673M 实证)
        if "douyinvod" in ul:
            return 0
        if ".mp4" in ul: return 1
        if ".m3u8" in ul: return 2
        if any(h in ul for h in STRICT_MEDIA_HOSTS): return 3
        # 静态/特效 CDN(douyinstatic/byteeffecttos 等)放最后, 可能是通用素材而非本视频
        if "douyinstatic" in ul or "byteeffecttos" in ul or "effectcdn" in ul:
            return 4
        return 9
    cand = [u for u in urls if score(u) < 9]
    if not cand:
        return None
    cand.sort(key=score)
    return cand[0]

def dump_cookies_netscape(context, path):
    # 把浏览器上下文里的全部 cookie(含 douyinvod.com 会话 cookie) 导出为 Netscape,
    # 复刻 NeatDM 用 chrome.cookies.getAll 取登录态、原样重放授权请求的做法
    lines = ["# Netscape HTTP Cookie File",
             "# https://curl.haxx.se/rfc/cookie_spec.html"]
    for c in context.cookies():
        domain = c.get("domain", "")
        if not domain.startswith("."):
            domain = "." + domain
        path_ = c.get("path", "/") or "/"
        secure = "TRUE" if c.get("secure") else "FALSE"
        exp = c.get("expires")
        exp = str(int(exp)) if exp else "0"
        lines.append("\t".join([domain, "TRUE", path_, secure, exp,
                                c.get("name", ""), c.get("value", "")]))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

def probe_streams(url):
    """快速探活远程直链: 确认是否含 video + audio 流(避免下到纯音频/纯视频分片)"""
    try:
        r = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "stream=codec_type",
             "-of", "csv=p=0",
             "-headers", "Referer: https://www.douyin.com/\r\n"
                        "Origin: https://www.douyin.com\r\n",
             url],
            capture_output=True, text=True, timeout=30)
        out = r.stdout + r.stderr
        return ("video" in out), ("audio" in out)
    except Exception:
        return True, True   # 探活失败则乐观假定带音轨, 不误杀

def run_yt_dlp(recid, url, cookie_path=None, out_tpl=None):
    # 每次现取，保证中途在设置页换了 Cookie 能立刻用上新的那份
    cookie_path = cookie_path or cookie_source()
    # 只用飞书记录 ID 命名, 避免 yt-dlp 把超长 URL 当文件名(Windows 260 字符上限报错)
    if out_tpl is None:
        out_tpl = os.path.join(OUTDIR, f"{recid}.%(ext)s")
    # 该直链已带签名; CDN(douyinvod.com) 要 Referer+Origin+同会话 cookie
    # 这正是 Neat Download Manager 的做法: 用浏览器登录态 cookie 重放授权请求
    args = [PY, "-m", "yt_dlp", url, "-o", out_tpl,
            "--ffmpeg-location", FFMPEG, "--merge-output-format", "mp4",
            "--no-playlist", "--restrict-filenames", "--no-warnings",
            "-R", "3", "--fragment-retries", "3",
            "--add-header", "Referer:https://www.douyin.com/",
            "--add-header", "Origin:https://www.douyin.com",
            "--cookies", cookie_path]
    return subprocess.run(args).returncode

def main():
    cookie_path = cookie_source()
    if not os.path.exists(cookie_path):
        print(f"[错误] 找不到 cookie 文件 {cookie_path}")
        print("请在 TokBrain 设置页「Cookie 库」上传抖音 cookies.txt，")
        print(f"或把 Netscape 格式 cookie 放到 {LEGACY_COOKIES}。")
        sys.exit(1)
    if not os.path.exists(LINKS):
        print(f"[错误] 找不到链接清单 {LINKS}，先跑飞书抓取。")
        sys.exit(1)
    links = json.load(open(LINKS, encoding="utf-8"))
    cookies = load_cookies(cookie_path)
    print(f"[info] cookie 文件={cookie_path} 条数={len(cookies)}, "
          f"链接条数={len(links)}, headless={HEADLESS}")

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=HEADLESS,
                                     args=["--disable-blink-features=AutomationControlled",
                                           "--autoplay-policy=no-user-gesture-required"])
        context = browser.new_context()
        context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        context.add_cookies(cookies)
        # 每条记录单独新建页面: 复用同一标签页时, 上一条视频的预取/自动连播会在
        # 下一条里抢先请求它的 aweme/detail, 造成"串片"(下到上一条的视频)。
        page = None

        # 捕获用的共享列表(在 on_response / capture_once 间共享)
        captured = []
        playlists = []   # HLS 播放列表(按 content-type=mpegurl 捕获)
        all_urls = []    # 原始网络日志(全记录, 用于排查)
        live_signals = []  # 直播流信号
        muxed_urls = []  # 直连 CDN 的 muxed 播放直链: (data_size, url, aweme_id)
        audio_urls = []  # bit_rate_audio 的纯音频直链(兜底: 万一只拿到视频分片可补音轨)
        state = {"target_id": None}   # 目标作品 id(从落地页 URL 解析), 用于过滤相关视频污染

        def _extract_play(body):
            """从 aweme/detail 返回体抠出'自带音轨的 muxed 直链'(含各级画质)。
            抖音 web 用 MSE 把视频/音频拆成两路分片(media-video/media-audio),
            单挑视频分片必然没声音; 但 play_addr/bit_rate[*].play_addr 是直连 CDN 的
            完整 muxed MP4(自带音轨), 浏览器自己请求的成功、不会被风控。
            收集所有画质直链(带 data_size), 后续按'最大体积=最高画质'挑选, 既能拿到
            HD/4K, 又能天然避开自动连播/相关视频那种短小错片。"""
            try:
                aw = body.get("aweme_detail") or {}
                v = aw.get("video") or {}
            except Exception:
                return
            aid = str(aw.get("aweme_id") or "")
            cands = []
            # 1) play_addr_h264 / play_addr: 直连 CDN 的 muxed MP4, 自带音轨
            for key in ("play_addr_h264", "play_addr"):
                obj = v.get(key)
                if isinstance(obj, dict):
                    for u in (obj.get("url_list") or []):
                        if isinstance(u, str) and "http" in u \
                                and "douyin.com/aweme/v1/play" not in u:
                            cands.append((obj.get("data_size") or 0, u))
            # 2) bit_rate 各级画质(更高分辨率, 通常也是 muxed)
            for br in (v.get("bit_rate") or []):
                if not isinstance(br, dict):
                    continue
                obj = br.get("play_addr") or {}
                if isinstance(obj, dict):
                    for u in (obj.get("url_list") or []):
                        if isinstance(u, str) and "http" in u \
                                and "douyin.com/aweme/v1/play" not in u:
                            cands.append((obj.get("data_size") or 0, u))
            for sz, u in cands:
                muxed_urls.append((sz, u, aid))
            # 3) 兜底音频: bit_rate_audio 的 audio_meta.url_list(纯音频源, 万一只拿到视频分片可补)
            for ba in (v.get("bit_rate_audio") or []):
                if not isinstance(ba, dict):
                    continue
                am = ba.get("audio_meta") or {}
                for u in (am.get("url_list") or []):
                    if isinstance(u, str) and "http" in u:
                        audio_urls.append(u)

        def on_response(resp):
            u = resp.url
            all_urls.append(u)   # 原始网络日志, 全记录用于排查
            ul = u.lower()
            ct = (resp.headers.get("content-type") or "").lower()   # 提前取, 直播精判要用
            # 直播流判定: 只认"真直播播放投递"主机(douyinliving.com / pull-flv/-hls/-lls)。
            # 这些只会在真正播放直播流时出现; VOD 页一律不请求它们(它请求 douyinvod),
            # 所以据此判"整页是直播"最稳, 不会把侧载 webcast 接口的 VOD 页误杀。
            if any(h in ul for h in LIVE_HOSTS):
                live_signals.append(u)
                return
            # aweme/detail 返回体里就有"自带音轨的 muxed 直链", 浏览器自己请求的成功、
            # 不会被风控; 抠出来优先用, 比抓 MSE 分片稳且天然带声音
            if "/aweme/v1/web/aweme/detail/" in ul:
                try:
                    _extract_play(json.loads(resp.text()))
                except Exception:
                    pass
                return
            # 其余(API/网页/水印/钻石/SDK 等)都不是媒体, 也不在此判直播, 直接放行给后续逻辑
            # 1) 直接的媒体响应 (按 content-type, 最可靠)
            if "video" in ct or "mpegurl" in ct or "/mp4" in ct:
                captured.append(u)
                if "mpegurl" in ct:
                    playlists.append(u)   # HLS 播放列表优先
                return
            # 2) CDN 媒体直链 (严格排除网页/SDK/直播域名)
            if is_real_media(u):
                captured.append(u)
                return
            # 3) aweme 接口: 只解析其 JSON 抠里面的播放地址, 自身不当媒体
            if "/aweme/" in u:
                try:
                    body = resp.json()
                    dig_urls(body, captured)
                except Exception:
                    pass

        # 选择要处理的索引
        if len(sys.argv) > 1 and sys.argv[1].lower() == "all":
            limit = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else len(links)
            idxs = range(min(limit, len(links)))
        else:
            idxs = [int(sys.argv[1])] if len(sys.argv) > 1 else [0]

        MAX_RETRY = 3   # 抖音直链签名时效极短, 下载失败就重新抓新鲜直链再试

        def capture_once(link, recid):
            """打开链接并抓直链, 返回 (best_url_or_None, is_live)"""
            captured.clear(); playlists.clear(); all_urls.clear(); live_signals.clear()
            muxed_urls.clear(); audio_urls.clear()
            state["target_id"] = None
            try:
                page.evaluate("window.__mediaCapture = []")   # 清空页面内钩子缓冲
            except Exception:
                pass
            try:
                page.goto(link, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                print(f"  [goto 失败] {e}")
                return None, False
            # 记录目标作品 id: 落地页 URL 里的 /video/<id> 就是本条链接真正指向的作品。
            # 抖音详情页会顺带请求"相关视频"的 aweme/detail, 用它过滤掉错片污染。
            try:
                mu = re.search(r"/(?:share/)?video/(\d+)", page.url or "")
                if mu:
                    state["target_id"] = mu.group(1)
            except Exception:
                state["target_id"] = None
            print(f"  [目标作品] {state['target_id'] or '未知'}")

            def _has_target():
                """是否已拿到'属于目标作品'的 muxed 直链(无 id 信息时按拿到算)"""
                tid = state.get("target_id")
                return any((not tid) or (not aid) or aid == tid
                           for _sz, _u, aid in muxed_urls)

            # 等 aweme/detail 里的 muxed 直链(自带音轨的完整 MP4)。
            # 注意: 绝不能因为"MSE 分片已出现"就提前退出——分片 1~2 秒就有,
            # 而 detail 要更晚; 提前退出会退化成下到几十秒的分片(会丢声音/丢大段内容)。
            # 拿到直链后再多等 3 秒, 让 bit_rate 全画质档位都到齐, 好挑最高画质。
            t0 = time.time()
            got_at = None
            while time.time() - t0 < 22 and not live_signals:
                if _has_target():
                    if got_at is None:
                        got_at = time.time()
                    elif time.time() - got_at >= 3:
                        break
                time.sleep(1)
            # 只收到"别的作品"的直链 = 串片(上一条的预取/相关推荐), 重新加载再等一轮
            if muxed_urls and not _has_target() and not live_signals:
                print("  [串片] 只收到非目标作品的直链, 重新加载一次")
                muxed_urls.clear(); audio_urls.clear()
                try:
                    page.goto(link, wait_until="domcontentloaded", timeout=30000)
                except Exception:
                    pass
                t1 = time.time()
                while time.time() - t1 < 15 and not _has_target() and not live_signals:
                    time.sleep(1)
            # 读取页面内钩子捕获的直链(复刻 aix, 能抓 SW/MSE 通道)
            try:
                hooked = page.evaluate("window.__mediaCapture || []")
                if hooked:
                    captured.extend(hooked)
            except Exception:
                pass
            # 最后兜底: 直接读 <video> 元素当前的播放源
            try:
                vsrc = page.evaluate(
                    "var v=document.querySelector('video');"
                    "return v?(v.currentSrc||v.src||''):''")
                vsl = (vsrc or "").lower()
                if vsrc and not any(h in vsl for h in LIVE_HOSTS) \
                        and ("m3u8" in vsl or ".mp4" in vsl
                             or "douyinvod" in vsl or "tos" in vsl):
                    captured.append(vsrc)
            except Exception:
                pass
            # 落盘调试
            try:
                with open(os.path.join(OUTDIR, f"{recid}_captured.txt"), "w", encoding="utf-8") as fh:
                    mux_lines = [f"{sz}\t{aid}\t{u}" for sz, u, aid in muxed_urls]
                    fh.write("# muxed(自带音轨的直连 CDN 直链, 带 data_size):\n"
                             + "\n".join(mux_lines)
                             + "\n# playlists(mpegurl):\n" + "\n".join(playlists)
                             + "\n# all captured:\n" + "\n".join(captured))
                with open(os.path.join(OUTDIR, f"{recid}_all.txt"), "w", encoding="utf-8") as fh:
                    fh.write("\n".join(all_urls))
            except Exception:
                pass
            if live_signals:
                return None, True, None
            # 优先用 aweme 里的 muxed 直链: 按 data_size 降序挑最高画质, 并用 ffprobe
            # 校验确带 video+audio(避开纯音频版/视频分片, 也避开短小相关视频错片)
            if muxed_urls:
                tid = state.get("target_id")
                # 只保留"目标作品"的直链; 无 id 信息时不过滤。相关视频的直链一律丢掉,
                # 否则会下到 59 秒的相关推荐错片(而不是本条应有的长视频)。
                pool = [(sz or 0, u) for sz, u, aid in muxed_urls
                        if not tid or not aid or aid == tid]
                if not pool:
                    print(f"  [警告] 拿到的直链都不属于目标作品 {tid}, 丢弃后回退 MSE 分片")
                seen, cands = set(), []
                for sz, u in pool:
                    if u not in seen:
                        seen.add(u); cands.append((sz, u))
                cands.sort(key=lambda x: x[0], reverse=True)
                chosen, need_audio = None, None
                for sz, u in cands[:6]:
                    hv, ha = probe_streams(u)
                    if hv and ha:
                        chosen = u; break
                    if hv and not ha and not chosen:
                        chosen, need_audio = u, audio_urls[0] if audio_urls else None
                if chosen:
                    return chosen, False, need_audio
            # 退回: 抓 MSE 分片(视频分片, 可能无音轨)
            return pick_best(captured, playlists), False, None

        fails = []
        skipped_live = []
        for i in idxs:
            recid, link = links[i]
            # 每条开独立标签页, 用完即弃: 复用同页会让上一条的预取/自动连播串到下一条
            page = context.new_page()
            page.add_init_script(MEDIA_HOOK)
            page.on("response", on_response)
            print(f"\n=== [#{i}] {recid} -> {link} ===")
            try:
                best, is_live, audio_url = capture_once(link, recid)
                if is_live:
                    print("  [直播流] 跳过(抖音直播需单独 FLV 处理, 不在本次范围内)")
                    skipped_live.append(i)
                    continue
                if not best:
                    print(f"  [调试] 共捕获 {len(captured)} 条响应, 前 8 条:")
                    for d in captured[:8]:
                        print("    -", d[:120])
                    print("  [未捕获到播放直链] 可能遇到验证码/风控, 截图留存")
                    try:
                        page.screenshot(path=os.path.join(OUTDIR, f"{recid}_failed.png"))
                    except Exception:
                        pass
                    fails.append(i)
                    continue
                tag = "  [需补音轨合并]" if audio_url else ""
                print(f"  [捕获直链] {best[:90]}...{tag}")
                cookie_tmp = os.path.join(OUTDIR, f"{recid}_cookies.txt")
                dump_cookies_netscape(context, cookie_tmp)
                rc = 1
                if audio_url:
                    # 视频分片(无音轨) + 纯音频, 下载后 ffmpeg 合并
                    vtmp = os.path.join(OUTDIR, f"{recid}_v.mp4")
                    atmp = os.path.join(OUTDIR, f"{recid}_a.m4a")
                    rc = run_yt_dlp(recid, best, cookie_tmp, out_tpl=vtmp)
                    if rc == 0:
                        rc = run_yt_dlp(recid, audio_url, cookie_tmp, out_tpl=atmp)
                    if rc == 0:
                        final = os.path.join(OUTDIR, f"{recid}.mp4")
                        rc = subprocess.run([FFMPEG_EXE, "-y", "-i", vtmp, "-i", atmp,
                                            "-c", "copy", "-movflags", "+faststart",
                                            final]).returncode
                    for t in (vtmp, atmp):
                        try: os.remove(t)
                        except Exception: pass
                else:
                    for attempt in range(1, MAX_RETRY + 1):
                        rc = run_yt_dlp(recid, best, cookie_tmp)
                        if rc == 0:
                            break
                        print(f"  [yt-dlp exit={rc}] 第{attempt}次失败, 重新抓新鲜直链重试")
                        best, is_live, audio_url = capture_once(link, recid)
                        if is_live:
                            skipped_live.append(i); break
                        if not best:
                            break
                        if audio_url:   # 重试后变成需补音轨, 转走合并分支
                            vtmp = os.path.join(OUTDIR, f"{recid}_v.mp4")
                            atmp = os.path.join(OUTDIR, f"{recid}_a.m4a")
                            rc = run_yt_dlp(recid, best, cookie_tmp, out_tpl=vtmp)
                            if rc == 0:
                                rc = run_yt_dlp(recid, audio_url, cookie_tmp, out_tpl=atmp)
                            if rc == 0:
                                final = os.path.join(OUTDIR, f"{recid}.mp4")
                                rc = subprocess.run([FFMPEG_EXE, "-y", "-i", vtmp, "-i", atmp,
                                                    "-c", "copy", "-movflags", "+faststart",
                                                    final]).returncode
                            for t in (vtmp, atmp):
                                try: os.remove(t)
                                except Exception: pass
                            break
                print(f"  [yt-dlp exit={rc}]")
                if rc != 0:
                    fails.append(i)
            finally:
                try:
                    page.remove_listener("response", on_response)
                except Exception:
                    pass
                try:
                    page.close()
                except Exception:
                    pass
                time.sleep(3)  # 限速, 降低风控概率

        browser.close()
        print(f"\n完成。失败 {len(fails)} 条: {fails}; 跳过直播 {len(skipped_live)} 条: {skipped_live}")

if __name__ == "__main__":
    main()
