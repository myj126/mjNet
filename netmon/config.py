"""
netmon.config —— 极简用户偏好（INI 格式，出错一律忽略，绝不影响程序启动）

位置（Windows 与 Linux 各自的家目录都适用）：
    ~/.config/mjnet/config.ini

目前支持：
    [ui]
    font_family = Noto Sans CJK SC   # 手动指定界面字体（留空=自动挑选）
    font_size   = 12                 # 界面基准字号（11 是默认，觉得字小可调大）

为什么允许手动指定字体：极少数环境的 Tk 没有编译 Xft/fontconfig 支持，
只能用老式位图字体，界面上会零散出现方块。这时换字体或换解释器都能解决，
留个手动入口比让用户去改代码友好。
"""
from __future__ import annotations

import configparser
import os

APP_DIR_NAME = "mjnet"


def config_path() -> str:
    """配置文件路径（遵循 XDG 规范，Windows 上落到 %APPDATA%）"""
    base = os.environ.get("XDG_CONFIG_HOME")
    if not base:
        if os.name == "nt":
            base = os.environ.get("APPDATA") or os.path.expanduser("~")
        else:
            base = os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, APP_DIR_NAME, "config.ini")


def load_config() -> dict:
    """读取配置；文件不存在或格式有问题都返回空字典（程序继续用默认值）"""
    path = config_path()
    data: dict = {}
    try:
        parser = configparser.ConfigParser()
        parser.read(path, encoding="utf-8")
        if parser.has_section("ui"):
            for key in ("font_family", "font_size"):
                if parser.has_option("ui", key):
                    data[key] = parser.get("ui", key).strip()
    except Exception:
        return {}
    return data


def save_config(values: dict) -> bool:
    """写回配置（保留未知字段由调用方负责，这里只写 ui 段）"""
    path = config_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        parser = configparser.ConfigParser()
        parser.read(path, encoding="utf-8")
        if not parser.has_section("ui"):
            parser.add_section("ui")
        for key, value in values.items():
            parser.set("ui", key, str(value))
        with open(path, "w", encoding="utf-8") as fh:
            parser.write(fh)
        return True
    except Exception:
        return False
