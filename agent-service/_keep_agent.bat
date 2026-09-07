@echo off
cd /d "D:\ADLINK\Myproject\novel-mind-new\agent-service"
set NOVELMIND_GATEWAY_TOKEN=dev-agent-gateway-token-local
set FASTAPI_BASE_URL=http://127.0.0.1:8000
set PORT=3100
set NO_PROXY=127.0.0.1,localhost
set no_proxy=127.0.0.1,localhost
set HTTP_PROXY=
set HTTPS_PROXY=
set http_proxy=
set https_proxy=
call npm run start >> "D:\ADLINK\Myproject\novel-mind-new\agent-service\runtime-agent.log" 2>&1
