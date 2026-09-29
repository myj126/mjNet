"""
netmon.netparse —— 纯解析逻辑（零平台依赖，任何系统都能 import 和测试）

把这类“容易写错、又跟系统 API 无关”的判断集中在这里，好处是：
  * Linux 上也能对 Windows 用到的解析逻辑做单元测试
  * Windows 与 Linux 共用同一份虚拟网卡名单，不会出现两边不一致
"""
from __future__ import annotations

import ctypes

# --------------------------------------------------------------------------- #
# 虚拟网卡识别
# --------------------------------------------------------------------------- #
# 必须区分「精确名 / 明确前缀 / 明确子串」三种匹配方式。
# 踩过的坑：早期用宽泛子串匹配，结果 WiFi 网卡 "wlo1" 因为含子串 "lo"（回环）
# 被误判成虚拟网卡，导致网速永远显示 0 —— 短关键词一定要精确匹配。
VIRTUAL_EXACT = {
    # Linux
    "lo", "docker0", "dummy0", "cni0", "flannel.1", "kube-ipvs0", "virbr0",
    "virbr0-nic", "lxdbr0", "podman0", "anbox0", "mp0", "ip6tnl0", "sit0",
    "wg0", "tun0", "tap0", "zt0", "tailscale0", "ham0", "ppp0",
    # macOS / BSD
    "lo0", "utun0", "utun1", "utun2", "utun3", "gif0", "stf0", "bridge0",
    "awdl0", "llw0", "ap1",
}
VIRTUAL_PREFIXES = (
    "br-", "veth", "virbr", "vmnet", "vboxnet", "docker", "dummy",
    "tun", "tap", "wg", "zt", "utun", "nordlynx", "warp", "sing",
    "clash", "meta", "ppp", "pppoe", "ham", "ip6tnl", "teredo",
)
# Windows 上大量虚拟网卡的名字是中文/英文描述，只能靠关键词判断
VIRTUAL_SUBSTRINGS = (
    "loopback", "zerotier", "tailscale", "wireguard", "openvpn", "vpn",
    "hyper-v", "vmware", "virtualbox", "virtual", "vethernet", "tap-windows",
    "wintun", "bluetooth", "teredo", "isatap", "6to4", "wan miniport",
    "npcap", "loopback adapter", "microsoft km-test",
)


def is_virtual_nic(name: str) -> bool:
    """判断网卡名是否属于“虚拟/隧道/回环”类，这类网卡不计入网速与出口 IP"""
    low = (name or "").strip().lower()
    if not low:
        return False
    if low in VIRTUAL_EXACT:
        return True
    return low.startswith(VIRTUAL_PREFIXES) or any(k in low for k in VIRTUAL_SUBSTRINGS)


# Windows 网卡类型常量
IF_TYPE_SOFTWARE_LOOPBACK = 24
IF_TYPE_IEEE80211 = 71
IF_TYPE_ETHERNET_CSMACD = 6


def is_virtual_if(alias: str, description: str, iftype: int) -> bool:
    """Windows 版判断：还需要看网卡类型和描述"""
    if iftype == IF_TYPE_SOFTWARE_LOOPBACK:
        return True
    return is_virtual_nic(alias) or is_virtual_nic(description)


# --------------------------------------------------------------------------- #
# Windows 网络地址解析
# --------------------------------------------------------------------------- #
def dword_to_ip(value: int) -> str:
    """
    Windows 的 dwForwardNextHop 等字段是网络字节序的 32 位整数，
    按字节取出即可还原成点分十进制（依赖主机的字节序，本函数仅 Windows 调用）。
    """
    raw = ctypes.c_uint32(value & 0xFFFFFFFF)
    octets = ctypes.cast(ctypes.byref(raw), ctypes.POINTER(ctypes.c_ubyte * 4)).contents
    return ".".join(str(octets[i]) for i in range(4))
