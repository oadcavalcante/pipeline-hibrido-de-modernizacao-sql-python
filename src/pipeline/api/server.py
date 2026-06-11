"""Aplicação FastAPI principal."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from pipeline.api.routes import router
from pipeline.config import settings
from pipeline.db.session import init_db

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    logger.info("Inicializando banco de dados…")
    await init_db()
    logger.info("SQL Modernizer pronto. Docs em /docs")
    yield


app = FastAPI(
    title="SQL Modernizer",
    description=(
        "Pipeline híbrido (LLM + Rules) para modernizar stored procedures "
        "PL/pgSQL → Python 3.14, orquestrado com LangGraph."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


def main() -> None:
    import uvicorn
    uvicorn.run(
        "pipeline.api.server:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=True,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
