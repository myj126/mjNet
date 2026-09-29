#!/usr/bin/env python3
"""
mjNet 启动入口。

用法：
    python3 run.py            # 启动图形界面
    python3 run.py --check    # 只做环境自检（不打开窗口），排查问题用
    python3 run.py --report   # 在终端打印一次网络报告（无图形环境时很有用）
    python3 run.py --diagnose # 在终端跑完整网络诊断（含"流量是否真的走代理"验证）
"""
from __future__ import annotations

import os
import sys

# 让脚本在任意工作目录下都能找到 netmon 包
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _fatal(message: str) -> None:
    print(message, file=sys.stderr)
    sys.exit(1)


def _check_tkinter() -> bool:
    try:
        import tkinter  # noqa: F401
        return True
    except Exception as exc:
        print("[X] 未找到 tkinter：", exc)
        if os.name != "nt":
            print("  Ubuntu/Debian: sudo apt install python3-tk")
            print("  Fedora:        sudo dnf install python3-tkinter")
            print("  Arch:          sudo pacman -S tk")
        return False


def _font_diagnose() -> None:
    """
    检查中文字体能不能正常显示。

    这一步很值得做：conda 之类发行版自带的 Tk 常常**没有编译 Xft/fontconfig**，
    它拿不到任何中文 TrueType 字体时会静默换成老式位图字体，界面就零散出现方块。
    程序本身会尽量挑可用的字体，但换成有 Xft 支持的 Python 解释器才是根本解法。
    """
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from netmon.app import BASE_FONT_SIZE, has_truetype_fonts, resolve_font_family
        family, _ = resolve_font_family()
        tt = has_truetype_fonts()
        tk_version = root.tk.call("info", "patchlevel")
        root.destroy()
        print(f"中文字体    : 选用 {family!r}（Tk {tk_version}；"
              f"{'可用 TrueType 矢量字体' if tt else '只有老式位图字体'}）")
        if tt:
            print("            : [OK] 显示正常（Tk 能用 TrueType 中文字体）")
        else:
            print("            : [X] 这个 Python 的 Tk 拿不到中文 TrueType 字体，")
            print("              界面可能出现方块字（不是程序问题，是解释器/字体环境问题）")
            print("              解决办法（任选其一）：")
            print("                1) 换用系统 Python 启动：")
            print("                   MJNET_PYTHON=/usr/bin/python3 ./启动-linux.sh")
            print("                2) 安装中文字体后重试：")
            print("                   sudo apt install fonts-noto-cjk")
            print("                3) 重新装一次桌面集成（会自动挑可用的解释器）：")
            print("                   ./install-desktop.sh")
    except Exception as exc:
        print(f"中文字体    : 无法检测（{exc}）")


def run_check() -> int:
    print("=== mjNet 环境自检 ===")
    print(f"Python      : {sys.version.split()[0]}  ({sys.platform})")
    tk_ok = _check_tkinter()
    print(f"tkinter     : {'[OK] 可用' if tk_ok else '[X] 不可用（图形界面无法启动）'}")
    if tk_ok:
        _font_diagnose()
    try:
        import psutil
        print(f"psutil      : [OK] {psutil.__version__}（可选增强，已启用）")
    except Exception:
        print("psutil      : – 未安装（可选，不影响使用；想装就 pip install psutil）")

    from netmon import netinfo, netprobe
    info = netinfo.InfoCollector().collect(force_slow=True)
    print("\n--- 本机信息 ---")
    print(f"主机名      : {info.host}")
    print(f"本机 IP     : {info.ip or '未获取'}")
    print(f"默认网关    : {info.gateway or '未获取'}")
    print(f"DNS 服务器  : {', '.join(info.dns) or '未获取'}")
    print(f"连接方式    : {info.link.kind} {info.link.iface} "
          + (f"SSID={info.link.ssid} 信号={info.link.signal}%" if info.link.kind.startswith('WiFi') else ""))
    print(f"TCP 连接数  : {info.connections}")
    print(f"代理        : " + (f"已启用 · {info.proxy.get('source')} {info.proxy.get('detail')}"
                              if info.proxy.get("enabled") else "未检测到"))

    print("\n--- 联网探测 ---")
    pub = netprobe.public_ip_info(timeout=5)
    if pub.get("ok"):
        print(f"公网 IP     : {pub['ip']}  {pub.get('country','')} {pub.get('region','')} "
              f"{pub.get('city','')} {pub.get('isp','')}  (来源 {pub['source']})")
    else:
        print(f"公网 IP     : 获取失败 -> {pub.get('error')}")
    lat = netprobe.probe_latency()
    for target in lat["targets"]:
        ms = f"{target['ms']:.0f} ms" if target["ms"] is not None else "超时"
        print(f"延迟 {target['name']:<16}: {ms}")
    print("\n自检结束。")
    return 0


def run_diagnose_cli() -> int:
    """在终端跑一次完整网络诊断（不需要图形界面）"""
    from netmon import diagnose
    print("正在诊断（约 5~10 秒）…\n")
    result = diagnose.run_diagnosis()
    print(diagnose.format_report(result))
    return 0 if result.worst in (diagnose.OK, diagnose.WARN) else 1


def run_report() -> int:
    from netmon import netinfo, netprobe
    info = netinfo.InfoCollector().collect(force_slow=True)
    print(f"主机名     : {info.host}")
    print(f"本机 IP    : {info.ip}")
    print(f"公网 IP    : {netprobe.public_ip_info().get('ip') or '获取失败'}")
    print(f"默认网关   : {info.gateway}")
    print(f"DNS        : {', '.join(info.dns)}")
    print(f"连接方式   : {info.link.kind} {info.link.iface} {info.link.ssid}")
    print(f"代理       : " + (f"已启用 {info.proxy.get('source')} {info.proxy.get('detail')}"
                              if info.proxy.get("enabled") else "未检测到"))
    return 0


def main() -> int:
    args = set(sys.argv[1:])
    if {"-h", "--help"} & args:
        print(__doc__)
        return 0
    if "--check" in args:
        return run_check()
    if "--report" in args:
        return run_report()
    if "--diagnose" in args:
        return run_diagnose_cli()
    if not _check_tkinter():
        _fatal("请先安装 tkinter 后再运行。")
    try:
        from netmon.app import main as app_main
    except Exception as exc:
        _fatal(f"加载程序失败：{exc}")
    try:
        return app_main()
    except Exception:
        import traceback
        traceback.print_exc()
        # Windows 下用 pythonw 启动时没有控制台，把崩溃信息落到文件里便于排查
        try:
            log_path = os.path.join(os.path.expanduser("~"), "mjnet-crash.log")
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write("\n===== 崩溃 =====\n")
                fh.write(traceback.format_exc())
            print(f"程序异常退出，详情已写入 {log_path}", file=sys.stderr)
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
