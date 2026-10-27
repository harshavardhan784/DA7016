@echo off
cd /d C:\app
rem Settings live in system environment variables (see DEPLOY.md). The app listens on this machine only; Caddy serves it to the internet over HTTPS.
python -m uvicorn ocr.api:app --host 127.0.0.1 --port 8000
