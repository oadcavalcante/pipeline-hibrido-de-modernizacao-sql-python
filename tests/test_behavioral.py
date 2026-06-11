"""
Testes comportamentais — equivalência entre procedure SQL e Python de referência.

Testes marcados com @pytest.mark.integration exigem PostgreSQL com schema legado.
Execute com: TEST_BEHAVIORAL=1 pytest tests/test_behavioral.py -m integration
"""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from pipeline.behavioral.reference import (
    fn_saldo_cliente,
    sp_atualizar_status_contas_inativas,
    sp_processar_lote_taxas,
    sp_relatorio_mensal_cliente,
    sp_transferir_entre_contas,
)
from tests.behavioral_support import (
    call_sp_atualizar_status_contas_inativas,
    install_procedure_wrappers,
    load_base_schema,
    load_procedures,
    normalize_snapshot,
    reset_schema,
    snapshot_state,
)

SQL_DIR = Path(__file__).resolve().parents[1] / "sql"
BEHAVIORAL_ENABLED = os.getenv("TEST_BEHAVIORAL", "").lower() in {"1", "true", "yes"}
DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://pipeline:pipeline@localhost:5432/pipeline",
)


def _skip_unless_behavioral() -> None:
    if not BEHAVIORAL_ENABLED:
        pytest.skip("Defina TEST_BEHAVIORAL=1 para rodar testes comportamentais com PostgreSQL")


async def _connect():
    try:
        import asyncpg
    except ImportError:
        pytest.skip("asyncpg não instalado")
    return await asyncpg.connect(DATABASE_URL)


# ── Seeds por anexo ───────────────────────────────────────────────────────────


async def _seed_b(conn) -> int:
    cliente_id = await conn.fetchval(
        "INSERT INTO clientes (nome, cpf) VALUES ('Maria', '12345678901') RETURNING id"
    )
    await conn.execute(
        """
        INSERT INTO contas (cliente_id, agencia, numero, tipo, saldo, status) VALUES
        ($1, '0001', '111', 'CORRENTE', 100.00, 'ATIVA'),
        ($1, '0001', '222', 'POUPANCA',  50.00, 'ATIVA'),
        ($1, '0001', '333', 'CORRENTE',  25.00, 'INATIVA')
        """,
        cliente_id,
    )
    return cliente_id


async def _seed_c(conn) -> tuple[int, int, int]:
    cliente_id = await conn.fetchval(
        "INSERT INTO clientes (nome, cpf) VALUES ('Joao', '98765432100') RETURNING id"
    )
    c_inativa = await conn.fetchval(
        """
        INSERT INTO contas (cliente_id, agencia, numero, tipo, saldo, status)
        VALUES ($1, '0001', '100', 'CORRENTE', 10, 'INATIVA') RETURNING id
        """,
        cliente_id,
    )
    c_sem_mov = await conn.fetchval(
        """
        INSERT INTO contas (cliente_id, agencia, numero, tipo, saldo, status)
        VALUES ($1, '0001', '200', 'CORRENTE', 20, 'ATIVA') RETURNING id
        """,
        cliente_id,
    )
    c_com_mov = await conn.fetchval(
        """
        INSERT INTO contas (cliente_id, agencia, numero, tipo, saldo, status)
        VALUES ($1, '0001', '300', 'CORRENTE', 30, 'ATIVA') RETURNING id
        """,
        cliente_id,
    )
    await conn.execute(
        """
        INSERT INTO transacoes (conta_origem_id, tipo, valor, data_transacao, status)
        VALUES ($1, 'SAQUE', 5.00, NOW() - INTERVAL '40 days', 'EFETIVADA')
        """,
        c_sem_mov,
    )
    await conn.execute(
        """
        INSERT INTO transacoes (conta_origem_id, tipo, valor, data_transacao, status)
        VALUES ($1, 'SAQUE', 2.00, NOW() - INTERVAL '5 days', 'EFETIVADA')
        """,
        c_com_mov,
    )
    return c_inativa, c_sem_mov, c_com_mov


async def _seed_d(conn) -> tuple[int, int]:
    cliente_id = await conn.fetchval(
        "INSERT INTO clientes (nome, cpf) VALUES ('Ana', '11122233344') RETURNING id"
    )
    origem = await conn.fetchval(
        """
        INSERT INTO contas (cliente_id, agencia, numero, tipo, saldo, status)
        VALUES ($1, '0001', '401', 'CORRENTE', 100.00, 'ATIVA') RETURNING id
        """,
        cliente_id,
    )
    destino = await conn.fetchval(
        """
        INSERT INTO contas (cliente_id, agencia, numero, tipo, saldo, status)
        VALUES ($1, '0001', '402', 'CORRENTE', 50.00, 'ATIVA') RETURNING id
        """,
        cliente_id,
    )
    return origem, destino


