@echo off
chcp 936 >nul
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $c = New-Object Net.Sockets.TcpClient('127.0.0.1', 49777); $s = $c.GetStream(); $b = [Text.Encoding]::ASCII.GetBytes('quit'); $s.Write($b, 0, $b.Length); Start-Sleep -Milliseconds 400; $c.Close(); Write-Host '已发送退出指令。' -ForegroundColor Green; Write-Host '（日志里会出现「已退出监听」）' } catch { Write-Host '没有检测到正在运行的钓鱼提醒。' -ForegroundColor Yellow }"
echo.
pause
endlocal