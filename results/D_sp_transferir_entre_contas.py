from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import asyncpg


@dataclass(slots=True)
class TransferenciaResult:
    """Resultado da transferência entre contas."""

    sucesso: bool


async def _log_error(
    conn: asyncpg.Connection,
    *,
    origem: int,
    destino: int,
    valor: Decimal | None,
    erro: str,
) -> None:
    """Registra erro de transferência na auditoria."""
    await conn.execute(
        """
        INSERT INTO log_auditoria (entidade, acao, detalhes)
        VALUES ($1, $2, $3)
        """,
        "transacoes",
        "TRANSFERENCIA_ERRO",
        {
            "origem": origem,
            "destino": destino,
            "valor": valor,
            "erro": erro,
        },
    )


async def sp_transferir_entre_contas(
    conn: asyncpg.Connection,
    p_conta_origem: int,
    p_conta_destino: int,
    p_valor: Decimal,
) -> TransferenciaResult:
    """Transfere um valor entre duas contas em uma operação atômica."""
    try:
        if p_valor is None or p_valor <= 0:
            raise ValueError(f"Valor invalido para transferencia: {p_valor}")
        if p_conta_origem == p_conta_destino:
            raise ValueError("Conta de origem e destino nao podem ser iguais")

        async with conn.transaction():
            row_origem = await conn.fetchrow(
                """
                SELECT saldo, status
                FROM contas
                WHERE id = $1
                FOR UPDATE
                """,
                p_conta_origem,
            )

            row_destino = await conn.fetchrow(
                """
                SELECT status
                FROM contas
                WHERE id = $1
                FOR UPDATE
                """,
                p_conta_destino,
            )

            v_saldo_origem: Decimal | None = None
            v_status_origem: str | None = None
            v_status_destino: str | None = None

            if row_origem is not None:
                v_saldo_origem = row_origem["saldo"]
                v_status_origem = row_origem["status"]

            if row_destino is not None:
                v_status_destino = row_destino["status"]

            if v_saldo_origem is None:
                raise ValueError(f"Conta de origem {p_conta_origem} nao encontrada")
            if v_status_origem != "ATIVA" or v_status_destino != "ATIVA":
                raise ValueError("Ambas as contas precisam estar ATIVAS")
            if v_saldo_origem < p_valor:
                raise ValueError(f"Saldo insuficiente: saldo={v_saldo_origem} valor={p_valor}")

            await conn.execute(
                """
                UPDATE contas
                SET saldo = saldo - $1
                WHERE id = $2
                """,
                p_valor,
                p_conta_origem,
            )

            await conn.execute(
                """
                UPDATE contas
                SET saldo = saldo + $1
                WHERE id = $2
                """,
                p_valor,
                p_conta_destino,
            )

            await conn.execute(
                """
                INSERT INTO transacoes (conta_origem_id, conta_destino_id, tipo, valor)
                VALUES ($1, $2, 'TRANSFERENCIA', $3)
                """,
                p_conta_origem,
                p_conta_destino,
                p_valor,
            )

            await conn.execute(
                """
                INSERT INTO log_auditoria (entidade, entidade_id, acao, detalhes)
                VALUES ($1, $2, $3, $4)
                """,
                "transacoes",
                None,
                "TRANSFERENCIA_OK",
                {
                    "origem": p_conta_origem,
                    "destino": p_conta_destino,
                    "valor": p_valor,
                },
            )

        return TransferenciaResult(sucesso=True)

    except Exception as exc:
        await _log_error(
            conn,
            origem=p_conta_origem,
            destino=p_conta_destino,
            valor=p_valor,
            erro=str(exc),
        )
        raise
