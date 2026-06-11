"""Wrapper que instrumenta cada nó do grafo com spans Langfuse."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pipeline.graph.state import PipelineState
from pipeline.observability.tracing import get_trace_manager

NodeFn = Callable[[PipelineState], Awaitable[dict[str, Any]]]


def trace_node(node_name: str, node_fn: NodeFn) -> NodeFn:
    """Envolve um nó do LangGraph com span Langfuse (no-op se desabilitado)."""

    async def wrapped(state: PipelineState) -> dict[str, Any]:
        tracer = get_trace_manager()
        trace_id = state.get("trace_id")
        span = tracer.span_by_id(
            trace_id,
            name=node_name,
            input_data={
                "request_id": state.get("request_id"),
                "status": state.get("status"),
            },
        )
        try:
            result = await node_fn(state)
            tracer.end_span(
                span,
                output={
                    "status": result.get("status", state.get("status")),
                    "keys_updated": list(result.keys()),
                },
            )
            return result
        except Exception as exc:
            tracer.end_span(span, output={"error": str(exc)}, error=str(exc))
            raise

    wrapped.__name__ = getattr(node_fn, "__name__", node_name)
    return wrapped
