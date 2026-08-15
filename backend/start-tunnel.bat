@echo off
rem 环境无关启动脚本：cloudflared 位于 %USERPROFILE%\.cloudflared 下
rem 隧道名/域名见 %USERPROFILE%\.cloudflared\config.novelmind-win.yml
start "" /MIN "%USERPROFILE%\.cloudflared\bin\cloudflared.exe" tunnel --config "%USERPROFILE%\.cloudflared\config.novelmind-win.yml" run novelmind-win
