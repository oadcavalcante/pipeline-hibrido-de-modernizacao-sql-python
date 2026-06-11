from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import asyncpg


@dataclass(slots=True)
class TransferenciaEntreContasResult:
    """Resultado da operação de transferência entre contas."""

    sucesso: bool


async def _log_transferencia_erro(
    conn: asyncpg.Connection,
    p_conta_origem: int,
    p_conta_destino: int,
    p_valor: Decimal | None,
    erro: str,
) -> None:
    """Registra erro de transferência na trilha de auditoria."""
    await conn.execute(
        """
        INSERT INTO log_auditoria (entidade, acao, detalhes)
        VALUES (
            'transacoes',
            'TRANSFERENCIA_ERRO',
            jsonb_build_object(
                'origem', $1,
                'destino', $2,
                'valor', $3,
                'erro', $4
            )
        )
        """,
        p_conta_origem,
        p_conta_destino,
        p_valor,
        erro,
    )


async def sp_transferir_entre_contas(
    conn: asyncpg.Connection,
    p_conta_origem: int,
    p_conta_destino: int,
    p_valor: Decimal | None,
) -> TransferenciaEntreContasResult:
    """Transfere um valor entre duas contas de forma atômica.

    Valida valor, saldo, status das contas e registra transação e auditoria.
    """
    try:
        if p_valor is None or p_valor <= Decimal("0"):
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

            if row_origem is None:
                raise ValueError(f"Conta de origem {p_conta_origem} nao encontrada")

            if row_destino is None:
                raise ValueError(f"Conta de destino {p_conta_destino} nao encontrada")

            v_saldo_origem: Decimal = row_origem["saldo"]
            v_status_origem: str = row_origem["status"]
            v_status_destino: str = row_destino["status"]

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

            # asyncpg serializa/deserializa JSONB automaticamente como dict Python
            await conn.execute(
                """
                INSERT INTO log_auditoria (entidade, entidade_id, acao, detalhes)
                VALUES (
                    'transacoes',
                    NULL,
                    'TRANSFERENCIA_OK',
                    jsonb_build_object(
                        'origem', $1,
                        'destino', $2,
                        'valor', $3
                    )
                )
                """,
                p_conta_origem,
                p_conta_destino,
                p_valor,
            )

        return TransferenciaEntreContasResult(sucesso=True)

    except Exception as exc:
        try:
            await _log_transferencia_erro(
                conn=conn,
                p_conta_origem=p_conta_origem,
                p_conta_destino=p_conta_destino,
                p_valor=p_valor,
                erro=str(exc),
            )
        except Exception:
            pass
        raise
