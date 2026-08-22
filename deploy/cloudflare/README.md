# NovelMind Cloudflare Tunnel
#
# Hosts (tunnel: <tunnel-name> / <tunnel-id>)
# - https://<fe-domain>     -> http://127.0.0.1:3005  (Next.js)
# - https://<api-domain> -> http://127.0.0.1:8010  (FastAPI)
#
# Note: existing tunnel `my-pc` runs on another Linux host (<my-pc-domain>).
# This Windows box uses a separate tunnel so configs do not conflict.
#
# Start local apps first:
#   backend:  uvicorn on 8010 (not 8000 — leave 8000 free for other tools)
#   frontend: npm run dev -- --port 3005
#   BACKEND_URL=http://127.0.0.1:8010 for Next rewrites
#
# Start tunnel:
#   powershell -File deploy/cloudflare/start-tunnel.ps1
#
# Stop tunnel:
#   powershell -File deploy/cloudflare/stop-tunnel.ps1
#
# CORS: backend .env must include https://<fe-domain>
