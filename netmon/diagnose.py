"""
netmon.diagnose —— 分层网络诊断：直接告诉新手“到底哪一段坏了”

诊断顺序（由近到远，一层失败就能定位问题所在）：
  1. 本机网卡      是否有可用的物理网卡、拿到 IP
  2. 默认网关      能不能连上路由器（ICMP ping 失败时自动退回 TCP 探测）
  3. DNS 解析      域名能不能翻译成 IP（不依赖上一步之外的服务）
  4. 国内连通      国内网站/服务能不能通
  5. 国外连通      国外服务能不能通（不通很常见，通常是网络环境所限）
  6. 代理出口      公网 IP 是否与预期一致（辅助判断是否走了代理）

设计原则：任何一层失败都不抛异常，只记录原因，保证总能给出人话结论。
耗时约 5~10 秒，必须在后台线程里调用。
"""
from __future__ import annotations

import re
import socket
import subprocess
import time
from dataclasses import dataclass, field

from . import netinfo, netprobe

# 状态取值
OK = "ok"          # 正常
WARN = "warn"      # 有异常但不致命
FAIL = "fail"      # 失败
SKIP = "skip"      # 跳过

STATUS_LABEL = {OK: "正常", WARN: "有异常", FAIL: "失败", SKIP: "跳过"}


@dataclass
class Layer:
    key: str
    name: str
    status: str = SKIP
    detail: str = ""
    ms: float | None = None
    advice: str = ""


@dataclass
class Diagnosis:
    layers: list[Layer] = field(default_factory=list)
    conclusion: str = ""
    advice: str = ""
    ran_at: float = 0.0

    def layer(self, key: str) -> Layer | None:
        return next((x for x in self.layers if x.key == key), None)

    @property
    def worst(self) -> str:
        for status in (FAIL, WARN, OK):
            if any(x.status == status for x in self.layers):
                return status
        return SKIP


# --------------------------------------------------------------------------- #
# 单项探测
# --------------------------------------------------------------------------- #
def icmp_ping(host: str, timeout_s: float = 1.5, count: int = 2) -> float | None:
    """
    ICMP ping（毫秒）。需要 root 或 ping 带 setuid/setcap，失败时调用方会退回 TCP 探测。
    输出示例：rtt min/avg/max/mdev = 1.234/1.234/1.234/0.000 ms
    """
    try:
        p = subprocess.run(
            ["ping", "-n", "-q", "-c", str(count), "-W", str(max(1, int(timeout_s))), host],
            capture_output=True, text=True, errors="ignore", timeout=timeout_s * count + 2,
        )
    except Exception:
        return None
    if p.returncode != 0:
        return None
    m = re.search(r"=\s*[\d.]+/([\d.]+)/", p.stdout or "")
    return float(m.group(1)) if m else None


def reachable(host: str, ports=(80, 443, 53), timeout: float = 1.2) -> tuple[bool, float | None, str]:
    """
    判断某个地址是否可达：先 ICMP，不行再试几个常见 TCP 端口。
    返回 (是否可达, 延迟ms, 使用的方式说明)
    """
    ms = icmp_ping(host)
    if ms is not None:
        return True, ms, "ICMP"
    for port in ports:
        got = netprobe.tcp_latency_ms(host, port, timeout=timeout)
        if got is not None:
            return True, got, f"TCP:{port}"
    return False, None, ""


