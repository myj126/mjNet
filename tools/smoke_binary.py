#!/usr/bin/env python3
"""
试运行验证：启动打包好的 mjNet 二进制，确认它真的能跑起来。

为什么需要它：`--windowed` 打包出来的程序如果启动时抛异常（缺模块、字体加载失败等），
进程会**立刻退出**，双击的人只看到"什么都没发生"。所以在打包流程里加一步真实验证：
启动 → 等几秒 → 进程还活着就说明窗口起来了。

用法：
    python3 tools/smoke_binary.py dist/mjNet
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python3 tools/smoke_binary.py <可执行文件路径>")
        return 2
    binary = sys.argv[1]
    if not os.path.exists(binary):
        print(f"找不到文件：{binary}")
        return 2

    print(f"启动 {os.path.basename(binary)} …")
    env = dict(os.environ)
    proc = subprocess.Popen([binary], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, env=env, start_new_session=True)
    # 给足启动时间：onefile 要解压运行时，首次可能要几秒
    deadline = time.time() + 8
    while time.time() < deadline:
        if proc.poll() is not None:
            output = ""
            try:
                output = proc.stdout.read() if proc.stdout else ""
            except Exception:
                pass
            print(f"  ✗ 程序启动后立即退出（退出码 {proc.returncode}）")
            if output.strip():
                print("    输出：" + output.strip()[-800:])
            return 1
        time.sleep(0.4)

    # 还活着 = 窗口已经起来了
    print("  ✓ 进程存活，窗口已启动")
    # 温和关闭：先 SIGTERM，必要时再 KILL
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        proc.terminate()
    try:
        proc.wait(timeout=6)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            proc.kill()
    print("  ✓ 已正常关闭")
    return 0


if __name__ == "__main__":
    sys.exit(main())
