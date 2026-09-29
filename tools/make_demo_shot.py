#!/usr/bin/env python3
"""
生成用于公开仓库的截图：自动把真实隐私信息替换成示例数据。

为什么不用简单打码：灰块很难看，而且"打码的位置"本身也暴露信息布局。
这里改成**用示例数据覆盖**——看起来仍是正常界面，但看不到真实 IP / WiFi 名 / DNS。

实现思路：
  1. 启动程序，等数据填好；
  2. 用控件自身的坐标（winfo_rootx/rooty）拿到每个敏感标签的屏幕矩形，
     注意不是猜坐标，而是直接问界面要 —— 换主题、改字号都不影响；
  3. 在顶层画同色矩形盖住原文字，再用同样的字体画上示例文字；
     （只截主窗口——诊断窗口里还包含真实网关/DNS，不适合入镜）
  4. 抓屏，此时屏幕上是"示例数据版"界面；
  5. 退出程序，按窗口位置裁剪出干净截图。

用法：
    python3 tools/make_demo_shot.py docs/screenshot.png
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# 覆盖时用的示例数据（都是文档专用地址，不会指向真实主机）
from netmon.app import C_CARD, C_TEXT, NetMonApp  # noqa: E402


def capture(x: int, y: int, w: int, h: int, out: str) -> bool:
    """用 ffmpeg 抓屏一块区域"""
    cmd = ["ffmpeg", "-loglevel", "error", "-y",
           "-f", "x11grab", "-video_size", f"{w}x{h}", "-i", f":0.0+{x}+{y}",
           "-frames:v", "1", out]
    try:
        return subprocess.run(cmd, timeout=30).returncode == 0
    except Exception:
        return False


def main() -> int:
    out_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "docs", "screenshot.png")
    app = NetMonApp()
    app.geometry("940x780+40+40")
    app.attributes("-topmost", True)
    app.update()
    app.update_idletasks()

    # 等真实数据填好（我们要替换的就是真实数据所在的位置）
    for _ in range(40):
        app.update()
        time.sleep(0.25)
        if app._last_local and app.remote.public_ip is not None:
            break
    app.update_idletasks()

    # 计算窗口在屏幕上的位置，抓屏时按这个裁剪
    win_x = app.winfo_rootx() - 1
    win_y = app.winfo_rooty() - 1
    win_w = app.winfo_width() + 2
    win_h = app.winfo_height() + 2

    def rect(widget):
        """控件的屏幕矩形，四周略微扩展，确保盖住文字"""
        app.update_idletasks()
        x = widget.winfo_rootx() - 3
        y = widget.winfo_rooty() - 2
        return x, y, widget.winfo_width() + 6, widget.winfo_height() + 4

    # 要替换的敏感信息 -> 示例文字
    replacements = [
        (app.card_local.value_label, "192.168.1.100", None),
        (app.card_local.hint_label, "主机名 my-pc    网卡 wlan0", None),
        (app.card_public.value_label, "203.0.113.45", None),
        (app.card_public.hint_label, "中国 · 示例省 示例市 · 示例运营商（来源 ip-api.com 317ms）", None),
        (app.card_link.hint_label, "SSID MyWiFi-5G    信号 82%    协商速率 866 Mbps", None),
        (app.card_dns.value_label, "223.5.5.5   119.29.29.29", None),
        (app.card_dns.hint_label, "默认网关 192.168.1.1", None),
    ]

    # 逐个覆盖：先铺同色底，再写示例文字
    for widget, text, color in replacements:
        x, y, w, h = rect(widget)
        patch = tk.Toplevel(app)
        patch.overrideredirect(True)          # 无边框，纯色块
        patch.geometry(f"{w}x{h}+{x}+{y}")
        patch.configure(bg=C_CARD)
        try:
            patch.attributes("-topmost", True)
        except Exception:
            pass
        font = widget.cget("font")
        tk.Label(patch, text=text, bg=C_CARD, fg=C_TEXT, font=font,
                 anchor="w", justify="left").pack(fill="both", expand=True, padx=3)
        patch.update()

    app.update()
    time.sleep(1.2)               # 给窗口管理器一点时间把覆盖层画上去

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    ok = capture(win_x, win_y, win_w, win_h, out_path)
    print(f"{'✓ 已保存' if ok else '✗ 抓屏失败'}: {out_path}")
    app.on_close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
