"""
Grafo LangGraph da pipeline de modernização SQL → Python.

Diagrama:
    START
      │
      ▼
  [parse_procedure]
      │
  ┌───┴── (status=failure) ──► [persist_result] ──► END
      │
      ▼
  [analyze_semantics]
      │
  ┌───┴── (sem análise) ──► [persist_result] ──► END
      │
      ▼
  [generate_python]
      │
  ┌───┴── (status=failure) ──► [persist_result] ──► END
      │
      ▼
  [validate_output]
      │
      ▼
  [persist_result]
      │
      ▼
     END
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, START, StateGraph

logger = logging.getLogger(__name__)

from pipeline.graph.nodes.analysis import analyze_semantics_node
from pipeline.graph.nodes.generation import generate_python_node
from pipeline.graph.nodes.parsing import parse_procedure_node
from pipeline.graph.nodes.validation import validate_output_node
from pipeline.graph.state import PipelineState
from pipeline.observability.node_wrapper import trace_node

# ─────────────────────────── Funções de roteamento ───────────────────────────

def _route_after_parse(state: PipelineState) -> str:
    if state.get("status") == "failure":
        return "persist"
    return "analyze"


def _route_after_analyze(state: PipelineState) -> str:
    if not state.get("semantic_analysis"):
        return "persist"
    return "generate"


def _route_after_generate(state: PipelineState) -> str:
    if state.get("status") == "failure" or not state.get("generated_code"):
        return "persist"
    return "validate"


# ─────────────────────────── Nó de persistência ──────────────────────────────

def _make_persist_node(repository: Any | None):
    """Fábrica: cria o nó persist_result com acesso ao repositório."""

    async def persist_result_node(state: PipelineState) -> dict:
        """Persiste o resultado no banco modernization_history."""
        if repository is None:
            return {}
        try:
            await repository.save(
                request_id=state.get("request_id", ""),
                source_code=state.get("source_code", ""),
                generated_code=state.get("generated_code"),
                report={
                    "parsing": state.get("parsing_report", {}),
                    "analysis": state.get("analysis_report", {}),
                    "generation": state.get("generation_report", {}),
                    "validation": state.get("validation_report", {}),
                },
                status=state.get("status", "failure"),
            )
        except Exception as exc:
            logger.error("Falha ao persistir resultado no banco: %s", exc)
        return {}

    return persist_result_node


# ─────────────────────────── Builder principal ───────────────────────────────

def build_graph(repository: Any | None = None):
    """Constrói e compila o grafo LangGraph."""

    persist_node = _make_persist_node(repository)

    graph = StateGraph(PipelineState)

    # Nós (cada um instrumentado com span Langfuse quando trace_id estiver no estado)
    graph.add_node("parse", trace_node("parse_procedure", parse_procedure_node))
    graph.add_node("analyze", trace_node("analyze_semantics", analyze_semantics_node))
    graph.add_node("generate", trace_node("generate_python", generate_python_node))
    graph.add_node("validate", trace_node("validate_output", validate_output_node))
    graph.add_node("persist", trace_node("persist_result", persist_node))

    # Arestas
    graph.add_edge(START, "parse")

    graph.add_conditional_edges(
        "parse",
        _route_after_parse,
        {"analyze": "analyze", "persist": "persist"},
    )
    graph.add_conditional_edges(
        "analyze",
        _route_after_analyze,
        {"generate": "generate", "persist": "persist"},
    )
    graph.add_conditional_edges(
        "generate",
        _route_after_generate,
        {"validate": "validate", "persist": "persist"},
    )
    graph.add_edge("validate", "persist")
    graph.add_edge("persist", END)

    return graph.compile()


def build_graph_for_cli():
    """Entry point para `langgraph dev` (sem repositório)."""
    return build_graph(repository=None)
