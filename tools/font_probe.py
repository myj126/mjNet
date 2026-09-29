#!/usr/bin/env python3
"""
字体渲染测评器（开发工具，用来选出让中文不出方块的字体/字号组合）

背景：在缺少 TrueType 中文字体的精简 X11 环境里，Tk 只能用 X 服务器里的
老式位图字体，某些字体在某些字号下会把汉字画成「方块（豆腐块）」。
肉眼一张张看太费劲，所以这里做成自动测评：

  1. 把候选字体各渲染一行相同的文本；
  2. 用 ffmpeg 抓屏成 PNG；
  3. 自己解析 PNG（标准库 zlib，不依赖 PIL），逐字符判断是不是方块。

判断依据：方块是一个「空心的矩形」——字符格中间几乎全是背景色，
而正常汉字的笔画会填满相当比例。据此算出「格内墨迹占比」，
再配合左右竖边框的连续性，就能稳定区分。

用法：
    python3 tools/font_probe.py                 # 测评默认候选集合
    python3 tools/font_probe.py --text "自定义文本"
    python3 tools/font_probe.py --sizes 9,10,12
"""
from __future__ import annotations

import os
import struct
import subprocess
import sys
import tempfile
import tkinter as tk
import zlib

# 界面上真正出现过问题的文案片段
DEFAULT_TEXT = "网络状态监视器实时网速代理状态延迟丢失计数浏览内容当前时间已启用"
# 注意：候选里保留 song ti / fangsong ti 是为了暴露「Tk 静默回退」问题 ——
# 实测它们会被回退成 fixed，宽度与 fixed 完全一致，选它们等于没选。
DEFAULT_FONTS = ("gothic", "mincho", "fixed", "song ti", "TkDefaultFont")


