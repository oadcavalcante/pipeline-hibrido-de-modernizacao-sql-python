"""Utilitários compartilhados para testes comportamentais com PostgreSQL."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg

SQL_DIR = Path(__file__).resolve().parents[1] / "sql"
PROCEDURES = SQL_DIR / "procedures"


async def reset_schema(conn: asyncpg.Connection) -> None:
    await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")


async def load_base_schema(conn: asyncpg.Connection) -> None:
    schema_sql = (SQL_DIR / "00_schema_legado.sql").read_text(encoding="utf-8")
    await conn.execute(schema_sql)


async def load_procedures(conn: asyncpg.Connection, *names: str) -> None:
    for name in names:
        sql = (PROCEDURES / name).read_text(encoding="utf-8")
        await conn.execute(sql)


async def snapshot_state(conn: asyncpg.Connection) -> dict[str, Any]:
    contas = await conn.fetch(
        "SELECT id, saldo, status FROM contas ORDER BY id"
    )
    transacoes = await conn.fetch(
        """
        SELECT id, conta_origem_id, conta_destino_id, tipo, valor, status,
               DATE(data_transacao) AS data_ref
        FROM transacoes ORDER BY id
        """
    )
    logs = await conn.fetch(
        "SELECT entidade, acao, detalhes FROM log_auditoria ORDER BY id"
    )
    return {
        "contas": [dict(r) for r in contas],
        "transacoes": [dict(r) for r in transacoes],
        "logs": [dict(r) for r in logs],
    }


def _normalize_json_numbers(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {k: _normalize_json_numbers(v) for k, v in payload.items()}
    if isinstance(payload, list):
        return [_normalize_json_numbers(v) for v in payload]
    if isinstance(payload, (int, float, Decimal)):
        return f"{Decimal(str(payload)).quantize(Decimal('0.01')):.2f}"
    return payload


def normalize_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Converte Decimals/JSON para comparação estável entre SQL e Python."""
    def _norm(value: Any) -> Any:
        if isinstance(value, Decimal):
            return f"{value.quantize(Decimal('0.01')):.2f}"
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, dict):
            return {k: _norm(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_norm(v) for v in value]
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return value
            return json.dumps(_normalize_json_numbers(parsed), sort_keys=True)
        return value

    normalized = _norm(snapshot)
    if isinstance(normalized, dict) and "logs" in normalized:
        for log in normalized["logs"]:
            if isinstance(log.get("detalhes"), str):
                log["detalhes"] = json.dumps(
                    _normalize_json_numbers(json.loads(log["detalhes"])),
                    sort_keys=True,
                )
    return normalized


async def call_sp_atualizar_status_contas_inativas(
    conn: asyncpg.Connection, p_dias: int
) -> int:
    return int(
        await conn.fetchval(
            "SELECT _wrap_sp_atualizar_status_contas_inativas($1)",
            p_dias,
        )
    )


async def install_procedure_wrappers(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE OR REPLACE FUNCTION _wrap_sp_atualizar_status_contas_inativas(p_dias INT)
        RETURNS INT
        LANGUAGE plpgsql
        AS $$
        DECLARE
            v_afetadas INT;
        BEGIN
            CALL sp_atualizar_status_contas_inativas(p_dias, v_afetadas);
            RETURN v_afetadas;
        END;
        $$;
        """
    )
