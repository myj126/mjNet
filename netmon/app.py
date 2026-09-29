"""
netmon.app —— mjNet 图形界面（Tkinter）

界面结构：
  ┌ 顶栏：标题 + 状态指示灯 + 最后刷新时间 ─────────────────┐
  │ 网速(实时波形)      │ 延迟 / 丢包 / 抖动                │
  │ 本机 IP             │ 公网 IP / 归属地                  │
  │ 网络连接(WiFi/有线) │ 代理状态                          │
  │ DNS 与网关(通栏)    │ 网卡与连接数(通栏)                │
  ├ 底栏：刷新 | 自动刷新 | 复制IP | 导出报告 | 关于 ────────┤
  └ 状态栏 ────────────────────────────────────────────────┘

线程模型：所有网络请求都丢到后台线程，结果通过 queue 回到主线程刷新界面，
         保证界面永不卡顿（Tkinter 只能在主线程操作控件）。
"""
from __future__ import annotations

import os
import queue
import threading
import time
import tkinter as tk
import traceback
from dataclasses import dataclass
from tkinter import font as tkfont
from tkinter import messagebox, ttk

from . import diagnose, netinfo, netprobe
from .config import load_config

APP_NAME = "mjNet"
APP_TITLE = "mjNet · 网络状态监视器"
APP_VERSION = "1.0.0"

# --------------------------------------------------------------------------- #
# 配色
# --------------------------------------------------------------------------- #
C_BG = "#101722"
C_CARD = "#18212f"
C_CARD_ALT = "#1d2839"
C_TEXT = "#e8eef7"
C_MUTED = "#8ea0b8"
C_ACCENT = "#4da3ff"
C_DOWN = "#3ddc97"
C_UP = "#ffb454"
C_OK = "#3ddc97"
C_WARN = "#ffb454"
C_BAD = "#ff6b6b"
C_LINE = "#263449"
C_CHART_BG = "#131c29"

FONT_CANDIDATES = (
    # Windows / macOS 常见中文字体
    "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Hiragino Sans GB",
    # Linux 常见中文字体
    "Noto Sans CJK SC", "Noto Sans SC", "Source Han Sans SC", "WenQuanYi Micro Hei",
    "WenQuanYi Zen Hei", "Droid Sans Fallback", "Droid Sans Fallback CJK",
    # 老式 X11 核心字体（精简系统上可能只剩这些）
    "song ti", "mincho", "gothic",
    # 纯西文兜底
    "Segoe UI", "DejaVu Sans",
)

# 已知的 TrueType/OpenType 字体族。用来判断当前 Tk 到底能不能使用矢量字体：
# 一个都认不出来，说明它只认得 X 服务器里的老式**位图**字体（缺 Xft 支持），
# 这种 Tk 渲染中文时会零散出现方块。
TRUETYPE_FAMILIES = (
    "Noto Sans CJK SC", "Noto Sans SC", "Noto Sans CJK JP", "Source Han Sans SC",
    "WenQuanYi Micro Hei", "WenQuanYi Zen Hei", "Droid Sans Fallback",
    "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Hiragino Sans GB",
    "DejaVu Sans", "Liberation Sans", "Segoe UI", "Ubuntu", "Cantarell",
)
# 实测哪些字体族会被 Tk 悄悄替换掉（替换目标就是它）
_SUBSTITUTE_HINT = "fixed"

# 界面字号基准。老式位图字体在小字号下容易缺字，所以基准不要太小。
BASE_FONT_SIZE = 11
def has_truetype_fonts(size: int = BASE_FONT_SIZE) -> bool:
    """当前 Tk 能不能使用 TrueType 字体（拿不到就说明只有位图字体，必然出方块）"""
    try:
        available = {f.lower() for f in tkfont.families()}
    except Exception:
        return False
    for name in TRUETYPE_FAMILIES:
        if name.lower() not in available:
            continue
        try:
            if tkfont.Font(family=name, size=size).actual("family").lower() == name.lower():
                return True
        except Exception:
            continue
    return False


def sizes_for(base: int) -> dict:
    """由基准字号推导出各级字号（纯函数，方便测试）"""
    return {
        "tiny": max(7, base - 3),      # 波形图角标
        "small": max(8, base - 2),     # 卡片辅助说明、状态栏
        "body": base,                  # 常规文字
        "title": base + 1,             # 卡片标题
        "section": base + 2,           # 区块小标题
        "heading": base + 6,           # 页面主标题
    }


def resolve_font_family(preferred: str = "", size: int = BASE_FONT_SIZE) -> tuple[str, bool]:
    """
    挑一个字体，返回 (字体名, 是否能用 TrueType 矢量字体)。

    第二步返回值很关键：**它只以 has_truetype_fonts() 为准**。
    因为 Tk 在位图字体环境下会"撒谎"——你请求 "song ti"，它找不到时静默换成
    "fixed"，但 Font.actual("family") 依然报回 "song ti"。所以不能靠它判断。
    """
    try:
        available = {name.lower() for name in tkfont.families()}
        truetype_ok = has_truetype_fonts(size)

        def usable(name: str) -> bool:
            """字体存在，且（若支持矢量字体）确实被 Tk 采用而不是被替换"""
            if name.lower() not in available:
                return False
            if not truetype_ok:
                return True          # 位图环境下无法用替换检测区分，只能先认了
            try:
                return tkfont.Font(family=name, size=size).actual("family").lower() == name.lower()
            except Exception:
                return False

        if preferred and usable(preferred):
            return preferred, truetype_ok
        # 支持矢量字体时优先选矢量中文字体；否则在候选里挑一个存在的（位图兜底）
        for name in (TRUETYPE_FAMILIES if truetype_ok else ()) + FONT_CANDIDATES:
            if usable(name):
                return name, truetype_ok
    except Exception:
        pass
    return "TkDefaultFont", False


