@echo off
rem Launch fish_alert (front window, keeps log visible).
chcp 65001 >nul
setlocal
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "HERE=%~dp0"
set "BUNDLED=D:\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"

if exist "%BUNDLED%" (
  "%BUNDLED%" "%HERE%fish_alert.py" run
) else (
  python "%HERE%fish_alert.py" run
)
echo.
echo fish_alert exited. Closing.
pause >nul
endlocal
