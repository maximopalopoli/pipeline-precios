"""Database connection helpers for the Streamlit dashboard."""

import os

import pandas as pd
import psycopg2
import streamlit as st


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


def run_query(sql: str, params: tuple | None = None) -> pd.DataFrame:
    """Execute a read query and return a DataFrame."""
    conn = get_connection()
    try:
        return pd.read_sql_query(sql, conn, params=params)
    except Exception:
        # Reconnect on stale/broken connection
        conn.reset()
        return pd.read_sql_query(sql, conn, params=params)
