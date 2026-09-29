#!/usr/bin/env python3
"""
生成 mjNet 的图标文件（开发/维护用脚本，程序运行本身不需要它）。

为什么不用 subsample 缩放：Tk 的 subsample 只支持整数倍，256/48 只能除以 5，
会得到 52x52 这种对不上号的文件名。这里改成**按目标尺寸重新绘制**，
每个尺寸都是精确像素，线条粗细也随尺寸调整，小图标不会糊成一团。

用法：
    python3 tools/make_icons.py                 # 生成到 assets/
    python3 tools/make_icons.py 输出目录         # 生成到指定目录
    python3 tools/make_icons.py --hicolor 根目录  # 生成 hicolor 主题目录结构
                                              # （安装脚本用这种，供桌面菜单读取）
"""
from __future__ import annotations

import math
import os
import sys
import tkinter as tk

BG = "#101722"      # 外框底色
CARD = "#1d2839"    # 卡片底色
ACCENT = "#4da3ff"  # 蓝色柱
OK = "#3ddc97"      # 绿色（WiFi 弧线与最高柱）

SIZES = (48, 64, 128, 256, 512)


def draw_icon(size: int) -> tk.PhotoImage:
    """按给定尺寸画一个“WiFi 扇形 + 信号柱”的图标"""
    img = tk.PhotoImage(width=size, height=size)
    s = size / 256.0                      # 相对 256 设计稿的缩放系数
    pad = max(1, int(16 * s))
    img.put(BG, to=(0, 0, size, size))
    img.put(CARD, to=(pad, pad, size - pad, size - pad))

    dot_r = max(1, int(3 * s))            # 小尺寸时像素点半径不能小于 1

    def put(x: float, y: float, color: str) -> None:
        if not (0 <= x < size and 0 <= y < size):
            return
        img.put(color, to=(int(x) - dot_r, int(y) - dot_r,
                           int(x) + dot_r + 1, int(y) + dot_r + 1))

    # ---- WiFi 扇形：三条弧 + 一个圆点 ----
    cx, cy = 128 * s, 92 * s
    for radius, thick in ((22, 2), (40, 3), (58, 4)):
        r = radius * s
        w = max(1, int(thick * s))
        for deg in range(200, 341):        # 只画上半圈
            rad = math.radians(deg)
            for t in range(-w, w + 1):
                rr = r + t
                put(cx + rr * math.cos(rad),
                    cy - rr * math.sin(rad) * 0.55,   # 压扁成扇形
                    OK)
    # 弧线下方的小圆点（WiFi 图标的那一点）
    dot_y = 110 * s
    img.put(OK, to=(int(cx - dot_r * 2), int(dot_y - dot_r * 2),
                    int(cx + dot_r * 2), int(dot_y + dot_r * 2)))

    # ---- 底部四根信号柱：从矮到高，最后一根绿色 ----
    bar_w = 27 * s
    base_y = 216 * s
    for i, h in enumerate((34, 50, 66, 82)):
        x0 = (44 + i * 43) * s
        img.put(ACCENT if i < 3 else OK,
                to=(int(x0), int(base_y - h * s), int(x0 + bar_w), int(base_y)))
    return img


def main() -> int:
    args = sys.argv[1:]
    hicolor = "--hicolor" in args
    args = [a for a in args if a != "--hicolor"]
    if hicolor:
        # 按 XDG 图标主题规范生成：<root>/<size>x<size>/apps/mjnet.png
        root_dir = args[0] if args else os.path.join(
            os.path.expanduser("~"), ".local", "share", "icons", "hicolor")
        targets = {size: os.path.join(root_dir, f"{size}x{size}", "apps", "mjnet.png")
                   for size in (48, 64, 128, 256)}
    else:
        out_dir = args[0] if args else os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
        os.makedirs(out_dir, exist_ok=True)
        targets = {size: os.path.join(out_dir, "mjnet.png" if size == 256
                                      else f"mjnet-{size}.png")
                   for size in SIZES}

    tk_root = tk.Tk()
    tk_root.withdraw()
    try:
        for size, path in targets.items():
            os.makedirs(os.path.dirname(path), exist_ok=True)
            draw_icon(size).write(path, format="png")
            print(f"  ✓ {path}")
    finally:
        tk_root.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
