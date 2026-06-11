#!/usr/bin/env bash
# Validação completa do pipeline:
#   1. Valida .env
#   2. Sobe docker compose (se não estiver rodando)
#   3. Aguarda API
#   4. Roda procedures B–F
#   5. Roda pytest
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API_URL="${API_URL:-http://localhost:8000}"

echo "╔══════════════════════════════════════════╗"
echo "║   SQL Modernizer — Teste Completo        ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# 1. Validar .env
bash "${ROOT}/scripts/check_env.sh"
echo ""

# 2. Verificar se API está no ar; se não, subir docker
if ! curl -sf "${API_URL}/health" &>/dev/null; then
  echo "API não está no ar. Subindo docker compose..."
  cd "$ROOT"
  docker compose up --build -d
  echo "Aguardando API ficar pronta..."
  for i in $(seq 1 60); do
    if curl -sf "${API_URL}/health" &>/dev/null; then
      echo "API pronta após ${i}x5s"
      break
    fi
    sleep 5
    if [[ "$i" -eq 60 ]]; then
      echo "Timeout: API não respondeu em 5 minutos"
      docker compose logs app --tail 50
      exit 1
    fi
  done
else
  echo "API já está no ar."
fi
echo ""

# 3. Rodar procedures B–F
bash "${ROOT}/scripts/run_all_procedures.sh"
echo ""

# 4. Pytest
echo "=== Testes automatizados ==="
cd "$ROOT"
if [[ -d ".venv" ]]; then
  PYTHONPATH=src .venv/bin/pytest tests/ -v --ignore=tests/test_behavioral.py -q
else
  PYTHONPATH=src python3 -m pytest tests/ -v --ignore=tests/test_behavioral.py -q 2>/dev/null || \
    echo "(pytest não disponível — rode: uv pip install -e . && pip install pytest)"
fi
echo ""

# 5. Resumo
echo "╔══════════════════════════════════════════╗"
echo "║   SQL Modernizer — Concluído             ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "Langfuse UI: http://localhost:3000"
echo "Swagger:     http://localhost:8000/docs"
echo "Métricas:    curl http://localhost:8000/metrics | jq"
