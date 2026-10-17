@echo off
rem Record fishing "bite" sound sample (learn).
chcp 65001 >nul
setlocal
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "HERE=%~dp0"
set "BUNDLED=D:\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"

echo ============================================================
echo  RECORD SAMPLE   (press Enter the moment you hear the splash)
echo  1) In game: cast your rod.
echo  2) The moment you hear the "bite" splash, press Enter.
echo  3) It replays ~2s - answer y if it is the splash sound.
echo  4) Record 1-3 segments, then type q and press Enter to end.
echo ============================================================
echo.

if exist "%BUNDLED%" (
  "%BUNDLED%" "%HERE%fish_alert.py" learn
) else (
  python "%HERE%fish_alert.py" learn
)
echo.
echo Done. Press any key to close.
pause >nul
endlocal
