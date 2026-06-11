"""Testes de integração dos nós do LangGraph (sem LLM real)."""

from __future__ import annotations

from pipeline.graph.nodes.analysis import analyze_semantics_node
from pipeline.graph.nodes.parsing import parse_procedure_node
from pipeline.graph.nodes.validation import validate_output_node


class TestParseNode:
    async def test_success_state(self, proc_b):
        state = {"source_code": proc_b, "status": "running"}
        result = await parse_procedure_node(state)
        assert result["parsing_report"]["status"] == "success"
        assert result["parsed_procedure"] is not None
        assert result["status"] == "running"

    async def test_failure_state(self):
        state = {"source_code": "NOT SQL AT ALL", "status": "running"}
        result = await parse_procedure_node(state)
        assert result["status"] == "failure"
        assert len(result["parsing_errors"]) > 0

    async def test_duration_recorded(self, proc_b):
        state = {"source_code": proc_b, "status": "running"}
        result = await parse_procedure_node(state)
        assert result["parsing_report"]["duration_ms"] >= 0


class TestAnalyzeNode:
    async def test_skips_on_failure(self):
        state = {"status": "failure"}
        result = await analyze_semantics_node(state)
        assert result == {}

    async def test_analyzes_parsed_proc(self, proc_b):
        from pipeline.graph.nodes.parsing import PGSQLParser
        parsed = PGSQLParser().parse(proc_b).to_dict()
        state = {"source_code": proc_b, "parsed_procedure": parsed, "status": "running"}
        result = await analyze_semantics_node(state)
        assert result["analysis_report"]["status"] == "success"
        assert result["semantic_analysis"] is not None


class TestValidateNode:
    async def test_valid_code(self, valid_python):
        state = {"generated_code": valid_python, "status": "running"}
        result = await validate_output_node(state)
        assert result["validation_passed"] is True
        assert result["status"] == "success"

    async def test_invalid_code(self, invalid_python):
        state = {"generated_code": invalid_python, "status": "running"}
        result = await validate_output_node(state)
        assert result["validation_passed"] is False
        assert result["status"] == "partial"

    async def test_skips_on_failure(self):
        state = {"status": "failure"}
        result = await validate_output_node(state)
        assert result == {}

    async def test_skips_on_no_code(self):
        state = {"status": "running", "generated_code": None}
        result = await validate_output_node(state)
        assert result["validation_passed"] is False
