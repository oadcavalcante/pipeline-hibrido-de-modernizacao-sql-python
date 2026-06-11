"""Repositório para modernization_history."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pipeline.db.models import ModernizationHistory


class ModernizationRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    async def ping(self) -> bool:
        """Verifica conectividade com o banco."""
        try:
            async with self._factory() as session:
                await session.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    async def save(
        self,
        *,
        request_id: str,
        source_code: str,
        generated_code: str | None,
        report: dict[str, Any],
        status: str,
    ) -> ModernizationHistory:
        async with self._factory() as session, session.begin():
            record = ModernizationHistory(
                id=uuid.uuid4(),
                request_id=request_id,
                source_code=source_code,
                generated_code=generated_code,
                report=report,
                status=status,
                created_at=datetime.now(tz=UTC),
            )
            session.add(record)
        return record

    async def get_by_request_id(self, request_id: str) -> ModernizationHistory | None:
        async with self._factory() as session:
            result = await session.execute(
                select(ModernizationHistory).where(
                    ModernizationHistory.request_id == request_id
                )
            )
            return result.scalar_one_or_none()

    async def list_all(self, limit: int = 100) -> list[ModernizationHistory]:
        async with self._factory() as session:
            result = await session.execute(
                select(ModernizationHistory)
                .order_by(ModernizationHistory.created_at.desc())
                .limit(limit)
            )
            return list(result.scalars().all())

    async def compute_metrics(self) -> dict[str, Any]:
        """
        Calcula métricas de avaliação sobre todas as execuções.
        """
        async with self._factory() as session:
            # Contagens por status
            counts_q = await session.execute(
                select(
                    ModernizationHistory.status,
                    func.count().label("n"),
                ).group_by(ModernizationHistory.status)
            )
            counts: dict[str, int] = {row.status: row.n for row in counts_q}

            total = sum(counts.values()) or 1  # evita divisão por zero

            # Scores de qualidade extraídos do JSONB
            scores_q = await session.execute(
                text(
                    "SELECT "
                    "  report->'validation'->>'ast_valid' AS ast_valid, "
                    "  (report->'validation'->>'quality_score')::float AS quality_score, "
                    "  (report->'generation'->>'duration_ms')::float AS gen_ms, "
                    "  (report->'parsing'->>'duration_ms')::float AS parse_ms, "
                    "  status, "
                    "  request_id "
                    "FROM modernization_history "
                    "ORDER BY created_at DESC LIMIT 200"
                )
            )
            rows = scores_q.fetchall()

            ast_valid_count = sum(1 for r in rows if r.ast_valid == "true")
            quality_scores = [r.quality_score for r in rows if r.quality_score is not None]
            durations = [
                (r.gen_ms or 0) + (r.parse_ms or 0) for r in rows
            ]

            procedures_evaluated = [
                {
                    "request_id": r.request_id,
                    "status": r.status,
                    "ast_valid": r.ast_valid == "true",
                    "quality_score": r.quality_score,
                }
                for r in rows[:20]
            ]

        n_rows = len(rows) or 1
        return {
            "total_executions": total,
            "success_rate": round(counts.get("success", 0) / total, 3),
            "partial_rate": round(counts.get("partial", 0) / total, 3),
            "failure_rate": round(counts.get("failure", 0) / total, 3),
            "ast_valid_rate": round(ast_valid_count / n_rows, 3),
            "avg_quality_score": round(sum(quality_scores) / len(quality_scores), 3) if quality_scores else 0.0,
            "avg_duration_ms": round(sum(durations) / len(durations), 1) if durations else 0.0,
            "procedures_evaluated": procedures_evaluated,
        }
