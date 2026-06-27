"""Асинхронное подключение к PostgreSQL через SQLAlchemy 2.0."""
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings

settings = get_settings()

engine = create_async_engine(settings.database_url, pool_pre_ping=True, echo=False)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with SessionLocal() as session:
        yield session


_MIGRATIONS = [
    "ALTER TABLE signals ADD COLUMN IF NOT EXISTS grade VARCHAR(3) DEFAULT 'B'",
    "ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS avg_rr FLOAT DEFAULT 0",
    "ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS expected_value FLOAT DEFAULT 0",
]


async def init_db() -> None:
    from sqlalchemy import text
    from . import models  # noqa: F401  (регистрация моделей)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for stmt in _MIGRATIONS:  # простые идемпотентные миграции (до Alembic)
            try:
                await conn.execute(text(stmt))
            except Exception:  # noqa: BLE001
                pass
