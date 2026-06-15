from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import asyncpg


@dataclass(slots=True)
class ProcessarLoteTaxasResult:
    """Resultado do processamento em lote de taxas."""

    transacoes_processadas: int
    total_taxas: Decimal


async def sp_processar_lote_taxas(
    conn: asyncpg.Connection,
    p_data_referencia: dt.date,
) -> None:
    """Processa transações efetivadas na data de referência, aplica taxas e registra auditoria.

    Args:
        conn: Conexão asyncpg já aberta.
        p_data_referencia: Data de referência para seleção das transações.

    Raises:
        ValueError: Se ocorrer qualquer condição inválida durante o processamento.
    """
    sql_transacoes = """
        SELECT id, conta_origem_id, tipo, valor
        FROM transacoes
        WHERE DATE(data_transacao) = $1
          AND status = 'EFETIVADA'
          AND tipo <> 'TARIFA'
    """
    rows = await conn.fetch(sql_transacoes, p_data_referencia)

    v_total_taxas: Decimal = Decimal("0")
    v_count: int = 0

    async with conn.transaction():
        for row in rows:
            v_id: int = row["id"]
            v_origem: int | None = row["conta_origem_id"]
            v_tipo: str = row["tipo"]
            v_valor: Decimal = row["valor"]

            sql_taxa = """
                SELECT percentual, valor_minimo
                FROM taxas
                WHERE tipo_operacao = $1
                  AND vigente_de <= $2
                  AND (vigente_ate IS NULL OR vigente_ate >= $2)
                ORDER BY vigente_de DESC
                LIMIT 1
            """
            taxa_row = await conn.fetchrow(sql_taxa, v_tipo, p_data_referencia)

            if taxa_row is None or taxa_row["percentual"] is None:
                continue

            v_percentual: Decimal = taxa_row["percentual"]
            v_minimo: Decimal = taxa_row["valor_minimo"]

            v_taxa: Decimal = max((v_valor * v_percentual / Decimal("100.0")), v_minimo)

            if v_tipo == "TRANSFERENCIA":
                v_taxa = v_taxa
            elif v_tipo == "SAQUE":
                v_taxa = v_taxa * Decimal("1.10")
            else:
                v_taxa = v_taxa * Decimal("0.90")

            if v_origem is not None:
                result = await conn.execute(
                    "UPDATE contas SET saldo = saldo - $1 WHERE id = $2",
                    v_taxa,
                    v_origem,
                )
                _ = result

                await conn.execute(
                    """
                    INSERT INTO transacoes (conta_origem_id, tipo, valor, status)
                    VALUES ($1, 'TARIFA', $2, 'EFETIVADA')
                    """,
                    v_origem,
                    v_taxa,
                )

                await conn.execute(
                    """
                    INSERT INTO log_auditoria (entidade, entidade_id, acao, detalhes)
                    VALUES (
                        'transacoes',
                        $1,
                        'TARIFA_APLICADA',
                        $2
                    )
                    """,
                    v_id,
                    {
                        "transacao_origem": v_id,
                        "tipo_origem": v_tipo,
                        "valor_origem": str(v_valor),
                        "percentual": str(v_percentual),
                        "taxa_aplicada": str(v_taxa),
                    },
                )

                v_total_taxas += v_taxa
                v_count += 1

        await conn.execute(
            """
            INSERT INTO log_auditoria (entidade, acao, detalhes)
            VALUES (
                'lote_taxas',
                'LOTE_PROCESSADO',
                $1
            )
            """,
            {
                "data_referencia": p_data_referencia.isoformat(),
                "transacoes": v_count,
                "total_taxas": str(v_total_taxas),
            },
        )
