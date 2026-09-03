#!/usr/bin/env bash
# 真测 Realcast —— Railway 镜像本地自检（在装了 Docker 的机器上跑，提前暴露
# Railway 构建/启动会出的问题，省去在 Railway 控制台反复试错）。
#
# 它会做的：
#   1. 用 deploy/Dockerfile.railway 构建镜像（构建上下文=仓库根，与 Railway 一致）
#   2. 起容器，挂一个临时卷到 /data，模拟 Railway 的 Volume 挂载
#   3. 等 /api/health 就绪
#   4. 校验：版本号 / seed 生效 / 首页 CSP 含 nonce 且 style-src 无 'unsafe-inline'
#            / 首页 HTML 的 <style> 与 <script> 都带 nonce
#   5. 全部通过则 ✅；否则 ❌ 并打印诊断。最后清掉容器（临时卷默认一并删）。
#
# 用法（在仓库根目录运行）：
#   bash deploy/verify_local.sh
# 可选环境变量：
#   IMAGE=realcast:railway  镜像名（默认 realcast:railway）
#   HOST_PORT=8123          本机映射端口（默认 8123，避免占用 8000）
#   APP_TZ=8               业务时区（默认 8）
#   SEED_ON_BOOT=1         是否自动播种演示数据（默认 1，便于看到 counts 非空）
#   KEEP_VOLUME=0          非零则保留临时卷目录（默认 0=删）
set -uo pipefail

cd "$(dirname "$0")/.."   # 切到仓库根（脚本在 deploy/ 下）
REPO_ROOT="$(pwd)"

IMAGE="${IMAGE:-realcast:railway}"
HOST_PORT="${HOST_PORT:-8123}"
APP_TZ="${APP_TZ:-8}"
SEED_ON_BOOT="${SEED_ON_BOOT:-1}"
KEEP_VOLUME="${KEEP_VOLUME:-0}"

DOCKERFILE="deploy/Dockerfile.railway"
VOL_DIR="$(mktemp -d -t realcast_verify.XXXXXX)"   # 临时「卷」，模拟 Railway /data
CTR="realcast_verify_$$"
PASS=0; FAIL=0

say(){ printf '%s\n' "$1"; }
check(){ if [ "$1" = "1" ]; then PASS=$((PASS+1)); say "  ✅ $2"; else FAIL=$((FAIL+1)); say "  ❌ $2"; fi; }

# 容器退出时兜底清理（无论成功失败）
cleanup(){ docker rm -f "$CTR" >/dev/null 2>&1 || true; [ "$KEEP_VOLUME" = "0" ] && rm -rf "$VOL_DIR" >/dev/null 2>&1 || true; }
trap cleanup EXIT

# 0) 前置检查
if ! command -v docker >/dev/null 2>&1; then
  say "❌ 未找到 docker。请先安装 Docker Desktop / docker-cli 再运行本脚本。"
  exit 1
fi
[ -f "$DOCKERFILE" ] || { say "❌ 找不到 $DOCKERFILE"; exit 1; }

# 1) 构建
say "== 1) 构建 Railway 镜像 ($DOCKERFILE) =="
docker build -f "$DOCKERFILE" -t "$IMAGE" . || { say "❌ 构建失败（见上方 docker 输出）"; exit 1; }

# 2) 起容器（模拟 Railway：动态 PORT + 卷挂 /data + DB_PATH 指向卷）
say "== 2) 起容器（卷挂 $VOL_DIR -> /data, PORT=$HOST_PORT）=="
docker run -d --name "$CTR" \
  -e PORT="$HOST_PORT" -e DB_PATH=/data/platform.db \
  -e APP_TZ="$APP_TZ" -e SEED_ON_BOOT="$SEED_ON_BOOT" -e APP_ENV=production \
  -p "127.0.0.1:$HOST_PORT:$HOST_PORT" \
  -v "$VOL_DIR:/data" \
  "$IMAGE" >/dev/null 2>&1 || { say "❌ 容器启动失败（docker logs $CTR）："; docker logs "$CTR" 2>&1 | tail -20; exit 1; }

