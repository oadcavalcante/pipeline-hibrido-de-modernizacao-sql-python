"""Testes da instrumentação Langfuse (modo no-op sem credenciais)."""

from __future__ import annotations

import pytest

from pipeline.graph.nodes.parsing import parse_procedure_node
from pipeline.observability.node_wrapper import trace_node
from pipeline.observability.tracing import TraceManager


@pytest.fixture
def proc_b() -> str:
    return (
        "CREATE OR REPLACE FUNCTION fn_saldo_cliente(p_cliente_id BIGINT)\n"
        "RETURNS NUMERIC(18,2) LANGUAGE plpgsql AS $$\n"
        "BEGIN RETURN 0; END; $$;"
    )


async def test_trace_node_wrapper_executes(proc_b: str) -> None:
    wrapped = trace_node("parse_procedure", parse_procedure_node)
    result = await wrapped(
        {
            "source_code": proc_b,
            "request_id": "test-trace-1",
            "trace_id": "test-trace-1",
            "status": "running",
        }
    )
    assert result["parsing_report"]["status"] == "success"


def test_trace_manager_noop_without_credentials() -> None:
    tracer = TraceManager()
    span = tracer.span_by_id("fake-id", name="parse", input_data={})
    assert span is None
    assert tracer.get_langchain_callback(trace_id="fake-id") == []
