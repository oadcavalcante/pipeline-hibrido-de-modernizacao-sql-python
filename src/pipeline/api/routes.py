"""Rotas FastAPI da pipeline de modernização."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from pipeline.api.schemas import (
    HealthResponse,
    MetricsResponse,
    ModernizeRequest,
    ModernizeResponse,
    PipelineReport,
)
from pipeline.config import settings
from pipeline.db.repository import ModernizationRepository
from pipeline.graph.graph import build_graph
from pipeline.graph.state import PipelineState
from pipeline.observability.tracing import TraceManager

router = APIRouter()


# ─────────────────────────── Dependências ────────────────────────────────────

async def get_repository() -> ModernizationRepository:
    from pipeline.db.session import AsyncSessionFactory
    return ModernizationRepository(AsyncSessionFactory)


async def get_trace_manager() -> TraceManager:
    from pipeline.observability.tracing import get_trace_manager as _get
    return _get()


# ─────────────────────────── Endpoints ───────────────────────────────────────

STUDIO_URL = "https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:8000"


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def home(request: Request) -> str:
    """Página inicial com links úteis (use http://127.0.0.1:8000 no browser)."""
    base = str(request.base_url).rstrip("/")
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>SQL Modernizer</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 42rem; margin: 2rem auto; padding: 0 1rem; line-height: 1.5; color: #1a1a1a; }}
    h1 {{ font-size: 1.5rem; }}
    p {{ color: #444; }}
    ul {{ padding-left: 1.25rem; }}
    a {{ color: #2563eb; }}
    code {{ background: #f4f4f5; padding: 0.1rem 0.35rem; border-radius: 4px; font-size: 0.9em; }}
    .note {{ background: #fef9c3; border-left: 4px solid #eab308; padding: 0.75rem 1rem; margin: 1rem 0; font-size: 0.9rem; }}
  </style>
</head>
<body>
  <h1>SQL Modernizer</h1>
  <p>Pipeline híbrido (LLM + regras) para modernizar stored procedures
     <strong>PL/pgSQL → Python 3.14</strong>, orquestrado com LangGraph.</p>
  <div class="note">
    No terminal o LangGraph pode mostrar <code>0.0.0.0:8000</code> (bind do servidor).
    No browser use <code>127.0.0.1</code> — o LangSmith Studio não aceita <code>0.0.0.0</code>.
  </div>
  <h2>Links</h2>
  <ul>
    <li><a href="{base}/docs">API Docs (Swagger)</a> — <code>POST /modernize</code></li>
    <li><a href="{base}/health">Health</a></li>
    <li><a href="{base}/metrics">Métricas</a></li>
    <li><a href="{STUDIO_URL}">LangGraph Studio</a> (grafo visual)</li>
    <li><a href="http://localhost:3000">Langfuse</a> (observabilidade)</li>
  </ul>
  <h2>Pipeline</h2>
  <p><code>parse → analyze → generate → validate → persist</code></p>
</body>
</html>"""


@router.get("/health", response_model=HealthResponse, tags=["sistema"])
async def health(
    repo: ModernizationRepository = Depends(get_repository),
) -> HealthResponse:
    """Verifica o status do pipeline."""
    db_ok = await repo.ping()
    return HealthResponse(
        status="ok",
        version="0.1.0",
        llm_model=settings.default_llm_model,
        db_connected=db_ok,
    )


@router.post(
    "/modernize",
    response_model=ModernizeResponse,
    status_code=status.HTTP_200_OK,
    tags=["pipeline"],
)
async def modernize(
    request: ModernizeRequest,
    repo: ModernizationRepository = Depends(get_repository),
    tracer: TraceManager = Depends(get_trace_manager),
) -> ModernizeResponse:
    """
    Moderniza uma stored procedure PL/pgSQL para Python 3.14.

    Executa o pipeline híbrido:
    1. Parsing → representação estruturada
    2. Análise semântica → pontos de risco
    3. Geração → código Python via LLM
    4. Validação → ast.parse + ruff
    """
    request_id = str(uuid.uuid4())
    trace = tracer.start_trace(
        request_id=request_id,
        source_code=request.source_code,
        model=request.model,
    )

    graph = build_graph(repository=repo)

    initial_state: PipelineState = {
        "source_code": request.source_code,
        "schema_context": request.schema_context,
        "llm_model": request.model,
        "request_id": request_id,
        "trace_id": request_id,
        "langfuse_callbacks": tracer.get_langchain_callback(trace, trace_id=request_id),
        "status": "running",
        "error": None,
        "parsed_procedure": None,
        "parsing_report": {},
        "parsing_errors": [],
        "semantic_analysis": None,
        "analysis_report": {},
        "generated_code": None,
        "generation_report": {},
        "validation_passed": False,
        "validation_report": {},
    }

    try:
        final_state: dict[str, Any] = await graph.ainvoke(initial_state)
    except Exception as exc:
        tracer.end_trace(trace, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro interno no pipeline. Consulte os logs para detalhes.",
        ) from exc

    tracer.end_trace(
        trace,
        status=final_state.get("status", "failure"),
        quality_score=final_state.get("validation_report", {}).get("quality_score"),
    )

    # Busca o registro persistido para obter o ID real do banco
    record = await repo.get_by_request_id(request_id)

    return ModernizeResponse(
        id=record.id if record else uuid.uuid4(),
        status=final_state.get("status", "failure"),
        generated_code=final_state.get("generated_code"),
        report=PipelineReport(
            parsing=final_state.get("parsing_report", {}),
            analysis=final_state.get("analysis_report", {}),
            generation=final_state.get("generation_report", {}),
            validation=final_state.get("validation_report", {}),
        ),
        created_at=record.created_at if record else datetime.now(tz=UTC),
    )


@router.get("/metrics", response_model=MetricsResponse, tags=["avaliação"])
async def metrics(
    repo: ModernizationRepository = Depends(get_repository),
) -> MetricsResponse:
    """
    Métricas de avaliação automática do pipeline.

    Calcula sobre todas as execuções persistidas:
    - Taxa de sucesso / parcial / falha
    - Taxa de código que passa em ast.parse
    - Score médio de qualidade
    - Duração média
    """
    stats = await repo.compute_metrics()
    return MetricsResponse(**stats)
