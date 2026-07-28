@echo off
cd /d "D:\ADLINK\Myproject\novel-mind\backend"
set NO_PROXY=127.0.0.1,localhost
set no_proxy=127.0.0.1,localhost
set HTTPS_PROXY=http://127.0.0.1:7897
set HTTP_PROXY=http://127.0.0.1:7897
"D:\ADLINK\Myproject\novel-mind\backend\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8010 >> "D:\ADLINK\Myproject\novel-mind\backend\uvicorn.out.log" 2>&1
