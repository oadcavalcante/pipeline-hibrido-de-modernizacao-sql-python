"""
Implementações de referência em Python 3.14 para validação comportamental.

Usadas nos testes de equivalência: mesmo input → mesmo output que a procedure SQL.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import asyncpg


async def fn_saldo_cliente(conn: asyncpg.Connection, p_cliente_id: int) -> Decimal:
    """Referência para Anexo B — fn_saldo_cliente."""
    result = await conn.fetchval(
        """
        SELECT COALESCE(SUM(saldo), 0)
        FROM contas
        WHERE cliente_id = $1 AND status = 'ATIVA'
        """,
        p_cliente_id,
    )
    return Decimal(str(result or 0))


async def sp_atualizar_status_contas_inativas(
    conn: asyncpg.Connection, p_dias: int
) -> int:
    """Referência para Anexo C — sp_atualizar_status_contas_inativas."""
    if p_dias is None or p_dias <= 0:
        raise ValueError(f"Parametro p_dias deve ser positivo, recebido: {p_dias}")

    status = await conn.execute(
        """
        UPDATE contas c
        SET status = 'INATIVA'
        WHERE c.status = 'ATIVA'
          AND NOT EXISTS (
              SELECT 1
              FROM transacoes t
              WHERE (t.conta_origem_id = c.id OR t.conta_destino_id = c.id)
                AND t.data_transacao >= NOW() - ($1::text || ' days')::INTERVAL
          )
        """,
        str(p_dias),
    )
    afetadas = int(status.split()[-1])

    await conn.execute(
        """
        INSERT INTO log_auditoria (entidade, acao, detalhes)
        VALUES (
            'contas',
            'INATIVACAO_LOTE',
            jsonb_build_object('dias', $1::int, 'afetadas', $2::int)
        )
        """,
        p_dias,
        afetadas,
    )
    return afetadas


async def sp_transferir_entre_contas(
    conn: asyncpg.Connection,
    p_conta_origem: int,
    p_conta_destino: int,
    p_valor: Decimal,
) -> None:
    """Referência para Anexo D — sp_transferir_entre_contas."""
    if p_valor is None or p_valor <= 0:
        raise ValueError(f"Valor invalido para transferencia: {p_valor}")
    if p_conta_origem == p_conta_destino:
        raise ValueError("Conta de origem e destino nao podem ser iguais")

    async with conn.transaction():
        try:
            origem = await conn.fetchrow(
                "SELECT saldo, status FROM contas WHERE id = $1 FOR UPDATE",
                p_conta_origem,
            )
            destino = await conn.fetchrow(
                "SELECT status FROM contas WHERE id = $1 FOR UPDATE",
                p_conta_destino,
            )

            if origem is None:
                raise ValueError(f"Conta de origem {p_conta_origem} nao encontrada")
            if origem["status"] != "ATIVA" or destino is None or destino["status"] != "ATIVA":
                raise ValueError("Ambas as contas precisam estar ATIVAS")
            if Decimal(str(origem["saldo"])) < p_valor:
                raise ValueError(
                    f"Saldo insuficiente: saldo={origem['saldo']} valor={p_valor}"
                )

            await conn.execute(
                "UPDATE contas SET saldo = saldo - $1 WHERE id = $2",
                p_valor,
                p_conta_origem,
            )
            await conn.execute(
                "UPDATE contas SET saldo = saldo + $1 WHERE id = $2",
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
                VALUES (
                    'transacoes', NULL, 'TRANSFERENCIA_OK',
                    jsonb_build_object(
                        'origem', $1::bigint,
                        'destino', $2::bigint,
                        'valor', $3::numeric
                    )
                )
                """,
                p_conta_origem,
                p_conta_destino,
                p_valor,
            )
        except Exception as exc:
            await conn.execute(
                """
                INSERT INTO log_auditoria (entidade, acao, detalhes)
                VALUES (
                    'transacoes', 'TRANSFERENCIA_ERRO',
                    jsonb_build_object(
                        'origem', $1::bigint,
                        'destino', $2::bigint,
                        'valor', $3::numeric,
                        'erro', $4::text
                    )
                )
                """,
                p_conta_origem,
                p_conta_destino,
                p_valor,
                str(exc),
            )
            raise


