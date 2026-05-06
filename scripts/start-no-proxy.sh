#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

unset HTTP_PROXY
unset HTTPS_PROXY
unset ALL_PROXY
unset http_proxy
unset https_proxy
unset all_proxy

exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
