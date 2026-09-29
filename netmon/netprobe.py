"""
netmon.netprobe —— 需要联网的探测：公网 IP、归属地、网络延迟

所有函数都是“尽力而为”：依次尝试多个公共服务，任何一个成功就返回；
全部失败也会正常返回错误信息，绝不抛出异常打断界面。
"""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request

UA = "Mozilla/5.0 (mjNet/1.0)"
DEFAULT_TIMEOUT = 5.0

# 公网 IP 接口清单（已实测可用性，按优先级排列）
#   kind=json_ip    -> 只返回 {"ip": ...} 之类
#   kind=text_ip    -> 直接返回 IP 文本
#   kind=json_full  -> 额外带国家/地区/运营商
PROVIDERS = [
    {
        "name": "ip-api.com",
        "url": "http://ip-api.com/json/?lang=zh-CN&fields=status,message,country,regionName,city,isp,org,as,query",
        "kind": "json_full",
    },
    {
        "name": "ip.3322.net",
        "url": "http://ip.3322.net/",
        "kind": "text_ip",
    },
    {
        "name": "api.ipify.org",
        "url": "https://api.ipify.org?format=json",
        "kind": "json_ip",
    },
    {
        "name": "myip.ipip.net",
        "url": "https://myip.ipip.net/json",
        "kind": "ipip",
    },
    {
        "name": "ident.me",
        "url": "https://ident.me/json",
        "kind": "json_ip",
    },
]

# 延迟探测目标：TCP 握手耗时，无需管理员权限，跨平台结果可比
# 国内优先，便于得到真实可用性；每个目标同时给出对应的“代理特征”
LATENCY_TARGETS = [
    ("223.5.5.5", 443, "阿里 DNS(国内)"),
    ("www.baidu.com", 443, "百度(国内)"),
    ("1.1.1.1", 443, "Cloudflare(国外)"),
]

_IPV4_RE = __import__("re").compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def _looks_like_ip(text: str) -> bool:
    return bool(_IPV4_RE.match(text.strip()))


def _http_get(url: str, timeout: float = DEFAULT_TIMEOUT) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(4096).decode("utf-8", "ignore")


def _parse_provider(provider: dict, body: str) -> dict | None:
    kind = provider["kind"]
    text = body.strip()
    if kind == "text_ip":
        return {"ip": text} if _looks_like_ip(text) else None
    try:
        data = json.loads(text)
    except Exception:
        return None
    if kind == "json_ip":
        ip = str(data.get("ip") or data.get("IPv4") or "").strip()
        return {"ip": ip} if _looks_like_ip(ip) else None
    if kind == "ipip":
        # {"ret":"ok","data":{"ip":"1.2.3.4","location":["中国","上海","上海","电信"]}}
        d = data.get("data") or {}
        ip = str(d.get("ip", "")).strip()
        if not _looks_like_ip(ip):
            return None
        loc = d.get("location") or []
        return {
            "ip": ip,
            "country": loc[0] if len(loc) > 0 else "",
            "region": loc[1] if len(loc) > 1 else "",
            "city": loc[2] if len(loc) > 2 else "",
            "isp": loc[3] if len(loc) > 3 else "",
        }
    # json_full
    if str(data.get("status", "success")).lower() == "fail":
        return None
    ip = str(data.get("query") or data.get("ip") or "").strip()
    if not _looks_like_ip(ip):
        return None
    return {
        "ip": ip,
        "country": str(data.get("country", "") or ""),
        "region": str(data.get("regionName", "") or ""),
        "city": str(data.get("city", "") or ""),
        "isp": str(data.get("isp", "") or ""),
    }


