#!/usr/bin/env python3
"""
mjNet 打包脚本 —— 生成免安装、双击即用的单文件程序（Linux）

用法：
    python3 build.py            打包，产物在 dist/mjNet
    python3 build.py --clean    先清掉上次的构建产物再打包
    python3 build.py --check    只检查打包环境，不打包

打包出来的 dist/mjNet 内含 Python 运行时，**拷到别的 x86_64 Linux 电脑上
不需要安装 Python 也能双击运行**（相当于 Windows 的 exe）。

两个关键点（都是踩坑得来的）：
  1. 必须用「Tk 支持 Xft/fontconfig」的 Python 打包（一般是 /usr/bin/python3）。
     conda 的 Tk 往往没编译 Xft，打出来的程序界面上会零散出现方块字。
  2. Ubuntu 自带的 `python3 -m venv` 常常因为缺 ensurepip 直接失败，而装
     python3-venv 需要 sudo。所以这里用 `venv --without-pip` +
     下载 get-pip.py 引导 pip，全程不需要管理员权限。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".build-venv"
WORK = ROOT / "build"
OUT = ROOT / "dist"
APP_NAME = "mjNet"
PROBE = ROOT / "tools" / "tk_check.py"
SMOKE = ROOT / "tools" / "smoke_binary.py"
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"


def say(msg: str) -> None:
    print(msg, flush=True)


def run(args: list[str], err: str, hint: str = "") -> None:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        if result.stdout:
            say(result.stdout[-1500:])
        if result.stderr:
            say(result.stderr[-1500:])
        if hint:
            say(f"  → {hint}")
        sys.exit(f"  ✗ {err}")


def rmrf(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def pick_system_python() -> str:
    """挑一个能正常显示中文的 Python 用来打包"""
    candidates = [os.environ.get("MJNET_PYTHON", ""), "/usr/bin/python3",
                  "/usr/local/bin/python3", "python3", "python"]
    fallback = ""
    for cand in candidates:
        if not cand:
            continue
        exe = shutil.which(cand)
        if not exe:
            continue
        if subprocess.run([exe, "-c", "import tkinter"],
                          capture_output=True).returncode != 0:
            continue
        if PROBE.exists():
            ok = subprocess.run([exe, str(PROBE)], capture_output=True).returncode == 0
            if ok:
                return exe
            fallback = fallback or exe
        else:
            return exe
    if fallback:
        say("  ! 只找到中文渲染能力不全的 Python，界面可能出现方块字")
        say("    建议： sudo apt install fonts-noto-cjk")
        return fallback
    sys.exit("  ✗ 没有找到可用的 python3（需要 tkinter）。\n"
             "    请先安装： sudo apt install python3 python3-tk fonts-noto-cjk")


def bootstrap_venv(sys_py: str) -> Path:
    """创建干净的构建环境（无需 sudo）"""
    rmrf(VENV)
    say("[1/4] 创建独立构建环境 .build-venv（不影响系统 Python）")
    r = subprocess.run([sys_py, "-m", "venv", "--without-pip", str(VENV)],
                       capture_output=True, text=True)
    vpy = VENV / "bin" / "python"
    if not vpy.exists():
        if r.stderr:
            say(r.stderr[-800:])
        sys.exit("  ✗ 创建虚拟环境失败。请确认系统有 python3-venv 或用 MJNET_PYTHON 指定解释器")

    # 引导 pip：venv 里没有 pip，就下一个 get-pip.py 装进去
    say("[2/4] 引导 pip（下载 get-pip.py，仅首次需要联网）")
    get_pip = ROOT / ".build" / "get-pip.py"
    get_pip.parent.mkdir(exist_ok=True)
    if not get_pip.exists():
        import urllib.request
        try:
            urllib.request.urlretrieve(GET_PIP_URL, get_pip)
        except Exception as exc:
            sys.exit(f"  ✗ 下载 get-pip.py 失败：{exc}\n"
                     "    可以手动下载后放到 .build/get-pip.py，或改用 MJNET_PYTHON 指定带 pip 的解释器")
    r = subprocess.run([str(vpy), str(get_pip), "-q"], capture_output=True, text=True)
    if subprocess.run([str(vpy), "-m", "pip", "--version"],
                      capture_output=True).returncode != 0:
        say(r.stderr[-1200:])
        sys.exit("  ✗ pip 引导失败")
    return vpy


def ensure_venv(sys_py: str) -> Path:
    vpy = VENV / "bin" / "python"
    if vpy.exists() and subprocess.run([str(vpy), "-m", "pip", "--version"],
                                       capture_output=True).returncode == 0:
        return vpy
    return bootstrap_venv(sys_py)


def main() -> int:
    parser = argparse.ArgumentParser(description="把 mjNet 打包成免安装单文件程序")
    parser.add_argument("--clean", action="store_true", help="先清理上次的构建产物")
    parser.add_argument("--check", action="store_true", help="只检查环境，不打包")
    parser.add_argument("--keep-venv", action="store_true", help="保留构建环境（下次更快）")
    opts = parser.parse_args()

    if opts.clean:
        rmrf(VENV)
        rmrf(WORK)
        rmrf(OUT)
        say("已清理上次的构建产物")

    say("=== mjNet 打包环境检查 ===")
    say(f"  项目目录    : {ROOT}")
    sys_py = pick_system_python()
    say(f"  打包解释器  : {sys_py}")
    if PROBE.exists():
        info = subprocess.run([sys_py, str(PROBE), "--verbose"],
                              capture_output=True, text=True).stdout
        for line in info.strip().splitlines():
            say(f"    {line}")
    if not (ROOT / "run.py").exists():
        sys.exit("  ✗ 找不到 run.py，请在项目目录里运行本脚本")
    if not (ROOT / "assets" / "mjnet.png").exists():
        say("  ! 缺 assets/mjnet.png（程序窗口图标会退化成代码绘制）")

    if opts.check:
        say("  · 检查完毕（未打包）。直接运行 python3 build.py 开始打包")
        return 0

    vpy = ensure_venv(sys_py)

    say("[3/4] 安装 PyInstaller（首次约 10MB，只装一次）")
    r = subprocess.run([str(vpy), "-m", "pip", "install", "-q", "pyinstaller"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        say(r.stdout[-1500:])
        say(r.stderr[-1500:])
        sys.exit("  ✗ 安装 PyInstaller 失败（检查网络/代理）")
    ver = subprocess.run([str(vpy), "-m", "PyInstaller", "--version"],
                         capture_output=True, text=True).stdout.strip()
    say(f"  ✓ PyInstaller {ver}")

    say("[4/4] 开始打包（需要一两分钟）")
    rmrf(WORK)
    rmrf(OUT)
    OUT.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    log = WORK / "build.log"
    args = [
        str(vpy), "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", APP_NAME,
        "--add-data", f"{ROOT / 'assets'}:assets",
        "--paths", str(ROOT),
        "--workpath", str(WORK),
        "--distpath", str(OUT),
        "--specpath", str(WORK),
        str(ROOT / "run.py"),
    ]
    with open(log, "w", encoding="utf-8") as fh:
        code = subprocess.run(args, stdout=fh, stderr=subprocess.STDOUT).returncode
    binary = OUT / APP_NAME
    if code != 0 or not binary.exists():
        say("  ! 打包失败，日志末尾：")
        text = log.read_text(encoding="utf-8", errors="ignore")
        say("\n".join(text.splitlines()[-40:]))
        sys.exit(f"  ✗ 完整日志：{log}")
    binary.chmod(0o755)
    alias = OUT / "mjNet网络监视器"
    shutil.copy2(binary, alias)
    size_mb = binary.stat().st_size / 1024 / 1024

    # 真实启动一次做验证：能起来、且中文渲染正常，才算打包成功
    if SMOKE.exists():
        say("  · 试运行验证（自动关闭）…")
        try:
            smoke = subprocess.run([str(vpy), str(SMOKE), str(binary)],
                                   capture_output=True, text=True, timeout=120)
            for line in (smoke.stdout or "").strip().splitlines():
                say(f"    {line}")
            if smoke.returncode != 0:
                say("    " + (smoke.stderr or "").strip()[-600:])
                say("  ! 试运行没通过，产物仍保留，可手动双击试试")
        except subprocess.TimeoutExpired:
            say("  ! 试运行超时（程序可能仍在运行，请手动检查）")

    say("")
    say(f"  ✓ 打包完成：{binary}  （{size_mb:.1f} MB）")
    say(f"  ✓ 中文别名：{alias}")
    say("")
    say("用法：")
    say(f"  1) 双击 {OUT / APP_NAME} 即可运行，不需要装 Python；")
    say("  2) 拷到其他 x86_64 Linux 电脑同样直接可跑；")
    say("  3) 想让它在应用菜单里出现： ./install-desktop.sh --use-binary")

    if not opts.keep_venv:
        say("  · 已删除构建环境（加 --keep-venv 可保留，下次打包更快）")
        rmrf(VENV)
    return 0


if __name__ == "__main__":
    sys.exit(main())
