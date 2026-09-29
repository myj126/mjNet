#!/usr/bin/env bash
# ============================================================
#  mjNet 桌面集成安装脚本（只装到当前用户，不需要 sudo）
#
#    ./install-desktop.sh            安装：应用菜单图标 + mjnet 命令
#    ./install-desktop.sh --use-binary       菜单项指向打包好的免安装程序（dist/mjNet）
#    ./install-desktop.sh --with-autostart   额外设置登录后自动启动
#    ./install-desktop.sh --uninstall        卸载
#    ./install-desktop.sh --check            只检查环境，不安装
#
#  安装后：在应用菜单（按 Super 键）里搜索 “mjNet” 即可启动。
# ============================================================
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ID="mjnet"
APP_NAME="mjNet"
COMMENT="网络状态监视器（IP / 网速 / 代理 / 诊断）"

DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
BIN_HOME="$HOME/.local/bin"
APPS_DIR="$DATA_HOME/applications"
ICON_ROOT="$DATA_HOME/icons/hicolor"
DESKTOP_FILE="$APPS_DIR/$APP_ID.desktop"
WRAPPER="$BIN_HOME/$APP_ID"
AUTOSTART_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
AUTOSTART_FILE="$AUTOSTART_DIR/$APP_ID.desktop"

WITH_AUTOSTART=0
USE_BINARY=0
ACTION="install"
BINARY="$SRC/dist/mjNet"

info()  { printf '  %s\n' "$*"; }
ok()    { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn()  { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()   { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

for arg in "$@"; do
    case "$arg" in
        --with-autostart) WITH_AUTOSTART=1 ;;
        --use-binary)     USE_BINARY=1 ;;
        --uninstall)      ACTION="uninstall" ;;
        --check)          ACTION="check" ;;
        -h|--help)        sed -n '2,14p' "$0"; exit 0 ;;
        *) die "未知参数：$arg（用 --help 查看用法）" ;;
    esac
done

# --------------------------------------------------------------------------- #
# 找一个能正常显示中文的 python
#
# 光有 tkinter 还不够：conda 自带的 Tk 常常没编译 Xft/fontconfig，
# 拿不到中文 TrueType 字体时会静默换成位图字体，界面上会出现方块字。
# 所以这里用 tools/tk_check.py 实测，优先选真正可用的解释器。
# --------------------------------------------------------------------------- #
find_python() {
    local cand fallback=""
    for cand in "${MJNET_PYTHON:-}" /usr/bin/python3 /usr/local/bin/python3 python3 python; do
        [ -n "$cand" ] || continue
        command -v "$cand" >/dev/null 2>&1 || continue
        "$cand" -c "import tkinter" >/dev/null 2>&1 || continue
        if [ -f "$SRC/tools/tk_check.py" ] && "$cand" "$SRC/tools/tk_check.py" >/dev/null 2>&1; then
            command -v "$cand"
            return 0
        fi
        [ -n "$fallback" ] || fallback="$(command -v "$cand")"
    done
    if [ -n "$fallback" ]; then
        printf '%s\n' "$fallback"
        return 0
    fi
    return 1
}

PYTHON="$(find_python || true)"

