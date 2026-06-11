"""
Nó 2 — Análise Semântica: identifica construções relevantes e pontos de risco.

Entrada: ParsedProcedure (dict)
Saída:  SemanticAnalysis com:
  - construções identificadas (cursor, transação, JSONB, CTE recursivo, etc.)
  - pontos de risco com severidade e estratégia de tradução
  - dicas de tradução para a etapa de geração
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from pipeline.graph.state import PipelineState

# ─────────────────────────── Modelos ─────────────────────────────────────────

@dataclass
class RiskPoint:
    type: str
    description: str
    severity: Literal["low", "medium", "high"]
    recommendation: str
    translation_hint: str


@dataclass
class TranslationHint:
    """Dica estruturada para o nó de geração."""
    topic: str
    pattern: str        # Padrão Python recomendado (texto descritivo)
    example: str = ""   # Snippet de código de exemplo


@dataclass
class SemanticAnalysis:
    constructs: list[str]
    risks: list[RiskPoint]
    translation_hints: list[TranslationHint]
    complexity: Literal["low", "medium", "high", "very_high"]
    return_pattern: Literal["scalar", "void", "out_params", "table", "setof"]
    uses_cursor: bool = False
    uses_transactions: bool = False
    uses_jsonb: bool = False
    uses_recursive_cte: bool = False
    uses_cte: bool = False
    uses_for_update: bool = False
    uses_raise: bool = False
    uses_get_diagnostics: bool = False
    dependencies: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ─────────────────────────── Analisador ──────────────────────────────────────

class SemanticAnalyzer:

    def analyze(
        self,
        parsed: dict[str, Any],
        schema_context: str | None = None,
    ) -> SemanticAnalysis:
        raw_source: str = parsed.get("raw_source", "")
        full_text = raw_source.upper()

        constructs: list[str] = []
        risks: list[RiskPoint] = []
        hints: list[TranslationHint] = []

        # ── Cursores ─────────────────────────────────────────────────────────
        uses_cursor = bool(parsed.get("cursors")) or "CURSOR" in full_text
        if uses_cursor:
            constructs.append("explicit_cursor")
            risks.append(RiskPoint(
                type="cursor_n_plus_one",
                description="Cursor explícito com FETCH row-a-row gera N+1 queries.",
                severity="high",
                recommendation="Substituir por bulk fetch (fetchall) e iterar em Python.",
                translation_hint=(
                    "Use `rows = await conn.fetch(query, *params)` e itere sobre a lista. "
                    "Evite OPEN/FETCH/CLOSE — prefira um único SELECT."
                ),
            ))
            hints.append(TranslationHint(
                topic="cursor_to_bulk_fetch",
                pattern="rows = await conn.fetch(sql, *params)\nfor row in rows: ...",
                example=(
                    "rows = await conn.fetch(\n"
                    "    'SELECT id, conta_origem_id, tipo, valor FROM transacoes WHERE ...'\n"
                    ")\nfor row in rows:\n    process(row)"
                ),
            ))

        # ── FOR UPDATE ────────────────────────────────────────────────────────
        uses_for_update = "FOR UPDATE" in full_text
        if uses_for_update:
            constructs.append("for_update_lock")
            risks.append(RiskPoint(
                type="row_locking",
                description="FOR UPDATE requer que a query esteja dentro de uma transação ativa.",
                severity="medium",
                recommendation="Usar `async with conn.transaction()` ou `session.begin()` e emitir a query com FOR UPDATE.",
                translation_hint="Wrap em `async with conn.transaction(): row = await conn.fetchrow('... FOR UPDATE', id)`",
            ))
            hints.append(TranslationHint(
                topic="for_update",
                pattern="async with conn.transaction():\n    row = await conn.fetchrow('SELECT ... FOR UPDATE', id)",
            ))

        # ── RAISE EXCEPTION ───────────────────────────────────────────────────
        uses_raise = bool(parsed.get("raise_statements")) or "RAISE" in full_text
        if uses_raise:
            constructs.append("raise_exception")
            hints.append(TranslationHint(
                topic="raise_to_python_exception",
                pattern="raise ValueError(f'mensagem {var}')\n# ou classe customizada: raise BusinessError(...)",
                example="raise ValueError(f'Saldo insuficiente: saldo={saldo} valor={valor}')",
            ))

        # ── EXCEPTION WHEN OTHERS ─────────────────────────────────────────────
        has_exc = parsed.get("has_exception_block", False)
        if has_exc:
            constructs.append("exception_handler")
            hints.append(TranslationHint(
                topic="exception_handler",
                pattern=(
                    "try:\n    ...\nexcept Exception as exc:\n"
                    "    await _log_error(conn, ...)\n    raise"
                ),
            ))

        # ── GET DIAGNOSTICS ───────────────────────────────────────────────────
        uses_get_diag = "GET DIAGNOSTICS" in full_text
        if uses_get_diag:
            constructs.append("get_diagnostics")
            hints.append(TranslationHint(
                topic="get_diagnostics_rowcount",
                pattern="# asyncpg: result = await conn.execute(sql, *params)\n# rowcount = int(result.split()[-1])",
            ))

        # ── JSONB ─────────────────────────────────────────────────────────────
        uses_jsonb = "JSONB" in full_text or "JSONB_BUILD_OBJECT" in full_text
        if uses_jsonb:
            constructs.append("jsonb_operations")
            hints.append(TranslationHint(
                topic="jsonb_as_dict",
                pattern="# asyncpg serializa/deserializa JSONB automaticamente como dict Python",
                example="await conn.execute('INSERT INTO log_auditoria (detalhes) VALUES ($1)', {'key': 'value'})",
            ))

        # ── CTE ───────────────────────────────────────────────────────────────
        uses_cte = bool(re.search(r"\bWITH\b.*?\bSELECT\b", raw_source, re.IGNORECASE | re.DOTALL))
        uses_recursive_cte = bool(re.search(r"\bWITH\s+RECURSIVE\b", raw_source, re.IGNORECASE))
        if uses_recursive_cte:
            constructs.append("recursive_cte")
            risks.append(RiskPoint(
                type="recursive_cte",
                description="CTE recursiva é difícil de reescrever em Python puro.",
                severity="high",
                recommendation="Manter a CTE recursiva em SQL bruto via SQLAlchemy `text()` ou asyncpg.",
                translation_hint="Use `text('WITH RECURSIVE meses AS (...) SELECT ...')` — não tente reescrever em Python.",
            ))
        elif uses_cte:
            constructs.append("cte")

        # ── RETURN QUERY / SETOF ──────────────────────────────────────────────
        returns_set = parsed.get("returns_set", False)
        if returns_set:
            constructs.append("setof_return")
            hints.append(TranslationHint(
                topic="setof_as_list",
                pattern="async def fn(...) -> list[RowType]:\n    rows = await conn.fetch(sql)\n    return [dict(r) for r in rows]",
            ))

        # ── OUT params ────────────────────────────────────────────────────────
        out_params = [p for p in parsed.get("parameters", []) if p.get("mode") in ("OUT", "INOUT")]
        if out_params:
            constructs.append("out_parameters")
            names = ", ".join(p["name"] for p in out_params)
            hints.append(TranslationHint(
                topic="out_params_as_namedtuple",
                pattern=f"from typing import NamedTuple\nclass Result(NamedTuple):\n    {names}: int\nreturn Result({names}=...)",
            ))

        # ── Transações ────────────────────────────────────────────────────────
        uses_transactions = parsed.get("has_transactions", False) or uses_for_update
        if uses_transactions:
            constructs.append("explicit_transaction")
            hints.append(TranslationHint(
                topic="transaction_context_manager",
                pattern="async with conn.transaction():\n    # operações atômicas",
            ))

        # ── Dependências ─────────────────────────────────────────────────────
        dependencies = parsed.get("called_functions", [])
        if dependencies:
            constructs.append("cross_function_calls")
            hints.append(TranslationHint(
                topic="function_dependencies",
                pattern="# Importar e chamar diretamente: result = await fn_saldo_cliente(conn, cliente_id)",
            ))

        # ── Complexidade ──────────────────────────────────────────────────────
        complexity = self._estimate_complexity(parsed, constructs)

        # ── Padrão de retorno ─────────────────────────────────────────────────
        return_pattern = self._classify_return(parsed)

        return SemanticAnalysis(
            constructs=constructs,
            risks=risks,
            translation_hints=hints,
            complexity=complexity,
            return_pattern=return_pattern,
            uses_cursor=uses_cursor,
            uses_transactions=uses_transactions,
            uses_jsonb=uses_jsonb,
            uses_recursive_cte=uses_recursive_cte,
            uses_cte=uses_cte,
            uses_for_update=uses_for_update,
            uses_raise=uses_raise,
            uses_get_diagnostics=uses_get_diag,
            dependencies=dependencies,
        )

    def _estimate_complexity(
        self, parsed: dict[str, Any], constructs: list[str]
    ) -> Literal["low", "medium", "high", "very_high"]:
        score = 0
        score += len(parsed.get("parameters", []))
        score += len(parsed.get("cursors", [])) * 3
        score += len(parsed.get("declare_vars", [])) * 0.5
        score += 3 if parsed.get("has_exception_block") else 0
        score += 2 if "recursive_cte" in constructs else 0
        score += 2 if "for_update_lock" in constructs else 0
        score += 1 if "jsonb_operations" in constructs else 0
        score += 1 if "setof_return" in constructs else 0
        score += 2 if "cross_function_calls" in constructs else 0

        if score < 3:
            return "low"
        if score < 6:
            return "medium"
        if score < 12:
            return "high"
        return "very_high"

    def _classify_return(
        self, parsed: dict[str, Any]
    ) -> Literal["scalar", "void", "out_params", "table", "setof"]:
        rt = (parsed.get("return_type") or "").upper()
        out_params = [p for p in parsed.get("parameters", []) if p.get("mode") in ("OUT", "INOUT")]

        if parsed.get("returns_set") or rt == "TABLE":
            return "setof"
        if out_params:
            return "out_params"
        if rt in ("VOID", ""):
            return "void"
        if rt == "TABLE":
            return "table"
        return "scalar"


# ─────────────────────────── Nó do LangGraph ─────────────────────────────────

async def analyze_semantics_node(state: PipelineState) -> dict[str, Any]:
    """Nó 2 — Análise semântica."""
    if state.get("status") == "failure":
        return {}

    start = time.perf_counter()
    analyzer = SemanticAnalyzer()
    try:
        analysis = analyzer.analyze(
            state["parsed_procedure"],  # type: ignore[arg-type]
            state.get("schema_context"),
        )
        elapsed = (time.perf_counter() - start) * 1000
        return {
            "semantic_analysis": analysis.to_dict(),
            "analysis_report": {
                "status": "success",
                "duration_ms": round(elapsed, 2),
                "constructs": analysis.constructs,
                "complexity": analysis.complexity,
                "return_pattern": analysis.return_pattern,
                "risks_count": len(analysis.risks),
                "high_risks": [r.type for r in analysis.risks if r.severity == "high"],
            },
        }
    except Exception as exc:
        elapsed = (time.perf_counter() - start) * 1000
        return {
            "semantic_analysis": None,
            "analysis_report": {
                "status": "failure",
                "duration_ms": round(elapsed, 2),
                "error": str(exc),
            },
        }
