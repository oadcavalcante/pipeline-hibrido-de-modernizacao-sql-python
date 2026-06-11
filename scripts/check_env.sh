#!/usr/bin/env bash
# Valida se o .env está configurado corretamente.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ROOT}/.env"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok=0
warn=0
fail=0

check() {
  local label="$1" val="$2" required="${3:-true}"
  if [[ -z "$val" || "$val" == *"..."* ]]; then
    if [[ "$required" == "true" ]]; then
      echo -e "${RED}✗${NC} $label — não configurado"
      ((fail++)) || true
    else
      echo -e "${YELLOW}○${NC} $label — opcional, não configurado"
      ((warn++)) || true
    fi
  else
    echo -e "${GREEN}✓${NC} $label"
    ((ok++)) || true
  fi
}

if [[ ! -f "$ENV_FILE" ]]; then
  echo -e "${RED}Arquivo .env não encontrado. Rode: cp .env.example .env${NC}"
  exit 1
fi

# shellcheck disable=SC1090
source "$ENV_FILE"

echo "=== Verificação do .env ==="
echo ""

check "OPENAI_API_KEY (obrigatório para GPT)" "${OPENAI_API_KEY:-}" "$(
  [[ "${DEFAULT_LLM_MODEL:-gpt-5.4-mini}" == *claude* ]] && echo false || echo true
)"
check "DEFAULT_LLM_MODEL" "${DEFAULT_LLM_MODEL:-}"
check "LANGFUSE_PUBLIC_KEY (observabilidade)" "${LANGFUSE_PUBLIC_KEY:-}" false
check "LANGFUSE_SECRET_KEY (observabilidade)" "${LANGFUSE_SECRET_KEY:-}" false

echo ""
echo "Resumo: ${ok} ok | ${warn} opcionais | ${fail} faltando"
echo ""

if [[ "$fail" -gt 0 ]]; then
  echo -e "${RED}Corrija os itens acima antes de executar o pipeline.${NC}"
  echo ""
  echo "Guia rápido:"
  echo "  1. OpenAI:  https://platform.openai.com/api-keys"
  echo "  2. Langfuse: suba o stack, acesse http://localhost:3000, crie projeto → Settings → API Keys"
  exit 1
fi

if [[ "${LANGFUSE_PUBLIC_KEY:-}" == *"..."* || -z "${LANGFUSE_PUBLIC_KEY:-}" ]]; then
  echo -e "${YELLOW}Dica: Langfuse não configurado — observabilidade ficará desabilitada.${NC}"
fi

echo -e "${GREEN}.env pronto para teste!${NC}"
