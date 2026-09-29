"""
netmon.netwin —— Windows 原生网络信息读取（纯标准库 ctypes，无需 psutil / WMI / 子进程）

用到的系统 API：
  * GetIfTable2          -> 网卡收发字节数（算网速）、网卡状态
  * GetBestRoute         -> 默认路由的网卡索引
  * GetIpForwardTable    -> 默认网关地址
  * GetAdaptersAddresses -> 网卡名称、DNS 服务器、WiFi 网卡 GUID
  * GetExtendedTcpTable  -> TCP 连接数
  * winreg               -> WiFi 名称(SSID)、系统代理设置
非 Windows 系统 import 本模块会失败，调用方需先判断 os.name。
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

from .netparse import IF_TYPE_SOFTWARE_LOOPBACK, dword_to_ip, is_virtual_if

_iphlpapi = ctypes.WinDLL("iphlpapi")
_ws2_32 = ctypes.WinDLL("ws2_32")

AF_UNSPEC = 0
AF_INET = 2
AF_INET6 = 23
GAA_FLAG_SKIP_ANYCAST = 0x0002
GAA_FLAG_SKIP_MULTICAST = 0x0004
GAA_FLAG_SKIP_DNS_SERVER = 0x0008
ERROR_BUFFER_OVERFLOW = 111
IF_TYPE_IEEE80211 = 71
IF_OPER_STATUS_UP = 1

_dword = wintypes.DWORD
_ulonglong = ctypes.c_ulonglong
_pvoid = ctypes.c_void_p


# --------------------------------------------------------------------------- #
# 结构体定义
# --------------------------------------------------------------------------- #
class MIB_IF_ROW2(ctypes.Structure):
    """对应 Windows 的 MIB_IF_ROW2（只声明到我们要用的字段）"""
    _fields_ = [
        ("InterfaceLuid", _ulonglong),
        ("InterfaceIndex", _dword),
        ("InterfaceGuid", ctypes.c_byte * 16),
        ("Alias", wintypes.WCHAR * 257),
        ("Description", wintypes.WCHAR * 257),
        ("PhysicalAddressLength", _dword),
        ("PhysicalAddress", ctypes.c_byte * 32),
        ("PermanentPhysicalAddress", ctypes.c_byte * 32),
        ("Mtu", _dword),
        ("Type", _dword),
        ("TunnelType", _dword),
        ("MediaType", _dword),
        ("PhysicalMediumType", _dword),
        ("AccessType", _dword),
        ("DirectionType", _dword),
        ("InterfaceAndOperStatusFlags", ctypes.c_byte),
        ("OperStatus", _dword),
        ("AdminStatus", _dword),
        ("MediaConnectState", _dword),
        ("NetworkGuid", ctypes.c_byte * 16),
        ("ConnectionType", _dword),
        ("TransmitLinkSpeed", _ulonglong),
        ("ReceiveLinkSpeed", _ulonglong),
        ("InOctets", _ulonglong),
        ("InUcastPkts", _ulonglong),
        ("InNUcastPkts", _ulonglong),
        ("InDiscards", _ulonglong),
        ("InErrors", _ulonglong),
        ("InUnknownProtos", _ulonglong),
        ("InUcastOctets", _ulonglong),
        ("InMulticastOctets", _ulonglong),
        ("InBroadcastOctets", _ulonglong),
        ("OutOctets", _ulonglong),
        ("OutUcastPkts", _ulonglong),
        ("OutNUcastPkts", _ulonglong),
        ("OutDiscards", _ulonglong),
        ("OutErrors", _ulonglong),
        ("OutUcastOctets", _ulonglong),
        ("OutMulticastOctets", _ulonglong),
        ("OutBroadcastOctets", _ulonglong),
    ]


class MIB_IF_TABLE2(ctypes.Structure):
    _fields_ = [("NumEntries", _dword), ("Table", MIB_IF_ROW2 * 1)]


class MIB_IPFORWARDROW(ctypes.Structure):
    _fields_ = [
        ("dwForwardDest", _dword),
        ("dwForwardMask", _dword),
        ("dwForwardPolicy", _dword),
        ("dwForwardNextHop", _dword),
        ("dwForwardIfIndex", _dword),
        ("dwForwardType", _dword),
        ("dwForwardProto", _dword),
        ("dwForwardAge", _dword),
        ("dwForwardNextHopAS", _dword),
        ("dwForwardMetric1", _dword),
        ("dwForwardMetric2", _dword),
        ("dwForwardMetric3", _dword),
        ("dwForwardMetric4", _dword),
        ("dwForwardMetric5", _dword),
    ]


class MIB_IPFORWARDTABLE(ctypes.Structure):
    _fields_ = [("dwNumEntries", _dword), ("table", MIB_IPFORWARDROW * 1)]


class SOCKADDR_IN(ctypes.Structure):
    _fields_ = [
        ("sin_family", ctypes.c_ushort),
        ("sin_port", ctypes.c_ushort),
        ("sin_addr", ctypes.c_byte * 4),
        ("sin_zero", ctypes.c_byte * 8),
    ]


class SOCKET_ADDRESS(ctypes.Structure):
    _fields_ = [("lpSockaddr", ctypes.POINTER(SOCKADDR_IN)), ("iSockaddrLength", ctypes.c_int)]


class IP_ADAPTER_UNICAST_ADDRESS(ctypes.Structure):
    pass


IP_ADAPTER_UNICAST_ADDRESS._fields_ = [
    ("Length", _dword),
    ("Flags", _dword),
    ("Next", ctypes.POINTER(IP_ADAPTER_UNICAST_ADDRESS)),
    ("Address", SOCKET_ADDRESS),
]


class IP_ADAPTER_DNS_SERVER_ADDRESS(ctypes.Structure):
    pass


IP_ADAPTER_DNS_SERVER_ADDRESS._fields_ = [
    ("Length", _dword),
    ("Reserved", _dword),
    ("Next", ctypes.POINTER(IP_ADAPTER_DNS_SERVER_ADDRESS)),
    ("Address", SOCKET_ADDRESS),
]


class IP_ADAPTER_ADDRESSES(ctypes.Structure):
    pass


# 指针字段统一用 c_void_p 占位（64 位下同为 8 字节），我们只读后面的定长字段，
# 这样即使某个字段结构与真实定义略有出入也不会读错偏移。
IP_ADAPTER_ADDRESSES._fields_ = [
    ("Length", _dword),
    ("IfIndex", _dword),
    ("Next", ctypes.POINTER(IP_ADAPTER_ADDRESSES)),
    ("AdapterName", ctypes.c_char_p),
    ("FirstUnicastAddress", ctypes.POINTER(IP_ADAPTER_UNICAST_ADDRESS)),
    ("FirstAnycastAddress", _pvoid),
    ("FirstMulticastAddress", _pvoid),
    ("FirstDnsServerAddress", ctypes.POINTER(IP_ADAPTER_DNS_SERVER_ADDRESS)),
    ("DnsSuffix", wintypes.WCHAR * 256),
    ("Description", wintypes.WCHAR * 256),
    ("FriendlyName", wintypes.WCHAR * 256),
    ("PhysicalAddress", ctypes.c_byte * 8),
    ("PhysicalAddressLength", _dword),
    ("Flags", _dword),
    ("Mtu", _dword),
    ("IfType", _dword),
    ("OperStatus", _dword),
    ("Ipv6IfIndex", _dword),
    ("ZoneIndices", _dword * 16),
    ("FirstPrefix", _pvoid),
]

# 函数签名
_iphlpapi.GetIfTable2.argtypes = [ctypes.POINTER(ctypes.POINTER(MIB_IF_TABLE2))]
_iphlpapi.GetIfTable2.restype = _dword
_iphlpapi.FreeMibTable.argtypes = [_pvoid]
_iphlpapi.GetBestRoute.argtypes = [_dword, _dword, ctypes.POINTER(_dword)]
_iphlpapi.GetBestRoute.restype = _dword
_iphlpapi.GetIpForwardTable.argtypes = [
    ctypes.POINTER(MIB_IPFORWARDTABLE), ctypes.POINTER(_dword), wintypes.BOOL
]
_iphlpapi.GetIpForwardTable.restype = _dword
_iphlpapi.GetAdaptersAddresses.argtypes = [
    wintypes.ULONG, wintypes.ULONG, _pvoid,
    ctypes.POINTER(IP_ADAPTER_ADDRESSES), ctypes.POINTER(wintypes.ULONG),
]
_iphlpapi.GetAdaptersAddresses.restype = wintypes.ULONG
_iphlpapi.GetExtendedTcpTable.argtypes = [
    _pvoid, ctypes.POINTER(_dword), wintypes.BOOL, wintypes.ULONG, ctypes.c_int, wintypes.ULONG
]
_iphlpapi.GetExtendedTcpTable.restype = _dword
_ws2_32.inet_ntoa.argtypes = [SOCKADDR_IN]
_ws2_32.inet_ntoa.restype = ctypes.c_char_p

TCP_TABLE_OWNER_PID_ALL = 5


def _sockaddr_to_ip(sa: SOCKADDR_IN) -> str:
    """把 SOCKADDR_IN 转成点分十进制字符串"""
    if not sa:
        return ""
    if sa.sin_family == AF_INET:
        return _ws2_32.inet_ntoa(sa).decode("ascii", "ignore")
    return ""


# --------------------------------------------------------------------------- #
# 网卡表：字节数 / 状态
# --------------------------------------------------------------------------- #
def _if_table():
    """返回 GetIfTable2 的原始表指针；调用方负责 FreeMibTable"""
    table = ctypes.POINTER(MIB_IF_TABLE2)()
    if _iphlpapi.GetIfTable2(ctypes.byref(table)) != 0 or not table:
        return None
    return table


def _iter_rows(table):
    n = table.contents.NumEntries
    # Table 声明为长度 1 的数组，用指针算术遍历后续元素
    base = ctypes.cast(
        ctypes.byref(table.contents, MIB_IF_TABLE2.Table.offset),
        ctypes.POINTER(MIB_IF_ROW2),
    )
    for i in range(n):
        yield base[i]


def _real_rows():
    """遍历 GetIfTable2，yield 出所有「非虚拟、非回环」网卡行（只取一次表，避免重复开销）"""
    table = _if_table()
    if not table:
        return
    try:
        for row in _iter_rows(table):
            alias = str(row.Alias)
            desc = str(row.Description)
            if is_virtual_if(alias, desc, int(row.Type)):
                continue
            yield row, alias, desc
    finally:
        _iphlpapi.FreeMibTable(table)


def read_counters() -> tuple[int, int, int]:
    """返回 (收到字节数, 发送字节数, 真实网卡数量)。

    注意如果这里恒为 0，网速就会永远显示 0 —— 这也是虚拟网卡识别最容易出错的地方，
    识别规则统一放在 netparse.is_virtual_if 里并有单元测试覆盖。
    """
    rx = tx = count = 0
    for row, _alias, _desc in _real_rows():
        rx += int(row.InOctets)
        tx += int(row.OutOctets)
        count += 1
    return rx, tx, count


def active_adapters() -> list[dict]:
    """返回状态为 UP 的真实网卡列表：[{alias, desc, iftype, speed_mbps}]"""
    out = []
    for row, alias, desc in _real_rows():
        if int(row.OperStatus) != IF_OPER_STATUS_UP:
            continue
        out.append({
            "alias": alias,
            "desc": desc,
            "iftype": int(row.Type),
            "speed_mbps": int(row.TransmitLinkSpeed) / 1_000_000.0,
            "oper_status": int(row.OperStatus),
        })
    return out


# --------------------------------------------------------------------------- #
# 网关 / DNS / 网卡详情
# --------------------------------------------------------------------------- #
def default_route_ifindex() -> int:
    """默认路由使用的网卡索引"""
    idx = _dword(0)
    # GetBestRoute(dest=0.0.0.0, source=0.0.0.0, &ifindex)
    if _iphlpapi.GetBestRoute(0, 0, ctypes.byref(idx)) == 0:
        return int(idx.value)
    return 0


def default_gateway() -> str:
    """默认网关（取默认路由 0.0.0.0/0 的下一跳）"""
    size = _dword(0)
    _iphlpapi.GetIpForwardTable(None, ctypes.byref(size), False)
    if size.value == 0:
        return ""
    buf = ctypes.create_string_buffer(size.value)
    if _iphlpapi.GetIpForwardTable(ctypes.cast(buf, ctypes.POINTER(MIB_IPFORWARDTABLE)),
                                   ctypes.byref(size), False) != 0:
        return ""
    table = ctypes.cast(buf, ctypes.POINTER(MIB_IPFORWARDTABLE))
    base = ctypes.cast(
        ctypes.byref(table.contents, MIB_IPFORWARDTABLE.table.offset),
        ctypes.POINTER(MIB_IPFORWARDROW),
    )
    best = ""
    best_metric = None
    for i in range(int(table.contents.dwNumEntries)):
        row = base[i]
        if row.dwForwardDest == 0 and row.dwForwardMask == 0 and row.dwForwardNextHop != 0:
            metric = int(row.dwForwardMetric1)
            if best_metric is None or metric < best_metric:
                best, best_metric = dword_to_ip(row.dwForwardNextHop), metric
    return best


def _adapters() -> list:
    """调用 GetAdaptersAddresses，返回 IP_ADAPTER_ADDRESSES 列表"""
    flags = GAA_FLAG_SKIP_ANYCAST | GAA_FLAG_SKIP_MULTICAST
    size = wintypes.ULONG(15000)
    for _ in range(3):
        buf = ctypes.create_string_buffer(size.value)
        ret = _iphlpapi.GetAdaptersAddresses(
            AF_UNSPEC, flags, None,
            ctypes.cast(buf, ctypes.POINTER(IP_ADAPTER_ADDRESSES)),
            ctypes.byref(size),
        )
        if ret == 0:
            out = []
            node = ctypes.cast(buf, ctypes.POINTER(IP_ADAPTER_ADDRESSES))
            while node:
                out.append((node.contents, buf))
                nxt = node.contents.Next
                node = nxt if nxt else None
            return out
        if ret != ERROR_BUFFER_OVERFLOW:
            return []
    return []


def dns_servers() -> list[str]:
    servers: list[str] = []
    for ad, _buf in _adapters():
        if int(ad.OperStatus) != IF_OPER_STATUS_UP:
            continue
        node = ad.FirstDnsServerAddress
        while node:
            ip = _sockaddr_to_ip(node.contents.Address.lpSockaddr.contents)
            if ip and ip not in servers and ip != "0.0.0.0":
                servers.append(ip)
            nxt = node.contents.Next
            node = nxt if nxt else None
    return servers


def adapter_full_names() -> dict[int, str]:
    """{网卡索引: 友好名称}"""
    names = {}
    for ad, _buf in _adapters():
        if ad.IfIndex:
            names[int(ad.IfIndex)] = str(ad.FriendlyName)
    return names


def active_wifi_ssid_netsh() -> str:
    r"""
    读取当前已连接 WiFi 的 SSID（首选方案，约 0.2s）。

    为什么不用注册表：NetworkList\Interfaces 里的 Description 字段格式在不同
    Windows 版本上不一致，容易读错；netsh 输出稳定可靠，而本函数每 15 秒才调用一次，
    这点开销完全可以接受（已设 CREATE_NO_WINDOW，不会闪黑框）。
    """
    import subprocess
    try:
        p = subprocess.run(["netsh", "wlan", "show", "interfaces"],
                           capture_output=True, timeout=4,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        text = p.stdout.decode("utf-8", "ignore")
        for line in text.splitlines():
            if "SSID" in line and "BSSID" not in line:
                parts = line.split(":", 1)
                if len(parts) == 2 and parts[1].strip():
                    return parts[1].strip()
    except Exception:
        pass
    return ""


# --------------------------------------------------------------------------- #
# 连接数
# --------------------------------------------------------------------------- #
def tcp_connection_count(pid: int | None = None) -> int:
    """TCP 连接总数；给定 pid 时只统计该进程"""
    size = _dword(0)
    _iphlpapi.GetExtendedTcpTable(None, ctypes.byref(size), False, AF_INET,
                                  TCP_TABLE_OWNER_PID_ALL, 0)
    if size.value == 0:
        return 0
    buf = ctypes.create_string_buffer(size.value)
    if _iphlpapi.GetExtendedTcpTable(ctypes.cast(buf, _pvoid), ctypes.byref(size), False,
                                     AF_INET, TCP_TABLE_OWNER_PID_ALL, 0) != 0:
        return 0
    count = ctypes.cast(buf, ctypes.POINTER(_dword)).contents.value
    if pid is None or count == 0:
        return int(count)
    # 每行 = 4 个 DWORD 状态/地址端口 + 1 个 DWORD 进程号 = 24 字节
    row_size = 24
    base = ctypes.addressof(buf) + ctypes.sizeof(_dword)
    found = 0
    for i in range(int(count)):
        owner = ctypes.cast(base + i * row_size + 20, ctypes.POINTER(_dword)).contents.value
        if owner == pid:
            found += 1
    return found


# --------------------------------------------------------------------------- #
# 系统代理
# --------------------------------------------------------------------------- #
def system_proxy() -> dict | None:
    """读取 IE/WinINET 代理设置"""
    try:
        import winreg
    except Exception:
        return None
    enabled = 0
    server = ""
    pac = ""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as key:
            try:
                enabled = int(winreg.QueryValueEx(key, "ProxyEnable")[0])
            except OSError:
                enabled = 0
            try:
                server = str(winreg.QueryValueEx(key, "ProxyServer")[0])
            except OSError:
                server = ""
            try:
                pac = str(winreg.QueryValueEx(key, "AutoConfigURL")[0])
            except OSError:
                pac = ""
    except OSError:
        return None
    if pac:
        return {"source": "系统PAC", "raw": pac, "auto": True}
    if enabled and server:
        return {"source": "系统代理", "raw": server}
    return None


def is_process_elevated() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False