async def _seed_e(conn, ref: date) -> tuple[int, int]:
    cliente_id = await conn.fetchval(
        "INSERT INTO clientes (nome, cpf) VALUES ('Carlos', '55566677788') RETURNING id"
    )
    conta = await conn.fetchval(
        """
        INSERT INTO contas (cliente_id, agencia, numero, tipo, saldo, status)
        VALUES ($1, '0001', '501', 'CORRENTE', 1000.00, 'ATIVA') RETURNING id
        """,
        cliente_id,
    )
    await conn.execute(
        """
        INSERT INTO taxas (tipo_operacao, percentual, valor_minimo, vigente_de)
        VALUES
            ('TRANSFERENCIA', 1.0000, 2.00, '2020-01-01'),
            ('SAQUE', 2.0000, 3.00, '2020-01-01')
        """
    )
    await conn.execute(
        """
        INSERT INTO transacoes (conta_origem_id, tipo, valor, data_transacao, status)
        VALUES ($1, 'TRANSFERENCIA', 100.00, $2::date + TIME '10:00', 'EFETIVADA')
        """,
        conta,
        ref,
    )
    await conn.execute(
        """
        INSERT INTO transacoes (conta_origem_id, tipo, valor, data_transacao, status)
        VALUES ($1, 'SAQUE', 50.00, $2::date + TIME '11:00', 'EFETIVADA')
        """,
        conta,
        ref,
    )
    return cliente_id, conta


async def _seed_f(conn) -> tuple[int, int, int]:
    cliente_id = await conn.fetchval(
        "INSERT INTO clientes (nome, cpf) VALUES ('Paula', '99988877766') RETURNING id"
    )
    conta_a = await conn.fetchval(
        """
        INSERT INTO contas (cliente_id, agencia, numero, tipo, saldo, status)
        VALUES ($1, '0001', '601', 'CORRENTE', 200.00, 'ATIVA') RETURNING id
        """,
        cliente_id,
    )
    conta_b = await conn.fetchval(
        """
        INSERT INTO contas (cliente_id, agencia, numero, tipo, saldo, status)
        VALUES ($1, '0001', '602', 'POUPANCA', 100.00, 'ATIVA') RETURNING id
        """,
        cliente_id,
    )
    await conn.execute(
        """
        INSERT INTO transacoes (conta_destino_id, tipo, valor, data_transacao, status)
        VALUES ($1, 'DEPOSITO', 80.00, '2026-01-15 12:00:00', 'EFETIVADA')
        """,
        conta_a,
    )
    await conn.execute(
        """
        INSERT INTO transacoes (conta_origem_id, tipo, valor, data_transacao, status)
        VALUES ($1, 'SAQUE', 30.00, '2026-02-10 12:00:00', 'EFETIVADA')
        """,
        conta_b,
    )
    return cliente_id, conta_a, conta_b


# ── Testes ────────────────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_fn_saldo_cliente_equivalence() -> None:
    """Anexo B: Python de referência deve retornar o mesmo valor que a função SQL."""
    _skip_unless_behavioral()
    conn = await _connect()
    try:
        await reset_schema(conn)
        await load_base_schema(conn)
        await load_procedures(conn, "B_fn_saldo_cliente.sql")
        cliente_id = await _seed_b(conn)

        sql_result = await conn.fetchval("SELECT fn_saldo_cliente($1)", cliente_id)
        py_result = await fn_saldo_cliente(conn, cliente_id)

        assert Decimal(str(sql_result)) == py_result
        assert py_result == Decimal("150.00")
    finally:
        await conn.close()


@pytest.mark.integration
async def test_sp_atualizar_status_contas_inativas_equivalence() -> None:
    """Anexo C: inativação em lote — mesmo retorno e mesmo estado do banco."""
    _skip_unless_behavioral()
    conn = await _connect()
    try:
        await reset_schema(conn)
        await load_base_schema(conn)
        await load_procedures(conn, "C_sp_atualizar_status_contas_inativas.sql")
        await install_procedure_wrappers(conn)
        await _seed_c(conn)

        sql_afetadas = await call_sp_atualizar_status_contas_inativas(conn, 30)
        sql_snapshot = normalize_snapshot(await snapshot_state(conn))

        await reset_schema(conn)
        await load_base_schema(conn)
        await load_procedures(conn, "C_sp_atualizar_status_contas_inativas.sql")
        await _seed_c(conn)

        py_afetadas = await sp_atualizar_status_contas_inativas(conn, 30)
        py_snapshot = normalize_snapshot(await snapshot_state(conn))

        assert sql_afetadas == 1
        assert py_afetadas == sql_afetadas
        assert py_snapshot == sql_snapshot
    finally:
        await conn.close()


