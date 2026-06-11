"""
Estado tipado do LangGraph para a pipeline de modernização SQL → Python.
"""

from __future__ import annotations

from typing import Any, TypedDict


class PipelineState(TypedDict, total=False):
    # ── Entrada ───────────────────────────────────────────────────────────────
    source_code: str
    schema_context: str | None
    llm_model: str
    request_id: str

    # ── Observabilidade (Langfuse) ────────────────────────────────────────────
    trace_id: str | None
    langfuse_callbacks: list[Any]

    # ── Nó 1: Parsing ─────────────────────────────────────────────────────────
    parsed_procedure: dict[str, Any] | None
    parsing_report: dict[str, Any]
    parsing_errors: list[str]

    # ── Nó 2: Análise semântica ───────────────────────────────────────────────
    semantic_analysis: dict[str, Any] | None
    analysis_report: dict[str, Any]

    # ── Nó 3: Geração ─────────────────────────────────────────────────────────
    generated_code: str | None
    generation_report: dict[str, Any]

    # ── Nó 4: Validação ───────────────────────────────────────────────────────
    validation_passed: bool
    validation_report: dict[str, Any]

    # ── Status geral ──────────────────────────────────────────────────────────
    status: str   # "running" | "success" | "partial" | "failure"
    error: str | None