# --------------------------------------------------------------------------- #
# 最小 PNG 读取（标准库实现，避免依赖 PIL）
# --------------------------------------------------------------------------- #
def read_png(path: str) -> tuple[int, int, list[list[tuple[int, int, int]]]]:
    """读取 8 位 RGB/RGBA 的 PNG，返回 (宽, 高, 像素行)"""
    data = open(path, "rb").read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("不是 PNG 文件")
    pos = 8
    width = height = color_type = bit_depth = 0
    idat = b""
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        if ctype == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack(">IIBB", chunk[:10])
        elif ctype == b"IDAT":
            idat += chunk
        elif ctype == b"IEND":
            break
        pos += 12 + length
    if bit_depth != 8 or color_type not in (2, 6):
        raise ValueError(f"只支持 8 位 RGB/RGBA 的 PNG（当前 color_type={color_type}）")
    channels = 3 if color_type == 2 else 4
    raw = zlib.decompress(idat)
    stride = width * channels
    rows: list[list[tuple[int, int, int]]] = []
    prev = bytearray(stride)

    def paeth(a: int, b: int, c: int) -> int:
        p = a + b - c
        pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
        return a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)

    p = 0
    for _ in range(height):
        ftype = raw[p]
        p += 1
        line = bytearray(raw[p:p + stride])
        p += stride
        for i in range(stride):
            a = line[i - channels] if i >= channels else 0
            b = prev[i]
            c = prev[i - channels] if i >= channels else 0
            if ftype == 1:
                line[i] = (line[i] + a) & 0xFF
            elif ftype == 2:
                line[i] = (line[i] + b) & 0xFF
            elif ftype == 3:
                line[i] = (line[i] + (a + b) // 2) & 0xFF
            elif ftype == 4:
                line[i] = (line[i] + paeth(a, b, c)) & 0xFF
        rows.append([(line[x * channels], line[x * channels + 1], line[x * channels + 2])
                     for x in range(width)])
        prev = line
    return width, height, rows


# --------------------------------------------------------------------------- #
# 抓屏
# --------------------------------------------------------------------------- #
def grab(x: int, y: int, w: int, h: int, out_path: str) -> None:
    """用 ffmpeg 抓取屏幕上的一块区域（本机已确认 ffmpeg 可用）"""
    cmd = ["ffmpeg", "-loglevel", "error", "-y",
           "-f", "x11grab", "-video_size", f"{w}x{h}", "-i", f":0.0+{x}+{y}",
           "-frames:v", "1", out_path]
    subprocess.run(cmd, check=True, timeout=30)


# --------------------------------------------------------------------------- #
# 方块判定
# --------------------------------------------------------------------------- #
def ink_ratio(rows, x0: int, x1: int, y0: int, y1: int,
              bg: tuple[int, int, int], tol: int = 40) -> float:
    """计算矩形区域内的「墨迹占比」（非背景像素的比例）"""
    height = len(rows)
    width = len(rows[0]) if rows else 0
    y0, y1 = max(0, y0), min(height, y1)
    x0, x1 = max(0, x0), min(width, x1)
    total = 0
    ink = 0
    for y in range(y0, y1):
        row = rows[y]
        for x in range(x0, x1):
            r, g, b = row[x]
            near_bg = (abs(r - bg[0]) < tol and abs(g - bg[1]) < tol
                       and abs(b - bg[2]) < tol)
            total += 1
            if not near_bg:
                ink += 1
    return ink / total if total else 0.0


def looks_like_tofu(rows, x0: int, x1: int, y0: int, y1: int,
                    bg: tuple[int, int, int]) -> bool:
    """
    判断这个字符格是不是「方块」。

    方块的像素特征很鲜明：外圈是一整圈方框，框内基本是空的。
    所以看中间区域（去边框各留 2px）的墨迹占比：方块约 <18%，正常汉字约 >30%。
    """
    inner_x0, inner_x1 = x0 + 2, x1 - 2
    inner_y0, inner_y1 = y0 + 2, y1 - 2
    if inner_x1 - inner_x0 < 2 or inner_y1 - inner_y0 < 2:
        return False
    return ink_ratio(rows, inner_x0, inner_x1, inner_y0, inner_y1, bg) < 0.18


def analyse_line(rows, y0: int, y1: int, x_start: int, cell: int,
                 chars: str, bg: tuple[int, int, int]) -> list[str]:
    """逐字符分析一行文本，返回被判为方块的字符列表"""
    bad = []
    for i, ch in enumerate(chars):
        x0 = x_start + i * cell
        x1 = x0 + cell
        if x1 >= len(rows[0]):
            break
        if looks_like_tofu(rows, x0, x1, y0, y1, bg):
            bad.append(ch)
    return bad


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main() -> int:
    args = sys.argv[1:]
    text = DEFAULT_TEXT
    sizes = [9, 10, 11, 12]
    fonts = list(DEFAULT_FONTS)
    if "--text" in args:
        text = args[args.index("--text") + 1]
    if "--sizes" in args:
        sizes = [int(s) for s in args[args.index("--sizes") + 1].split(",")]
    if "--fonts" in args:
        fonts = args[args.index("--fonts") + 1].split(",")

    BG = (16, 23, 34)          # 与程序主题一致的深色背景
    LINE_H = max(26, max(sizes) * 2 + 12)

    root = tk.Tk()
    root.geometry(f"+0+0")
    top = tk.Toplevel(root)
    top.geometry(f"+0+0")
    top.configure(bg="#101722")
    top.attributes("-topmost", True)
    labels = []
    for fam in fonts:
        for size in sizes:
            lbl = tk.Label(top, text=f"{fam}|{size}|{text}", bg="#101722", fg="#e8eef7",
                           font=(fam, size), anchor="w")
            lbl.pack(anchor="w", fill="x")
            labels.append((fam, size, lbl))
    top.update()
    top.update_idletasks()

    width = max(lbl.winfo_reqwidth() for _f, _s, lbl in labels) + 20
    height = sum(lbl.winfo_reqheight() for _f, _s, lbl in labels) + 20
    top.geometry(f"{width}x{height}+0+0")
    top.update()
    top.update_idletasks()
    root.update()

    out = os.path.join(tempfile.gettempdir(), "font_probe.png")
    grab(0, 0, width, height, out)
    w, h, rows = read_png(out)

    print(f"测评文本：{text}")
    print(f"截图：{out}  ({w}x{h})\n")
    print(f"{'字体':<16}{'字号':<6}{'方块数':<8}方块字符")
    print("-" * 66)
    results = {}
    for fam, size, lbl in labels:
        y0 = lbl.winfo_rooty()
        y1 = y0 + lbl.winfo_height()
        if y1 > h or y0 < 0:
            print(f"{fam:<16}{size:<6}{'-':<8}（超出截图范围，跳过）")
            continue
        # 前缀 "字体名|字号|" 的实际像素宽度用 font.measure 算出来，跳过它
        from tkinter import font as tkfont
        prefix = f"{fam}|{size}|"
        f = tkfont.Font(family=fam, size=size)
        x_start = 8 + f.measure(prefix)
        cell = max(1, f.measure("网"))
        bad = analyse_line(rows, y0, y1, x_start, cell, text, BG)
        results[(fam, size)] = bad
        print(f"{fam:<16}{size:<6}{len(bad):<8}{''.join(bad) if bad else '（全部正常）'}")

    good = [k for k, v in results.items() if not v]
    print("\n无方块的最佳组合：", good[:6] if good else "（没有完全干净的字体，建议安装 fonts-noto-cjk）")
    top.destroy()
    root.destroy()
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
