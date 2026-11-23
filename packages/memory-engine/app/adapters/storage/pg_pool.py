"""
PostgreSQL + pgvector connection pool manager.
Provides clean asyncpg pool lifecycle with pgvector type registration.
"""
from __future__ import annotations
import asyncpg
from pgvector.asyncpg import register_vector


class PostgresPoolManager:
    """
    Manages asyncpg connection pool lifecycle with pgvector registration.
    Replaces legacy PostgresStorageAdapter for the core memory engine stack.
    """
    def __init__(self, dsn: str, pool_min: int = 2, pool_max: int = 10):
        self._dsn = dsn
        self._pool_min = pool_min
        self._pool_max = pool_max
        self._pool: asyncpg.Pool | None = None

    @property
    def pool(self) -> asyncpg.Pool:
        if not self._pool:
            raise RuntimeError("Postgres pool is not connected")
        return self._pool

    async def connect(self) -> None:
        async def init_connection(conn: asyncpg.Connection):
            await register_vector(conn)

        self._pool = await asyncpg.create_pool(
            dsn=self._dsn,
            min_size=self._pool_min,
            max_size=self._pool_max,
            init=init_connection,
        )

    async def disconnect(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None
