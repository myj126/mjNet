#!/usr/bin/env python3
"""
单元测试：不需要图形界面，也不需要联网。
覆盖几个容易出错的地方 —— 这些点在本机开发时都真实踩过坑。

用法: python3 tests/test_units.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from netmon import netinfo, netprobe  # noqa: E402
from netmon.app import fmt_speed, shorten  # noqa: E402

FAILED = []


def check(name, got, want):
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f"\n      得到 {got!r}\n      期望 {want!r}"))
    if not ok:
        FAILED.append(name)


def check_true(name, cond, detail=""):
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"  ({detail})"))
    if not cond:
        FAILED.append(name)


print("== 虚拟网卡识别（历史 bug：wlo1 因含 'lo' 子串被误判为回环）==")
check("回环 lo 是虚拟", netinfo._is_virtual("lo"), True)
check("WiFi wlo1 不是虚拟", netinfo._is_virtual("wlo1"), False)
check("有线 enp47s0 不是虚拟", netinfo._is_virtual("enp47s0"), False)
check("Windows 以太网 不是虚拟", netinfo._is_virtual("Ethernet"), False)
check("Windows WiFi 不是虚拟", netinfo._is_virtual("Wi-Fi"), False)
check("docker0 是虚拟", netinfo._is_virtual("docker0"), True)
check("br-xxx 是虚拟", netinfo._is_virtual("br-1a2b3c"), True)
check("veth1234 是虚拟", netinfo._is_virtual("veth1234"), True)
check("wg0 是虚拟", netinfo._is_virtual("wg0"), True)
check("tailscale0 是虚拟", netinfo._is_virtual("tailscale0"), True)
check("vEthernet (WSL) 是虚拟", netinfo._is_virtual("vEthernet (WSL)"), True)

print("\n== 网速格式化 ==")
check("500 kb/s", fmt_speed(0.5), ("500.0", "kb/s"))
check("2.5 Mb/s", fmt_speed(2.5), ("2.50", "Mb/s"))
check("0.0004 Mb/s -> 400 b/s", fmt_speed(0.0004), ("400", "b/s"))
check("异常值", fmt_speed(-1), ("--", "Mb/s"))

print("\n== 文本截断 ==")
check("不截断", shorten("abc", 10), "abc")
check("截断加省略号", shorten("abcdefghij", 5), "abcd…")

print("\n== 代理检测（真实环境）==")
proxy = netinfo.detect_proxy()
check_true("返回 dict 且含必需字段",
           isinstance(proxy, dict) and {"enabled", "source", "detail", "local_clients"} <= set(proxy),
           f"实际 {proxy}")

print("\n== 代理状态语义（「已配置」vs「只是程序在跑」）==")
proxy = netinfo.detect_proxy()
check_true("结果包含 configured / in_use / process 字段",
           {"configured", "in_use", "process"} <= set(proxy), f"实际字段 {sorted(proxy)}")
check_true("enabled 与 configured 不矛盾",
           (not proxy["enabled"]) or proxy["configured"] or bool(proxy["local_clients"]),
           f"enabled={proxy['enabled']} configured={proxy['configured']}")
if proxy.get("configured"):
    check_true("已配置时 in_use 为 True", proxy.get("in_use") is True)
    check_true("已配置时有具体来源", bool(proxy.get("source")), proxy.get("source"))
else:
    check_true("仅程序在跑时 in_use 为 False", proxy.get("in_use") is False,
               f"in_use={proxy.get('in_use')}")
    check_true("仅程序在跑时来源标注清楚",
               "程序" in str(proxy.get("source", "")) or not proxy.get("enabled"),
               proxy.get("source"))
# 探测进程名的函数必须安全（不能抛异常）
names = netinfo.proxy_process_names()
check_true("代理进程名探测返回列表", isinstance(names, list), str(type(names)))

print("\n== 出口对比（判断流量是否真走代理）==")
check_true("compare_egress 函数存在且可调用", callable(getattr(netprobe, "compare_egress", None)))
egress = netprobe.compare_egress(timeout=6)
check_true("返回 direct/current/proxied 字段",
           {"direct", "current", "proxied"} <= set(egress), f"实际 {sorted(egress)}")
check_true("proxied 取值合法",
           egress["proxied"] in (True, False, None), repr(egress["proxied"]))
if egress["proxied"] is not None:
    check_true("proxied 与两个 IP 是否相同一致",
               egress["proxied"] == (egress["direct"] != egress["current"]),
               f"{egress}")
    print(f"    · 直连出口 {egress['direct']} / 当前出口 {egress['current']} / 走代理={egress['proxied']}")

print("\n== 本机信息采集（真实环境，不应抛异常）==")
collector = netinfo.InfoCollector()
info = collector.collect(force_slow=True)
check_true("拿到本机 IP", bool(info.ip), f"ip={info.ip!r}")
check_true("拿到默认网关", bool(info.gateway), f"gateway={info.gateway!r}")
check_true("DNS 不含 127.x 本地存根", all(not d.startswith("127.") for d in info.dns), f"dns={info.dns}")
check_true("连接数为非负整数", isinstance(info.connections, int) and info.connections >= 0)
check_true("网卡计数 > 0（否则网速永远为 0）", collector.meter.nic_count > 0,
           f"nic_count={collector.meter.nic_count}")
check_true("连接方式已识别", info.link.kind != "未知", f"kind={info.link.kind!r}")

print("\n== 延迟统计 ==")
tracker = netprobe.LatencyTracker(size=4)
for value in (10.0, 20.0, None, 12.0):
    tracker.add(value)
check("丢包率 25%", round(tracker.loss_rate, 1), 25.0)
check("抖动 = (|20-10|+|12-20|)/2 = 9", round(tracker.jitter_ms, 1), 9.0)
tracker.reset()
check("重置后无抖动", tracker.jitter_ms, None)

print("\n== Windows 原生解析逻辑（已抽到 netparse，本机可完整验证）==")
from netmon import netparse

rows = [
    ("以太网", "Intel(R) Ethernet Connection I219-V", netparse.IF_TYPE_ETHERNET_CSMACD),
    ("WLAN", "Intel(R) Wi-Fi 6 AX201 160MHz", netparse.IF_TYPE_IEEE80211),
    ("本地连接* 1", "Microsoft Wi-Fi Direct Virtual Adapter", netparse.IF_TYPE_IEEE80211),
    ("Loopback", "Microsoft KM-TEST Loopback Adapter", netparse.IF_TYPE_SOFTWARE_LOOPBACK),
    ("vEthernet (WSL)", "Hyper-V Virtual Ethernet Adapter", netparse.IF_TYPE_ETHERNET_CSMACD),
    ("以太网 2", "TAP-Windows Adapter V9", netparse.IF_TYPE_ETHERNET_CSMACD),
]
kept = [alias for alias, desc, t in rows if not netparse.is_virtual_if(alias, desc, t)]
check("过滤后只剩真实网卡（含 WiFi）", kept, ["以太网", "WLAN"])
check("WiFi 类型被正确识别",
      [t for alias, desc, t in rows if not netparse.is_virtual_if(alias, desc, t)],
      [netparse.IF_TYPE_ETHERNET_CSMACD, netparse.IF_TYPE_IEEE80211])
# 网关 DWORD 解析：值是网络字节序
check("网关 DWORD 解析 192.168.1.1", netparse.dword_to_ip(0x0101A8C0), "192.168.1.1")
check("网关 DWORD 解析 10.0.0.1", netparse.dword_to_ip(0x0100000A), "10.0.0.1")
check("网关 DWORD 解析 172.16.5.9", netparse.dword_to_ip(0x090510AC), "172.16.5.9")
check("网关 DWORD 解析 192.168.31.1", netparse.dword_to_ip(0x011FA8C0), "192.168.31.1")

print("\n== 网络诊断：故障场景结论（纯函数，可离线验证）==")
from netmon import diagnose as D

def st(**kw):
    base = {k: D.OK for k in ("nic", "gateway", "dns", "domestic", "overseas", "egress")}
    base.update(kw)
    return base

cases = [
    ("全部正常", st(), "网络一切正常"),
    ("网卡不通", st(nic=D.FAIL), "本机没有可用网络连接"),
    ("路由器不通", st(gateway=D.FAIL), "连不上路由器"),
    ("DNS 解析失败", st(dns=D.FAIL), "域名解析失败"),
    ("外网不通", st(domestic=D.FAIL), "访问不了外网"),
    ("仅国外不通", st(overseas=D.FAIL), "国内上网正常"),
    ("仅出口查询失败", st(egress=D.WARN), "网络基本正常"),
]
for name, statuses, expect in cases:
    conclusion, advice = D.build_conclusion(statuses)
    check_true(f"{name} → {expect}", expect in conclusion, f"实际结论：{conclusion}")
    # 需要用户动手的场景必须给出建议；一切正常 / 已声明可忽略的场景不该硬凑建议
    benign = "一切正常" in conclusion or "可忽略" in conclusion
    if benign:
        check_true(f"{name} 无需建议", advice == "", f"不该有建议：{advice}")
    else:
        check_true(f"{name} 给出了建议", bool(advice), "缺少建议")

print("\n== 网络诊断：报告格式化 ==")
diag = D.Diagnosis(conclusion="测试结论", advice="测试建议")
diag.layers = [
    D.Layer("gateway", "② 路由器（默认网关）", D.FAIL, "10.0.0.1 不可达", None, "重启路由器"),
    D.Layer("dns", "③ 域名解析（DNS）", D.OK, "解析成功", 12.3),
]
report = D.format_report(diag)
check_true("报告含分层名称", "② 路由器" in report)
check_true("报告含建议", "建议：重启路由器" in report)
check_true("报告含耗时", "12ms" in report)
check_true("报告含结论", "结论：测试结论" in report)

print("\n== 字体解析与字号表 ==")
try:
    import tkinter as tk
    _root = tk.Tk(); _root.withdraw()
    from netmon.app import (BASE_FONT_SIZE, FONT_CANDIDATES, has_truetype_fonts,
                            resolve_font_family, sizes_for)
    from tkinter import font as tkfont
    family, capable = resolve_font_family()
    available = {f.lower() for f in tkfont.families()}
    check_true("选出的字体非空", bool(family))
    check_true("选出的字体确实存在", family.lower() in available, f"family={family!r}")
    check_true("capable 与实际 TrueType 能力一致", capable == has_truetype_fonts(),
               f"capable={capable} tt={has_truetype_fonts()}")
    # 字号表：必须单调递增且都不小于最小值
    sizes = sizes_for(BASE_FONT_SIZE)
    check_true("字号表包含全部语义档位",
               set(sizes) == {"tiny", "small", "body", "title", "section", "heading"})
    check_true("字号单调不减",
               sizes["tiny"] <= sizes["small"] <= sizes["body"] <= sizes["title"]
               <= sizes["section"] <= sizes["heading"], f"{sizes}")
    check_true("字号都不小于 8（太小会缺字）", min(sizes.values()) >= 8, f"{sizes}")
    check("基准字号用于正文", sizes["body"], BASE_FONT_SIZE)
    # 用户指定一个存在的字体时应该被采纳
    probe_name = next((n for n in FONT_CANDIDATES if n.lower() in available), "")
    if probe_name:
        got, _ = resolve_font_family(probe_name)
        check_true(f"用户指定的字体被优先采用（{probe_name}）", got == probe_name, f"得到 {got}")
    _root.destroy()
except Exception as exc:
    check_true(f"字体测试可执行（{type(exc).__name__}）", False, str(exc))

print("\n== 配置文件读写 ==")
from netmon.config import config_path, load_config
check_true("配置路径在用户目录下", os.path.expanduser("~") in config_path(), config_path())
check_true("配置读取不抛异常且返回 dict", isinstance(load_config(), dict))

print("\n== 解释器字体能力探测脚本 ==")
probe_script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "tools", "tk_check.py")
check_true("tk_check.py 存在", os.path.exists(probe_script))
if os.path.exists(probe_script):
    import subprocess as _sp
    r = _sp.run([sys.executable, probe_script], capture_output=True, text=True)
    check_true("探测脚本退出码合法（0=可用 / 1=缺字体）", r.returncode in (0, 1),
               f"returncode={r.returncode} stderr={r.stderr[:100]}")

print("\n== 桌面集成：已安装文件是否就位 ==")
home = os.path.expanduser("~")
desktop_file = os.path.join(home, ".local/share/applications/mjnet.desktop")
wrapper = os.path.join(home, ".local/bin/mjnet")
icons = [os.path.join(home, f".local/share/icons/hicolor/{n}x{n}/apps/mjnet.png")
         for n in (48, 64, 128, 256)]
if os.path.exists(desktop_file):
    content = open(desktop_file, encoding="utf-8").read()
    check_true("菜单项存在且指向启动命令", "Exec=" in content and "mjnet" in content, content[:80])
    check_true("菜单项不含 Terminal=true", "Terminal=false" in content)
    check_true("启动命令可执行", os.access(wrapper, os.X_OK))
    check_true("图标 4 个尺寸齐全", all(os.path.exists(i) for i in icons))
    import struct
    for size, path in zip((48, 64, 128, 256), icons):
        if os.path.exists(path):
            raw = open(path, "rb").read()
            w, h = struct.unpack(">II", raw[16:24])
            check(f"图标 {size} 像素精确", (w, h), (size, size))
else:
    print("  – 未安装桌面集成，跳过（执行 ./install-desktop.sh 可安装）")

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项: {', '.join(FAILED)}")
    sys.exit(1)
print("全部单元测试通过 ✅")
