#!/usr/bin/env bash
# Executa o pipeline sobre os Anexos B–F e salva resultados em results/
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API_URL="${API_URL:-http://localhost:8000}"
MODEL="${MODEL:-gpt-5.4-mini}"
RESULTS_DIR="${ROOT}/results"
SCHEMA_FILE="${ROOT}/sql/00_schema_legado.sql"

mkdir -p "$RESULTS_DIR"

if ! command -v jq &>/dev/null; then
  echo "Erro: jq é necessário. Instale com: brew install jq"
  exit 1
fi

if [[ ! -f "$SCHEMA_FILE" ]]; then
  echo "Erro: schema não encontrado em $SCHEMA_FILE"
  exit 1
fi

SCHEMA=$(cat "$SCHEMA_FILE")

echo "=== Pipeline SQL Modernizer — Anexos B a F ==="
echo "API:   $API_URL"
echo "Model: $MODEL"
echo ""

# Health check
echo -n "Health check... "
HEALTH=$(curl -sf "${API_URL}/health" || echo "FAIL")
if [[ "$HEALTH" == "FAIL" ]]; then
  echo "FALHOU"
  echo "Suba o stack: docker compose up --build"
  exit 1
fi
echo "OK ($(echo "$HEALTH" | jq -r '.status'))"
echo ""

for letter in B C D E F; do
  file=$(ls "${ROOT}/sql/procedures/${letter}"_*.sql 2>/dev/null | head -1)
  if [[ -z "$file" ]]; then
    echo "Arquivo do anexo ${letter} não encontrado, pulando."
    continue
  fi

  name=$(basename "$file" .sql)
  sql=$(cat "$file")

  echo -n "[$letter] $name ... "

  payload=$(jq -n \
    --arg sql "$sql" \
    --arg schema "$SCHEMA" \
    --arg model "$MODEL" \
    '{source_code: $sql, schema_context: $schema, model: $model}')

  response=$(curl -sf -X POST "${API_URL}/modernize" \
    -H "Content-Type: application/json" \
    -d "$payload" 2>/dev/null) || {
    echo "ERRO (curl falhou)"
    continue
  }

  echo "$response" | jq '.' > "${RESULTS_DIR}/${name}.json"
  echo "$response" | jq -r '.generated_code // empty' > "${RESULTS_DIR}/${name}.py"

  status=$(echo "$response" | jq -r '.status')
  quality=$(echo "$response" | jq -r '.report.validation.quality_score // "n/a"')
  lines=$(echo "$response" | jq -r '.report.generation.lines_generated // 0')

  echo "${status} | quality=${quality} | ${lines} linhas → results/${name}.{json,py}"
done

echo ""
echo "=== Métricas agregadas ==="
curl -sf "${API_URL}/metrics" | jq '.' || echo "(metrics indisponível)"
echo ""
echo "Concluído! Arquivos em: $RESULTS_DIR"
