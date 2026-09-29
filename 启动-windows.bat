@echo off
chcp 65001 >nul
title mjNet 网络状态监视器
rem ============================================================
rem  mjNet Windows 启动脚本（双击即可运行）
rem  优先用 pythonw 启动，这样不会弹出黑色命令行窗口
rem ============================================================
setlocal
cd /d "%~dp0"

rem 1) 找一个可用的 Python（py 启动器优先，其次是 PATH 里的 python）
set "PY_EXE="
where py >nul 2>nul && set "PY_EXE=py"
if not defined PY_EXE (
    where python >nul 2>nul && set "PY_EXE=python"
)
if not defined PY_EXE (
    echo [错误] 没有找到 Python。
    echo         请先到 https://www.python.org/downloads/windows/ 安装 Python 3.8 或更高版本，
    echo         安装时务必勾选 "Add Python to PATH"。
    echo.
    pause
    exit /b 1
)

rem 2) 先做一次环境自检，缺 tkinter 等依赖时给出中文提示
%PY_EXE% run.py --check
if errorlevel 1 (
    echo.
    echo [错误] 自检未通过，请根据上面的提示处理后重试。
    pause
    exit /b 1
)

rem 3) 无窗口启动图形界面（pythonw）
set "PYW="
for /f "delims=" %%i in ('%PY_EXE% -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))"') do set "PYW=%%i"
if exist "%PYW%" (
    start "" "%PYW%" run.py
) else (
    %PY_EXE% run.py
)

endlocal
