from __future__ import annotations

from decimal import Decimal
from typing import Any

import asyncpg


async def fn_saldo_cliente(conn: asyncpg.Connection, p_cliente_id: int) -> Decimal:
    """
    Retorna o saldo total consolidado de todas as contas ativas de um cliente.

    Args:
        conn: Conexão asyncpg já aberta.
        p_cliente_id: Identificador do cliente.

    Returns:
        Saldo total consolidado das contas ativas do cliente.
    """
    row: asyncpg.Record | None = await conn.fetchrow(
        """
        SELECT COALESCE(SUM(saldo), 0) AS v_total
        FROM contas
        WHERE cliente_id = $1
          AND status = 'ATIVA'
        """,
        p_cliente_id,
    )

    if row is None:
        return Decimal("0.00")

    v_total: Decimal = row["v_total"]
    return v_total
