#!/usr/bin/env python3
"""
UI 自动化验证脚本（开发自用，不属于程序本体）
在真实 X 显示下启动界面，依次触发每个交互点，任何异常都会打印 traceback。
用法: python3 tests/ui_smoke.py
"""
import os
import sys
import threading
import time
import traceback
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from netmon.app import NetMonApp  # noqa: E402

errors: list[str] = []
steps: list[str] = []


def guard(name, fn):
    steps.append(name)
    try:
        fn()
        print(f"  ✓ {name}")
    except Exception:
        errors.append(name)
        print(f"  ✗ {name}\n{traceback.format_exc()}")


def main() -> int:
    app = NetMonApp()
    app.attributes("-topmost", True)
    print("== 启动完成，开始交互测试 ==")

    # 造真实下载流量：后台下载一个几十 MB 的测试文件
    def traffic():
        try:
            req = urllib.request.Request(
                "http://speed.cloudflare.com/__down?bytes=60000000",
                headers={"User-Agent": "mjNet-test"})
            with urllib.request.urlopen(req, timeout=25) as r:
                while r.read(262144):
                    pass
        except Exception as exc:
            print(f"  (流量测试结束: {type(exc).__name__})")

    threading.Thread(target=traffic, daemon=True).start()

    def step_manual_refresh():
        app.manual_refresh()

    def step_wait_results():
        # 等待后台线程把公网 IP / 延迟结果送回来
        deadline = time.time() + 20
        while time.time() < deadline:
            app.update()
            if app.remote.public_ip is not None and app.remote.latency is not None:
                return
            time.sleep(0.1)
        raise AssertionError("20 秒内没有拿到公网 IP 或延迟结果")

    def step_check_values():
        pub = app.remote.public_ip or {}
        assert pub.get("ok"), f"公网 IP 查询失败: {pub.get('error')}"
        assert app._last_local and app._last_local.ip, "本机 IP 为空"
        assert app._last_local.dns, "DNS 为空"
        assert app.remote.latency and app.remote.latency.get("min"), "延迟为空"
        # 网速必须能测到（前面有下载流量）
        deadline = time.time() + 25
        peak = 0.0
        while time.time() < deadline:
            app.update()
            time.sleep(0.2)
            peak = max(app._peak_down, peak)
            if peak > 1.0:
                break
        assert peak > 1.0, f"没有测到下载速度（峰值 {peak} Mb/s），网速功能可能失效"
        print(f"    · 实测下载峰值 {peak:.2f} Mb/s，公网 IP {pub.get('ip')}，"
              f"延迟 {app.remote.latency['min']:.0f}ms，SSID={app._last_local.link.ssid!r}")

    def step_layout_not_clipped():
        """回归测试：每张卡片的内容都必须装得下（曾被字号撑破、把数值裁掉）"""
        app.update()
        app.update_idletasks()
        bad = []
        for name, card in (("网速", app.card_speed), ("延迟", app.card_latency),
                           ("本机IP", app.card_local), ("公网IP", app.card_public),
                           ("连接方式", app.card_link), ("代理", app.card_proxy),
                           ("DNS/网关", app.card_dns), ("网卡/连接", app.card_nic)):
            need, have = card.winfo_reqheight(), card.winfo_height()
            if need > have + 2:
                bad.append(f"{name} 需要{need}px 只有{have}px")
            # 数值/说明文字也不能超出卡片宽度
            for label in (card.value_label, card.hint_label):
                if label.winfo_reqwidth() > card.winfo_width():
                    bad.append(f"{name} 的 {label.winfo_class()} 过宽")
        assert not bad, "卡片内容被裁切：" + "；".join(bad)

    def step_chart():
        app._draw_chart()
        assert len(app.chart.find_all()) > 0, "波形图没有画出任何元素"

    def step_toggle_auto():
        app.auto_refresh.set(False)
        app._on_toggle_auto()
        assert "暂停" in app.state_label.cget("text")
        app.auto_refresh.set(True)
        app._on_toggle_auto()
        assert "已连接" in app.state_label.cget("text") or "未连接" in app.state_label.cget("text")

    def step_copy():
        app.copy_text(app._last_local.ip, "本机 IP")
        assert app.clipboard_get() == app._last_local.ip, "剪贴板内容不符"
        app.copy_text((app.remote.public_ip or {}).get("ip", ""), "公网 IP")

    def step_report():
        text = app._report_text()
        for key in ("本机 IP", "公网 IP", "默认网关", "DNS 服务器", "代理", "实时网速"):
            assert key in text, f"报告缺少字段 {key}"
        out = "/tmp/mjnet-report.txt"
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"    · 报告已写到 {out}（{len(text)} 字符）")

    def step_about():
        app.show_about()
        app.update()
        tops = [w for w in app.winfo_children() if w.winfo_class() == "Toplevel"]
        assert tops, "关于窗口没有创建"
        for w in tops:
            w.destroy()
        app.update()

    def step_latency_loss_path():
        # 模拟探测全部失败的情况，确认界面不崩
        app._apply_latency({"targets": [], "min": None, "max": None, "avg": None,
                            "domestic_ms": None, "overseas_ms": None})
        assert "超时" in app.card_latency.value_label.cget("text")
        # 模拟公网 IP 失败
        app._apply_public_ip({"ok": False, "ip": "", "error": "模拟失败"})
        assert "失败" in app.card_public.value_label.cget("text")

    def step_diagnosis():
        """点击诊断按钮 -> 后台跑完 -> 窗口里出现分层结果"""
        app.run_diagnosis()
        app.update()
        assert app._diag_window is not None and app._diag_window.winfo_exists(), "诊断窗口没打开"
        assert "诊断中" in app.btn_diagnose.cget("text"), "按钮没有进入诊断中状态"
        deadline = time.time() + 60
        while time.time() < deadline:
            app.update()
            if app.last_diagnosis is not None:
                break
            time.sleep(0.2)
        assert app.last_diagnosis is not None, "60 秒内没拿到诊断结果"
        diag = app.last_diagnosis
        keys = [l.key for l in diag.layers]
        # 第 7 层「流量走向验证」用于判断流量是否真的经过代理
        assert keys == ["nic", "gateway", "dns", "domestic", "overseas", "egress", "routing"], \
            f"分层缺失: {keys}"
        assert diag.conclusion, "没有生成结论"
        assert "网络诊断" in app.btn_diagnose.cget("text"), "按钮没有恢复"
        # 窗口里的文字应包含结论与各层名称
        shown = app._diag_text.get("1.0", "end")
        for token in ("结论：", "[1] 本机网卡与 IP", "[2] 路由器", "[3] 域名解析",
                      "[4] 国内网络连通", "[7] 流量走向验证"):
            assert token in shown, f"诊断窗口缺少内容: {token}"
        # 复制报告
        app._copy_diag_report()
        clip = app.clipboard_get()
        assert "网络诊断报告" in clip, "复制的诊断报告内容不对"
        print(f"    · 诊断结论：{diag.conclusion}")
        for layer in diag.layers:
            print(f"      [{layer.status}] {layer.name} {layer.detail[:46]}")
        # 导出报告里也要带上诊断结果
        assert "网络诊断" in app._report_text(), "总报告里没有带诊断结果"

    def step_pump_events():
        for _ in range(5):
            app._drain_events()
            app.update()
            time.sleep(0.05)

    for name, fn in [
        ("手动刷新", step_manual_refresh),
        ("等待远程结果", step_wait_results),
        ("校验各项数值", step_check_values),
        ("布局不被裁切", step_layout_not_clipped),
        ("波形绘制", step_chart),
        ("自动刷新开关", step_toggle_auto),
        ("剪贴板复制", step_copy),
        ("导出报告文本", step_report),
        ("关于/诊断窗口", step_about),
        ("异常分支(超时/失败)", step_latency_loss_path),
        ("一键网络诊断", step_diagnosis),
        ("事件队列消费", step_pump_events),
    ]:
        guard(name, fn)

    print("\n== 界面内部日志 ==")
    print(app._log_text())

    app.destroy()
    print(f"\n结果: {len(steps) - len(errors)}/{len(steps)} 项通过")
    if errors:
        print("失败项: " + ", ".join(errors))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
