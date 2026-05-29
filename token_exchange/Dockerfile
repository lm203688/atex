FROM python:3.12-slim

WORKDIR /app

# Copy core files
COPY atex.py .
COPY config.json .
COPY package.json .
COPY service_executor.py .
COPY services/services.json ./services/
COPY api/server.py ./api/

# Install dependencies (none external - stdlib only)
EXPOSE 8420

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8420/api/v1/status')" || exit 1

CMD ["python3", "api/server.py"]
