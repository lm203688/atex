#!/usr/bin/env bash
# 真测 Realcast 健康检查（供 cron / 监控调用；非 200 退出码非 0）
#   HEALTH_URL=http://127.0.0.1:8000/api/health ./healthcheck.sh
set -uo pipefail

URL="${HEALTH_URL:-http://127.0.0.1:8000/api/health}"
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$URL" 2>/dev/null || echo 000)

if [ "$code" = "200" ]; then
    echo "OK ($code) $(date -u +%FT%TZ)"
    exit 0
else
    echo "UNHEALTHY ($code) $(date -u +%FT%TZ)"
    exit 1
fi
