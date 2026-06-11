"""Testes do nó de análise semântica."""

from __future__ import annotations

import pytest

from pipeline.graph.nodes.analysis import SemanticAnalyzer
from pipeline.graph.nodes.parsing import PGSQLParser


@pytest.fixture
def analyzer():
    return SemanticAnalyzer()


@pytest.fixture
def parser():
    return PGSQLParser()


def parsed(proc_sql: str) -> dict:
    return PGSQLParser().parse(proc_sql).to_dict()


class TestAnalysisProcB:
    def test_complexity_low(self, analyzer, proc_b):
        analysis = analyzer.analyze(parsed(proc_b))
        assert analysis.complexity == "low"

    def test_return_scalar(self, analyzer, proc_b):
        analysis = analyzer.analyze(parsed(proc_b))
        assert analysis.return_pattern == "scalar"

    def test_no_cursor(self, analyzer, proc_b):
        analysis = analyzer.analyze(parsed(proc_b))
        assert not analysis.uses_cursor


class TestAnalysisProcC:
    def test_out_params_detected(self, analyzer, proc_c):
        analysis = analyzer.analyze(parsed(proc_c))
        assert analysis.return_pattern == "out_params"

    def test_raise_detected(self, analyzer, proc_c):
        analysis = analyzer.analyze(parsed(proc_c))
        assert analysis.uses_raise

    def test_get_diagnostics_detected(self, analyzer, proc_c):
        analysis = analyzer.analyze(parsed(proc_c))
        assert analysis.uses_get_diagnostics

    def test_jsonb_detected(self, analyzer, proc_c):
        analysis = analyzer.analyze(parsed(proc_c))
        assert analysis.uses_jsonb


class TestAnalysisProcD:
    def test_for_update_detected(self, analyzer, proc_d):
        analysis = analyzer.analyze(parsed(proc_d))
        assert analysis.uses_for_update

    def test_complexity_medium_or_higher(self, analyzer, proc_d):
        analysis = analyzer.analyze(parsed(proc_d))
        assert analysis.complexity in ("medium", "high", "very_high")

    def test_has_risk_row_locking(self, analyzer, proc_d):
        analysis = analyzer.analyze(parsed(proc_d))
        risk_types = [r.type for r in analysis.risks]
        assert "row_locking" in risk_types

    def test_transaction_hint_present(self, analyzer, proc_d):
        analysis = analyzer.analyze(parsed(proc_d))
        hint_topics = [h.topic for h in analysis.translation_hints]
        assert "transaction_context_manager" in hint_topics


class TestAnalysisToDict:
    def test_to_dict_serializable(self, analyzer, proc_b):
        import json
        analysis = analyzer.analyze(parsed(proc_b))
        json.dumps(analysis.to_dict())
