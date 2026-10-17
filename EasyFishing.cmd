@echo off
rem ============================================================
rem  EasyFishing - 一键启动小艺钓鱼助手（同时启动两个程序）
rem  1) fish_alert.py   ：声音识别，上鱼时向 vibx 发信号
rem  2) vibx_press.py   ：自动收杆 + 自动抛竿（需管理员权限，见下）
rem
rem  说明：
rem  - 游戏(WowB)以管理员权限运行，普通权限 SendInput 会被 Windows
rem    UIPI 拦截，导致"收杆没反应"。因此按键程序必须提权。
rem  - 本环境 UAC 已被抑制，RunAs 会自动提升、不弹窗。
rem ============================================================
chcp 65001 >nul
setlocal
set "HERE=%~dp0"
set "BUNDLED=D:\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if not exist "%BUNDLED%" set "BUNDLED=python"

echo Starting EasyFishing ...
echo   [1/2] fish_alert (recognition) ...
start "EasyFishing - Recognition" cmd /c ""%HERE%start-alert.cmd""
echo   [2/2] vibx_press (auto reel + cast, admin) ...
call "%HERE%start-vibx-admin.cmd"
echo All launched. Check the two new windows for logs.
endlocal
