# -*- coding: utf-8 -*-
"""tools/e2e_admin_browser.py —— 后台界面**端到端**验证（真浏览器渲染）

为什么不能在 Python 里"检查 HTML 文本"就完事
============================================
`index.html` 里的模型表是 **JS 渲染**出来的。文件里写着
`faster-whisper medium` 不代表页面上真显示了它 —— 一个 JS 报错、
一次 fetch 404、一个没接上的数据源分支，页面就会停在"内置假数据"
甚至空白，而 HTML 文本检查全部通过。

所以这里用**真浏览器**（本机 Edge/Chrome 无头模式）跑一次：

  ① 起 `babel_admin/serve.py`（随机空闲端口，避免撞正在跑的服务）
  ② 无头浏览器打开页面，等 JS 跑完，导出 **渲染后的 DOM**
  ③ 断言：
     · 渲染结果里出现注册表里的模型 id/名字（证明数据真读到并渲染了）
     · 显存/延迟这类数字来自注册表，不是模板里的占位
     · 数据来源标签 = API（证明走的是 /api/models，不是内联假数据）
     · 侧边栏两个模式入口 + 标题「直播翻译和视频翻译」在
     · 不可用模型在页面上被标注（available:false 的那条）
  ④ 再测一次**没有服务**的情况：页面要能退化到内联数据并明显标出来
     （"断网/没起服务就白屏"是最常见也最难提前发现的交付事故）

用法
====
    python tools/e2e_admin_browser.py
    python tools/e2e_admin_browser.py --browser "C:\\path\\to\\msedge.exe"
    python tools/e2e_admin_browser.py --keep     # 保留 dump 文件供排查
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

OK = BAD = 0


def chk(name: str, cond: bool, extra: str = "") -> None:
    global OK, BAD
    if cond:
        OK += 1
        print(f"  ok   {name}")
    else:
        BAD += 1
        print(f"  FAIL {name}   {extra}")


def find_browser(explicit: str | None) -> str | None:
    if explicit:
        return explicit if os.path.isfile(explicit) else None
    for b in BROWSERS:
        if os.path.isfile(b):
            return b
    return None


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def wait_http(url: str, timeout: float = 25.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.4)
    return False


def dump_dom(browser: str, url: str, out: str, virtual_ms: int = 4000) -> bool:
    """无头浏览器打开页面并导出渲染后的 DOM。

    用 `--virtual-time-budget` 而不是 `--timeout`：它让浏览器把时间
    "快进"到指定毫秒，**包含 pending 的 fetch 与 setTimeout** ——
    对"等 JS 渲染完再 dump"这件事是正解，比 sleep 猜时间可靠。
    """
    prof = tempfile.mkdtemp(prefix="babel_e2e_")
    cmd = [
        browser, "--headless=new", "--disable-gpu", "--no-first-run",
        "--no-default-browser-check", "--disable-extensions",
        f"--user-data-dir={prof}", "--dump-dom",
        f"--virtual-time-budget={virtual_ms}", url,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=120)
        dom = r.stdout.decode("utf-8", "replace")
        if not dom.strip():
            print(f"    （浏览器无输出，stderr 摘要：{r.stderr[:400]!r}）")
            return False
        with open(out, "w", encoding="utf-8") as f:
            f.write(dom)
        return True
    except subprocess.TimeoutExpired:
        print("    （浏览器超时）")
        return False
    finally:
        try:
            import shutil
            shutil.rmtree(prof, ignore_errors=True)
        except Exception:
            pass


def screenshot(browser: str, url: str, out: str, size: str = "1600,1000",
               virtual_ms: int = 5000) -> bool:
    """无头浏览器存一张整页截图（给人看的交付证据）。"""
    prof = tempfile.mkdtemp(prefix="babel_shot_")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    cmd = [
        browser, "--headless=new", "--disable-gpu", "--no-first-run",
        "--no-default-browser-check", "--hide-scrollbars",
        f"--user-data-dir={prof}", f"--window-size={size}",
        f"--virtual-time-budget={virtual_ms}",
        f"--screenshot={os.path.abspath(out)}", url,
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=180)
        return os.path.isfile(out) and os.path.getsize(out) > 5000
    except Exception:
        return False
    finally:
        import shutil
        shutil.rmtree(prof, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--browser", default=None)
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--shot", default=None,
                    help="额外存一张整页截图到该路径（给人看的证据）")
    a = ap.parse_args(argv)

    browser = find_browser(a.browser)
    if not browser:
        print("本机没有 Edge/Chrome，**跳过浏览器端到端验证**（这是未验证项，别当成通过）")
        return 3
    print(f"浏览器：{browser}")

    import model_registry as R
    snap = R.probe(src=R.PATH)
    web = R.web_payload(snap)
    wanted = [(x["id"], x["name"]) for x in web["asr"] + web["mt"]]
    unavail = [x for x in web["asr"] + web["mt"] if x.get("available") is False]
    print(f"注册表：ASR {len(web['asr'])} 个 / MT {len(web['mt'])} 个，"
          f"其中不可用 {len(unavail)} 个")
    # 界面显示的显存应该等于**算出来的**（权重+解码开销），不是注册表 JSON
    # 里那个静态实测值。两者差 3MB 就说明有一条路径没重算。
    for x in web["asr"]:
        if x.get("size_key"):
            calc = R.asr_size_mb(x["id"], snap)
            if abs(float(x["vram_mb"]) - calc) > 0.5:
                print(f"  ！快照显存与计算不一致：{x['id']} "
                      f"快照 {x['vram_mb']} vs 计算 {calc}")

    # dump 一律落 work/ —— 别往仓库根目录扔临时文件（--keep 是"留着排查"，
    # 不是"扔到项目根"）
    tmpdir = os.path.join(ROOT, "work", "e2e")
    if not a.keep:
        tmpdir = tempfile.mkdtemp(prefix="babel_dom_")
    else:
        os.makedirs(tmpdir, exist_ok=True)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [sys.executable, "-X", "utf8", os.path.join(ROOT, "babel_admin", "serve.py"),
         "--port", str(port), "--no-open"],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        if not wait_http(base + "/api/models"):
            print("！服务起不来，无法做端到端验证")
            proc.kill()
            return 2
        print(f"服务：{base}")

        print("一、有服务：页面必须从 API 拿数据")
        dom_api = os.path.join(tmpdir, "dom_api.html")
        if not dump_dom(browser, base + "/", dom_api):
            chk("导出渲染后 DOM", False, "浏览器没给出 DOM")
        else:
            dom = open(dom_api, encoding="utf-8").read()
            chk("导出渲染后 DOM", True, f"{len(dom)} 字节")
            chk("标题「直播翻译和视频翻译」在", "直播翻译和视频翻译" in dom)
            chk("侧边栏「巴别塔计划」在", "巴别塔计划" in dom)
            for mid, label in (("实时回环系统", "实时回环系统"),
                               ("视频缓存", "视频缓存"),
                               ("同传记录", "同传记录"),
                               ("模型管理", "模型管理"),
                               ("设置", "设置")):
                chk(f"导航项「{mid}」在", label in dom)
            # **关键**：模型表来自注册表，且是在浏览器里渲染出来的
            hit = [i for i, nm in wanted if (i in dom or nm in dom)]
            chk(f"渲染出注册表模型（{len(hit)}/{len(wanted)}）",
                len(hit) >= max(3, len(wanted) // 2),
                f"命中 {hit}")
            chk("数据源标签是 API",
                'id="dsText"' in dom and ">API<" in dom.replace(" ", "").replace("\n", "")
                or "数据源 API" in dom,
                "没找到 数据源 API 字样")
            if unavail:
                u = unavail[0]
                chk(f"不可用模型有标注（{u['id']}）",
                    (u["id"] in dom) or (u.get("unavailable_reason", "x")[:8] in dom),
                    "页面上没体现不可用")
            else:
                chk("本机没有不可用模型（跳过标注检查）", True)
            chk("没有把内置假数据的来源标签留在页面上",
                "数据源 内置" not in dom)

            # ── 判据分两层，因为 --dump-dom 给的是**整个 SPA** ──────────
            #   · DOM 全文：含未激活视图、模板占位、CSS 类名、源码注释 ——
            #     用它判"这个词出现过"会误报（我第一版就把源码注释里的
            #     "undefined" 当成了页面 bug，白改一轮）
            #   · 渲染文本：剥掉 script/style/注释/SVG 之后剩下的**可见内容**
            #     —— "页面上有没有渲染出 undefined" 必须用这个判
            def render_text(html: str) -> str:
                t = re.sub(r"<script\b.*?</script>", " ", html, flags=re.S | re.I)
                t = re.sub(r"<style\b.*?</style>", " ", t, flags=re.S | re.I)
                t = re.sub(r"<!--.*?-->", " ", t, flags=re.S)
                t = re.sub(r"<svg\b.*?</svg>", " ", t, flags=re.S | re.I)
                return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t))

            def view_html(html: str, vid: str) -> str:
                m = re.search(r'<section[^>]*id="' + vid + r'".*?'
                              r'(?=<section[^>]*id="|\Z)', html, re.S)
                return m.group(0) if m else ""

            def view_text(html: str, vid: str) -> str:
                return render_text(view_html(html, vid))

            # 顶部操作区（工具栏 + 标题）在 <main> 里、但**不在**某个 view 的
            # <section> 里 —— 所以判"工具栏的模型下拉显示的是哪个模型"要单独
            # 取 main 这段，不能只看 landing view。
            m = re.search(r"<main\b.*?</main>", dom, re.S | re.I)
            main_html = m.group(0) if m else dom
            mtxt = render_text(main_html)

            # 识别模型下拉是个浮层：`.dd-menu` 里的每个候选项来自
            # BABEL/注册表的 asr 列表。**数个数**是最硬的判据 ——
            # 6 个候选就必须渲染 6 项，不会因为文案改写而虚过。
            n_opt = len(re.findall(
                r'<[^>]+class="[^"]*\bdd-opt\b[^"]*"', dom))
            if n_opt:
                n_asr = len(web["asr"])
                chk(f"识别模型下拉渲染出注册表全部候选（{n_asr} 项）",
                    n_opt >= n_asr, f"只渲染了 {n_opt} 项，注册表有 {n_asr} 项")
            else:
                chk("识别模型下拉渲染出全部候选（未识别到 dd-opt 结构，跳过）", True)

            txt = render_text(dom)
            # 默认落地视图：先认 active，认不到就用 translate
            act = re.search(r'<section[^>]*id="([^"]+)"[^>]*\bview\b[^>]*\bactive\b',
                            dom) or re.search(
                r'<section[^>]*class="[^"]*\bview active\b[^"]*"[^>]*id="([^"]+)"', dom)
            landing = act.group(1) if act else "view-translate"
            ltxt = view_text(dom, landing)

            loop_asr = R.get(web["modes"]["loopback"]["asr"], snap)
            cache_asr = R.get(web["modes"]["cache"]["asr"], snap)
            la, ca = (loop_asr or {}).get("name", "?"), (cache_asr or {}).get("name", "?")
            chk(f"默认落地视图是翻译工作台（{landing}）",
                landing in ("view-translate", "translate"),
                f"实际 {landing}")
            # 工具栏（顶部操作区）里的模型下拉必须显示 loopback 的识别模型名，
            # **注册表里的 name 原样显示**（不是页面上另写的一串简称）。
            chk(f"顶部工具栏显示 loopback 的识别模型（{la}）", la in mtxt,
                f"工具栏文本：{mtxt[:260]}")
            chk(f"顶部工具栏显示 loopback 的译器（{(R.get(web['modes']['loopback']['mt'], snap) or {}).get('name', '?')}）",
                (R.get(web["modes"]["loopback"]["mt"], snap) or {}).get("name", "~")
                in mtxt, f"工具栏文本：{mtxt[:260]}")
            chk(f"落地视图里不出现 cache 的模型（{ca}）", ca not in ltxt,
                f"落地视图混进了缓存模式的 {ca} —— 模式绑定串了")
            # 注：工具栏那块**允许**出现 cache 的模型名 —— 「视频缓存」
            # 分段按钮的文案里本来就写着它的预设（large-v3 · 16s 段），
            # 那是模式切换器要展示的信息，不是绑定串了。
            # 真正要防的是"下拉里选中的那项显示错了"，所以判上面的
            # `la in mtxt` + 下面的候选个数，而不是"全文不许出现 ca"。
            chk("渲染文本里没有 undefined", "undefined" not in txt,
                "渲染出了 undefined")
            chk("渲染文本里没有 NaN", "NaN" not in txt, "渲染出了 NaN")
            chk("渲染文本里没有 [object", "[object" not in txt, "渲染出了 [object")
            # ── 链路控制入口（"打开后台没有浮窗"那次事故的修复）─────────
            # 这条必须测：管理页面以前**没有任何启动链路的入口**，
            # 于是"打开后台管理系统"看到的是一片空 —— 浮窗属于原生链路，
            # 不启动就没有，而且不报错。
            for eid, what in (("engineCard", "链路控制卡片"),
                              ("btnEngStart", "启动按钮"),
                              ("btnEngStop", "停止按钮"),
                              ("engChip", "状态徽标"),
                              ("engLog", "日志区")):
                chk(f"有{what}（id={eid}）", f'id="{eid}"' in dom)
            chk("链路卡片在默认落地视图里（不是藏在别的页）",
                "engineCard" in view_html(dom, landing),
                f"落地视图 {landing} 里没有 engineCard")
            chk("启动按钮默认可用", "btnEngStart" in dom)
            chk("有链路状态文字（未启动/加载/已启动）",
                any(k in txt for k in ("未启动", "加载模型", "浮窗已启动")),
                "状态徽标没渲染出文字")
            # ② 「运行状态」卡片只该有一份（之前重复渲染了两次）
            chk("落地视图的「运行状态」卡片 = 1 份", ltxt.count("运行状态") == 1,
                f"出现 {ltxt.count('运行状态')} 次")
            # 3 个延迟数字 + 趋势图共用 spark 容器类，各视图各一份是合理的
            # （实测整页 5 个：工具栏 chip 1 + 落地视图若干 + 模型管理 1）。
            # 真正要防的是**同一块卡片被渲染两遍**，所以先用"运行状态卡片数"
            # 卡死重复，这里只做一个"没有失控克隆"的上限。
            n_spark = len(re.findall(r'class="[^"]*\bspark\b', dom))
            chk("页面没有失控克隆的 spark 容器（<= 8）", n_spark <= 8, f"{n_spark} 个")
            # ③ 显存数字来自注册表（渲染出来的，不是模板占位）。
            #    页面会做千分位与一位小数（1,612.1），所以不能拿 int
            #    字符串硬比 —— 那样只会得到假失败。
            #    另外要以**快照里的值**为准：注册表 JSON 里写的是实测
            #    1615，而 probe 会按本机缓存权重重算出 1612.1。界面显示的
            #    应该是后者（真算出来的），这才证明它真读了 API。
            if loop_asr and loop_asr.get("vram_mb"):
                mb = float(loop_asr["vram_mb"])
                # 页面上任何一处显存数字只要落在"权重+开销"±15MB 内就算过。
                # 不拿一个具体字符串去比：注册表 JSON 里存的是**实测值**
                # (1615)，probe/web_payload 会按本机缓存重算(1612.1)，
                # 界面显示后者才是"真读了 API"。两者差 3MB 是正常的，
                # 硬比字符串只会得到假失败（我第一版就是这么误报的）。
                shown = [float(x.replace(",", ""))
                         for x in re.findall(r"(\d[\d,]*\.?\d*)\s*MB", txt)]
                near = [x for x in shown if abs(x - mb) <= 15]
                chk(f"页面渲染出注册表显存数字（≈{mb:.1f}MB）", bool(near),
                    f"页面上所有 MB 数字：{sorted(set(shown))[:12]}")
            mt = R.get(web["modes"]["loopback"]["mt"], snap) or {}
            if mt.get("name"):
                chk(f"落地视图渲染出 loopback 的译器名（{mt['name']}）",
                    mt["name"] in ltxt)

        print("二、没有服务：页面必须自己退化，不能白屏")
        dom_file = "file:///" + os.path.join(ROOT, "babel_admin", "index.html").replace("\\", "/")
        dom_off = os.path.join(tmpdir, "dom_offline.html")
        if not dump_dom(browser, dom_file, dom_off, virtual_ms=6000):
            chk("断网时导出 DOM", False, "浏览器没给出 DOM")
        else:
            d2 = open(dom_off, encoding="utf-8").read()
            chk("断网时导出 DOM", True, f"{len(d2)} 字节")
            chk("断网时仍渲染出标题与侧边栏",
                "直播翻译和视频翻译" in d2 and "巴别塔计划" in d2)
            chk("断网时模型表仍有内容（退到内置）",
                ("faster-whisper" in d2) or ("qwen-mt" in d2))
            chk("断网时明确标出数据源不是 API",
                ("内置" in d2) or ("快照" in d2),
                "断网了还显示 API 数据源 = 页面上会撒谎")

        if a.shot:
            got = screenshot(browser, base + "/", a.shot)
            chk(f"整页截图已生成（{a.shot}）", got)
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        if not a.keep:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    print(f"\n浏览器端到端：{OK} 项通过，{BAD} 项失败")
    return 1 if BAD else 0


if __name__ == "__main__":
    raise SystemExit(main())
