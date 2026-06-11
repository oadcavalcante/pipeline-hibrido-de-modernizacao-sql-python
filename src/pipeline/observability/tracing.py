"""
Observabilidade com Langfuse.

Integra rastreamento de execução da pipeline:
- Um trace por execução de /modernize
- Um span por nó do grafo (parse, analyze, generate, validate)
- Score de qualidade registrado no trace
- Custo e latência das chamadas de LLM (via callbacks LangChain)

Compatível com Langfuse Python SDK v2.x + servidor self-hosted v2.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from pipeline.config import settings

logger = logging.getLogger(__name__)

_langfuse_instance: Any = None


def _get_langfuse() -> Any:
    global _langfuse_instance
    if _langfuse_instance is not None:
        return _langfuse_instance
    if not settings.langfuse_active:
        logger.info("Langfuse desabilitado (chaves ausentes ou LANGFUSE_ENABLED=false)")
        return None
    try:
        from langfuse import Langfuse  # type: ignore[import]

        _langfuse_instance = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
        logger.info("Langfuse conectado em %s", settings.langfuse_host)
    except Exception as exc:
        logger.warning("Langfuse não disponível: %s", exc)
        _langfuse_instance = None
    return _langfuse_instance


class TraceManager:
    """Gerencia traces e spans Langfuse para uma execução da pipeline."""

    def __init__(self) -> None:
        self._lf = _get_langfuse()

    def start_trace(
        self, *, request_id: str, source_code: str, model: str
    ) -> Any | None:
        if not self._lf:
            return None
        try:
            trace = self._lf.trace(
                id=request_id,
                name="sql_modernization_pipeline",
                input={
                    "source_code_preview": source_code[:300],
                    "model": model,
                },
                metadata={"pipeline_version": "0.1.0"},
            )
            logger.debug("Langfuse trace iniciado: %s", request_id)
            return trace
        except Exception as exc:
            logger.warning("Langfuse start_trace falhou: %s", exc)
            return None

    def end_trace(
        self,
        trace: Any | None,
        *,
        status: str = "success",
        quality_score: float | None = None,
        error: str | None = None,
    ) -> None:
        if not trace:
            return
        try:
            trace.update(
                output={"status": status, "error": error},
                level="ERROR" if error else "DEFAULT",
            )
            if quality_score is not None:
                trace.score(
                    name="quality_score",
                    value=quality_score,
                    comment="Pontuação composta: ast_valid + ruff + type_hints + docstrings",
                )
            if self._lf:
                self._lf.flush()
            logger.debug("Langfuse trace finalizado: %s", getattr(trace, "id", "?"))
        except Exception as exc:
            logger.warning("Langfuse end_trace falhou: %s", exc)

    def span(self, trace: Any | None, *, name: str, input_data: dict) -> Any | None:
        if not trace:
            return None
        try:
            return trace.span(name=name, input=input_data)
        except Exception as exc:
            logger.warning("Langfuse span falhou (%s): %s", name, exc)
            return None

    def span_by_id(
        self, trace_id: str | None, *, name: str, input_data: dict
    ) -> Any | None:
        """Cria span a partir do trace_id (mesmo id usado em start_trace)."""
        if not self._lf or not trace_id:
            return None
        try:
            trace = self._lf.trace(id=trace_id)
            return trace.span(name=name, input=input_data)
        except Exception as exc:
            logger.warning("Langfuse span_by_id falhou (%s): %s", name, exc)
            return None

    def end_span(self, span: Any | None, *, output: dict, error: str | None = None) -> None:
        if not span:
            return
        with contextlib.suppress(Exception):
            span.end(
                output=output,
                level="ERROR" if error else "DEFAULT",
            )

    def get_langchain_callback(
        self, trace: Any | None = None, *, trace_id: str | None = None
    ) -> list:
        """Retorna callbacks LangChain para rastrear chamadas do LLM no Langfuse."""
        resolved_id = trace_id or (getattr(trace, "id", None) if trace else None)
        if not resolved_id or not self._lf:
            return []
        try:
            from langfuse.callback import CallbackHandler  # type: ignore[import]

            handler = CallbackHandler(
                trace_id=resolved_id,
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                host=settings.langfuse_host,
            )
            return [handler]
        except Exception as exc:
            logger.warning("Langfuse callback indisponível: %s", exc)
            return []


_trace_manager: TraceManager | None = None


def get_trace_manager() -> TraceManager:
    global _trace_manager
    if _trace_manager is None:
        _trace_manager = TraceManager()
    return _trace_manager