def pick_font(preferred: str = "", size: int = BASE_FONT_SIZE) -> str:
    """兼容旧调用名：返回字体族名"""
    return resolve_font_family(preferred, size)[0]


def apply_fonts(root: tk.Misc, family: str, base_size: int = BASE_FONT_SIZE) -> None:
    """
    把字体应用到 Tk 的各个「命名字体」上。

    好处是一次设置全生效：凡是没显式指定字体的控件都会跟着变，
    避免界面上不同控件用不同字体导致观感凌乱。
    """
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont",
                 "TkCaptionFont", "TkSmallCaptionFont", "TkTooltipFont"):
        try:
            tkfont.nametofont(name).configure(family=family, size=base_size)
        except Exception:
            continue
    try:
        tkfont.nametofont("TkFixedFont").configure(family=family, size=base_size)
    except Exception:
        pass
    try:
        root.option_add("*Font", (family, base_size))
    except Exception:
        pass

# 定时器（毫秒）
TICK_MS = 500          # 界面刷新（波形、代理探测线程的排队检查）
LOCAL_POLL_MS = 1000   # 本机信息 + 网速采样
LATENCY_POLL_MS = 2000 # 延迟探测
REMOTE_IP_MS = 60000   # 每 60 秒确认一次公网 IP

CHART_POINTS = 60      # 波形保留 60 个点（约 60 秒）


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def fmt_speed(mbps: float) -> tuple[str, str]:
    """把 Mb/s 格式化成 (数值, 单位)，小于 1 Mb/s 时自动换算成 kb/s"""
    if mbps is None or mbps < 0:
        return "--", "Mb/s"
    bits = mbps * 1e6
    if bits < 1000:
        return f"{bits:,.0f}", "b/s"
    if bits < 1_000_000:
        return f"{bits / 1000:,.1f}", "kb/s"
    return f"{mbps:,.2f}", "Mb/s"


def fmt_bytes_mb(mb: float) -> str:
    if mb < 1:
        return f"{mb * 1000:.0f} KB"
    if mb < 1024:
        return f"{mb:,.1f} MB"
    return f"{mb / 1024:,.2f} GB"


def shorten(text: str, limit: int = 60) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def color_for_speed(mbps: float) -> str:
    if mbps >= 100:
        return C_BAD        # 速率很高说明正在大流量下载
    if mbps >= 10:
        return C_WARN
    if mbps > 0.01:
        return C_DOWN
    return C_MUTED


# --------------------------------------------------------------------------- #
# 界面控件：信息卡片
# --------------------------------------------------------------------------- #
class Card(tk.Frame):
    """一个信息卡片：标题 + 大号数值 + 单位 + 辅助说明"""

    def __init__(self, master, title: str, *, value_size: int = 21,
                 wide: bool = False, colored: bool = False, **kw):
        # 先把自定义参数取出来，剩下的才是传给 tk.Frame 的合法选项
        self._font_family = kw.pop("font_family", None) or "TkDefaultFont"
        self.font_sizes = kw.pop("sizes", None) or sizes_for(BASE_FONT_SIZE)
        super().__init__(master, bg=C_CARD, highlightthickness=1,
                         highlightbackground=C_LINE, **kw)
        self.columnconfigure(0, weight=1)

        top = tk.Frame(self, bg=C_CARD)
        top.grid(row=0, column=0, sticky="ew", padx=14, pady=(8, 0))
        top.columnconfigure(0, weight=1)
        self.title_label = tk.Label(top, text=title, bg=C_CARD, fg=C_MUTED,
                                    font=(self._font_family, self.font_sizes["body"]))
        self.title_label.grid(row=0, column=0, sticky="w")
        # 右上角小圆点，用于表达状态（绿=正常 / 黄=注意 / 红=异常 / 灰=未知）
        self.dot = tk.Canvas(top, width=14, height=14, bg=C_CARD,
                             highlightthickness=0, bd=0)
        self.dot.grid(row=0, column=1, sticky="e")
        self._dot_id = self.dot.create_oval(3, 3, 11, 11, fill=C_MUTED, outline="")

        body = tk.Frame(self, bg=C_CARD)
        body.grid(row=1, column=0, sticky="nsew", padx=14, pady=(2, 10))
        self.rowconfigure(1, weight=1)
        body.columnconfigure(0, weight=1)

        line = tk.Frame(body, bg=C_CARD)
        line.grid(row=0, column=0, sticky="w")
        self.prefix_label = tk.Label(line, text="", bg=C_CARD, fg=C_MUTED,
                                     font=(self._font_family, self.font_sizes["body"]), anchor="w")
        self.prefix_label.pack(side="left", padx=(0, 6), pady=(8, 0))
        self.value_label = tk.Label(
            line, text="--", bg=C_CARD,
            fg=C_ACCENT if colored else C_TEXT,
            font=(self._font_family, value_size, "bold"), anchor="w", justify="left",
        )
        self.value_label.pack(side="left")
        self.unit_label = tk.Label(line, text="", bg=C_CARD, fg=C_MUTED,
                                   font=(self._font_family, self.font_sizes["body"]), anchor="w")
        self.unit_label.pack(side="left", padx=(6, 0), pady=(8, 0))

        self.hint_label = tk.Label(
            body, text="", bg=C_CARD, fg=C_MUTED, justify="left",
            anchor="w", font=(self._font_family, self.font_sizes["small"]), wraplength=360,
        )
        # 换行宽度跟着卡片实际宽度走：写死的话，字体变大后文字会多折一行、把卡片撑破
        body.bind("<Configure>", self._on_body_resize)
        self.hint_label.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        body.rowconfigure(1, weight=0)

        self.extra_frame = tk.Frame(body, bg=C_CARD)
        self.extra_frame.grid(row=2, column=0, sticky="nsew", pady=(4, 0))

    def _on_body_resize(self, event) -> None:
        """卡片变宽/变窄时重新计算说明文字的换行宽度"""
        wrap = max(120, event.width - 4)
        if abs(int(self.hint_label.cget("wraplength")) - wrap) > 8:
            self.hint_label.configure(wraplength=wrap)

    # -- 更新接口 ---------------------------------------------------------- #
    def set(self, value: str, unit: str = "", hint: str = "",
            color: str | None = None, dot: str | None = None,
            prefix: str | None = None) -> None:
        if prefix is not None:
            self.prefix_label.configure(text=prefix)
        self.value_label.configure(text=value, fg=color or C_TEXT)
        self.unit_label.configure(text=unit)
        self.hint_label.configure(text=hint)
        if dot:
            self.dot.itemconfigure(self._dot_id, fill=dot)


