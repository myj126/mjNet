"""
netmon.netinfo —— 跨平台本机网络信息采集

设计原则：
  1. 只依赖 Python 标准库；psutil 若存在会自动用作增强（不是必需）。
  2. Windows 走 netwin.py 的原生 API；Linux 走 /proc + 系统命令。
  3. 采集函数自己捕获全部异常，任何一项失败都只返回空值，绝不让界面崩溃。
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
import time
from dataclasses import dataclass, field

# 可选增强：装了 psutil 就用，没装也完全不影响
try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover
    psutil = None

IS_WINDOWS = os.name == "nt"
IS_LINUX = os.sys.platform.startswith("linux")

if IS_WINDOWS:
    from . import netwin as _win

from .netparse import IF_TYPE_IEEE80211, is_virtual_if, is_virtual_nic as _is_virtual


def _run(cmd: list[str], timeout: float = 3.0) -> str:
    """执行外部命令并返回 stdout；任何失败返回空串（不抛异常）"""
    try:
        kw = {}
        if IS_WINDOWS:
            kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        p = subprocess.run(cmd, capture_output=True, timeout=timeout,
                           text=True, errors="ignore", **kw)
        return p.stdout or ""
    except Exception:
        return ""


# --------------------------------------------------------------------------- #
# 本机 IP / 主机名
# --------------------------------------------------------------------------- #
def local_ip() -> str:
    """本机在局域网中的 IP（用一个不发包的 UDP connect 让系统自动选出网卡）"""
    for probe in (("223.5.5.5", 53), ("8.8.8.8", 53), ("1.1.1.1", 53)):
        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.5)
            s.connect(probe)
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
        except Exception:
            continue
        finally:
            if s:
                s.close()
    # 兜底：用主机名解析
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    return ""


def hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return ""


# --------------------------------------------------------------------------- #
# 网速采集
# --------------------------------------------------------------------------- #
@dataclass
class SpeedMeter:
    """两个时间点的字节差值 -> Mb/s（注意：网络习惯用 1 Mbit = 10^6 bit）"""
    rx: int = 0
    tx: int = 0
    last_time: float = 0.0
    down_mbps: float = 0.0
    up_mbps: float = 0.0
    nic_count: int = 0

    def _read_counters(self) -> tuple[int, int, int]:
        if IS_WINDOWS:
            return _win.read_counters()
        # Linux：直接读 /proc/net/dev，速度最快、无依赖
        if IS_LINUX:
            try:
                rx = tx = 0
                count = 0
                with open("/proc/net/dev", "r", encoding="utf-8", errors="ignore") as fh:
                    for line in fh:
                        if ":" not in line:
                            continue
                        name, _, rest = line.partition(":")
                        name = name.strip()
                        if _is_virtual(name):
                            continue
                        parts = rest.split()
                        if len(parts) < 9:
                            continue
                        rx += int(parts[0])
                        tx += int(parts[8])
                        count += 1
                if count:
                    return rx, tx, count
            except Exception:
                pass
        # 通用兜底：psutil
        if psutil is not None:
            try:
                rx = tx = count = 0
                for name, io in psutil.net_io_counters(pernic=True).items():
                    if _is_virtual(name):
                        continue
                    rx += io.bytes_recv
                    tx += io.bytes_sent
                    count += 1
                return rx, tx, count
            except Exception:
                pass
        return 0, 0, 0

    def sample(self) -> tuple[float, float]:
        """采一次样，返回 (下行 Mb/s, 上行 Mb/s)"""
        rx, tx, count = self._read_counters()
        now = time.monotonic()
        if self.last_time and count:
            dt = now - self.last_time
            if dt > 0.05:
                d_rx, d_tx = rx - self.rx, tx - self.tx
                # 计数器回绕/复位时忽略这一次
                if d_rx >= 0 and d_tx >= 0:
                    self.down_mbps = d_rx * 8 / 1e6 / dt
                    self.up_mbps = d_tx * 8 / 1e6 / dt
        self.rx, self.tx, self.last_time, self.nic_count = rx, tx, now, count
        return self.down_mbps, self.up_mbps

    @property
    def total_mb(self) -> tuple[float, float]:
        """开机以来累计流量（MB）"""
        return self.rx / 1e6, self.tx / 1e6


# --------------------------------------------------------------------------- #
# 网关 / DNS / 网卡
# --------------------------------------------------------------------------- #
def default_interface() -> str:
    """默认路由使用的网卡名（Windows 返回友好名称，如 “WLAN” / “以太网”）"""
    if IS_WINDOWS:
        idx = _win.default_route_ifindex()
        if idx:
            try:
                # 必须用索引精确匹配，随便取第一个网卡名在多网卡机器上是错的
                name = _win.adapter_full_names().get(idx)
                if name:
                    return name
            except Exception:
                pass
        for ad in _win.active_adapters():
            return ad["alias"]
        return ""
    # Linux：ip route show default
    out = _run(["ip", "-4", "route", "show", "default"])
    m = re.search(r"\bdev\s+(\S+)", out)
    if m:
        return m.group(1)
    # 兜底：/proc/net/route 找 Destination=0.0.0.0 的那条
    try:
        with open("/proc/net/route", "r", encoding="utf-8") as fh:
            next(fh, None)
            for line in fh:
                f = line.split()
                if len(f) > 2 and f[1] == "00000000" and int(f[7], 16) == 0:
                    return f[0]
    except Exception:
        pass
    return ""


def default_gateway() -> str:
    if IS_WINDOWS:
        return _win.default_gateway()
    out = _run(["ip", "-4", "route", "show", "default"])
    m = re.search(r"\bvia\s+(\d+\.\d+\.\d+\.\d+)", out)
    if m:
        return m.group(1)
    try:
        with open("/proc/net/route", "r", encoding="utf-8") as fh:
            next(fh, None)
            for line in fh:
                f = line.split()
                if len(f) > 2 and f[1] == "00000000":
                    raw = int(f[2], 16)
                    return ".".join(str((raw >> (8 * i)) & 0xFF) for i in range(4))
    except Exception:
        pass
    return ""


def dns_servers() -> list[str]:
    """真实上游 DNS 服务器（自动跳过 systemd-resolved 的 127.0.0.53 本地存根）"""
    if IS_WINDOWS:
        return _win.dns_servers()

    servers: list[str] = []

    def add(ip: str) -> None:
        ip = ip.strip()
        if not ip or ip in servers:
            return
        if ip.startswith("127.") or ip in ("::1", "0.0.0.0"):
            return
        servers.append(ip.split("%")[0])

    # 1) resolvectl（systemd-resolved 系统，能拿到真正的上游 DNS）
    for line in _run(["resolvectl", "status"], timeout=2.0).splitlines():
        m = re.match(r"\s*(Current DNS Server|DNS Servers):\s*(.+)", line)
        if m:
            for ip in m.group(2).split():
                add(ip)
    # 2) nmcli
    if not servers:
        out = _run(["nmcli", "-t", "-f", "IP4.DNS", "dev", "show"], timeout=2.0)
        for line in out.splitlines():
            for ip in line.split(":", 1)[-1].replace(",", " ").split():
                add(ip)
    # 3) /etc/resolv.conf 兜底
    if not servers:
        try:
            with open("/etc/resolv.conf", "r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    if line.strip().startswith("nameserver"):
                        parts = line.split()
                        if len(parts) > 1:
                            add(parts[1])
        except Exception:
            pass
    return servers


# --------------------------------------------------------------------------- #
# WiFi / 有线
# --------------------------------------------------------------------------- #
@dataclass
class LinkInfo:
    kind: str = "未知"          # 有线 / WiFi / 无网络
    iface: str = ""
    ssid: str = ""
    signal: int = 0             # 0-100，仅 WiFi 有意义
    speed_mbps: float = 0.0     # 协商速率


def link_info() -> LinkInfo:
    info = LinkInfo()
    if IS_WINDOWS:
        iface = default_interface()
        info.iface = iface
        adapters = _win.active_adapters()
        wifi = next((a for a in adapters if a["iftype"] == IF_TYPE_IEEE80211), None)
        if wifi:
            info.kind = "WiFi 无线"
            info.speed_mbps = wifi["speed_mbps"]
            # netsh 是最可靠的读取方式（约 0.2s；本函数每 15 秒才刷新一次，开销可忽略）
            info.ssid = _win.active_wifi_ssid_netsh()
        elif adapters:
            info.kind = "有线以太网"
            info.speed_mbps = max(a["speed_mbps"] for a in adapters)
        return info

    # Linux
    iface = default_interface()
    info.iface = iface
    if not iface:
        return info
    try:
        if os.path.exists(f"/sys/class/net/{iface}/wireless"):
            info.kind = "WiFi 无线"
            info.ssid, info.signal = _wifi_details(iface)
        elif os.path.exists(f"/sys/class/net/{iface}"):
            info.kind = "有线以太网"
            try:
                with open(f"/sys/class/net/{iface}/speed", "r") as fh:
                    spd = float(fh.read().strip())
                # WiFi 网卡这里通常返回 -1（未知），必须过滤掉
                if 0 < spd < 1_000_000:
                    info.speed_mbps = spd
            except Exception:
                pass
    except Exception:
        pass
    return info


def _unescape_nmcli(text: str) -> str:
    """nmcli -t 会把值里的冒号转义成 \\: ，这里还原"""
    return text.replace("\\:", ":").replace("\\\\", "\\").strip()


def _nmcli_active_bssid(iface: str = "") -> str:
    """
    取当前连接 AP 的 BSSID。

    注意：`GENERAL.ACTIVE-BSSID` 只在较新 NetworkManager 上存在（1.36 没有这个字段），
    所以改用 `dev wifi list` 里标记为当前连接的那一行，兼容性更好。
    `--rescan no` 避免额外触发一次扫描。
    """
    out = _run(["nmcli", "-t", "-f", "IN-USE,BSSID", "dev", "wifi", "list", "--rescan", "no"],
               timeout=2.5)
    fallback = ""
    for line in out.splitlines():
        flag, _, rest = line.partition(":")
        bssid = _unescape_nmcli(rest).upper()
        if not re.match(r"^(?:[0-9A-F]{2}:){5}[0-9A-F]{2}$", bssid):
            continue
        if flag.strip() in ("*", "yes", "是"):
            return bssid
        if not fallback:
            fallback = bssid
    return fallback


def _wifi_active_ssid_nmcli(iface: str) -> tuple[str, int]:
    """
    从 nmcli 读取「当前正在连接」的 WiFi 的 SSID 与信号。

    踩过的两个坑（都真实发生过）：
      1. 早期判断 active 字段 == "yes"，但中文系统返回「是/否」，导致永远匹配不上；
      2. 改成「取信号最强的一条」后，拿到了邻居家的网络（信号比自家还强），
         界面显示成别人的 SSID。
    现在按可靠性依次判断：
      IN-USE 标记(*)  →  ACTIVE/BSSID 精确匹配  →  信号最强兜底
    其中 IN-USE 的 "*" 与语言无关，是最可靠的第一判据。
    """
    out = _run(["nmcli", "-t", "-f", "IN-USE,SSID,BSSID,SIGNAL", "dev", "wifi"], timeout=3.0)
    rows: list[tuple[str, str, str, int]] = []
    for line in out.splitlines():
        # 形如：   :MyWiFi-5G:aa\:bb\:cc\:dd\:ee\:01:94
        #          *:MyWiFi-5G:aa\:bb\:cc\:dd\:ee\:01:80
        m = re.match(r"^(?P<inuse>[^:]*):(?P<rest>.*):(?P<sig>\d+)\s*$", line)
        if not m:
            continue
        rest = m.group("rest")
        mm = re.match(r"^(?P<ssid>.*):(?P<bssid>(?:[0-9A-Fa-f]{2}\\?:){5}[0-9A-Fa-f]{2})$", rest)
        ssid_raw, bssid_raw = (mm.group("ssid"), mm.group("bssid")) if mm else (rest, "")
        rows.append((
            m.group("inuse").strip(),
            _unescape_nmcli(ssid_raw),
            _unescape_nmcli(bssid_raw).upper(),
            int(m.group("sig")),
        ))
    if not rows:
        return "", 0

    # 1) nmcli 自己标出的当前网络（IN-USE 的 * 与语言无关）
    for flag, ssid, _bssid, signal in rows:
        if flag and ssid:
            return ssid, signal

    # 2) 按当前连接的 BSSID 精确匹配
    active_bssid = _nmcli_active_bssid(iface)
    if active_bssid:
        for _flag, ssid, bssid, signal in rows:
            if bssid == active_bssid and ssid:
                return ssid, signal

    # 3) 最后兜底：信号最强（可能认错网络，但总比空着好）
    best = max(rows, key=lambda r: r[3])
    return best[1], best[3]


def _wifi_details(iface: str) -> tuple[str, int]:
    """返回 (SSID, 信号百分比)；依次尝试 nmcli -> iw -> /proc/net/wireless"""
    ssid, signal = "", 0

    # 1) nmcli（NetworkManager 系统最通用，不需要 root）
    if _run(["nmcli", "--version"], timeout=1.5):
        ssid, signal = _wifi_active_ssid_nmcli(iface)

    # 2) iw（部分系统没装，且某些版本需要 root）
    if not ssid:
        out = _run(["iw", "dev", iface, "link"], timeout=2.0)
        if out:
            m = re.search(r"^\s*SSID:\s*(.+)$", out, re.M)
            if m:
                ssid = m.group(1).strip()
            m = re.search(r"signal:\s*(-?\d+)\s*dBm", out)
            if m:
                dbm = int(m.group(1))
                # -30dBm 满格，-90dBm 基本没信号
                signal = max(0, min(100, int((dbm + 90) * 100 / 60)))

    # 3) /proc/net/wireless 兜底（只有信号，没有 SSID）
    if not signal:
        try:
            with open("/proc/net/wireless", "r", encoding="utf-8") as fh:
                for line in fh:
                    if line.strip().startswith(iface):
                        f = line.split(":")
                        nums = f[1].split() if len(f) > 1 else []
                        if len(nums) > 1:
                            quality = float(nums[1].rstrip("."))
                            signal = max(0, min(100, int(quality * 100 / 70)))
        except Exception:
            pass
    return ssid, signal


# --------------------------------------------------------------------------- #
# 连接数
# --------------------------------------------------------------------------- #
def connection_count(pid: int | None = None) -> int:
    """TCP 连接数；pid 给定则只统计该进程"""
    if IS_WINDOWS:
        try:
            return _win.tcp_connection_count(pid)
        except Exception:
            return 0
    if psutil is not None:
        try:
            if pid is not None:
                p = psutil.Process(pid)
                return sum(1 for c in p.net_connections(kind="tcp")
                           if c.status != psutil.CONN_LISTEN)
            return sum(1 for c in psutil.net_connections(kind="tcp")
                       if c.status != psutil.CONN_LISTEN)
        except Exception:
            pass
    # Linux procfs 兜底（该文件含所有命名空间的连接，仅作粗略统计）
    total = 0
    try:
        for name in ("/proc/net/tcp", "/proc/net/tcp6"):
            with open(name, "r", encoding="utf-8") as fh:
                total += max(0, sum(1 for _ in fh) - 1)
    except Exception:
        pass
    return total


# --------------------------------------------------------------------------- #
# 代理检测
# --------------------------------------------------------------------------- #
PROXY_ENV_KEYS = (
    "http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY",
    "ALL_PROXY",
)
NO_PROXY_KEYS = ("no_proxy", "NO_PROXY")

# 本机常见代理客户端默认端口（TUN 模式或系统代理没设置、但客户端在跑时用于提示）
LOCAL_PROXY_PORTS = {
    7890: "Clash / Mihomo",
    7891: "Clash (socks)",
    7897: "Clash Verge",
    10801: "V2Ray / Xray (http)",
    10808: "V2Ray / Xray (socks)",
    10809: "V2Ray / Xray (http)",
    1080: "通用 SOCKS5",
    8889: "Surge / 通用 http",
    2080: "Nekoray",
    20171: "Proxifier",
    8118: "Privoxy",
    33210: "Motrix/其他",
}


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.12) -> bool:
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0
    except Exception:
        return False
    finally:
        if s:
            s.close()


def proxy_process_names() -> list[str]:
    """
    找出正在运行的代理客户端进程名（如 verge-mihomo / clash / sing-box）。
    只读 /proc，不依赖 psutil，也不调用外部命令。
    """
    keywords = ("clash", "mihomo", "v2ray", "xray", "sing-box", "singbox",
                "shadow", "trojan", "nekoray", "v2raya", "hysteria", "tun2socks")
    names: list[str] = []
    if IS_WINDOWS or not os.path.isdir("/proc"):
        return names
    try:
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                with open(f"/proc/{entry}/comm", "r", encoding="utf-8", errors="ignore") as fh:
                    name = fh.read().strip()
            except Exception:
                continue
            low = name.lower()
            if any(k in low for k in keywords) and name not in names:
                names.append(name)
    except Exception:
        pass
    return names


def _local_proxy_ports() -> list[str]:
    """扫描本机常见代理端口（全部没开时只花约 0.12s，因为是并发探测不到的快速失败）"""
    found = []
    for port, name in LOCAL_PROXY_PORTS.items():
        if _port_open(port):
            found.append(f"{name} (127.0.0.1:{port})")
    return found


def _windows_proxy() -> dict | None:
    return _win.system_proxy()


def _gnome_proxy() -> dict | None:
    mode = _run(["gsettings", "get", "org.gnome.system.proxy", "mode"], timeout=1.5).strip()
    mode = mode.strip("'\"")
    if mode not in ("manual", "auto"):
        return None
    if mode == "auto":
        url = _run(["gsettings", "get", "org.gnome.system.proxy", "autoconfig-url"],
                   timeout=1.5).strip().strip("'\"")
        return {"source": "GNOME 自动配置(PAC)", "raw": url or "(未填写地址)", "auto": True}
    parts = []
    for schema in ("http", "https", "ftp", "socks"):
        host = _run(["gsettings", "get", f"org.gnome.system.proxy.{schema}", "host"],
                    timeout=1.5).strip().strip("'\"")
        port = _run(["gsettings", "get", f"org.gnome.system.proxy.{schema}", "port"],
                    timeout=1.5).strip()
        if host:
            parts.append(f"{schema}://{host}:{port}")
    if not parts:
        return None
    return {"source": "GNOME 系统代理", "raw": "  ".join(parts)}


def _kde_proxy() -> dict | None:
    path = os.path.expanduser("~/.config/kioslaverc")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
    except Exception:
        return None
    m = re.search(r"^\s*ProxyType\s*=\s*(\d+)", text, re.M)
    if not m or m.group(1) == "0":
        return None
    m = re.search(r"^\s*httpProxy\s*=\s*(.+)$", text, re.M)
    raw = m.group(1).strip() if m else "(已启用，未读到地址)"
    return {"source": "KDE 系统代理", "raw": raw}


def detect_proxy() -> dict:
    """
    返回统一的代理状态字典：
      {enabled, source, detail, note, local_clients}
    """
    result = {
        "enabled": False,          # 是否已配置代理（环境变量 / 系统代理 / 正在运行的代理程序）
        "configured": False,       # 是否由配置明确启用（环境变量或系统代理）
        "in_use": False,           # 流量是否很可能被代理接管
        "source": "", "detail": "", "note": "",
        "local_clients": [],       # 探测到的本机代理程序
        "process": "",             # 代理进程名（如 verge-mihomo）
    }

    # 1) 环境变量（Linux 下最常用，也是 curl/wget/git 等真正认的配置）
    env_pairs = []
    for key in PROXY_ENV_KEYS:
        val = os.environ.get(key)
        if val:
            env_pairs.append(f"{key}={val}")
    if env_pairs:
        result.update({
            "enabled": True,
            "configured": True,
            "in_use": True,
            "source": "环境变量",
            "detail": "  ".join(env_pairs[:3]),
        })
        no_proxy = os.environ.get("no_proxy") or os.environ.get("NO_PROXY")
        if no_proxy:
            result["note"] = f"no_proxy 例外: {no_proxy[:120]}"
        return result

    # 2) 系统级设置
    sys_proxy = None
    if IS_WINDOWS:
        try:
            sys_proxy = _windows_proxy()
        except Exception:
            sys_proxy = None
    else:
        sys_proxy = _gnome_proxy() or _kde_proxy()
    if sys_proxy:
        result.update({
            "enabled": True,
            "configured": True,
            "in_use": True,
            "source": sys_proxy.get("source", "系统代理"),
            "detail": sys_proxy.get("raw", ""),
        })
        if sys_proxy.get("auto"):
            result["note"] = "自动配置脚本(PAC)：按规则分流，不是全局代理"
        return result

    # 3) 没有系统代理，但本机可能有代理客户端在跑（TUN 模式嗅探）
    clients = _local_proxy_ports()
    result["local_clients"] = clients
    if clients:
        procs = proxy_process_names()
        if procs:
            result["process"] = "、".join(procs[:2])
        # 关键区分：只是「代理程序在运行」，并不等于「系统已配置代理」。
        # 用户关掉系统代理开关后，代理内核往往还在后台跑，这时不该显示成"已启用"，
        # 而应该说清楚：程序在运行，但没配置代理（除 TUN/透明模式外，流量是直连的）。
        result.update({
            "enabled": True,
            "configured": False,
            "in_use": False,
            "source": "仅代理程序在运行",
            "detail": "、".join(clients[:3]),
            "note": "未配置系统代理/环境变量；若开了 TUN 或透明代理，流量仍会被接管",
        })
    return result


# --------------------------------------------------------------------------- #
# 汇总采集器
# --------------------------------------------------------------------------- #
@dataclass
class LocalInfo:
    ip: str = ""
    host: str = ""
    gateway: str = ""
    dns: list[str] = field(default_factory=list)
    link: LinkInfo = field(default_factory=LinkInfo)
    proxy: dict = field(default_factory=dict)
    connections: int = 0
    down_mbps: float = 0.0
    up_mbps: float = 0.0
    session_rx_mb: float = 0.0
    session_tx_mb: float = 0.0
    updated_at: float = 0.0


class InfoCollector:
    """把各项采集串起来，并缓存变化很慢的信息（接口名、本机 IP 等）"""

    def __init__(self) -> None:
        self.meter = SpeedMeter()
        self._env_ts = 0.0
        self._link = LinkInfo()
        self._gateway = ""
        self._dns: list[str] = []
        self._ip = ""
        self._host = hostname()
        self._proxy: dict = {}
        self._proxy_ts = 0.0

    def _refresh_slow(self) -> None:
        """慢信息：每 15 秒刷新一次即可"""
        self._ip = local_ip()
        self._gateway = default_gateway()
        self._dns = dns_servers()
        self._link = link_info()

    def collect(self, force_slow: bool = False) -> LocalInfo:
        now = time.monotonic()
        if force_slow or now - self._env_ts > 15 or not self._ip:
            self._refresh_slow()
            self._env_ts = now

        down, up = self.meter.sample()

        # 代理检测里含端口探测，不必每秒做
        if force_slow or not self._proxy or now - self._proxy_ts > 10:
            self._proxy = detect_proxy()
            self._proxy_ts = now

        rx_mb, tx_mb = self.meter.total_mb
        return LocalInfo(
            ip=self._ip,
            host=self._host,
            gateway=self._gateway,
            dns=self._dns,
            link=self._link,
            proxy=self._proxy,
            connections=connection_count(),
            down_mbps=down,
            up_mbps=up,
            session_rx_mb=rx_mb,
            session_tx_mb=tx_mb,
            updated_at=time.time(),
        )


def system_summary() -> dict:
    """给“关于”面板用的一点系统信息"""
    return {
        "平台": f"{os.name} / {os.sys.platform}",
        "Python": os.sys.version.split()[0],
        "psutil": getattr(psutil, "__version__", "未安装（可选增强）"),
        "主机名": hostname(),
    }
