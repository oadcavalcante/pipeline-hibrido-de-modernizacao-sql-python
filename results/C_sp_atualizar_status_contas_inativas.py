from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import asyncpg


@dataclass(frozen=True, slots=True)
class SpAtualizarStatusContasInativasResult:
    """Resultado da procedure sp_atualizar_status_contas_inativas."""

    p_afetadas: int


async def sp_atualizar_status_contas_inativas(
    conn: asyncpg.Connection,
    p_dias: int | None,
) -> SpAtualizarStatusContasInativasResult:
    """Marca como INATIVA toda conta sem movimentação há mais de p_dias dias.

    Args:
        conn: Conexão asyncpg já aberta.
        p_dias: Quantidade de dias de inatividade.

    Returns:
        SpAtualizarStatusContasInativasResult com o total de contas afetadas.

    Raises:
        ValueError: Se p_dias for nulo ou não positivo.
    """
    if p_dias is None or p_dias <= 0:
        raise ValueError(f"Parametro p_dias deve ser positivo, recebido: {p_dias}")

    async with conn.transaction():
        result = await conn.execute(
            """
            UPDATE contas c
            SET status = 'INATIVA'
            WHERE c.status = 'ATIVA'
              AND NOT EXISTS (
                  SELECT 1
                  FROM transacoes t
                  WHERE (t.conta_origem_id = c.id OR t.conta_destino_id = c.id)
                    AND t.data_transacao >= NOW() - ($1 || ' days')::INTERVAL
              )
            """,
            p_dias,
        )
        p_afetadas = int(result.split()[-1])

        await conn.execute(
            """
            INSERT INTO log_auditoria (entidade, acao, detalhes)
            VALUES ($1, $2, $3)
            """,
            "contas",
            "INATIVACAO_LOTE",
            {"dias": p_dias, "afetadas": p_afetadas},
        )

    return SpAtualizarStatusContasInativasResult(p_afetadas=p_afetadas)
