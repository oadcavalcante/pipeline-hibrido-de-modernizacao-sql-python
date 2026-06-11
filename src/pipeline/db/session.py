"""Configuração da sessão assíncrona SQLAlchemy."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pipeline.config import settings

engine = create_async_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    echo=False,
)

AsyncSessionFactory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
)


async def init_db() -> None:
    """Cria tabelas se não existirem (idempotente)."""
    from pipeline.db.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
