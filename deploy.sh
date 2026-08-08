#!/usr/bin/env bash
# 真测 Realcast — 一键部署脚本（供 GitHub webhook / 手动执行）
# 约定：在仓库根目录运行；需要 docker + docker compose 可用。
set -euo pipefail
cd "$(dirname "$0")"

echo "[deploy] 进入目录: $(pwd)"

# 1) 准备 .env（首次部署从示例复制，之后保留已配置的）
if [ ! -f .env ]; then
  cp .env.example .env
  echo "[deploy] 已生成 .env —— 上线前请编辑 ADMIN_TOKEN / CORS_ORIGINS / 证书路径"
fi

# 2) 准备持久化与证书目录
mkdir -p data certs

# 3) 拉取最新代码（若由 webhook 触发，仓库已是最新；手动跑则先 git pull）
if [ -d .git ]; then
  git pull --ff-only || echo "[deploy] git pull 跳过（可稍后手动同步）"
fi

# 4) 构建并启动
echo "[deploy] 构建并启动服务 ..."
docker compose up -d --build

# 5) 等待健康检查
echo "[deploy] 等待 /api/health 就绪 ..."
READY=0
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
    READY=1
    break
  fi
  sleep 2
done

if [ "$READY" -eq 1 ]; then
  echo "[deploy] ✅ 服务已就绪。HTTP 见 :80，HTTPS 需先把证书放到 certs/ 后 reload nginx"
  curl -fsS http://127.0.0.1:8000/api/health
  echo
else
  echo "[deploy] ⚠️ 健康检查超时，请查看: docker compose logs app" >&2
  exit 1
fi