# 3) 等健康检查
say "== 3) 等待 /api/health =="
READY=0
for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1:$HOST_PORT/api/health" 2>/dev/null || echo 000)
  [ "$code" = "200" ] && { READY=1; break; }
  sleep 2
done
[ "$READY" = "1" ] || { say "❌ 健康检查超时（docker logs $CTR）："; docker logs "$CTR" 2>&1 | tail -30; exit 1; }

# 4) 校验
say "== 4) 校验 =="
HEALTH=$(curl -s --max-time 5 "http://127.0.0.1:$HOST_PORT/api/health")
echo "   health: $HEALTH"
VER=$(printf '%s' "$HEALTH" | grep -oE '"version":"[^"]*"' | head -1 | sed 's/"version":"//; s/"//')
[ -n "$VER" ] && check 1 "health 返回版本号: $VER" || check 0 "health 未返回版本号"

# 首页 + 响应头
curl -s -D /tmp/realcast_hdr.$$ --max-time 5 "http://127.0.0.1:$HOST_PORT/" -o /tmp/realcast_idx.$$ 2>/dev/null
CSP=$(grep -i '^content-security-policy:' /tmp/realcast_hdr.$$ 2>/dev/null | tr -d '\r')
[ -n "$CSP" ] && check 1 "响应含 CSP 头" || check 0 "响应缺少 CSP 头"
echo "   CSP: $CSP"

# style-src 必须有 nonce 且不能有 'unsafe-inline'
echo "$CSP" | grep -qE "style-src[^;]*'nonce-" && check 1 "style-src 含 nonce" || check 0 "style-src 缺少 nonce"
echo "$CSP" | grep -qiE "style-src[^;]*'unsafe-inline'" && check 0 "style-src 仍含 'unsafe-inline'（应已去除）" || check 1 "style-src 无 'unsafe-inline'"
echo "$CSP" | grep -qE "script-src[^;]*'nonce-" && check 1 "script-src 含 nonce" || check 0 "script-src 缺少 nonce"

# HTML 里 <style>/<script> 是否带 nonce
SNONCE=$(grep -oE '<script nonce="[^"]*"' /tmp/realcast_idx.$$ 2>/dev/null | wc -l)
STNONCE=$(grep -oE '<style nonce="[^"]*"' /tmp/realcast_idx.$$ 2>/dev/null | wc -l)
[ "$SNONCE" -ge 1 ] && check 1 "HTML 含带 nonce 的 <script> (x$SNONCE)" || check 0 "HTML 缺少带 nonce 的 <script>"
[ "$STNONCE" -ge 1 ] && check 1 "HTML 含带 nonce 的 <style> (x$STNONCE)" || check 0 "HTML 缺少带 nonce 的 <style>"

# 目录缺失应响亮失败（反向验证：用错误 DB_PATH 起一个应立刻退出的容器）
say "== 5) 反向验证：DB_PATH 目录不存在应拒绝启动 =="
docker run -d --name "${CTR}_bad" -e DB_PATH=/no_such_dir/platform.db -e PORT=8199 "$IMAGE" >/dev/null 2>&1
sleep 3
BAD_LOGS=$(docker logs "${CTR}_bad" 2>&1 | grep -iE "目录不存在|拒绝启动" | head -2)
[ -n "$BAD_LOGS" ] && check 1 "目录缺失时响亮失败（见日志）" || check 0 "目录缺失时未检测到拒绝启动"
docker rm -f "${CTR}_bad" >/dev/null 2>&1 || true

# 汇总
say "== 汇总 =="
say "  PASS=$PASS  FAIL=$FAIL"
rm -f /tmp/realcast_hdr.$$ /tmp/realcast_idx.$$ 2>/dev/null
[ "$FAIL" = "0" ] && { say "✅ 本地镜像与 Railway 配置一致，可放心部署。"; exit 0; } || { say "❌ 存在失败项，见上方 ❌。"; exit 1; }
