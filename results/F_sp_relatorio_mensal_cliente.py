from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import asyncpg


@dataclass(slots=True)
class MonthlyClientReportRow:
    """Linha do relatório mensal do cliente."""

    mes_referencia: date
    total_creditos: Decimal
    total_debitos: Decimal
    saldo_consolidado: Decimal
    qtd_transacoes: int


async def _log_error(conn: asyncpg.Connection, *, entidade: str, acao: str, detalhes: dict[str, Any] | None = None) -> None:
    """Registra erro no log de auditoria, quando disponível."""
    await conn.execute(
        """
        INSERT INTO log_auditoria (entidade, entidade_id, acao, detalhes, criado_em)
        VALUES ($1, NULL, $2, $3::jsonb, NOW())
        """,
        entidade,
        acao,
        detalhes or {},
    )


async def fn_saldo_cliente(conn: asyncpg.Connection, cliente_id: int) -> Decimal:
    """Retorna o saldo consolidado atual do cliente."""
    row = await conn.fetchrow(
        """
        SELECT COALESCE(SUM(c.saldo), 0)::NUMERIC(18,2) AS saldo
        FROM contas c
        WHERE c.cliente_id = $1
        """,
        cliente_id,
    )
    return row["saldo"] if row is not None else Decimal("0.00")


async def sp_relatorio_mensal_cliente(
    conn: asyncpg.Connection,
    p_cliente_id: int,
    p_data_inicio: date,
    p_data_fim: date,
) -> list[MonthlyClientReportRow]:
    """Gera o relatório mensal de movimentação de um cliente."""
    v_saldo_atual: Decimal | None = None

    try:
        if p_data_inicio > p_data_fim:
            raise ValueError(f"Periodo invalido: inicio {p_data_inicio} > fim {p_data_fim}")

        v_saldo_atual = await fn_saldo_cliente(conn, p_cliente_id)

        text_sql = """
        WITH RECURSIVE meses AS (
            SELECT DATE_TRUNC('month', $2::date)::date AS mes
            UNION ALL
            SELECT (mes + INTERVAL '1 month')::date
            FROM meses
            WHERE mes < DATE_TRUNC('month', $3::date)
        ),
        movimento AS (
            SELECT
                DATE_TRUNC('month', t.data_transacao)::date AS mes,
                SUM(CASE WHEN t.conta_destino_id IN (
                    SELECT id FROM contas WHERE cliente_id = $1
                ) THEN t.valor ELSE 0 END) AS creditos,
                SUM(CASE WHEN t.conta_origem_id IN (
                    SELECT id FROM contas WHERE cliente_id = $1
                ) THEN t.valor ELSE 0 END) AS debitos,
                COUNT(*) AS qtd
            FROM transacoes t
            WHERE t.status = 'EFETIVADA'
              AND t.data_transacao >= $2::date
              AND t.data_transacao < ($3::date + INTERVAL '1 day')
              AND (
                  t.conta_origem_id IN (SELECT id FROM contas WHERE cliente_id = $1)
                  OR t.conta_destino_id IN (SELECT id FROM contas WHERE cliente_id = $1)
              )
            GROUP BY 1
        )
        SELECT
            m.mes AS mes_referencia,
            COALESCE(mv.creditos, 0)::NUMERIC(18,2) AS total_creditos,
            COALESCE(mv.debitos, 0)::NUMERIC(18,2) AS total_debitos,
            ($4::NUMERIC(18,2) + COALESCE(mv.creditos, 0) - COALESCE(mv.debitos, 0))::NUMERIC(18,2) AS saldo_consolidado,
            COALESCE(mv.qtd, 0)::INT AS qtd_transacoes
        FROM meses m
        LEFT JOIN movimento mv ON mv.mes = m.mes
        ORDER BY m.mes
        """

        rows = await conn.fetch(text_sql, p_cliente_id, p_data_inicio, p_data_fim, v_saldo_atual)

        return [
            MonthlyClientReportRow(
                mes_referencia=row["mes_referencia"],
                total_creditos=row["total_creditos"],
                total_debitos=row["total_debitos"],
                saldo_consolidado=row["saldo_consolidado"],
                qtd_transacoes=row["qtd_transacoes"],
            )
            for row in rows
        ]

    except Exception as exc:
        await _log_error(
            conn,
            entidade="sp_relatorio_mensal_cliente",
            acao="FALHA",
            detalhes={
                "p_cliente_id": p_cliente_id,
                "p_data_inicio": p_data_inicio.isoformat(),
                "p_data_fim": p_data_fim.isoformat(),
                "erro": str(exc),
            },
        )

        saldo_fallback = v_saldo_atual if v_saldo_atual is not None else Decimal("0.00")
        return [
            MonthlyClientReportRow(
                mes_referencia=date(p_data_inicio.year, p_data_inicio.month, 1),
                total_creditos=Decimal("0.00"),
                total_debitos=Decimal("0.00"),
                saldo_consolidado=saldo_fallback,
                qtd_transacoes=0,
            )
        ]
