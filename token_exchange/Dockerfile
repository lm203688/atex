FROM python:3.12-slim

WORKDIR /app

# Install z-ai-web-dev-sdk (Node.js runtime for AI capabilities)
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && curl -fsSL https://bun.sh/install | bash \
    && ~/.bun/bin/bun install -g z-ai-web-dev-sdk \
    && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

ENV NODE_PATH=/root/.bun/install/global/node_modules
ENV PATH="/root/.bun/bin:${PATH}"

# Copy core files
COPY atex.py .
COPY config.json .
COPY package.json .
COPY server.json .
COPY service_executor.py .
COPY content_safety.py .
COPY job_market.py .
COPY skill_market.py .
COPY realtime.py .
COPY a2a_protocol.py .
COPY smithery.yaml .
COPY services/services.json ./services/
COPY api/server.py ./api/
COPY payment/ ./payment/
COPY docs/ ./docs/
COPY static/ ./static/
COPY data/ ./data/

# Python stdlib only — no pip install needed
EXPOSE 8420

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8420/api/v1/status')" || exit 1

CMD ["python3", "api/server.py"]
