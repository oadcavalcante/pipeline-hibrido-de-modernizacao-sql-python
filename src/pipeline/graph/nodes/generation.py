"""
Nó 3 — Geração: produz código Python 3.14 equivalente via LLM.

Decisão arquitetural:
- O prompt é SEMPRE construído a partir das saídas dos nós anteriores
  (parsing + análise semântica), não da procedure bruta.
- Para queries ANINHADAS (CTEs recursivas), o SQL é mantido como texto
  e executado via asyncpg/SQLAlchemy — não reescrito em Python.
- Para cursores, o LLM é instruído a usar bulk fetch.
- O modelo, temperatura e max_tokens são configuráveis por requisição.
"""

from __future__ import annotations

import re
import time
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from pipeline.config import settings
from pipeline.graph.state import PipelineState

# ─────────────────────────── Prompt builder ──────────────────────────────────

SYSTEM_PROMPT = """\
Você é um especialista em migração de código de banco de dados para Python.
Sua tarefa é traduzir stored procedures PL/pgSQL para código Python 3.14 idiomático.

## Regras obrigatórias
1. Use `asyncpg` diretamente para acesso ao banco (sem ORM).
2. Use `async/await` em todas as operações de banco.
3. Type hints completos em estilo Python 3.14 (ex: `int | None`, `list[dict]`).
4. Docstrings em todas as funções públicas.
5. Inclua todos os imports necessários no topo do arquivo.
6. Para cursores PL/pgSQL: USE APENAS bulk fetch (`await conn.fetch(sql)`). NUNCA simule row-a-row.
7. Para CTEs recursivas: MANTENHA o SQL original via `await conn.fetch(text_sql)`. NÃO reescreva em Python.
8. Para transações explícitas: use `async with conn.transaction(): ...`.
9. Para parâmetros OUT: retorne `NamedTuple` ou `dataclass`.
10. Para RAISE EXCEPTION: use `raise ValueError(...)` ou exceção customizada.
11. Para GET DIAGNOSTICS ROW_COUNT: use o valor retornado por `conn.execute()`.
12. Para JSONB: use `dict` Python — asyncpg serializa automaticamente.
13. NUNCA use estado global. NUNCA use `time.sleep`. NUNCA use `subprocess`.
14. Gere APENAS o código Python. Nenhuma explicação fora do código.
"""


def _format_parameters(params: list[dict[str, Any]]) -> str:
    if not params:
        return "nenhum"
    lines = []
    for p in params:
        default_str = f" = {p['default']}" if p.get("default") else ""
        lines.append(f"  - {p['name']}: {p['type']} ({p['mode']}){default_str}")
    return "\n".join(lines)


def _format_risks(risks: list[dict[str, Any]]) -> str:
    if not risks:
        return "nenhum risco identificado"
    lines = []
    for r in risks:
        lines.append(
            f"  [{r['severity'].upper()}] {r['type']}\n"
            f"    Problema: {r['description']}\n"
            f"    Estratégia: {r['translation_hint']}"
        )
    return "\n".join(lines)


def _format_hints(hints: list[dict[str, Any]]) -> str:
    if not hints:
        return ""
    parts = []
    for h in hints:
        parts.append(f"### {h['topic']}\nPadrão: `{h['pattern']}`")
        if h.get("example"):
            parts.append(f"Exemplo:\n```python\n{h['example']}\n```")
    return "\n".join(parts)


def _format_variables(vars_: list[dict[str, Any]]) -> str:
    if not vars_:
        return "nenhuma"
    return "\n".join(f"  - {v['name']}: {v['type']}" + (f" = {v['default']}" if v.get("default") else "") for v in vars_)


def _format_cursors(cursors: list[dict[str, Any]]) -> str:
    if not cursors:
        return "nenhum"
    lines = []
    for c in cursors:
        query = c.get("query", "")
        preview = f"{query[:120]}..." if len(query) > 120 else query
        lines.append(f"  - {c['name']}: {preview}")
    return "\n".join(lines)


