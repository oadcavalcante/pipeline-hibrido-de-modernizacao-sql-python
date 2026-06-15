FROM python:3.14-slim

WORKDIR /app

# Dependências do sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY pyproject.toml ./
COPY src/ ./src/
COPY langgraph.json ./

# Instala o pacote e dependências (inclui langgraph-cli)
RUN uv pip install --system --no-cache -e .

COPY sql/ ./sql/

ENV PYTHONPATH=/app/src
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

COPY scripts/docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# 0.0.0.0 = bind no container (Docker). Browser/Studio: http://127.0.0.1:8000
ENTRYPOINT ["/app/docker-entrypoint.sh"]