async def sp_processar_lote_taxas(
    conn: asyncpg.Connection, p_data_referencia: date
) -> dict[str, Any]:
    """Referência para Anexo E — sp_processar_lote_taxas."""
    transacoes = await conn.fetch(
        """
        SELECT id, conta_origem_id, tipo, valor
        FROM transacoes
        WHERE DATE(data_transacao) = $1
          AND status = 'EFETIVADA'
          AND tipo <> 'TARIFA'
        """,
        p_data_referencia,
    )

    total_taxas = Decimal("0")
    count = 0

    async with conn.transaction():
        for tx in transacoes:
            taxa_cfg = await conn.fetchrow(
                """
                SELECT percentual, valor_minimo
                FROM taxas
                WHERE tipo_operacao = $1
                  AND vigente_de <= $2
                  AND (vigente_ate IS NULL OR vigente_ate >= $2)
                ORDER BY vigente_de DESC
                LIMIT 1
                """,
                tx["tipo"],
                p_data_referencia,
            )
            if taxa_cfg is None:
                continue

            percentual = Decimal(str(taxa_cfg["percentual"]))
            minimo = Decimal(str(taxa_cfg["valor_minimo"]))
            valor = Decimal(str(tx["valor"]))
            v_taxa = max(valor * percentual / Decimal("100"), minimo)

            if tx["tipo"] == "TRANSFERENCIA":
                pass
            elif tx["tipo"] == "SAQUE":
                v_taxa *= Decimal("1.10")
            else:
                v_taxa *= Decimal("0.90")

            if tx["conta_origem_id"] is not None:
                await conn.execute(
                    "UPDATE contas SET saldo = saldo - $1 WHERE id = $2",
                    v_taxa,
                    tx["conta_origem_id"],
                )
                await conn.execute(
                    """
                    INSERT INTO transacoes (conta_origem_id, tipo, valor, status)
                    VALUES ($1, 'TARIFA', $2, 'EFETIVADA')
                    """,
                    tx["conta_origem_id"],
                    v_taxa,
                )
                await conn.execute(
                    """
                    INSERT INTO log_auditoria (entidade, entidade_id, acao, detalhes)
                    VALUES (
                        'transacoes', $1, 'TARIFA_APLICADA',
                        jsonb_build_object(
                            'transacao_origem', $1::bigint,
                            'tipo_origem', $2::text,
                            'valor_origem', $3::numeric,
                            'percentual', $4::numeric,
                            'taxa_aplicada', $5::numeric
                        )
                    )
                    """,
                    tx["id"],
                    tx["tipo"],
                    tx["valor"],
                    percentual,
                    v_taxa,
                )
                total_taxas += v_taxa
                count += 1

        await conn.execute(
            """
            INSERT INTO log_auditoria (entidade, acao, detalhes)
            VALUES (
                'lote_taxas', 'LOTE_PROCESSADO',
                jsonb_build_object(
                    'data_referencia', $1::date,
                    'transacoes', $2::int,
                    'total_taxas', $3::numeric
                )
            )
            """,
            p_data_referencia,
            count,
            total_taxas,
        )

    return {"transacoes": count, "total_taxas": total_taxas}


async def sp_relatorio_mensal_cliente(
    conn: asyncpg.Connection,
    p_cliente_id: int,
    p_data_inicio: date,
    p_data_fim: date,
) -> list[dict[str, Any]]:
    """Referência para Anexo F — sp_relatorio_mensal_cliente."""
    if p_data_inicio > p_data_fim:
        raise ValueError(f"Periodo invalido: inicio {p_data_inicio} > fim {p_data_fim}")

    v_saldo_atual = await fn_saldo_cliente(conn, p_cliente_id)

    try:
        rows = await conn.fetch(
            """
            WITH RECURSIVE meses AS (
                SELECT DATE_TRUNC('month', $2::date)::DATE AS mes
                UNION ALL
                SELECT (mes + INTERVAL '1 month')::DATE
                FROM meses
                WHERE mes < DATE_TRUNC('month', $3::date)
            ),
            movimento AS (
                SELECT
                    DATE_TRUNC('month', t.data_transacao)::DATE AS mes,
                    SUM(CASE WHEN t.conta_destino_id IN (
                        SELECT id FROM contas WHERE cliente_id = $1
                    ) THEN t.valor ELSE 0 END) AS creditos,
                    SUM(CASE WHEN t.conta_origem_id IN (
                        SELECT id FROM contas WHERE cliente_id = $1
                    ) THEN t.valor ELSE 0 END) AS debitos,
                    COUNT(*) AS qtd
                FROM transacoes t
                WHERE t.status = 'EFETIVADA'
                  AND t.data_transacao >= $2
                  AND t.data_transacao < $3 + INTERVAL '1 day'
                  AND (
                      t.conta_origem_id IN (SELECT id FROM contas WHERE cliente_id = $1)
                      OR t.conta_destino_id IN (SELECT id FROM contas WHERE cliente_id = $1)
                  )
                GROUP BY 1
            )
            SELECT
                m.mes AS mes_referencia,
                COALESCE(mv.creditos, 0) AS total_creditos,
                COALESCE(mv.debitos, 0) AS total_debitos,
                $4::numeric + COALESCE(mv.creditos, 0) - COALESCE(mv.debitos, 0) AS saldo_consolidado,
                COALESCE(mv.qtd, 0)::INT AS qtd_transacoes
            FROM meses m
            LEFT JOIN movimento mv ON mv.mes = m.mes
            ORDER BY m.mes
            """,
            p_cliente_id,
            p_data_inicio,
            p_data_fim,
            v_saldo_atual,
        )
        return [dict(row) for row in rows]
    except Exception:
        return [
            {
                "mes_referencia": await conn.fetchval(
                    "SELECT DATE_TRUNC('month', $1::date)::DATE", p_data_inicio
                ),
                "total_creditos": Decimal("0"),
                "total_debitos": Decimal("0"),
                "saldo_consolidado": v_saldo_atual,
                "qtd_transacoes": 0,
            }
        ]
