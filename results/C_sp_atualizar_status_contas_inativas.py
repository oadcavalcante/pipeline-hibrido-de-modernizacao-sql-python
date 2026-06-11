from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import NamedTuple

import asyncpg


class SpAtualizarStatusContasInativasResult(NamedTuple):
    """Resultado da procedure sp_atualizar_status_contas_inativas."""

    p_afetadas: int


async def sp_atualizar_status_contas_inativas(
    conn: asyncpg.Connection,
    p_dias: int,
) -> SpAtualizarStatusContasInativasResult:
    """Marca como INATIVA toda conta sem movimentação há mais de p_dias dias.

    Args:
        conn: Conexão asyncpg ativa.
        p_dias: Quantidade de dias de inatividade. Deve ser positivo.

    Returns:
        NamedTuple com o total de contas afetadas em p_afetadas.

    Raises:
        ValueError: Se p_dias for nulo ou menor/igual a zero.
    """
    if p_dias is None or p_dias <= 0:
        raise ValueError(f"Parametro p_dias deve ser positivo, recebido: {p_dias}")

    sql_update = """
        UPDATE contas c
        SET status = 'INATIVA'
        WHERE c.status = 'ATIVA'
          AND NOT EXISTS (
              SELECT 1
              FROM transacoes t
              WHERE (t.conta_origem_id = c.id OR t.conta_destino_id = c.id)
                AND t.data_transacao >= NOW() - ($1 * INTERVAL '1 day')
          )
    """

    result = await conn.execute(sql_update, p_dias)
    p_afetadas = int(result.split()[-1])

    sql_insert_log = """
        INSERT INTO log_auditoria (entidade, acao, detalhes)
        VALUES (
            'contas',
            'INATIVACAO_LOTE',
            jsonb_build_object('dias', $1, 'afetadas', $2)
        )
    """
    await conn.execute(sql_insert_log, p_dias, p_afetadas)

    return SpAtualizarStatusContasInativasResult(p_afetadas=p_afetadas)
