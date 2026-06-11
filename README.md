# SQL Modernizer — Pipeline Híbrido PL/pgSQL → Python 3.14

Pipeline híbrido (LLM + regras determinísticas) para modernizar stored procedures
PL/pgSQL para código Python 3.14 idiomático, orquestrado com **LangGraph** e exposto
via **LangGraph CLI** (`langgraph dev`), com rotas customizadas FastAPI (`/modernize`, `/health`).

---

## Diagrama do Grafo LangGraph

```
START
  │
  ▼
[parse_procedure]        ← Nó 1: PL/pgSQL → estrutura intermediária (sqlglot + regex)
  │
  ├──(status=failure)──► [persist_result] ──► END
  │
  ▼
[analyze_semantics]      ← Nó 2: identifica construções, riscos e hints de tradução
  │
  ├──(sem análise)──────► [persist_result] ──► END
  │
  ▼
[generate_python]        ← Nó 3: LLM constrói código Python com contexto rico dos nós anteriores
  │
  ├──(status=failure)──► [persist_result] ──► END
  │
  ▼
[validate_output]        ← Nó 4: ast.parse + ruff + métricas de qualidade
  │
  ▼
[persist_result]         ← Sempre executado: salva em modernization_history (PostgreSQL)
  │
  ▼
 END
```

---

## Execução rápida

```bash
cp .env.example .env             # configurar OPENAI_API_KEY e credenciais Langfuse
docker compose up --build -d     # subir stack (app + postgres + langfuse)
./scripts/run_all_procedures.sh  # modernizar Anexos B a F
```

---

## Execução Local

### Pré-requisitos
- Docker + Docker Compose
- Chave de API OpenAI

### 1. Configurar variáveis de ambiente

```bash
cp .env.example .env
# Preencher OPENAI_API_KEY; Langfuse é opcional para observabilidade
```

### 2. Subir os serviços

```bash
docker compose up --build
```

Serviços iniciados:
| Serviço | URL / Porta |
|---------|-------------|
| **LangGraph CLI** (servidor principal) | http://localhost:8000 |
| Swagger UI (`/docs`) | http://localhost:8000/docs |
| LangGraph Studio | http://localhost:8000 (grafo `sql_modernizer`) |
| Langfuse (UI) | http://localhost:3000 |
| PostgreSQL — pipeline | `localhost:5432` (db: `pipeline`, user: `pipeline`) |
| PostgreSQL — Langfuse | `localhost:5433` (db: `langfuse`, user: `langfuse`) |

O container sobe com `langgraph dev` — o `langgraph.json` registra o grafo **e** a app FastAPI
via `http.app`, conforme exigido pelo desafio.

### 3. Testar

```bash
# Health check
curl http://localhost:8000/health

# Modernizar uma procedure (Anexo B — mais simples)
curl -X POST http://localhost:8000/modernize \
  -H "Content-Type: application/json" \
  -d '{
    "source_code": "'"$(cat sql/procedures/B_fn_saldo_cliente.sql)"'",
    "schema_context": "'"$(cat sql/00_schema_legado.sql)"'",
    "model": "gpt-5.4-mini"
  }'

# Métricas de avaliação (após rodar algumas procedures)
curl http://localhost:8000/metrics
```

### 4. Observabilidade — Langfuse

Após rodar `/modernize`, abra http://localhost:3000 e localize o trace `sql_modernization_pipeline`
com spans por nó (`parse_procedure`, `analyze_semantics`, `generate_python`, `validate_output`,
`persist_result`) e o score `quality_score`.

![Trace sql_modernization_pipeline com 5 spans e quality_score 1.0](docs/langfuse-tracing.png)

---

## Executar Localmente Sem Docker

```bash
# 1. Instalar dependências (Python 3.14+)
pip install uv
uv pip install -e .

# 2. Banco de dados (requer PostgreSQL rodando)
export DATABASE_URL="postgresql+asyncpg://pipeline:pipeline@localhost:5432/pipeline"

# 3. Iniciar servidor via LangGraph CLI (recomendado)
langgraph dev --host 0.0.0.0 --port 8000 --no-browser

# Alternativa: apenas FastAPI (desenvolvimento rápido)
# PYTHONPATH=src uvicorn pipeline.api.server:app --reload

# 4. Testes unitários
PYTHONPATH=src pytest tests/ -v --cov=pipeline --cov-report=term-missing

# 5. Testes comportamentais (exige PostgreSQL com schema legado)
TEST_BEHAVIORAL=1 pytest tests/test_behavioral.py -m integration -v
```

