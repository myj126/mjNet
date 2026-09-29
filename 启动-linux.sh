#!/usr/bin/env bash
# ============================================================
#  mjNet Linux 启动脚本
#
#  用法: ./启动-linux.sh          # 启动图形界面
#        ./启动-linux.sh --check  # 只做环境自检（不开窗口）
#        ./启动-linux.sh --report # 只在终端打印一次网络信息
# ============================================================
set -u
cd "$(dirname "$0")"

PROBE="tools/tk_check.py"

# ---------------------------------------------------------------------------
# 挑解释器：不仅要有 tkinter，还要能显示中文
#
# 为什么要挑：conda 自带的 Tk 常常没有编译 Xft/fontconfig，找不到中文字体时
# 会静默换成老式位图字体，界面上就会零散出现方块。用它等于让用户看残缺界面。
# ---------------------------------------------------------------------------
PY=""
FALLBACK=""
for cand in "${MJNET_PYTHON:-}" /usr/bin/python3 /usr/local/bin/python3 python3 python; do
    [ -n "$cand" ] || continue
    command -v "$cand" >/dev/null 2>&1 || continue
    "$cand" -c "import tkinter" >/dev/null 2>&1 || continue
    if [ -f "$PROBE" ] && "$cand" "$PROBE" >/dev/null 2>&1; then
        PY="$cand"
        break
    fi
    # 记录一个“能跑但中文渲染可能不完整”的解释器作为兜底
    [ -n "$FALLBACK" ] || FALLBACK="$cand"
done

if [ -z "$PY" ]; then
    if [ -n "$FALLBACK" ]; then
        echo "[警告] 只找到中文渲染能力不全的 Python：$FALLBACK"
        echo "       它自带的 Tk 缺少 Xft/fontconfig 支持，界面可能出现方块字。"
        echo "       建议装中文字体后重试： sudo apt install fonts-noto-cjk"
        echo "       或指定解释器： MJNET_PYTHON=/usr/bin/python3 ./启动-linux.sh"
        PY="$FALLBACK"
    else
        echo "[错误] 没有找到带 tkinter 的 python3。请先安装："
        echo "       Ubuntu/Debian : sudo apt install python3 python3-tk fonts-noto-cjk"
        echo "       Fedora        : sudo dnf install python3 python3-tkinter"
        echo "       Arch          : sudo pacman -S python tk"
        exit 1
    fi
fi

exec "$PY" run.py "$@"
