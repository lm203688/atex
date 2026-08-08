# 真测 Realcast — 生产镜像（构建上下文 = 仓库根目录）
FROM python:3.13-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai

# 依赖（利用层缓存）
COPY app/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# 应用代码
COPY app /app
RUN chmod +x /app/seed.py

EXPOSE 8000

# 首次启动落库 + 起服务（workers 由 UVICORN_WORKERS 环境变量控制，默认 2）
CMD ["sh", "-c", "python seed.py && uvicorn main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-2}"]