---

## Endpoints

### `POST /modernize`

Executa o pipeline completo sobre uma stored procedure.

**Request:**
```json
{
  "source_code": "CREATE OR REPLACE FUNCTION ...",
  "schema_context": "CREATE TABLE clientes ...",
  "model": "gpt-5.4-mini"
}
```

**Response:**
```json
{
  "id": "uuid",
  "status": "success | partial | failure",
  "generated_code": "import asyncpg\n\nasync def fn_saldo_cliente(...) ...",
  "report": {
    "parsing":    { "status": "success", "duration_ms": 3.2, "name": "fn_saldo_cliente", ... },
    "analysis":   { "status": "success", "complexity": "low", "constructs": [...], ... },
    "generation": { "status": "success", "model": "gpt-5.4-mini", "lines_generated": 18, ... },
    "validation": { "status": "success", "ast_valid": true, "quality_score": 0.9, ... }
  },
  "created_at": "2026-06-10T14:00:00Z"
}
```

### `GET /health`

```json
{ "status": "ok", "version": "0.1.0", "llm_model": "gpt-5.4-mini", "db_connected": true }
```

### `GET /metrics`

Calcula métricas de avaliação sobre todas as execuções persistidas.

```json
{
  "total_executions": 5,
  "success_rate": 0.8,
  "partial_rate": 0.2,
  "failure_rate": 0.0,
  "ast_valid_rate": 1.0,
  "avg_quality_score": 0.87,
  "avg_duration_ms": 4200.0,
  "procedures_evaluated": [...]
}
```

---

## Decisões Técnicas e Trade-offs

### 1. Parser customizado (regex + sqlglot) em vez de parser PL/pgSQL puro

**Decisão:** Combinação de regex para a estrutura procedural (DECLARE, BEGIN, EXCEPTION) e
`sqlglot` para as instruções SQL embutidas no corpo.

**Por quê:** Não existe biblioteca madura que faça parsing completo de PL/pgSQL *procedural*
em Python com extração de metadados ricos (cursores, variáveis, parâmetros OUT). `pglast`
e `sqlglot` são ótimos para SQL puro, mas não para blocos procedurais.

**Trade-off:** O parser customizado cobre os padrões dos Anexos B–F mas não é exaustivo
para toda a especificação PL/pgSQL. Com mais tempo, integraria com `libpg_query` via
`pglast` para uma AST mais fiel.

### 2. Prompt baseado em metadados estruturados, nunca SQL bruto

**Decisão:** O nó de geração recebe como prompt o resultado *processado* dos nós de parsing
e análise: parâmetros tipados, variáveis, pontos de risco com estratégias, hints de tradução.
O SQL original é incluído como referência, não como insumo principal.

**Por quê:** O LLM tende a traduzir literalmente quando recebe apenas o SQL. Com contexto
estruturado ele faz melhores escolhas — e.g., usar bulk fetch para cursores, manter CTEs
recursivas em SQL em vez de reescrever em Python.

**Trade-off:** Prompts maiores = mais tokens/custo. O ganho em qualidade da tradução compensa.

### 3. asyncpg no código gerado (não SQLAlchemy ORM)

**Decisão:** O prompt instrui o LLM a usar `asyncpg` diretamente.

**Por quê:** Para código de migração próximo ao SQL original, `asyncpg` é mais transparente,
mais rápido e mais fácil de depurar. SQLAlchemy ORM adicionaria uma camada de abstração que
obscurece a semântica transacional.

**Trade-off:** Mais verboso para operações simples. Para uma aplicação de domínio completo,
SQLAlchemy seria melhor. Para código de migração, asyncpg é mais adequado.

### 4. Cursores → bulk fetch obrigatório

**Decisão:** O analisador detecta cursores e a regra "use bulk fetch" é injetada no prompt
com severidade alta.

**Por quê:** Um cursor PL/pgSQL traduzido ingenuamente gera N+1 queries — impacto crítico
em produção. A tradução correta é `await conn.fetch(sql)` + loop Python.

