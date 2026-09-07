@echo off
cd /d "D:\ADLINK\Myproject\novel-mind-new\frontend"
set BACKEND_URL=http://127.0.0.1:8000
set NO_PROXY=127.0.0.1,localhost
set no_proxy=127.0.0.1,localhost
rem next/font downloader fails through the local proxy; frontend must connect directly
set HTTP_PROXY=
set HTTPS_PROXY=
set http_proxy=
set https_proxy=
rem Build before launch so the detached server cannot serve stale .next output.
call npm run build >> "D:\ADLINK\Myproject\novel-mind-new\frontend\runtime-frontend.log" 2>&1 && call npm run start -- --port 3005 --hostname 127.0.0.1 >> "D:\ADLINK\Myproject\novel-mind-new\frontend\runtime-frontend.log" 2>&1
