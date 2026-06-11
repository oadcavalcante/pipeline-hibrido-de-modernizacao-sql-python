"""
Validação estrutural das procedures dos Anexos B a F.

Garante que o parser e o analisador capturam a complexidade esperada
antes mesmo da geração via LLM.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.graph.nodes.analysis import SemanticAnalyzer
from pipeline.graph.nodes.parsing import PGSQLParser

PROCEDURES_DIR = Path(__file__).resolve().parents[1] / "sql" / "procedures"

ANNEX_EXPECTATIONS = {
    "B_fn_saldo_cliente.sql": {
        "name": "fn_saldo_cliente",
        "kind": "function",
        "complexity": "low",
        "return_pattern": "scalar",
        "constructs": [],
    },
    "C_sp_atualizar_status_contas_inativas.sql": {
        "name": "sp_atualizar_status_contas_inativas",
        "kind": "procedure",
        "complexity_min": "low",
        "complexity_max": "medium",
        "return_pattern": "out_params",
        "constructs_contains": ["raise_exception", "get_diagnostics", "jsonb_operations"],
    },
    "D_sp_transferir_entre_contas.sql": {
        "name": "sp_transferir_entre_contas",
        "kind": "procedure",
        "complexity_min": "medium",
        "return_pattern": "void",
        "constructs_contains": ["for_update_lock", "exception_handler"],
    },
    "E_sp_processar_lote_taxas.sql": {
        "name": "sp_processar_lote_taxas",
        "kind": "procedure",
        "complexity_min": "high",
        "return_pattern": "void",
        "constructs_contains": ["explicit_cursor", "jsonb_operations"],
    },
    "F_sp_relatorio_mensal_cliente.sql": {
        "name": "sp_relatorio_mensal_cliente",
        "kind": "function",
        "complexity_min": "high",
        "return_pattern": "setof",
        "constructs_contains": ["recursive_cte", "cross_function_calls", "exception_handler"],
    },
}

COMPLEXITY_ORDER = ["low", "medium", "high", "very_high"]


def _load_procedure(filename: str) -> str:
    return (PROCEDURES_DIR / filename).read_text(encoding="utf-8")


def _complexity_at_least(actual: str, minimum: str) -> bool:
    return COMPLEXITY_ORDER.index(actual) >= COMPLEXITY_ORDER.index(minimum)


@pytest.mark.parametrize("filename,expected", ANNEX_EXPECTATIONS.items())
def test_annex_parsing_metadata(filename: str, expected: dict) -> None:
    source = _load_procedure(filename)
    parsed = PGSQLParser().parse(source)

    assert parsed.name == expected["name"]
    assert parsed.kind == expected["kind"]
    if expected.get("return_pattern") == "scalar":
        assert parsed.return_type is not None
    if expected.get("return_pattern") == "setof":
        assert parsed.returns_set is True


@pytest.mark.parametrize("filename,expected", ANNEX_EXPECTATIONS.items())
def test_annex_semantic_analysis(filename: str, expected: dict) -> None:
    source = _load_procedure(filename)
    parsed = PGSQLParser().parse(source).to_dict()
    analysis = SemanticAnalyzer().analyze(parsed)

    assert analysis.return_pattern == expected["return_pattern"]

    if "complexity" in expected:
        assert analysis.complexity == expected["complexity"]
    if "complexity_min" in expected:
        assert _complexity_at_least(analysis.complexity, expected["complexity_min"])
    if "complexity_max" in expected:
        assert COMPLEXITY_ORDER.index(analysis.complexity) <= COMPLEXITY_ORDER.index(
            expected["complexity_max"]
        )

    for construct in expected.get("constructs_contains", []):
        assert construct in analysis.constructs, f"{filename}: falta construção {construct}"