# --------------------------------------------------------------------------- #
# 主应用
# --------------------------------------------------------------------------- #
@dataclass
class RemoteState:
    public_ip: dict | None = None
    latency: dict | None = None
    public_ip_error: str = ""


class NetMonApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        cfg = load_config()
        self.font_size = int(cfg.get("font_size") or BASE_FONT_SIZE)
        self.font_family, self.font_is_cjk_capable = resolve_font_family(
            str(cfg.get("font_family", "") or ""), self.font_size)
        self.font_sizes = sizes_for(self.font_size)
        self.title(APP_TITLE)
        self.configure(bg=C_BG)
        self.geometry("940x780")
        self.minsize(900, 780)
        self._set_icon()

        # ---- 状态 ----
        self.collector = netinfo.InfoCollector()
        self.latency_tracker = netprobe.LatencyTracker()
        self.auto_refresh = tk.BooleanVar(value=True)
        self.events: queue.Queue = queue.Queue()
        self.tasks_running: set[str] = set()
        self.remote = RemoteState()
        self.last_diagnosis: diagnose.Diagnosis | None = None
        self._diag_window: tk.Toplevel | None = None
        self._diag_text: tk.Text | None = None
        self._closing = False
        self._after_ids: list[str] = []      # 所有定时器 id，退出时统一取消
        self._spinner = 0
        self._last_local: netinfo.LocalInfo | None = None
        self._down_hist: list[float] = [0.0] * CHART_POINTS
        self._up_hist: list[float] = [0.0] * CHART_POINTS
        self._peak_down = 0.0
        self._peak_up = 0.0
        self._last_ip_refresh = 0.0

        self._build_style()
        self._build_ui()
        self._log(f"{APP_NAME} {APP_VERSION} 启动，平台 {os.name}")

        # 首次立刻采样一次，避免界面先显示 "--"
        try:
            self.collector.meter.sample()
            time.sleep(0.2)
        except Exception:
            pass

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._after(300, self._tick)
        self._after(200, self._poll_local)
        self._after(800, self._poll_latency)
        self._after(600, lambda: self._start_task("public_ip"))

    # ------------------------------------------------------------------ #
    # 构建：样式与布局
    # ------------------------------------------------------------------ #
    def _set_icon(self) -> None:
        """窗口图标：优先用 assets/mjnet.png（和桌面菜单同一个图标）"""
        asset = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "assets", "mjnet.png")
        try:
            if os.path.exists(asset):
                icon = tk.PhotoImage(file=asset)
                # 部分窗口管理器只认 16/32 像素的小图标，这里附赠一个缩小的
                small = icon.subsample(max(1, icon.width() // 32))
                self.iconphoto(True, icon, small)
                self._icon_ref = (icon, small)
                return
        except Exception:
            pass
        # 兜底：找不到图片就用代码画一个简单图标（保持程序零外部资源也能跑）
        try:
            icon = tk.PhotoImage(width=16, height=16)
            icon.put(C_BG, to=(0, 0, 16, 16))
            for i, h in enumerate((4, 7, 10, 13)):
                x0 = 2 + i * 3
                icon.put(C_ACCENT, to=(x0, 16 - h, x0 + 2, 14))
            self.iconphoto(True, icon)
            self._icon_ref = icon
        except Exception:
            pass

    def _build_style(self) -> None:
        apply_fonts(self, self.font_family, self.font_sizes["body"])
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TButton", font=(self.font_family, self.font_sizes["body"]), padding=(12, 7))
        style.configure("TCheckbutton", background=C_BG, foreground=C_TEXT,
                        font=(self.font_family, self.font_sizes["body"]))
        style.map("TCheckbutton", background=[("active", C_BG)])
        style.configure("TFrame", background=C_BG)

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        # ---------------- 顶栏 ----------------
        header = tk.Frame(self, bg=C_BG)
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 8))
        header.columnconfigure(1, weight=1)

        tk.Label(header, text="mjNet", bg=C_BG, fg=C_TEXT,
                 font=(self.font_family, self.font_sizes["heading"] + 3, "bold")).grid(row=0, column=0, sticky="w")
        subtitle = tk.Frame(header, bg=C_BG)
        subtitle.grid(row=0, column=1, sticky="w", padx=(12, 0))
        tk.Label(subtitle, text="网络状态监视器 · Linux / Windows 通用",
                 bg=C_BG, fg=C_MUTED, font=(self.font_family, self.font_sizes["small"])).pack(anchor="w")
        self.host_label = tk.Label(subtitle, text="", bg=C_BG, fg=C_MUTED,
                                   font=(self.font_family, self.font_sizes["small"]))
        self.host_label.pack(anchor="w")

        status_box = tk.Frame(header, bg=C_BG)
        status_box.grid(row=0, column=2, sticky="e")
        self.state_dot = tk.Canvas(status_box, width=14, height=14, bg=C_BG,
                                   highlightthickness=0, bd=0)  # 纯色圆点，不依赖字体
        self.state_dot.pack(side="left", padx=(0, 6))
        self._state_dot_id = self.state_dot.create_oval(3, 3, 11, 11, fill=C_MUTED, outline="")
        self.state_label = tk.Label(status_box, text="初始化…", bg=C_BG, fg=C_MUTED,
                                    font=(self.font_family, self.font_sizes["body"], "bold"))
        self.state_label.pack(side="left")

        # ---------------- 卡片区 ----------------
        grid = tk.Frame(self, bg=C_BG)
        grid.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 8))
        grid.columnconfigure(0, weight=1, uniform="col")
        grid.columnconfigure(1, weight=1, uniform="col")
        for r in range(4):
            grid.rowconfigure(r, weight=1, uniform="row")

        # 1. 网速
        self.card_speed = Card(grid, "实时网速", value_size=20, sizes=self.font_sizes,
                             font_family=self.font_family)
        self.card_speed.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=4)
        # 波形图。以前这里上面还有一行单独的说明文字，但内容和 hint 重复，
        # 白占 22px 高度导致整行卡片被裁，已合并到 hint 里。
        self.chart = tk.Canvas(self.card_speed.extra_frame, height=22, bg=C_CHART_BG,
                               highlightthickness=1, highlightbackground=C_LINE, bd=0)
        self.chart.pack(fill="x", pady=(2, 0))

        # 2. 延迟
        self.card_latency = Card(grid, "网络延迟 (TCP 握手)", value_size=20, sizes=self.font_sizes,
                             font_family=self.font_family)
        self.card_latency.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=4)

        # 3. 本机 IP
        self.card_local = Card(grid, "本机 IP（局域网）", value_size=20,
                               sizes=self.font_sizes,
                               font_family=self.font_family, colored=True)
        self.card_local.grid(row=1, column=0, sticky="nsew", padx=(0, 6), pady=4)

        # 4. 公网 IP
        self.card_public = Card(grid, "公网 IP（出口地址）", value_size=20, sizes=self.font_sizes,
                             font_family=self.font_family)
        self.card_public.grid(row=1, column=1, sticky="nsew", padx=(6, 0), pady=4)

        # 5. 网络连接
        self.card_link = Card(grid, "网络连接方式", value_size=18, sizes=self.font_sizes,
                             font_family=self.font_family)
        self.card_link.grid(row=2, column=0, sticky="nsew", padx=(0, 6), pady=4)

        # 6. 代理
        self.card_proxy = Card(grid, "代理状态", value_size=18, sizes=self.font_sizes,
                             font_family=self.font_family)
        self.card_proxy.grid(row=2, column=1, sticky="nsew", padx=(6, 0), pady=4)

        # 7. DNS / 网关
        self.card_dns = Card(grid, "DNS 服务器 与 默认网关", value_size=16, sizes=self.font_sizes,
                             font_family=self.font_family)
        self.card_dns.grid(row=3, column=0, sticky="nsew", padx=(0, 6), pady=4)

        # 8. 网卡 / 连接数 / 流量
        self.card_nic = Card(grid, "网卡 · 连接数 · 本次会话流量", value_size=16, sizes=self.font_sizes,
                             font_family=self.font_family)
        self.card_nic.grid(row=3, column=1, sticky="nsew", padx=(6, 0), pady=4)

        # ---------------- 底栏按钮 ----------------
        bar = tk.Frame(self, bg=C_BG)
        bar.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 6))

        self.btn_refresh = ttk.Button(bar, text="立即刷新", command=self.manual_refresh)
        self.btn_refresh.pack(side="left")
        ttk.Checkbutton(bar, text="自动刷新", variable=self.auto_refresh,
                        command=self._on_toggle_auto).pack(side="left", padx=(10, 0))
        ttk.Button(bar, text="复制本机 IP", command=lambda: self.copy_text(
            self._last_local.ip if self._last_local else "", "本机 IP")).pack(side="left", padx=(10, 0))
        ttk.Button(bar, text="复制公网 IP", command=lambda: self.copy_text(
            (self.remote.public_ip or {}).get("ip", ""), "公网 IP")).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="导出报告", command=self.export_report).pack(side="left", padx=(6, 0))
        self.btn_diagnose = ttk.Button(bar, text="网络诊断", command=self.run_diagnosis)
        self.btn_diagnose.pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="关于/诊断", command=self.show_about).pack(side="right")

        # ---------------- 状态栏 ----------------
        self.status = tk.Label(self, text="准备就绪", bg=C_BG, fg=C_MUTED,
                               anchor="w", font=(self.font_family, self.font_sizes["small"]))
        self.status.grid(row=3, column=0, sticky="ew", padx=18, pady=(0, 10))

    # ------------------------------------------------------------------ #
    # 主循环
    # ------------------------------------------------------------------ #
    def _after(self, delay_ms: int, callback) -> None:
        """登记定时器，方便退出时全部取消（否则销毁窗口后回调会报错）"""
        if self._closing:
            return
        try:
            self._after_ids.append(self.after(delay_ms, callback))
        except Exception:
            pass

    def _tick(self) -> None:
        """每 500ms：处理后台线程结果 + 重绘波形"""
        if self._closing:
            return
        self._drain_events()
        self._draw_chart()
        if self.tasks_running:
            self._spinner = (self._spinner + 1) % 4
            marks = "<|  |>"
            self.btn_refresh.configure(text=f"{marks[self._spinner]} 检测中…")
        else:
            self.btn_refresh.configure(text="立即刷新")
        self._after(TICK_MS, self._tick)

    def _drain_events(self) -> None:
        """把后台线程的结果搬到界面上（只允许主线程碰控件）"""
        for _ in range(50):
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                return
            try:
                if kind == "public_ip":
                    self._apply_public_ip(payload)
                elif kind == "latency":
                    self._apply_latency(payload)
                elif kind == "diagnose":
                    self._apply_diagnosis(payload)
                elif kind == "log":
                    self._log(payload)
            except Exception:
                self._log("界面更新异常: " + traceback.format_exc(limit=2))

    def _poll_local(self) -> None:
        if self._closing:
            return
        try:
            info = self.collector.collect()
            self._apply_local(info)
        except Exception:
            self._log("本机信息采集异常: " + traceback.format_exc(limit=2))
        self._after(LOCAL_POLL_MS, self._poll_local)

    def _poll_latency(self) -> None:
        if self._closing:
            return
        if self.auto_refresh.get():
            self._start_task("latency")
        self._after(LATENCY_POLL_MS, self._poll_latency)

    # ------------------------------------------------------------------ #
    # 后台任务
    # ------------------------------------------------------------------ #
    def _start_task(self, name: str, force: bool = False) -> None:
        if self._closing:
            return
        if name in self.tasks_running:
            return
        if not force and not self.auto_refresh.get():
            return
        self.tasks_running.add(name)
        target = {
            "public_ip": self._task_public_ip,
            "latency": self._task_latency,
            "diagnose": self._task_diagnose,
        }[name]
        threading.Thread(target=target, name=f"netmon-{name}", daemon=True).start()

    def _task_public_ip(self) -> None:
        try:
            result = netprobe.public_ip_info()
        except Exception as exc:
            result = {"ok": False, "ip": "", "error": f"{type(exc).__name__}: {exc}"}
        finally:
            self.tasks_running.discard("public_ip")
        self.events.put(("public_ip", result))

    def _task_latency(self) -> None:
        try:
            result = netprobe.probe_latency()
        except Exception as exc:
            result = {"targets": [], "min": None, "max": None, "avg": None,
                      "domestic_ms": None, "overseas_ms": None, "error": str(exc)}
        finally:
            self.tasks_running.discard("latency")
        self.events.put(("latency", result))

    def _task_diagnose(self) -> None:
        """后台跑一遍完整诊断（5~10 秒，绝不能放在主线程）"""
        try:
            result = diagnose.run_diagnosis(self.collector)
        except Exception as exc:
            result = diagnose.Diagnosis(
                conclusion=f"诊断过程出错：{type(exc).__name__}: {exc}",
                advice="请把这条信息反馈给开发者。", ran_at=time.time())
        finally:
            self.tasks_running.discard("diagnose")
        self.events.put(("diagnose", result))

    # ------------------------------------------------------------------ #
    # 网络诊断
    # ------------------------------------------------------------------ #
    def run_diagnosis(self) -> None:
        """点击「网络诊断」：开窗、显示进度、后台执行"""
        self._open_diag_window()
        if self._diag_text is not None:
            self._diag_text.configure(state="normal")
            self._diag_text.delete("1.0", "end")
            self._diag_text.insert("1.0", "正在诊断，请稍候（约 5~10 秒）\n\n")
            self._diag_text.insert("end", "依次检查：本机网卡 → 路由器 → 域名解析 → 国内连通 → 国外连通 → 公网出口\n", "muted")
            self._diag_text.configure(state="disabled")
        if self.btn_diagnose is not None:
            self.btn_diagnose.configure(text="诊断中…")
        self._log("开始网络诊断")
        self._start_task("diagnose", force=True)

    def _open_diag_window(self) -> None:
        if self._diag_window is not None and self._diag_window.winfo_exists():
            self._diag_window.lift()
            return
        win = tk.Toplevel(self)
        win.title("网络诊断")
        win.configure(bg=C_BG)
        win.geometry("760x520")
        win.minsize(620, 400)
        win.transient(self)
        self._diag_window = win

        tk.Label(win, text="网络诊断", bg=C_BG, fg=C_TEXT,
                 font=(self.font_family, self.font_sizes["heading"], "bold")).pack(anchor="w", padx=16, pady=(14, 2))
        tk.Label(win, text="由近到远逐层检查，哪一层出问题会直接告诉你",
                 bg=C_BG, fg=C_MUTED, font=(self.font_family, self.font_sizes["small"])).pack(anchor="w", padx=16)

        box = tk.Frame(win, bg=C_BG)
        box.pack(fill="both", expand=True, padx=16, pady=10)
        text = tk.Text(box, bg=C_CARD, fg=C_TEXT, relief="flat", wrap="word",
                       padx=12, pady=10, font=(self.font_family, self.font_sizes["body"]),
                       insertbackground=C_TEXT)
        scroll = ttk.Scrollbar(box, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        text.tag_configure("ok", foreground=C_OK)
        text.tag_configure("warn", foreground=C_WARN)
        text.tag_configure("fail", foreground=C_BAD)
        text.tag_configure("skip", foreground=C_MUTED)
        text.tag_configure("title", foreground=C_TEXT,
                           font=(self.font_family, self.font_sizes["title"], "bold"))
        text.tag_configure("conclusion", foreground=C_ACCENT,
                           font=(self.font_family, self.font_sizes["section"], "bold"))
        text.tag_configure("advice", foreground=C_WARN)
        text.tag_configure("muted", foreground=C_MUTED)
        self._diag_text = text

        bar = tk.Frame(win, bg=C_BG)
        bar.pack(fill="x", padx=16, pady=(0, 14))
        ttk.Button(bar, text="重新诊断", command=self.run_diagnosis).pack(side="left")
        ttk.Button(bar, text="复制诊断报告", command=self._copy_diag_report).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="关闭", command=win.destroy).pack(side="right")

    def _apply_diagnosis(self, diag: diagnose.Diagnosis) -> None:
        self.last_diagnosis = diag
        if self.btn_diagnose is not None:
            self.btn_diagnose.configure(text="网络诊断")
        self._log(f"诊断完成：{diag.conclusion}")
        self._open_diag_window()
        text = self._diag_text
        if text is None:
            return
        text.configure(state="normal")
        text.delete("1.0", "end")
        tag_map = {diagnose.OK: "ok", diagnose.WARN: "warn",
                   diagnose.FAIL: "fail", diagnose.SKIP: "skip"}
        for layer in diag.layers:
            tag = tag_map.get(layer.status, "skip")
            label = diagnose.STATUS_LABEL.get(layer.status, "")
            ms = f"  （{layer.ms:.0f} ms）" if layer.ms is not None else ""
            text.insert("end", f"{layer.name}", (tag, "title"))
            text.insert("end", f"   [{label}]{ms}\n", tag)
            text.insert("end", f"    {layer.detail}\n", "muted")
            if layer.advice:
                text.insert("end", f"    建议：{layer.advice}\n", "advice")
            text.insert("end", "\n")
        text.insert("end", f"结论：{diag.conclusion}\n", "conclusion")
        if diag.advice:
            text.insert("end", f"建议：{diag.advice}\n", "advice")
        text.insert("end", f"\n诊断时间：{time.strftime('%Y-%m-%d %H:%M:%S')}", "muted")
        text.configure(state="disabled")
        if self._diag_window is not None and self._diag_window.winfo_exists():
            self._diag_window.lift()

    def _copy_diag_report(self) -> None:
        if not self.last_diagnosis:
            self.status.configure(text="还没有诊断结果，请先点「网络诊断」")
            return
        self.copy_text(diagnose.format_report(self.last_diagnosis), "诊断报告")

    # ------------------------------------------------------------------ #
    # 界面更新
    # ------------------------------------------------------------------ #
    def _apply_local(self, info: netinfo.LocalInfo) -> None:
        self._last_local = info

        # ---- 顶栏状态灯 ----
        online = bool(info.link.iface) and bool(info.ip)
        if self.auto_refresh.get():
            self.state_dot.itemconfigure(self._state_dot_id, fill=C_OK if online else C_BAD)
            self.state_label.configure(
                text="已连接网络" if online else "网络未连接",
                fg=C_OK if online else C_BAD)
        else:
            self.state_dot.itemconfigure(self._state_dot_id, fill=C_WARN)
            self.state_label.configure(text="已暂停自动刷新", fg=C_WARN)
        self.host_label.configure(text=f"本机：{info.host}")

        # ---- 网速 ----
        value, unit = fmt_speed(info.down_mbps)
        self._down_hist.append(info.down_mbps)
        self._up_hist.append(info.up_mbps)
        self._down_hist = self._down_hist[-CHART_POINTS:]
        self._up_hist = self._up_hist[-CHART_POINTS:]
        self._peak_down = max(self._peak_down, info.down_mbps)
        self._peak_up = max(self._peak_up, info.up_mbps)
        up_value, up_unit = fmt_speed(info.up_mbps)
        self.card_speed.set(
            value, unit,
            hint=f"↑ 上传 {up_value} {up_unit}    "
                 f"累计 ↓{fmt_bytes_mb(info.session_rx_mb)} / ↑{fmt_bytes_mb(info.session_tx_mb)}",
            color=color_for_speed(info.down_mbps),
            dot=C_DOWN if info.down_mbps > 0.05 else (C_WARN if info.up_mbps > 0.05 else C_MUTED),
            prefix="下行",
        )

        # ---- 本机 IP ----
        self.card_local.set(
            info.ip or "未获取", "",
            hint=f"主机名 {info.host}    网卡 {info.link.iface or '—'}",
            color=C_ACCENT, dot=C_OK if info.ip else C_BAD)

        # ---- 公网 IP ----
        pub = self.remote.public_ip
        if pub and pub.get("ok"):
            loc = " ".join(x for x in (pub.get("country"), pub.get("region"),
                                       pub.get("city")) if x)
            isp = pub.get("isp") or ""
            detail = " · ".join(x for x in (loc, isp) if x) or "归属地未知"
            self.card_public.set(
                pub["ip"], "", hint=f"{detail}（来源 {pub.get('source','')} "
                                    f"{pub.get('elapsed_ms','?')}ms）",
                color=C_ACCENT, dot=C_OK)
        elif pub is not None:
            self.card_public.set("获取失败", "", hint=shorten(pub.get("error", ""), 90),
                                 color=C_BAD, dot=C_BAD)
        else:
            self.card_public.set("查询中…", "", hint="正在向公网接口查询出口 IP",
                                 color=C_MUTED, dot=C_WARN)

        # ---- 连接方式 ----
        link = info.link
        extra = ""
        if link.kind.startswith("WiFi"):
            extra = f"SSID {link.ssid or '未知'}    信号 {link.signal}%"
            if link.speed_mbps:
                extra += f"    协商速率 {link.speed_mbps:.0f} Mbps"
            dot = C_OK if link.signal >= 50 else (C_WARN if link.signal > 0 else C_MUTED)
        else:
            extra = f"协商速率 {link.speed_mbps:.0f} Mbps" if link.speed_mbps else "—"
            dot = C_OK if online else C_BAD
        self.card_link.set(link.kind, "", hint=extra, dot=dot)

        # ---- 代理 ----
        # 这里必须区分两种情况，否则用户关了系统代理却看到"已启用"会以为程序坏了：
        #   configured=True  → 环境变量/系统代理明确配置了代理，流量基本确定走代理
        #   configured=False → 只是探测到代理程序在跑（关掉开关后内核常常还在后台）
        # 只显示一条最相关的地址并截断：代理地址很长，全列出来会折成两三行把卡片撑破
        # （完整信息在「导出报告」和诊断窗口里）
        proxy = info.proxy or {}
        proc = proxy.get("process") or ""
        if proxy.get("configured"):
            first = (proxy.get("detail") or "").split("  ")[0]
            count = len([x for x in (proxy.get("detail") or "").split("  ") if x])
            if count > 1:
                first = f"{first} 等{count}项"
            text = f"已启用 · {proxy.get('source','')}"
            if proc:
                text += f"（{proc}）"
            self.card_proxy.set(text, "", hint=shorten(first, 44), color=C_OK, dot=C_OK)
        elif proxy.get("enabled"):
            client = proc or (proxy.get("detail") or "").split("、")[0]
            self.card_proxy.set("代理程序在运行", "",
                                hint=shorten(f"未配置代理，但 {client} 在后台运行", 44),
                                color=C_WARN, dot=C_WARN)
        else:
            self.card_proxy.set("未检测到代理", "", hint="环境变量与系统代理均为空",
                                color=C_MUTED, dot=C_MUTED)

        # ---- DNS / 网关 ----
        dns_text = "  ".join(info.dns[:2]) if info.dns else "未读取到"
        more = f"（共 {len(info.dns)} 个）" if len(info.dns) > 2 else ""
        self.card_dns.set(
            shorten(dns_text, 34), "", hint=f"默认网关 {info.gateway or '—'} {more}",
            dot=C_OK if info.dns else C_WARN)

        # ---- 网卡 / 连接数 ----
        self.card_nic.set(
            f"TCP 连接 {info.connections}", "",
            hint=f"统计网卡 {self.collector.meter.nic_count} 个    "
                 f"本会话累计 ↓{fmt_bytes_mb(info.session_rx_mb)} / ↑{fmt_bytes_mb(info.session_tx_mb)}",
            dot=C_OK)

        # ---- 状态栏 ----
        proxy_tag = "未检测到代理"
        if (info.proxy or {}).get("configured"):
            proxy_tag = f"代理已启用({info.proxy.get('source','')})"
        elif (info.proxy or {}).get("enabled"):
            proxy_tag = "代理程序在运行(未配置)"
        self.status.configure(
            text=f"最后更新 {time.strftime('%H:%M:%S')}    "
                 f"本机 {info.ip or '—'}    网关 {info.gateway or '—'}    "
                 f"DNS {len(info.dns)} 个    {proxy_tag}")

    def _apply_public_ip(self, result: dict) -> None:
        if result.get("ok"):
            self._log(f"公网 IP = {result.get('ip')} （来源 {result.get('source')}）")
        else:
            self._log("公网 IP 查询失败：" + shorten(result.get("error", ""), 160))
        self.remote.public_ip = result
        if self._last_local:
            self._apply_local(self._last_local)

    def _apply_latency(self, result: dict) -> None:
        self.remote.latency = result          # 报告与“关于”面板都要用
        best = result.get("min")
        self.latency_tracker.add(best)
        dom = result.get("domestic_ms")
        ovs = result.get("overseas_ms")
        if best is None:
            self.card_latency.set("超时", "", hint="三个探测目标均无响应",
                                  color=C_BAD, dot=C_BAD)
            return
        # 颜色阈值：<60ms 优，<150ms 良，其余偏差
        color = C_OK if best < 60 else (C_WARN if best < 150 else C_BAD)
        marks = []
        for target in result.get("targets", []):
            tag = "国内" if "国内" in target["name"] else "国外"
            marks.append(f"{tag} {'通' if target['ms'] is not None else '超时'}")
        bits = "   ".join(marks)
        if dom is not None:
            bits += f"    （国内最快 {dom:.0f}ms"
            bits += f"，国外最快 {ovs:.0f}ms）" if ovs is not None else "，国外不通）"
        jitter = self.latency_tracker.jitter_ms
        if jitter is not None:
            bits += f"    抖动 {jitter:.1f}ms"
        # 有点响应就是绿的；一个都不通才是红的
        dot = C_OK if (dom is not None or ovs is not None) else C_BAD
        self.card_latency.set(f"{best:.0f}", "ms", hint=bits, color=color, dot=dot)
        loss = self.latency_tracker.loss_rate
        if loss >= 5 and self.latency_tracker.samples >= 3:
            self.card_latency.hint_label.configure(text=f"{bits}    丢包 {loss:.0f}%")

    # ------------------------------------------------------------------ #
    # 波形绘制
    # ------------------------------------------------------------------ #
    def _draw_chart(self) -> None:
        c = self.chart
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 20 or h < 10:
            return
        c.delete("all")
        peak = max(max(self._down_hist), max(self._up_hist), 0.05)
        for frac in (0.25, 0.5, 0.75):
            y = h * frac
            c.create_line(0, y, w, y, fill=C_LINE, dash=(2, 4))
        for hist, color in ((self._down_hist, C_DOWN), (self._up_hist, C_UP)):
            n = len(hist)
            if n < 2:
                continue
            pts = []
            for i, v in enumerate(hist):
                x = w * i / (CHART_POINTS - 1)
                y = h - (v / peak) * (h - 4) - 2
                pts.extend((x, max(2, min(h - 2, y))))
            c.create_line(*pts, fill=color, width=1.6, smooth=True)
        c.create_text(4, 2, text=f"峰值 {peak:.2f} Mb/s", anchor="nw",
                      fill=C_MUTED, font=(self.font_family, self.font_sizes["tiny"]))

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #
    def _on_toggle_auto(self) -> None:
        if self.auto_refresh.get():
            self._log("已开启自动刷新")
            self.manual_refresh()
        else:
            self._log("已暂停自动刷新（网速采样继续，远程探测停止）")
            self._apply_local(self._last_local or self.collector.collect())

    def manual_refresh(self) -> None:
        """刷新按钮：立即重采本机信息，并重新查询公网 IP 与延迟"""
        self._log("手动刷新…")
        # 强制刷新慢信息（IP、网关、DNS、WiFi、代理）
        try:
            info = self.collector.collect(force_slow=True)
            self._apply_local(info)
        except Exception:
            self._log("刷新失败: " + traceback.format_exc(limit=2))
        self.latency_tracker.reset()
        self._start_task("public_ip", force=True)
        self._start_task("latency", force=True)

    def copy_text(self, text: str, what: str) -> None:
        if not text:
            self.status.configure(text=f"{what} 还没有获取到，稍后再试")
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update_idletasks()
            self.status.configure(text=f"已复制{what}：{text}")
            self._log(f"复制{what}：{text}")
        except Exception as exc:
            self.status.configure(text=f"复制失败：{exc}")

    def _report_text(self) -> str:
        info = self._last_local
        pub = self.remote.public_ip or {}
        lines = [
            f"===== {APP_NAME} {APP_VERSION} 网络报告 =====",
            f"生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
            "",
        ]
        if info:
            lines += [
                f"主机名：{info.host}",
                f"本机 IP：{info.ip or '未获取'}",
                f"默认网关：{info.gateway or '未获取'}",
                f"DNS 服务器：{', '.join(info.dns) if info.dns else '未获取'}",
                f"连接方式：{info.link.kind}"
                + (f"  SSID={info.link.ssid}  信号={info.link.signal}%"
                   if info.link.kind.startswith('WiFi') else "")
                + (f"  网卡={info.link.iface}" if info.link.iface else ""),
                f"协商速率：{info.link.speed_mbps:.0f} Mbps" if info.link.speed_mbps else "协商速率：未知",
                f"实时网速：↓ {info.down_mbps:.3f} Mb/s  ↑ {info.up_mbps:.3f} Mb/s",
                f"本次会话流量：↓ {fmt_bytes_mb(info.session_rx_mb)}  ↑ {fmt_bytes_mb(info.session_tx_mb)}",
                f"TCP 连接数：{info.connections}",
                f"代理：{'已启用 · ' + str(info.proxy.get('source','')) + ' ' + str(info.proxy.get('detail','')) if info.proxy.get('enabled') else '未检测到'}",
            ]
            if info.proxy.get("note"):
                lines.append(f"代理备注：{info.proxy['note']}")
        if pub:
            if pub.get("ok"):
                lines.append(f"公网 IP：{pub.get('ip')}（{pub.get('country','')} {pub.get('region','')} "
                             f"{pub.get('city','')} {pub.get('isp','')}）来源 {pub.get('source','')}")
            else:
                lines.append(f"公网 IP：查询失败（{shorten(pub.get('error',''), 120)}）")
        lat = self.remote.latency
        if lat and lat.get("min") is not None:
            detail = "，".join(f"{t['name']}={t['ms']:.0f}ms" if t["ms"] is not None
                               else f"{t['name']}=超时" for t in lat["targets"])
            lines.append(f"延迟：最低 {lat['min']:.0f}ms / 平均 {lat.get('avg', 0):.0f}ms（{detail}）")
        if self.last_diagnosis:
            lines += ["", "最近一次网络诊断：", diagnose.format_report(self.last_diagnosis)]
        lines += ["", "系统信息："]
        lines += [f"  {k}: {v}" for k, v in netinfo.system_summary().items()]
        return "\n".join(lines)

    def export_report(self) -> None:
        text = self._report_text()
        default = f"mjnet-report-{time.strftime('%Y%m%d-%H%M%S')}.txt"
        try:
            from tkinter import filedialog
            path = filedialog.asksaveasfilename(
                title="保存网络报告", initialfile=default,
                defaultextension=".txt",
                filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
            if not path:
                return
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            self.status.configure(text=f"报告已保存：{path}")
            self._log(f"报告已保存到 {path}")
        except Exception as exc:
            self.status.configure(text=f"保存失败：{exc}")

    def show_about(self) -> None:
        win = tk.Toplevel(self)
        win.title(f"关于 {APP_NAME}")
        win.configure(bg=C_BG)
        win.geometry("640x460")
        win.transient(self)
        text = tk.Text(win, bg=C_CARD, fg=C_TEXT, insertbackground=C_TEXT,
                       relief="flat", padx=12, pady=10,
                       font=(self.font_family, self.font_sizes["small"]), wrap="word")
        text.pack(fill="both", expand=True, padx=12, pady=12)
        tip = (
            f"{APP_NAME} {APP_VERSION} —— 轻量跨平台网络状态监视器\n"
            "纯 Python 标准库实现，Linux 与 Windows 均可直接运行。\n\n"
            "各项指标说明：\n"
            "  · 实时网速：按物理网卡字节数差值计算(1 Mb/s = 10^6 bit/s)，不含本地回环与虚拟网卡\n"
            "  · 网络延迟：TCP 握手耗时（无需管理员权限），并统计抖动与丢包\n"
            "  · 代理状态：依次检查 环境变量 → 系统代理(Windows 注册表 / GNOME / KDE) → 本机代理端口\n"
            "        注意：仅检测到代理端口说明“代理程序在跑”，若为 TUN/透明代理则是全流量接管\n"
            "  · 公网 IP：向多个公共服务依次查询，含国家/地区/运营商\n"
            "  · 信号强度：WiFi 由 iw/nmcli 得到，显示为百分比\n\n"
        )
        text.insert("1.0", tip + self._report_text() + "\n\n----- 运行日志 -----\n" + self._log_text())
        text.configure(state="disabled")
        ttk.Button(win, text="关闭", command=win.destroy).pack(pady=(0, 12))

    # ------------------------------------------------------------------ #
    # 日志
    # ------------------------------------------------------------------ #
    def _log(self, message: str) -> None:
        if not hasattr(self, "_logs"):
            self._logs: list[str] = []
        stamp = time.strftime("%H:%M:%S")
        self._logs.append(f"[{stamp}] {message}")
        del self._logs[:-200]

    def _log_text(self) -> str:
        return "\n".join(getattr(self, "_logs", []))

    # ------------------------------------------------------------------ #
    # 退出
    # ------------------------------------------------------------------ #
    def on_close(self) -> None:
        """退出：先取消所有定时器，再销毁窗口（顺序反了会有一堆 Tcl 报错）"""
        self._closing = True
        for after_id in self._after_ids:
            try:
                self.after_cancel(after_id)
            except Exception:
                pass
        self._after_ids.clear()
        try:
            self.update_idletasks()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass


def main() -> int:
    app = NetMonApp()
    try:
        app.mainloop()
    except KeyboardInterrupt:
        pass
    return 0
