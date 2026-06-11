"""Fixtures compartilhadas para os testes."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

# ─────────────────────────── SQL fixtures ────────────────────────────────────

PROC_B = """\
CREATE OR REPLACE FUNCTION fn_saldo_cliente(p_cliente_id BIGINT)
RETURNS NUMERIC(18,2)
LANGUAGE plpgsql
AS $$
DECLARE
    v_total NUMERIC(18,2);
BEGIN
    SELECT COALESCE(SUM(saldo), 0)
    INTO v_total
    FROM contas
    WHERE cliente_id = p_cliente_id
      AND status = 'ATIVA';
    RETURN v_total;
END;
$$;
"""

PROC_C = """\
CREATE OR REPLACE PROCEDURE sp_atualizar_status_contas_inativas(
    IN p_dias INT,
    OUT p_afetadas INT
)
LANGUAGE plpgsql
AS $$
BEGIN
    IF p_dias IS NULL OR p_dias <= 0 THEN
        RAISE EXCEPTION 'Parametro p_dias deve ser positivo, recebido: %', p_dias;
    END IF;
    UPDATE contas c
    SET status = 'INATIVA'
    WHERE c.status = 'ATIVA'
      AND NOT EXISTS (
          SELECT 1 FROM transacoes t
          WHERE (t.conta_origem_id = c.id OR t.conta_destino_id = c.id)
            AND t.data_transacao >= NOW() - (p_dias || ' days')::INTERVAL
      );
    GET DIAGNOSTICS p_afetadas = ROW_COUNT;
    INSERT INTO log_auditoria (entidade, acao, detalhes)
    VALUES ('contas', 'INATIVACAO_LOTE',
            jsonb_build_object('dias', p_dias, 'afetadas', p_afetadas));
END;
$$;
"""

PROC_D = """\
CREATE OR REPLACE PROCEDURE sp_transferir_entre_contas(
    IN p_conta_origem BIGINT,
    IN p_conta_destino BIGINT,
    IN p_valor NUMERIC(18,2)
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_saldo_origem NUMERIC(18,2);
    v_status_origem VARCHAR(20);
    v_status_destino VARCHAR(20);
BEGIN
    IF p_valor IS NULL OR p_valor <= 0 THEN
        RAISE EXCEPTION 'Valor invalido para transferencia: %', p_valor;
    END IF;
    IF p_conta_origem = p_conta_destino THEN
        RAISE EXCEPTION 'Conta de origem e destino nao podem ser iguais';
    END IF;
    SELECT saldo, status INTO v_saldo_origem, v_status_origem
    FROM contas WHERE id = p_conta_origem FOR UPDATE;
    SELECT status INTO v_status_destino
    FROM contas WHERE id = p_conta_destino FOR UPDATE;
    IF v_saldo_origem IS NULL THEN
        RAISE EXCEPTION 'Conta de origem % nao encontrada', p_conta_origem;
    END IF;
    IF v_status_origem <> 'ATIVA' OR v_status_destino <> 'ATIVA' THEN
        RAISE EXCEPTION 'Ambas as contas precisam estar ATIVAS';
    END IF;
    IF v_saldo_origem < p_valor THEN
        RAISE EXCEPTION 'Saldo insuficiente: saldo=% valor=%', v_saldo_origem, p_valor;
    END IF;
    UPDATE contas SET saldo = saldo - p_valor WHERE id = p_conta_origem;
    UPDATE contas SET saldo = saldo + p_valor WHERE id = p_conta_destino;
    INSERT INTO transacoes (conta_origem_id, conta_destino_id, tipo, valor)
    VALUES (p_conta_origem, p_conta_destino, 'TRANSFERENCIA', p_valor);
EXCEPTION
    WHEN OTHERS THEN
        INSERT INTO log_auditoria (entidade, acao, detalhes)
        VALUES ('transacoes', 'TRANSFERENCIA_ERRO',
                jsonb_build_object('erro', SQLERRM));
        RAISE;
END;
$$;
"""

INVALID_PYTHON = """\
def broken(
    x: int
    return x
"""

VALID_PYTHON = """\
from decimal import Decimal
import asyncpg


async def fn_saldo_cliente(conn: asyncpg.Connection, p_cliente_id: int) -> Decimal:
    \"\"\"Retorna o saldo consolidado de todas as contas ativas do cliente.\"\"\"
    result = await conn.fetchval(
        \"SELECT COALESCE(SUM(saldo), 0) FROM contas WHERE cliente_id = $1 AND status = 'ATIVA'\",
        p_cliente_id,
    )
    return Decimal(str(result or 0))
"""


@pytest.fixture
def proc_b() -> str:
    return PROC_B


@pytest.fixture
def proc_c() -> str:
    return PROC_C


@pytest.fixture
def proc_d() -> str:
    return PROC_D


@pytest.fixture
def valid_python() -> str:
    return VALID_PYTHON


@pytest.fixture
def invalid_python() -> str:
    return INVALID_PYTHON


@pytest.fixture
async def async_client():
    """Cliente HTTP assíncrono para testes da API (sem banco real)."""
    from unittest.mock import AsyncMock, MagicMock, patch

    mock_repo = MagicMock()
    mock_repo.ping = AsyncMock(return_value=True)
    mock_repo.save = AsyncMock(return_value=MagicMock(id="test-id", created_at=None))
    mock_repo.get_by_request_id = AsyncMock(return_value=None)

    from pipeline.api.server import app

    with (
        patch("pipeline.api.routes.get_repository", return_value=AsyncMock(return_value=mock_repo)),
        patch("pipeline.api.routes.get_trace_manager", return_value=AsyncMock()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client
