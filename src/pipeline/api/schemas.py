"""Schemas Pydantic para request/response da API."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ModernizeRequest(BaseModel):
    source_code: str = Field(..., description="Stored procedure PL/pgSQL a ser modernizada.")
    schema_context: str | None = Field(
        default=None,
        description="DDL das tabelas referenciadas (opcional, enriquece o contexto do LLM).",
    )
    model: str = Field(
        default="gpt-5.4-mini",
        description="Modelo LLM a usar. Ex: gpt-5.4-mini, gpt-5.4, claude-3-5-sonnet-20241022.",
    )


class NodeReport(BaseModel):
    status: str
    duration_ms: float | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class PipelineReport(BaseModel):
    parsing: dict[str, Any] = Field(default_factory=dict)
    analysis: dict[str, Any] = Field(default_factory=dict)
    generation: dict[str, Any] = Field(default_factory=dict)
    validation: dict[str, Any] = Field(default_factory=dict)


class ModernizeResponse(BaseModel):
    id: UUID
    status: str = Field(..., description="success | partial | failure")
    generated_code: str | None = None
    report: PipelineReport
    created_at: datetime


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.1.0"
    llm_model: str
    db_connected: bool


class MetricsResponse(BaseModel):
    """Métricas de avaliação do pipeline."""
    total_executions: int
    success_rate: float
    partial_rate: float
    failure_rate: float
    avg_quality_score: float
    ast_valid_rate: float
    avg_duration_ms: float
    procedures_evaluated: list[dict[str, Any]]