def resolve_dns(name: str = "www.baidu.com", timeout: float = 3.0) -> tuple[bool, float | None, str]:
    """测一次域名解析，返回 (是否成功, 耗时ms, 解析到的IP)"""
    old = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout)
    try:
        t0 = time.monotonic()
        infos = socket.getaddrinfo(name, 443, socket.AF_INET, socket.SOCK_STREAM)
        return True, (time.monotonic() - t0) * 1000, infos[0][4][0]
    except Exception:
        return False, None, ""
    finally:
        socket.setdefaulttimeout(old)


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def run_diagnosis(collector: netinfo.InfoCollector | None = None) -> Diagnosis:
    """完整跑一遍诊断（约 5~10 秒，调用方请放在后台线程）"""
    diag = Diagnosis(ran_at=time.time())
    collector = collector or netinfo.InfoCollector()

    # ---- 1) 本机网卡与 IP ----
    info = collector.collect(force_slow=True)
    link = info.link
    if not info.ip:
        diag.layers.append(Layer(
            "nic", "[1] 本机网卡与 IP", FAIL, "没有取到任何有效 IP 地址",
            advice="检查网线是否插好、WiFi 是否连上；也可以在终端执行 ip addr 看网卡状态。"))
    elif collector.meter.nic_count == 0:
        diag.layers.append(Layer(
            "nic", "[1] 本机网卡与 IP", WARN,
            f"IP {info.ip}，但没有识别到物理网卡（网速会显示 0）",
            advice="可能只有虚拟网卡在工作，请把网卡名字反馈给开发者。"))
    else:
        kind = link.kind or "未知"
        extra = f"，{kind}"
        if kind.startswith("WiFi"):
            extra += f" SSID={link.ssid or '未知'} 信号={link.signal}%"
        diag.layers.append(Layer("nic", "[1] 本机网卡与 IP", OK,
                                 f"IP {info.ip}，统计到 {collector.meter.nic_count} 个物理网卡{extra}"))

    gateway = info.gateway
    # ---- 2) 默认网关 ----
    if not gateway:
        diag.layers.append(Layer("gateway", "[2] 路由器（默认网关）", WARN, "没有读到默认网关",
                                 advice="可能是网络未配置完整，试试重新连接网络或重启路由器。"))
    else:
        ok, ms, how = reachable(gateway)
        if ok:
            diag.layers.append(Layer("gateway", "[2] 路由器（默认网关）", OK,
                                     f"{gateway} 可达（{how}，{ms:.0f}ms）", ms))
        else:
            diag.layers.append(Layer(
                "gateway", "[2] 路由器（默认网关）", FAIL, f"{gateway} 不可达",
                advice="问题就出在这里：本机到路由器这一段不通。"
                       "先确认 WiFi/网线连接，再重启路由器试试。"))

    # ---- 3) DNS 解析 ----
    dns_ok, dns_ms, dns_ip = resolve_dns()
    if dns_ok:
        detail = f"解析 www.baidu.com → {dns_ip}（{dns_ms:.0f}ms，使用 {', '.join(info.dns[:2]) or '系统默认 DNS'}）"
        status = OK if dns_ms < 800 else WARN
        advice = "" if status == OK else "解析偏慢，可以考虑把 DNS 换成 223.5.5.5 / 119.29.29.29。"
        diag.layers.append(Layer("dns", "[3] 域名解析（DNS）", status, detail, dns_ms, advice))
    else:
        diag.layers.append(Layer(
            "dns", "[3] 域名解析（DNS）", FAIL, "域名无法解析成 IP",
            advice="典型症状是“浏览器打不开网页，但 QQ 能用”。"
                   "把 DNS 改成 223.5.5.5 或 119.29.29.29 通常能解决。"))

    # ---- 4) 国内连通 ----
    dom_hosts = [("223.5.5.5", "阿里 DNS"), ("www.baidu.com", "百度")]
    dom_ok, dom_ms, dom_how = False, None, ""
    for host, label in dom_hosts:
        ok, ms, how = reachable(host, ports=(443, 80))
        if ok:
            dom_ok, dom_ms, dom_how = True, ms, f"{label} {how}"
            break
    if dom_ok:
        diag.layers.append(Layer("domestic", "[4] 国内网络连通", OK,
                                 f"可达（{dom_how}，{dom_ms:.0f}ms）", dom_ms))
    else:
        diag.layers.append(Layer(
            "domestic", "[4] 国内网络连通", FAIL, "国内网站/服务都连不上",
            advice="路由器通了但外网不通，多为宽带欠费、光猫故障或运营商线路问题。"
                   "可以打电话给运营商报修。"))

    # ---- 5) 国外连通（不通不算故障）----
    ovs_ok, ovs_ms, ovs_how = False, None, ""
    for host, label in (("1.1.1.1", "Cloudflare"), ("www.google.com", "Google")):
        ok, ms, how = reachable(host, ports=(443,), timeout=1.5)
        if ok:
            ovs_ok, ovs_ms, ovs_how = True, ms, f"{label} {how}"
            break
    if ovs_ok:
        diag.layers.append(Layer("overseas", "[5] 国外网络连通", OK,
                                 f"可达（{ovs_how}，{ovs_ms:.0f}ms）", ovs_ms))
    else:
        diag.layers.append(Layer(
            "overseas", "[5] 国外网络连通", WARN, "国外服务全部超时",
            advice="国内正常、国外不通 —— 绝大多数情况下是网络环境所限（防火墙/线路），"
                   "不是你的电脑坏了，通常需要代理才能访问。"))

    # ---- 6) 公网出口与代理 ----
    pub = netprobe.public_ip_info(timeout=6)
    if pub.get("ok"):
        loc = " ".join(x for x in (pub.get("country"), pub.get("region"), pub.get("city")) if x)
        proxy = info.proxy or {}
        detail = f"出口 IP {pub['ip']}"
        if loc:
            detail += f"（{loc}）"
        if pub.get("isp"):
            detail += f" · {pub['isp']}"
        if proxy.get("configured"):
            detail += "；代理已启用（" + str(proxy.get("source", ""))
            if proxy.get("process"):
                detail += f" · {proxy['process']}"
            detail += "）"
        elif proxy.get("enabled"):
            detail += "；代理程序在运行，但未配置系统代理/环境变量"
        else:
            detail += "；未检测到代理"
        diag.layers.append(Layer("egress", "[6] 公网出口与代理", OK, detail))

        # 决定性验证：直连出口 vs 当前出口。两者不同就说明流量真的被代理接管了，
        # 这正是"关了代理开关却感觉没变化"的判定依据。
        cmp = netprobe.compare_egress(timeout=6)
        if cmp.get("proxied") is True:
            diag.layers.append(Layer(
                "routing", "[7] 流量走向验证", WARN,
                f"直连出口 {cmp['direct']}，当前出口 {cmp['current']} —— 两者不同，流量确实经过代理",
                advice="如果以为代理已经关了：请彻底退出代理程序、并清掉 shell 里的 "
                       "http_proxy/https_proxy 环境变量，然后重新诊断。"))
        elif cmp.get("proxied") is False:
            diag.layers.append(Layer(
                "routing", "[7] 流量走向验证", OK,
                f"直连出口与当前出口相同（{cmp['direct']}）—— 没有走代理"))
        else:
            diag.layers.append(Layer("routing", "[7] 流量走向验证", SKIP,
                                     cmp.get("note", "无法判断")))
    else:
        diag.layers.append(Layer(
            "egress", "[6] 公网出口与代理", WARN,
            "公网 IP 查询失败：" + (pub.get("error") or "未知原因")[:80],
            advice="如果诊断前几层都正常而这里失败，多半是查询接口被网络环境拦截了，"
                   "不影响上网，可忽略。"))

    _make_conclusion(diag)
    return diag


