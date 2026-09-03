# 真测 Realcast 生产镜像
# 构建（构建上下文为 app/ 目录）：
#   docker build -f deploy/Dockerfile -t realcast:0.4.1 ./app
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 先装依赖，利用层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 再拷代码
COPY . .

# 应用启动时会自动 init_db() 建表；生产数据请通过 DB_PATH 指向挂载卷（见 docker-compose.yml）
EXPOSE 8000

# worker 数默认 **1**——这不是保守，是正确性要求。
# 未配 REDIS_URL 时，实时广播走进程内 MemoryBackplane（见 app/core/backplane.py）：
# 一个 worker 就是一个独立广播域。worker>1 时，A 进程里的市场状态变更推不到
# 连在 B 进程上的客户端，不同用户看到的概率会不一致——对预测平台是致命的。
# 要扩容必须先配 REDIS_URL 切到 RedisBackplane（并建议同时迁 Postgres），
# 再调大 UVICORN_WORKERS。
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-1}"]
