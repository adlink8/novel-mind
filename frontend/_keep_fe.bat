@echo off
cd /d "D:\ADLINK\Myproject\novel-mind\frontend"
set BACKEND_URL=http://127.0.0.1:8010
set NO_PROXY=127.0.0.1,localhost
set no_proxy=127.0.0.1,localhost
call npm run dev -- --port 3005 --hostname 127.0.0.1 >> "D:\ADLINK\Myproject\novel-mind\frontend\runtime-frontend.log" 2>&1