do_check() {
    echo "=== mjNet 桌面集成自检 ==="
    info "项目目录    : $SRC"
    [ -f "$SRC/run.py" ] && ok "找到 run.py" || die "当前目录不像项目目录（缺 run.py）"
    if [ -n "$PYTHON" ]; then
        ok "Python 可用 : $PYTHON ($("$PYTHON" -V 2>&1))"
        if [ -f "$SRC/tools/tk_check.py" ] && "$PYTHON" "$SRC/tools/tk_check.py" >/dev/null 2>&1; then
            ok "中文渲染    : 正常（Tk 支持 TrueType 中文字体）"
        else
            warn "中文渲染    : 这个 Python 的 Tk 拿不到中文 TrueType 字体，界面可能出现方块字"
            info "  解决：sudo apt install fonts-noto-cjk  或指定 MJNET_PYTHON=/usr/bin/python3"
        fi
    else
        warn "没有找到带 tkinter 的 Python —— 图形界面无法启动"
        info "Ubuntu/Debian: sudo apt install python3 python3-tk"
    fi
    [ -f "$SRC/assets/mjnet.png" ] && ok "图标文件存在" || warn "缺 assets/mjnet.png（图标会显示成默认样式）"
    if [ -x "$BINARY" ]; then
        ok "免安装程序 : $BINARY（$(du -h "$BINARY" | cut -f1)）—— 加 --use-binary 即可让菜单指向它"
    else
        info "免安装程序 : 尚未打包（可选：python3 build.py 生成，之后用 --use-binary）"
    fi
    command -v update-desktop-database >/dev/null 2>&1 \
        && ok "update-desktop-database 可用" || warn "缺 update-desktop-database（菜单可能要等一会儿才刷新）"
    case ":$PATH:" in
        *":$BIN_HOME:"*) ok "$BIN_HOME 已在 PATH 中（可直接用 mjnet 命令）" ;;
        *) warn "$BIN_HOME 不在 PATH 中，安装后需要手动加，或直接用菜单图标启动" ;;
    esac
    echo
    info "将安装到："
    info "  菜单项  $DESKTOP_FILE"
    info "  命令    $WRAPPER"
    info "  图标    $ICON_ROOT/256x256/apps/$APP_ID.png 等尺寸"
    [ "$WITH_AUTOSTART" = 1 ] && info "  自启动  $AUTOSTART_FILE"
    return 0
}

do_uninstall() {
    echo "=== 卸载 mjNet 桌面集成 ==="
    local removed=0
    for f in "$DESKTOP_FILE" "$WRAPPER" "$AUTOSTART_FILE"; do
        if [ -e "$f" ]; then rm -f "$f"; ok "已删除 $f"; removed=1; fi
    done
    for size in 48 64 128 256; do
        local png="$ICON_ROOT/${size}x${size}/apps/$APP_ID.png"
        [ -e "$png" ] && { rm -f "$png"; removed=1; }
    done
    [ "$removed" = 1 ] && ok "图标文件已清理" || info "没有找到已安装的内容"
    command -v update-desktop-database >/dev/null 2>&1 && \
        update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
    command -v gtk-update-icon-cache >/dev/null 2>&1 && \
        gtk-update-icon-cache -f -t "$ICON_ROOT" >/dev/null 2>&1 || true
    echo "卸载完成（项目文件本身没有被删除）。"
}

