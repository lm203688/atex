#!/usr/bin/env bash
# 真测 Realcast SQLite 在线热备脚本（建议 crontab 每日 03:00 执行）
#   crontab -e  ->  0 3 * * * /opt/realcast/deploy/backup.sh >> /opt/realcast/backups/cron.log 2>&1
set -euo pipefail

DB_PATH="${DB_PATH:-/opt/realcast/data/platform.db}"
BACKUP_DIR="${BACKUP_DIR:-/opt/realcast/backups}"
PY="${PYTHON:-python3}"
TS="$(date +%Y%m%d_%H%M%S)"
DST="$BACKUP_DIR/platform_$TS.db"

mkdir -p "$BACKUP_DIR"

# 使用 python sqlite3 在线备份 API：不阻塞写入、保证 WAL 一致性
"$PY" - "$DB_PATH" "$DST" <<'PY'
import sqlite3, sys, os
src, dst = sys.argv[1], sys.argv[2]
with sqlite3.connect(src) as c, sqlite3.connect(dst) as b:
    c.backup(b)
print("backup ok:", dst, os.path.getsize(dst), "bytes")
PY

# 仅保留最近 14 天
find "$BACKUP_DIR" -name 'platform_*.db' -mtime +14 -delete
echo "backup done -> $DST"
