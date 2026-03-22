"""SEPA Pipeline Dashboard — main entry point."""

import streamlit as st

from dashboard.db import run_query

st.set_page_config(
    page_title="SEPA Pipeline",
    page_icon=":bar_chart:",
    layout="wide",
)

st.title("SEPA Pipeline Dashboard")
st.markdown(
    "Monitoreo del pipeline ETL y explorador de precios minoristas "
    "del sistema SEPA de transparencia de precios de Argentina."
)

# ── Last ingestion status ────────────────────────────────────────────────────

df_last = run_query("""
    SELECT ir.status, ir.started_at, rf.fecha_datos
    FROM ingestion_runs ir
    JOIN raw_files rf ON ir.raw_file_id = rf.id
    ORDER BY ir.started_at DESC
    LIMIT 1
""")

if not df_last.empty:
    row = df_last.iloc[0]
    status_icon = {"completed": "🟢", "running": "🟡", "failed": "🔴"}.get(
        row["status"], "⚪"
    )
    st.caption(
        f"{status_icon} Ultima ingesta: {row['fecha_datos']} — "
        f"estado: {row['status']} — {row['started_at']:%Y-%m-%d %H:%M}"
    )

# ── Page directory ───────────────────────────────────────────────────────────

st.header("Pantallas")

col1, col2 = st.columns(2)

with col1:
    st.page_link("pages/1_salud.py", label="Salud del Pipeline", icon="🏥")
    st.caption("Estado de ingestas, filas cargadas/rechazadas, alertas de calidad.")

    st.page_link("pages/2_evolucion.py", label="Evolucion de Precios", icon="📈")
    st.caption("Precio de lista vs promo a lo largo del tiempo, desglose por provincia.")

    st.page_link("pages/3_mapa.py", label="Mapa de Precios", icon="🗺️")
    st.caption("Mapa geografico de precios por sucursal con filtros por tipo y promo.")

with col2:
    st.page_link("pages/4_comparador.py", label="Comparador por Cadena", icon="⚖️")
    st.caption("Tabla comparativa de precios entre cadenas para un producto dado.")

    st.page_link("pages/5_variaciones.py", label="Ranking de Variaciones", icon="🔀")
    st.caption("Top productos con mayor variacion de precio en los ultimos 7 dias.")

    st.page_link("pages/6_promos.py", label="Analisis de Promociones", icon="🏷️")
    st.caption("Cobertura de promos por cadena, descuentos efectivos y leyendas.")