def _direct_ip(timeout: float = 6.0) -> str:
    """
    绕开一切代理设置，直连查询出口 IP。

    做法：新建一个 opener 且**不给它任何 ProxyHandler**，
    这样即使环境里有 http_proxy/HTTPS_PROXY 也不会被使用。
    和「走代理查到的 IP」对比，就能确定流量到底有没有被代理接管。
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for url in ("http://ip.3322.net/", "http://ip-api.com/line/?fields=query"):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with opener.open(req, timeout=timeout) as resp:
                text = resp.read(256).decode("utf-8", "ignore").strip()
            first = text.splitlines()[0].strip() if text else ""
            if _looks_like_ip(first):
                return first
        except Exception:
            continue
    return ""


def compare_egress(timeout: float = 6.0) -> dict:
    """
    对比「直连出口 IP」与「当前（可能走代理）出口 IP」。

    返回 {direct, current, proxied, note}
      proxied=True  → 两者不同，说明流量确实经过了代理
      proxied=False → 两者相同，说明没有走代理（或代理刚好同出口）
    """
    current = public_ip_info(timeout=timeout)
    direct = _direct_ip(timeout=timeout)
    cur_ip = current.get("ip", "") if current.get("ok") else ""
    if not direct or not cur_ip:
        return {"direct": direct, "current": cur_ip, "proxied": None,
                "note": "有一个出口查询失败，无法对比"}
    proxied = direct != cur_ip
    note = (f"直连出口 {direct}，当前出口 {cur_ip} → 流量**经过代理**"
            if proxied else
            f"直连出口与当前出口相同（{direct}）→ 没有走代理")
    return {"direct": direct, "current": cur_ip, "proxied": proxied, "note": note}


def public_ip_info(timeout: float = DEFAULT_TIMEOUT) -> dict:
    """
    查询公网出口 IP 及归属地。
    返回 {ok, ip, country, region, city, isp, source, error}
    """
    errors = []
    for provider in PROVIDERS:
        t0 = time.monotonic()
        try:
            body = _http_get(provider["url"], timeout=timeout)
            parsed = _parse_provider(provider, body)
            if parsed and parsed.get("ip"):
                parsed.update({
                    "ok": True,
                    "source": provider["name"],
                    "elapsed_ms": round((time.monotonic() - t0) * 1000, 1),
                    "error": "",
                })
                return parsed
            errors.append(f"{provider['name']}: 返回格式无法识别")
        except urllib.error.HTTPError as exc:
            errors.append(f"{provider['name']}: HTTP {exc.code}")
        except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
            reason = getattr(exc, "reason", exc)
            errors.append(f"{provider['name']}: {reason}")
        except Exception as exc:  # 任何意外都只记录下来
            errors.append(f"{provider['name']}: {type(exc).__name__}")
    return {
        "ok": False,
        "ip": "",
        "country": "", "region": "", "city": "", "isp": "",
        "source": "",
        "error": "；".join(errors) or "无法连接任何公网 IP 查询服务",
    }


# --------------------------------------------------------------------------- #
# 延迟（TCP 握手）
# --------------------------------------------------------------------------- #
def tcp_latency_ms(host: str, port: int = 443, timeout: float = 2.0) -> float | None:
    """TCP 三次握手耗时（毫秒）；失败返回 None。不解析 DNS 的时间不计入缓存开销"""
    s = None
    try:
        t0 = time.monotonic()
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))
        return (time.monotonic() - t0) * 1000.0
    except Exception:
        return None
    finally:
        if s:
            s.close()


def probe_latency() -> dict:
    """
    逐个探测延迟目标。
    返回 {targets: [{name, host, ms}], best_ms, min, max, avg, domestic_ms, overseas_ms}
    """
    targets = []
    for host, port, label in LATENCY_TARGETS:
        ms = tcp_latency_ms(host, port)
        targets.append({"name": label, "host": host, "ms": ms})

    values = [t["ms"] for t in targets if t["ms"] is not None]
    domestic = [t["ms"] for t in targets if t["ms"] is not None and "国内" in t["name"]]
    overseas = [t["ms"] for t in targets if t["ms"] is not None and "国外" in t["name"]]
    return {
        "targets": targets,
        "min": min(values) if values else None,
        "max": max(values) if values else None,
        "avg": sum(values) / len(values) if values else None,
        "domestic_ms": min(domestic) if domestic else None,
        "overseas_ms": min(overseas) if overseas else None,
    }


class LatencyTracker:
    """滚动保存最近 N 次延迟，用于显示抖动与丢包率"""

    def __init__(self, size: int = 20) -> None:
        self.size = size
        self.history: list[float | None] = []
        self.samples = 0
        self.lost = 0

    def add(self, ms: float | None) -> None:
        self.samples += 1
        if ms is None:
            self.lost += 1
        self.history.append(ms)
        if len(self.history) > self.size:
            self.history.pop(0)

    @property
    def loss_rate(self) -> float:
        return (self.lost / self.samples * 100.0) if self.samples else 0.0

    @property
    def jitter_ms(self) -> float | None:
        """相邻两次延迟差值的平均绝对值（网络抖动的常用近似）"""
        vals = [v for v in self.history if v is not None]
        if len(vals) < 2:
            return None
        diffs = [abs(vals[i] - vals[i - 1]) for i in range(1, len(vals))]
        return sum(diffs) / len(diffs)

    def reset(self) -> None:
        self.history.clear()
        self.samples = 0
        self.lost = 0
