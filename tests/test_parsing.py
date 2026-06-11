"""Testes do nó de parsing."""

from __future__ import annotations

import pytest

from pipeline.graph.nodes.parsing import PGSQLParser


@pytest.fixture
def parser() -> PGSQLParser:
    return PGSQLParser()


class TestParserProcB:
    """Anexo B — fn_saldo_cliente (função escalar simples)."""

    def test_name_and_kind(self, parser, proc_b):
        parsed = parser.parse(proc_b)
        assert parsed.name == "fn_saldo_cliente"
        assert parsed.kind == "function"

    def test_parameter(self, parser, proc_b):
        parsed = parser.parse(proc_b)
        assert len(parsed.parameters) == 1
        p = parsed.parameters[0]
        assert p.name == "p_cliente_id"
        assert "BIGINT" in p.type.upper()
        assert p.mode == "IN"

    def test_return_type(self, parser, proc_b):
        parsed = parser.parse(proc_b)
        assert parsed.return_type is not None
        assert "NUMERIC" in parsed.return_type.upper()
        assert not parsed.returns_set

    def test_declare_variable(self, parser, proc_b):
        parsed = parser.parse(proc_b)
        assert len(parsed.declare_vars) >= 1
        names = [v.name for v in parsed.declare_vars]
        assert "v_total" in names

    def test_no_cursor(self, parser, proc_b):
        parsed = parser.parse(proc_b)
        assert len(parsed.cursors) == 0

    def test_no_exception_block(self, parser, proc_b):
        parsed = parser.parse(proc_b)
        assert not parsed.has_exception_block


class TestParserProcC:
    """Anexo C — sp_atualizar_status_contas_inativas (OUT param, UPDATE)."""

    def test_kind_is_procedure(self, parser, proc_c):
        parsed = parser.parse(proc_c)
        assert parsed.kind == "procedure"

    def test_out_parameter(self, parser, proc_c):
        parsed = parser.parse(proc_c)
        out_params = [p for p in parsed.parameters if p.mode == "OUT"]
        assert len(out_params) >= 1
        assert out_params[0].name == "p_afetadas"

    def test_has_raise(self, parser, proc_c):
        parsed = parser.parse(proc_c)
        assert len(parsed.raise_statements) >= 1


class TestParserProcD:
    """Anexo D — sp_transferir_entre_contas (transação, EXCEPTION)."""

    def test_has_exception_block(self, parser, proc_d):
        parsed = parser.parse(proc_d)
        assert parsed.has_exception_block

    def test_multiple_parameters(self, parser, proc_d):
        parsed = parser.parse(proc_d)
        assert len(parsed.parameters) == 3

    def test_declare_vars(self, parser, proc_d):
        parsed = parser.parse(proc_d)
        names = [v.name for v in parsed.declare_vars]
        assert "v_saldo_origem" in names


class TestParserEdgeCases:
    def test_invalid_sql_raises(self, parser):
        with pytest.raises(ValueError):
            parser.parse("SELECT 1")

    def test_to_dict_is_serializable(self, parser, proc_b):
        import json
        parsed = parser.parse(proc_b)
        d = parsed.to_dict()
        # deve ser serializável como JSON
        json.dumps(d)
