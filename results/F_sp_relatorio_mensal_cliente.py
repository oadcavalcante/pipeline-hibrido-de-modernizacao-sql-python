from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import asyncpg


@dataclass(slots=True, frozen=True)
class RelatorioMensalClienteRow:
    """Linha do relatório mensal de cliente."""

    mes_referencia: date
    total_creditos: Decimal
    total_debitos: Decimal
    saldo_consolidado: Decimal
    qtd_transacoes: int


async def _log_error(conn: asyncpg.Connection, mensagem: str, detalhes: dict[str, Any] | None = None) -> None:
    """Registra erro em log_auditoria, se a tabela estiver disponível."""
    await conn.execute(
        """
        INSERT INTO log_auditoria (entidade, entidade_id, acao, detalhes)
        VALUES ($1, $2, $3, $4)
        """,
        "sp_relatorio_mensal_cliente",
        None,
        "ERRO",
        {"mensagem": mensagem, "detalhes": detalhes or {}},
    )


async def fn_saldo_cliente(conn: asyncpg.Connection, p_cliente_id: int) -> Decimal:
    """Calcula o saldo atual consolidado do cliente."""
    row = await conn.fetchrow(
        """
        SELECT COALESCE(SUM(c.saldo), 0)::NUMERIC(18,2) AS saldo
        FROM contas c
        WHERE c.cliente_id = $1
        """,
        p_cliente_id,
    )
    return Decimal(str(row["saldo"])) if row and row["saldo"] is not None else Decimal("0.00")


async def sp_relatorio_mensal_cliente(
    conn: asyncpg.Connection,
    p_cliente_id: int,
    p_data_inicio: date,
    p_data_fim: date,
) -> list[RelatorioMensalClienteRow]:
    """Gera o relatório mensal de movimentação de um cliente.

    Args:
        conn: Conexão asyncpg.
        p_cliente_id: ID do cliente.
        p_data_inicio: Data inicial do período.
        p_data_fim: Data final do período.

    Returns:
        Lista de linhas do relatório mensal.
    """
    v_saldo_atual: Decimal | None = None

    try:
        if p_data_inicio > p_data_fim:
            raise ValueError(f"Periodo invalido: inicio {p_data_inicio} > fim {p_data_fim}")

        v_saldo_atual = await fn_saldo_cliente(conn, p_cliente_id)

        sql = """
        WITH RECURSIVE meses AS (
            SELECT DATE_TRUNC('month', $2::DATE)::DATE AS mes
            UNION ALL
            SELECT (mes + INTERVAL '1 month')::DATE
            FROM meses
            WHERE mes < DATE_TRUNC('month', $3::DATE)
        ),
        contas_cliente AS (
            SELECT id
            FROM contas
            WHERE cliente_id = $1
        ),
        movimento AS (
            SELECT
                DATE_TRUNC('month', t.data_transacao)::DATE AS mes,
                SUM(
                    CASE
                        WHEN t.conta_destino_id IN (SELECT id FROM contas_cliente)
                        THEN t.valor ELSE 0
                    END
                ) AS creditos,
                SUM(
                    CASE
                        WHEN t.conta_origem_id IN (SELECT id FROM contas_cliente)
                        THEN t.valor ELSE 0
                    END
                ) AS debitos,
                COUNT(*) AS qtd
            FROM transacoes t
            WHERE t.status = 'EFETIVADA'
              AND t.data_transacao >= $2::DATE
              AND t.data_transacao < ($3::DATE + INTERVAL '1 day')
              AND (
                  t.conta_origem_id IN (SELECT id FROM contas_cliente)
                  OR t.conta_destino_id IN (SELECT id FROM contas_cliente)
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

        rows = await conn.fetch(sql, p_cliente_id, p_data_inicio, p_data_fim, v_saldo_atual)
        return [
            RelatorioMensalClienteRow(
                mes_referencia=row["mes_referencia"],
                total_creditos=Decimal(str(row["total_creditos"])),
                total_debitos=Decimal(str(row["total_debitos"])),
                saldo_consolidado=Decimal(str(row["saldo_consolidado"])),
                qtd_transacoes=int(row["qtd_transacoes"]),
            )
            for row in rows
        ]

    except Exception as exc:
        try:
            await _log_error(
                conn,
                "Falha ao gerar relatorio: Retornando fallback.",
                {
                    "cliente_id": p_cliente_id,
                    "data_inicio": p_data_inicio.isoformat(),
                    "data_fim": p_data_fim.isoformat(),
                    "erro": str(exc),
                },
            )
        except Exception:
            pass

        fallback_saldo = v_saldo_atual if v_saldo_atual is not None else Decimal("0.00")
        return [
            RelatorioMensalClienteRow(
                mes_referencia=p_data_inicio.replace(day=1),
                total_creditos=Decimal("0.00"),
                total_debitos=Decimal("0.00"),
                saldo_consolidado=fallback_saldo,
                qtd_transacoes=0,
            )
        ]