def build_generation_prompt(
    parsed: dict[str, Any],
    analysis: dict[str, Any],
    schema_context: str | None,
) -> tuple[str, str]:
    """Retorna (system_prompt, human_message)."""

    return_info = parsed.get("return_type") or "void"
    if parsed.get("returns_set"):
        return_info = f"SETOF {return_info}" if return_info != "TABLE" else "TABLE(...)"

    human = f"""\
Traduza o seguinte {parsed['kind']} PL/pgSQL para Python 3.14.

## Metadados da procedure
- **Nome**: `{parsed['name']}`
- **Tipo**: {parsed['kind']}
- **Retorno**: {return_info}
- **Padrão de retorno identificado**: {analysis.get('return_pattern', 'unknown')}
- **Complexidade**: {analysis.get('complexity', 'unknown')}

## Parâmetros
{_format_parameters(parsed.get('parameters', []))}

## Variáveis declaradas
{_format_variables(parsed.get('declare_vars', []))}

## Cursores
{_format_cursors(parsed.get('cursors', []))}

## Construções identificadas
{', '.join(analysis.get('constructs', [])) or 'nenhuma'}

## Pontos de risco e estratégias de tradução
{_format_risks(analysis.get('risks', []))}

## Dicas de tradução (SIGA OBRIGATORIAMENTE)
{_format_hints(analysis.get('translation_hints', []))}

## Schema do banco legado (contexto)
```sql
{schema_context or '-- Schema não fornecido. Use nomes de tabela/coluna do código original.'}
```

## Código-fonte original
```sql
{parsed.get('raw_source', '')}
```

Gere um módulo Python 3.14 completo e funcional. \
Inclua imports, type aliases se necessário, funções auxiliares e a função principal.
"""
    return SYSTEM_PROMPT, human


# ─────────────────────────── Extrator de código ───────────────────────────────

def _extract_python_code(llm_response: str) -> str:
    """Extrai bloco ```python ... ``` ou retorna o texto limpo."""
    fence_m = re.search(r"```python\s*(.*?)```", llm_response, re.DOTALL)
    if fence_m:
        return fence_m.group(1).strip()
    # Fallback: remove qualquer bloco de código genérico
    generic_m = re.search(r"```\s*(.*?)```", llm_response, re.DOTALL)
    if generic_m:
        return generic_m.group(1).strip()
    return llm_response.strip()


# ─────────────────────────── Nó do LangGraph ─────────────────────────────────

async def generate_python_node(state: PipelineState) -> dict[str, Any]:
    """Nó 3 — Geração de código Python via LLM."""
    if state.get("status") == "failure":
        return {}

    parsed = state.get("parsed_procedure")
    analysis = state.get("semantic_analysis")
    if not parsed or not analysis:
        return {
            "generated_code": None,
            "generation_report": {"status": "skipped", "reason": "parsing ou analysis ausentes"},
            "status": "failure",
        }

    start = time.perf_counter()
    model_name = state.get("llm_model") or settings.default_llm_model

    try:
        llm = _build_llm(model_name)
        system_prompt, human_prompt = build_generation_prompt(
            parsed, analysis, state.get("schema_context")
        )

        # Callbacks Langfuse para rastrear custo/latência do LLM
        callbacks = state.get("langfuse_callbacks") or []

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=human_prompt),
        ]

        response = await llm.ainvoke(messages, config={"callbacks": callbacks} if callbacks else {})
        raw_text = response.content if hasattr(response, "content") else str(response)
        generated_code = _extract_python_code(raw_text)

        elapsed = (time.perf_counter() - start) * 1000

        # Tokens (disponíveis apenas para alguns providers)
        usage = getattr(response, "usage_metadata", None) or {}
        input_tokens = usage.get("input_tokens", 0) if isinstance(usage, dict) else getattr(usage, "input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0) if isinstance(usage, dict) else getattr(usage, "output_tokens", 0)

        return {
            "generated_code": generated_code,
            "generation_report": {
                "status": "success",
                "duration_ms": round(elapsed, 2),
                "model": model_name,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "lines_generated": len(generated_code.splitlines()),
            },
        }
    except Exception as exc:
        elapsed = (time.perf_counter() - start) * 1000
        return {
            "generated_code": None,
            "generation_report": {
                "status": "failure",
                "duration_ms": round(elapsed, 2),
                "error": str(exc),
                "model": model_name,
            },
            "status": "failure",
            "error": f"Geração falhou: {exc}",
        }


def _build_llm(model_name: str):
    """Instancia o LLM correto com base no nome do modelo."""
    model_lower = model_name.lower()
    if "claude" in model_lower or "anthropic" in model_lower:
        from langchain_anthropic import ChatAnthropic  # type: ignore[import]
        key = settings.anthropic_api_key_value()
        return ChatAnthropic(
            model=model_name,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
            anthropic_api_key=key or None,
        )
    else:
        from langchain_openai import ChatOpenAI  # type: ignore[import]
        key = settings.openai_api_key_value()
        return ChatOpenAI(
            model=model_name,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
            api_key=key or None,
        )