@pytest.mark.integration
async def test_sp_transferir_entre_contas_equivalence() -> None:
    """Anexo D: transferência atômica — saldos e transações equivalentes."""
    _skip_unless_behavioral()
    conn = await _connect()
    try:
        valor = Decimal("30.00")

        await reset_schema(conn)
        await load_base_schema(conn)
        await load_procedures(conn, "D_sp_transferir_entre_contas.sql")
        origem, destino = await _seed_d(conn)
        await conn.execute(
            "CALL sp_transferir_entre_contas($1, $2, $3)",
            origem,
            destino,
            valor,
        )
        sql_snapshot = normalize_snapshot(await snapshot_state(conn))

        await reset_schema(conn)
        await load_base_schema(conn)
        await load_procedures(conn, "D_sp_transferir_entre_contas.sql")
        origem, destino = await _seed_d(conn)
        await sp_transferir_entre_contas(conn, origem, destino, valor)
        py_snapshot = normalize_snapshot(await snapshot_state(conn))

        assert py_snapshot == sql_snapshot
        assert sql_snapshot["contas"][0]["saldo"] == "70.00"
        assert sql_snapshot["contas"][1]["saldo"] == "80.00"
    finally:
        await conn.close()


@pytest.mark.integration
async def test_sp_processar_lote_taxas_equivalence() -> None:
    """Anexo E: lote de taxas — tarifas e saldos equivalentes."""
    _skip_unless_behavioral()
    conn = await _connect()
    ref = date(2026, 3, 1)

    conn_sql = await _connect()
    conn_py = await _connect()
    try:
        await reset_schema(conn_sql)
        await load_base_schema(conn_sql)
        await load_procedures(conn_sql, "E_sp_processar_lote_taxas.sql")
        await _seed_e(conn_sql, ref)
        await conn_sql.execute("CALL sp_processar_lote_taxas($1)", ref)
        sql_snapshot = normalize_snapshot(await snapshot_state(conn_sql))

        await reset_schema(conn_py)
        await load_base_schema(conn_py)
        await load_procedures(conn_py, "E_sp_processar_lote_taxas.sql")
        await _seed_e(conn_py, ref)
        py_summary = await sp_processar_lote_taxas(conn_py, ref)
        py_snapshot = normalize_snapshot(await snapshot_state(conn_py))

        assert py_snapshot == sql_snapshot
        assert py_summary["transacoes"] == 2
        assert Decimal(str(py_summary["total_taxas"])) == Decimal("5.30")
    finally:
        await conn_sql.close()
        await conn_py.close()


@pytest.mark.integration
async def test_sp_relatorio_mensal_cliente_equivalence() -> None:
    """Anexo F: relatório mensal — mesmas linhas que a função SQL."""
    _skip_unless_behavioral()
    conn = await _connect()
    inicio = date(2026, 1, 1)
    fim = date(2026, 2, 28)
    try:
        await reset_schema(conn)
        await load_base_schema(conn)
        await load_procedures(conn, "B_fn_saldo_cliente.sql", "F_sp_relatorio_mensal_cliente.sql")
        cliente_id, _, _ = await _seed_f(conn)

        sql_rows = await conn.fetch(
            "SELECT * FROM sp_relatorio_mensal_cliente($1, $2, $3)",
            cliente_id,
            inicio,
            fim,
        )
        py_rows = await sp_relatorio_mensal_cliente(conn, cliente_id, inicio, fim)

        assert len(py_rows) == len(sql_rows) == 2

        for sql_row, py_row in zip(sql_rows, py_rows, strict=True):
            assert py_row["mes_referencia"] == sql_row["mes_referencia"]
            assert Decimal(str(py_row["total_creditos"])) == Decimal(str(sql_row["total_creditos"]))
            assert Decimal(str(py_row["total_debitos"])) == Decimal(str(sql_row["total_debitos"]))
            assert Decimal(str(py_row["saldo_consolidado"])) == Decimal(
                str(sql_row["saldo_consolidado"])
            )
            assert py_row["qtd_transacoes"] == sql_row["qtd_transacoes"]
    finally:
        await conn.close()


def test_reference_functions_are_async() -> None:
    """Smoke test: implementações de referência são importáveis e assíncronas."""
    import inspect

    refs = [
        fn_saldo_cliente,
        sp_atualizar_status_contas_inativas,
        sp_transferir_entre_contas,
        sp_processar_lote_taxas,
        sp_relatorio_mensal_cliente,
    ]
    for fn in refs:
        assert inspect.iscoroutinefunction(fn)
