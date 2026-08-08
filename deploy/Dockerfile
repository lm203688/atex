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

# 多 worker（SQLite + WAL，配合 busy_timeout 抗并发）。生产建议 2~4 个 worker。
# UVICORN_WORKERS 可由 compose/.env 注入。
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-3}"]
