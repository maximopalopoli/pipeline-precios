"""Database connection pool for the FastAPI backend."""

import os
from pathlib import Path

import asyncpg

# Load .env if present
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            host=os.environ.get("POSTGRES_HOST", "localhost"),
            port=int(os.environ.get("POSTGRES_PORT", "5432")),
            database=os.environ["POSTGRES_DB"],
            user=os.environ["POSTGRES_USER"],
            password=os.environ["POSTGRES_PASSWORD"],
            min_size=2,
            max_size=10,
            # The API runs short queries; compiling one took about 0.7 s, more
            # than running it (a search's "from" prices: 0.84 s against 0.18 s).
            server_settings={"jit": "off"},
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def fetch(sql: str, *args) -> list[dict]:
    """Execute a query and return rows as list of dicts."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
        return [dict(r) for r in rows]


async def fetchrow(sql: str, *args) -> dict | None:
    """Execute a query and return a single row as dict."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, *args)
        return dict(row) if row else None


async def fetchval(sql: str, *args):
    """Execute a query and return a single value."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(sql, *args)
