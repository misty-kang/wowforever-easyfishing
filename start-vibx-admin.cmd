@echo off
rem Launch vibx_press with Administrator (HIGH integrity) token, no UAC prompt
rem Reason: game WowB runs as HIGH integrity; normal SendInput is blocked by UIPI.
rem ConsentPromptBehaviorAdmin=0 so RunAs auto-elevates without prompting.
chcp 65001 >nul
setlocal
set "HERE=%~dp0"
set "BUNDLED=D:\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
set "SCRIPT=%HERE%vibx_press.py"

powershell -NoProfile -Command "Start-Process -FilePath '%BUNDLED%' -ArgumentList '\"%SCRIPT%\" run' -Verb RunAs -WorkingDirectory '%HERE%'"
endlocal
