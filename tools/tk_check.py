#!/usr/bin/env python3
"""
解释器体检 —— 判断某个 Python 能否正常跑 mjNet 的图形界面。

检查两件事（缺一不可）：
  1. 有没有 tkinter；
  2. Tk 有没有中文渲染能力。

第 2 点是踩过的大坑：conda 自带的 Tk 往往**没有编译 Xft/fontconfig**，
Tk 找不到请求的字体族时会静默替换成 "fixed" 这类老式位图字体，
界面上就会零散出现方块（豆腐块）。这种 Tk 用起来看着"能跑"，实际是残废的。
这里用一个已知的中文字体名去试探：能原样报回来就说明 Tk 真的能用 TrueType 字体。

用法（供 shell 脚本调用）：
    python3 tools/tk_check.py            # 正常：无输出、退出码 0；有问题：打印原因、退出码非 0
    python3 tools/tk_check.py --verbose  # 总是打印详细信息
"""
from __future__ import annotations

import sys

# 拿来做试探的中文字体：Linux/Windows/macOS 上任一命中即可
PROBE_FAMILIES = (
    "Noto Sans CJK SC",
    "Droid Sans Fallback",
    "WenQuanYi Micro Hei",
    "Microsoft YaHei",
    "PingFang SC",
    "DejaVu Sans",          # 西文兜底，至少确认字体系统是通的
)


def main() -> int:
    verbose = "--verbose" in sys.argv
    try:
        import tkinter as tk
        from tkinter import font as tkfont
    except Exception as exc:
        print(f"tkinter 不可用：{exc}", file=sys.stderr)
        return 2

    try:
        root = tk.Tk()
        root.withdraw()
    except Exception as exc:
        print(f"无法连接图形界面（DISPLAY 是否正确？）：{exc}", file=sys.stderr)
        return 3

    try:
        available = {f.lower() for f in tkfont.families()}
        capable = []
        for name in PROBE_FAMILIES:
            if name.lower() not in available:
                continue
            actual = tkfont.Font(family=name, size=11).actual("family")
            if actual.lower() == name.lower():
                capable.append(name)
        resolved = tkfont.Font(family="TkDefaultFont", size=11).actual("family")
        tk_version = root.tk.call("info", "patchlevel")
    finally:
        root.destroy()

    if verbose:
        print(f"tkinter     : 可用（Tk {tk_version}）")
        print(f"默认字体    : {resolved!r}")
        print(f"可用中文字体: {capable or '（无）'}")
        print(f"结论        : {'[OK] 能正常显示中文' if capable else '[X] 缺少中文 TrueType 字体'}")

    if not capable:
        print("该 Python 的 Tk 无法使用任何中文 TrueType 字体（很可能没编译 Xft/fontconfig），"
              "界面会出现方块字。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