do_install() {
    echo "=== 安装 mjNet 桌面集成 ==="
    if [ "$USE_BINARY" = 1 ]; then
        [ -x "$BINARY" ] || die "找不到打包好的程序：$BINARY
      请先运行： python3 build.py"
        ok "使用免安装程序：$BINARY"
    fi
    [ -f "$SRC/run.py" ] || die "找不到 $SRC/run.py，请在项目目录里运行本脚本"
    [ -n "$PYTHON" ] || die "没有找到带 tkinter 的 Python。请先安装：
      Ubuntu/Debian: sudo apt install python3 python3-tk
      Fedora:        sudo dnf install python3 python3-tkinter
      Arch:          sudo pacman -S python tk"

    # 修正脚本自身权限（避免出现“无法执行”）
    chmod 755 "$SRC/启动-linux.sh" "$SRC/run.py" 2>/dev/null || true
    [ -f "$SRC/install-desktop.sh" ] && chmod 755 "$SRC/install-desktop.sh" 2>/dev/null || true
    ok "已修正脚本权限"

    mkdir -p "$APPS_DIR" "$BIN_HOME" "$DATA_HOME"

    # ---- 1) 命令行启动器（路径全部写死，避免 PATH 环境不同导致起不来）----
    if [ "$USE_BINARY" = 1 ]; then
        cat > "$WRAPPER" <<EOF
#!/bin/sh
# mjNet 启动器（由 install-desktop.sh 生成，删除此文件即可取消安装）
# 指向打包好的免安装程序，不需要系统安装 Python
exec "$BINARY" "\$@"
EOF
        ok "已安装命令：$WRAPPER（免安装版）"
    else
        cat > "$WRAPPER" <<EOF
#!/bin/sh
# mjNet 启动器（由 install-desktop.sh 生成，删除此文件即可取消安装）
cd "$SRC" || exit 1
exec "$PYTHON" "$SRC/run.py" "\$@"
EOF
        ok "已安装命令：$WRAPPER"
    fi
    chmod 755 "$WRAPPER"

    # ---- 2) 各尺寸图标（按尺寸重新绘制，保证像素精确）----
    if [ -f "$SRC/assets/mjnet.png" ]; then
        if "$PYTHON" "$SRC/tools/make_icons.py" --hicolor "$ICON_ROOT" >/dev/null 2>&1; then
            ok "已安装图标（48/64/128/256，像素精确）"
        else
            warn "图标生成失败，改用单尺寸图标"
            mkdir -p "$ICON_ROOT/256x256/apps"
            cp -f "$SRC/assets/mjnet.png" "$ICON_ROOT/256x256/apps/$APP_ID.png"
        fi
    else
        warn "缺 assets/mjnet.png，跳过图标安装"
    fi

    # ---- 3) 桌面菜单项 ----
    cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=$APP_NAME
Name[zh_CN]=$APP_NAME 网络状态监视器
GenericName=Network Monitor
GenericName[zh_CN]=网络状态监视器
Comment=$COMMENT
Comment[zh_CN]=查看本机 IP、实时网速、代理状态，并一键诊断网络问题
Exec=$WRAPPER
Path=$SRC
Icon=$APP_ID
Terminal=false
Categories=Network;Monitor;
Keywords=network;ip;speed;proxy;wifi;网络;网速;代理;诊断;
StartupNotify=true
StartupWMClass=$APP_NAME
EOF
    chmod 644 "$DESKTOP_FILE"
    ok "已安装菜单项：$DESKTOP_FILE"

    # ---- 4) 可选：登录后自动启动 ----
    if [ "$WITH_AUTOSTART" = 1 ]; then
        mkdir -p "$AUTOSTART_DIR"
        cp -f "$DESKTOP_FILE" "$AUTOSTART_FILE"
        # 自启动时最小化由程序自身控制，这里只加标记
        printf 'X-GNOME-Autostart-enabled=true\n' >> "$AUTOSTART_FILE"
        ok "已设置登录后自动启动：$AUTOSTART_FILE"
    fi

    # ---- 5) 刷新桌面与图标缓存 ----
    command -v update-desktop-database >/dev/null 2>&1 && \
        update-desktop-database "$APPS_DIR" >/dev/null 2>&1 && ok "已刷新桌面数据库" || true
    command -v gtk-update-icon-cache >/dev/null 2>&1 && \
        gtk-update-icon-cache -f -t "$ICON_ROOT" >/dev/null 2>&1 || true

    # ---- 6) 校验 desktop 文件是否合法 ----
    if command -v desktop-file-validate >/dev/null 2>&1; then
        if desktop-file-validate "$DESKTOP_FILE" 2>/dev/null; then
            ok "desktop 文件校验通过"
        else
            warn "desktop 文件有问题："
            desktop-file-validate "$DESKTOP_FILE" || true
        fi
    fi

    echo
    echo "安装完成 ✅"
    echo "  1) 启动方式一：按 Super 键，搜索 “mjNet” 点击图标"
    echo "  2) 启动方式二：终端执行  mjnet"
    case ":$PATH:" in
        *":$BIN_HOME:"*) : ;;
        *) echo "     注意：$BIN_HOME 不在 PATH 中，要用命令启动请先执行："
           echo "       echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.profile && source ~/.profile" ;;
    esac
    echo "  3) 卸载：重新运行本脚本并加 --uninstall"
    return 0
}

case "$ACTION" in
    check)     do_check ;;
    uninstall) do_uninstall ;;
    install)   do_install ;;
esac
