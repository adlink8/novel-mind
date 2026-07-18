$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$env:NO_PROXY = "127.0.0.1,localhost"
$env:HTTPS_PROXY = "http://127.0.0.1:7897"
$env:HTTP_PROXY = "http://127.0.0.1:7897"
$py = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
& $py -m uvicorn app.main:app --host 127.0.0.1 --port 8010 *>> (Join-Path $PSScriptRoot "uvicorn.out.log")
