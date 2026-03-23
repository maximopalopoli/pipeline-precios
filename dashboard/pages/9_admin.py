"""Screen Admin — Pipeline Health (internal use)."""

import plotly.express as px
import streamlit as st

from dashboard.db import run_query
from dashboard.filters import (
    COLOR_REJECTED,
    COLOR_VALID,
    render_global_filters,
)

st.set_page_config(page_title="Salud del Pipeline", layout="wide")
st.title("Salud del Pipeline")

gf = render_global_filters()

# ── 1. Last ingestion runs ──────────────────────────────────────────────────

st.header("Estado de las ultimas ingestas")

df_runs = run_query("""
    SELECT
        ir.id        AS run_id,
        rf.filename,
        rf.fecha_datos,
        ir.status,
        ir.rows_valid,
        ir.rows_rejected,
        ir.rows_loaded,
        ir.duration_seconds,
        ir.started_at,
        ir.error_message
    FROM ingestion_runs ir
    JOIN raw_files rf ON ir.raw_file_id = rf.id
    ORDER BY ir.started_at DESC
    LIMIT 20
""")

if df_runs.empty:
    st.info("No hay corridas registradas todavia.")
else:
    def _status_color(status: str) -> str:
        colors = {"completed": "🟢", "running": "🟡", "failed": "🔴"}
        return f"{colors.get(status, '⚪')} {status}"

    df_display = df_runs.copy()
    df_display["status"] = df_display["status"].apply(_status_color)
    st.dataframe(
        df_display,
        column_config={
            "run_id": st.column_config.NumberColumn("Run"),
            "fecha_datos": st.column_config.DateColumn("Fecha datos"),
            "duration_seconds": st.column_config.NumberColumn(
                "Duracion (s)", format="%.1f"
            ),
        },
        use_container_width=True,
        hide_index=True,
    )

# ── 2. Loaded vs rejected rows per day ──────────────────────────────────────

st.header("Filas cargadas vs rechazadas por dia")

df_daily = run_query("""
    SELECT
        rf.fecha_datos,
        SUM(ir.rows_valid)    AS valid,
        SUM(ir.rows_rejected) AS rejected
    FROM ingestion_runs ir
    JOIN raw_files rf ON ir.raw_file_id = rf.id
    WHERE ir.status = 'completed'
    GROUP BY rf.fecha_datos
    ORDER BY rf.fecha_datos
""")

if df_daily.empty:
    st.info("Sin datos para mostrar.")
else:
    df_melted = df_daily.melt(
        id_vars="fecha_datos",
        value_vars=["valid", "rejected"],
        var_name="tipo",
        value_name="filas",
    )
    fig_bars = px.bar(
        df_melted,
        x="fecha_datos",
        y="filas",
        color="tipo",
        barmode="group",
        color_discrete_map={"valid": COLOR_VALID, "rejected": COLOR_REJECTED},
        labels={"fecha_datos": "Fecha", "filas": "Filas", "tipo": "Tipo"},
    )
    st.plotly_chart(fig_bars, use_container_width=True)

# ── 3. Quality-check alerts ─────────────────────────────────────────────────

st.header("Alertas de quality checks fallidos")

df_rejected = run_query("""
    SELECT
        run_id,
        rejection_reason,
        COUNT(*) AS count
    FROM rejected_rows
    WHERE run_id IN (
        SELECT id FROM ingestion_runs ORDER BY started_at DESC LIMIT 10
    )
    GROUP BY run_id, rejection_reason
    ORDER BY run_id DESC, count DESC
""")

if df_rejected.empty:
    st.info("No hay filas rechazadas en las ultimas corridas.")
else:
    for run_id, group in df_rejected.groupby("run_id", sort=False):
        with st.expander(f"Run {run_id} — {group['count'].sum()} rechazos"):
            st.dataframe(
                group[["rejection_reason", "count"]].reset_index(drop=True),
                use_container_width=True,
                hide_index=True,
            )

# ── 4. Execution time per run ───────────────────────────────────────────────

st.header("Tiempo de ejecucion por corrida")

df_duration = run_query("""
    SELECT
        rf.fecha_datos,
        ir.duration_seconds
    FROM ingestion_runs ir
    JOIN raw_files rf ON ir.raw_file_id = rf.id
    WHERE ir.status = 'completed'
      AND ir.duration_seconds IS NOT NULL
    ORDER BY rf.fecha_datos
""")

if df_duration.empty:
    st.info("Sin datos de duracion.")
else:
    fig_duration = px.line(
        df_duration,
        x="fecha_datos",
        y="duration_seconds",
        labels={"fecha_datos": "Fecha", "duration_seconds": "Duracion (s)"},
        markers=True,
    )
    st.plotly_chart(fig_duration, use_container_width=True)