def build_conclusion(statuses: dict[str, str]) -> tuple[str, str]:
    """
    由各层状态推导出「人话结论 + 建议」。

    抽成纯函数是为了能单元测试各种故障场景（总不能在测试里真把网线拔了）。
    statuses 形如 {"nic": OK, "gateway": FAIL, ...}
    """
    def get(key: str) -> str:
        return statuses.get(key, SKIP)

    nic, gw, dns = get("nic"), get("gateway"), get("dns")
    dom, ovs, egress = get("domestic"), get("overseas"), get("egress")

    if nic == FAIL:
        return ("本机没有可用网络连接",
                "先连上 WiFi 或插好网线，再重新诊断。")
    if gw == FAIL:
        return ("连不上路由器（局域网这一段断了）",
                "检查 WiFi 密码/网线，或重启路由器。")
    if dns == FAIL:
        return ("能连上路由器，但域名解析失败",
                "把 DNS 改成 223.5.5.5 或 119.29.29.29，通常立刻恢复。")
    if dom == FAIL:
        return ("局域网正常，但访问不了外网",
                "多为宽带/光猫/运营商问题，建议联系运营商报修。")
    if dom == OK and ovs != OK:
        return ("国内上网正常；国外访问不通（常见，通常需要代理）",
                "如果确实需要访问国外服务，请配置代理后再试。")
    if statuses.get("routing") == WARN:
        return ("检测到流量正在经过代理",
                "如果你并不想用代理：请彻底退出代理程序（不只是关开关），"
                "并清理 http_proxy/https_proxy 环境变量后重新诊断。")
    if egress == WARN:
        return ("网络基本正常；仅公网 IP 查询接口不可用（可忽略）", "")
    return ("网络一切正常，可以放心上网", "")


def _make_conclusion(diag: Diagnosis) -> None:
    """把各层状态汇总成结论，写回 Diagnosis"""
    diag.conclusion, diag.advice = build_conclusion(
        {layer.key: layer.status for layer in diag.layers})


def format_report(diag: Diagnosis) -> str:
    """把诊断结果格式化成纯文本（给复制/导出用）"""
    lines = [f"===== 网络诊断报告 {time.strftime('%Y-%m-%d %H:%M:%S')} ====="]
    for layer in diag.layers:
        ms = f"  {layer.ms:.0f}ms" if layer.ms is not None else ""
        lines.append(f"[{STATUS_LABEL.get(layer.status, layer.status)}] {layer.name}：{layer.detail}{ms}")
        if layer.advice:
            lines.append(f"        建议：{layer.advice}")
    lines.append("")
    lines.append(f"结论：{diag.conclusion}")
    if diag.advice:
        lines.append(f"建议：{diag.advice}")
    return "\n".join(lines)
