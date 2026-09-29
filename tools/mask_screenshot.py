#!/usr/bin/env python3
"""
截图打码工具（开发自用）—— 把截图里的隐私信息模糊掉，再放进公开仓库。

为什么需要：界面截图里会包含真实公网 IP、内网 IP、WiFi 名称、DNS 服务器等隐私信息，
直接传到公开 GitHub 仓库等于泄露自己的网络信息。这里用纯标准库（zlib）读写 PNG，
把指定区域做马赛克处理，不依赖 PIL。

用法：
    python3 tools/mask_screenshot.py 输入.png 输出.png  [x,y,w,h ...]
"""
from __future__ import annotations

import struct
import sys
import zlib


def read_png(path: str):
    data = open(path, "rb").read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("不是 PNG")
    pos, idat = 8, b""
    width = height = color_type = bit_depth = 0
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
        raise ValueError("只支持 8 位 RGB/RGBA")
    channels = 3 if color_type == 2 else 4
    raw = zlib.decompress(idat)
    stride = width * channels
    rows = []
    prev = bytearray(stride)

    def paeth(a, b, c):
        p = a + b - c
        pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
        return a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)

    p = 0
    for _ in range(height):
        ftype = raw[p]; p += 1
        line = bytearray(raw[p:p + stride]); p += stride
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
        rows.append(line)
        prev = line
    return width, height, channels, rows


def write_png(path: str, width: int, height: int, channels: int, rows) -> None:
    color_type = 2 if channels == 3 else 6
    raw = b"".join(b"\x00" + bytes(row) for row in rows)

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    out = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
    open(path, "wb").write(out)


def pixelate(rows, channels: int, x: int, y: int, w: int, h: int, block: int = 9) -> None:
    """把矩形区域做成马赛克：按 block×block 求平均色再填回"""
    height = len(rows)
    width = len(rows[0]) // channels if rows else 0
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(width, x + w), min(height, y + h)
    for by in range(y0, y1, block):
        for bx in range(x0, x1, block):
            ex, ey = min(bx + block, x1), min(by + block, y1)
            total = [0, 0, 0]
            count = 0
            for yy in range(by, ey):
                row = rows[yy]
                for xx in range(bx, ex):
                    base = xx * channels
                    total[0] += row[base]
                    total[1] += row[base + 1]
                    total[2] += row[base + 2]
                    count += 1
            if not count:
                continue
            avg = [t // count for t in total]
            for yy in range(by, ey):
                row = rows[yy]
                for xx in range(bx, ex):
                    base = xx * channels
                    row[base], row[base + 1], row[base + 2] = avg


def main() -> int:
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    src, dst = sys.argv[1], sys.argv[2]
    regions = []
    for spec in sys.argv[3:]:
        parts = [int(v) for v in spec.split(",")]
        if len(parts) != 4:
            print(f"区域格式应为 x,y,w,h：{spec}")
            return 2
        regions.append(parts)
    width, height, channels, rows = read_png(src)
    for region in regions:
        px, py, pw, ph = region
        pixelate(rows, channels, px, py, pw, ph)
    write_png(dst, width, height, channels, rows)
    print(f"✓ 已处理 {len(regions)} 个区域 -> {dst} ({width}x{height})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
