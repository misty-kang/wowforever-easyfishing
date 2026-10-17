@echo off
rem Launch vibx_press (front window, keeps log visible).
chcp 65001 >nul
setlocal
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "HERE=%~dp0"
set "BUNDLED=D:\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"

if exist "%BUNDLED%" (
  "%BUNDLED%" "%HERE%vibx_press.py" run
) else (
  python "%HERE%vibx_press.py" run
)
echo.
echo vibx_press exited. Closing.
pause >nul
endlocal
