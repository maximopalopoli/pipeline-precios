"""Database connection helpers for the Streamlit dashboard."""

import os
from pathlib import Path

import pandas as pd
import psycopg2
import streamlit as st

# Load .env if present (so `streamlit run` picks up DB credentials)
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())


@st.cache_resource
def get_connection():
    """Return a cached psycopg2 connection."""
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


@st.cache_data(ttl=300)
def run_query(sql: str, params: tuple | None = None) -> pd.DataFrame:
    """Execute a read query and return a DataFrame (cached 5 min)."""
    conn = get_connection()
    try:
        return pd.read_sql_query(sql, conn, params=params)
    except Exception:
        # Reconnect on stale/broken connection
        conn.reset()
        return pd.read_sql_query(sql, conn, params=params)