### 5. CTEs recursivas mantidas em SQL

**Decisão:** Para CTEs recursivas (Anexo F), o prompt instrui o LLM a manter o SQL via
`asyncpg` e não reescrever em Python.

**Por quê:** Reescrever uma CTE recursiva em Python resulta em código mais lento e difícil
de manter. O PostgreSQL executa a CTE com otimizações que Python não consegue replicar.

### 6. LangGraph para orquestração

**Decisão:** Cada etapa é um nó independente com estado tipado (`TypedDict`).

**Por quê:** LangGraph permite: roteamento condicional (pular etapas em falha), estado imutável
entre nós, reprodutibilidade e observabilidade fácil. Alternativas como Celery ou asyncio puro
seriam mais difíceis de instrumentar e manter.

### 7. Persistência independente do desfecho

**Decisão:** O nó `persist_result` é sempre o último nó, inclusive em falhas.

**Por quê:** O desafio exige que *toda* execução seja persistida. Com o grafo LangGraph,
os caminhos de erro também terminam em `persist_result`.

---

### Observabilidade com Langfuse

- Um **trace** por execução do `/modernize`
- Um **span** por nó do grafo
- **Score `quality_score`** registrado ao final (0.0–1.0)
- Callbacks LangChain integrados para rastrear custo e latência do LLM
- Langfuse self-hosted via Docker Compose (porta 3000)

**Para ativar:** configure `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` e `LANGFUSE_ENABLED=true`.

### Checks de qualidade estática + Pytest

- `ruff check` automático em todo código gerado
- Cobertura de testes com `pytest-cov`
- Testes para parsing, análise semântica, validação e nós do grafo
- Testes estruturais dos Anexos B–F (`tests/test_annexes_b_to_f.py`)
- Testes comportamentais B–F: SQL vs Python de referência (`tests/test_behavioral.py`, `@integration`)

### Métrica de avaliação automática

Endpoint `GET /metrics` expõe:

| Métrica | O que captura | Limitação |
|---------|---------------|-----------|
| `ast_valid_rate` | % de código que passa em `ast.parse` | Não garante corretude semântica |
| `quality_score` | Combinação de ast + ruff + type hints + docstrings | Não testa comportamento |
| `success_rate` | % de execuções sem falha de pipeline | Pipeline pode ter "sucesso" com código ruim |

**Como evoluir em produção:** implementar testes comportamentais (input → output) executando
o código gerado contra uma instância PostgreSQL de teste com dados fixtures dos Anexos A–F.

---

## Limitações Conhecidas

1. **Parser não cobre 100% da especificação PL/pgSQL** — construções exóticas (COPY, DDL dentro
   de procedures, nested BEGIN/END) podem não ser extraídas.

2. **Geração depende do LLM** — variabilidade entre execuções é esperada. O nó de validação
   captura erros de sintaxe, mas não erros lógicos.

3. **Testes comportamentais com PostgreSQL** — `tests/test_behavioral.py` compara procedures
   SQL e implementações Python de referência para os Anexos B–F (`TEST_BEHAVIORAL=1`).
   Não valida o código gerado pelo LLM em cada execução, apenas a semântica de referência.

4. **LangGraph `persist_result`** — em alta concorrência, a sessão SQLAlchemy pode precisar
   de pool sizing mais agressivo.

---

## O Que Faria Com Mais Tempo

1. **Validação do código gerado pelo LLM** — executar automaticamente o Python produzido
   pelo pipeline (em `results/`) contra o mesmo banco e comparar com as procedures SQL.
2. **Cache de análise** — guardar parse/analysis em Redis para procedures idênticas.
3. **Múltiplos dialetos** — estender o parser para T-SQL e PL/SQL.
4. **Fine-tuning de prompt** — criar few-shot examples para cada padrão identificado.
5. **CI/CD** — GitHub Actions com `ruff`, `mypy`, `pytest` em cada PR.
6. **Retry com backoff** — Tenacity no nó de geração para falhas de API do LLM.
7. **Avaliação LLM-as-judge** — segundo LLM avalia a qualidade da tradução com critérios
   objetivos registrados no Langfuse como scores adicionais.
