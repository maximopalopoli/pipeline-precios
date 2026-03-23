"""SEPA Pipeline Dashboard — main entry point with daily KPIs."""

import streamlit as st

from dashboard.db import run_query
from dashboard.filters import COLOR_BAJA, COLOR_SUBA

st.set_page_config(
    page_title="SEPA Precios",
    page_icon=":bar_chart:",
    layout="wide",
)

st.title("SEPA Precios")
st.markdown(
    "Comparador de precios minoristas del sistema SEPA "
    "de transparencia de precios de Argentina."
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
        f"{status_icon} Datos al: {row['fecha_datos']} — "
        f"actualizado: {row['started_at']:%Y-%m-%d %H:%M}"
    )

# ── KPI Row ──────────────────────────────────────────────────────────────────

st.header("Resumen del dia")

c1, c2, c3, c4 = st.columns(4)

# 1. Weekly average inflation
df_inflation = run_query("""
    SELECT ROUND(AVG(variacion_pct), 2) AS inflacion_semanal
    FROM v_variacion_precios
    WHERE fecha >= (SELECT MAX(fecha) FROM v_variacion_precios) - INTERVAL '7 days'
      AND variacion_pct IS NOT NULL
""")
if not df_inflation.empty and df_inflation.iloc[0]["inflacion_semanal"] is not None:
    val = df_inflation.iloc[0]["inflacion_semanal"]
    c1.metric("Variacion semanal prom.", f"{val:+.2f}%")
else:
    c1.metric("Variacion semanal prom.", "—")

# 2. Total products tracked
df_products = run_query("SELECT COUNT(*) AS n FROM dim_producto")
c2.metric("Productos", f"{df_products.iloc[0]['n']:,}" if not df_products.empty else "—")

# 3. Total stores
df_stores = run_query("SELECT COUNT(*) AS n FROM dim_sucursal")
c3.metric("Sucursales", f"{df_stores.iloc[0]['n']:,}" if not df_stores.empty else "—")

# 4. Promo coverage today
df_promo = run_query("""
    SELECT
        ROUND(100.0 * SUM(CASE WHEN f.tiene_promo THEN 1 ELSE 0 END)
              / NULLIF(COUNT(*), 0), 1) AS pct_promo
    FROM fact_precios f
    JOIN dim_fecha fe ON f.fecha_id = fe.fecha_id
    WHERE fe.fecha = (
        SELECT MAX(fecha) FROM dim_fecha
        JOIN agg_precios_diarios USING (fecha_id)
    )
""")
if not df_promo.empty and df_promo.iloc[0]["pct_promo"] is not None:
    c4.metric("Productos en promo", f"{df_promo.iloc[0]['pct_promo']:.1f}%")
else:
    c4.metric("Productos en promo", "—")

# ── Top increases / decreases ────────────────────────────────────────────────

col_up, col_down = st.columns(2)

df_top_subas = run_query("""
    SELECT productos_descripcion AS producto, provincia,
           ROUND(variacion_pct, 1) AS variacion
    FROM v_variacion_precios
    WHERE variacion_pct IS NOT NULL
      AND fecha = (SELECT MAX(fecha) FROM v_variacion_precios)
    ORDER BY variacion_pct DESC
    LIMIT 5
""")

df_top_bajas = run_query("""
    SELECT productos_descripcion AS producto, provincia,
           ROUND(variacion_pct, 1) AS variacion
    FROM v_variacion_precios
    WHERE variacion_pct IS NOT NULL
      AND fecha = (SELECT MAX(fecha) FROM v_variacion_precios)
    ORDER BY variacion_pct ASC
    LIMIT 5
""")

with col_up:
    st.subheader("Mayores subas")
    if not df_top_subas.empty:
        for _, r in df_top_subas.iterrows():
            st.markdown(
                f"<span style='color:{COLOR_SUBA}'>**+{r['variacion']:.1f}%**</span> "
                f"{r['producto'][:50]} ({r['provincia'][:10]})",
                unsafe_allow_html=True,
            )
    else:
        st.info("Sin datos.")

with col_down:
    st.subheader("Mayores bajas")
    if not df_top_bajas.empty:
        for _, r in df_top_bajas.iterrows():
            st.markdown(
                f"<span style='color:{COLOR_BAJA}'>**{r['variacion']:.1f}%**</span> "
                f"{r['producto'][:50]} ({r['provincia'][:10]})",
                unsafe_allow_html=True,
            )
    else:
        st.info("Sin datos.")

# ── Best promo chain ─────────────────────────────────────────────────────────

df_best_chain = run_query("""
    SELECT
        c.comercio_bandera_nombre AS cadena,
        ROUND(100.0 * SUM(CASE WHEN f.tiene_promo THEN 1 ELSE 0 END)
              / NULLIF(COUNT(*), 0), 1) AS pct_promo
    FROM fact_precios f
    JOIN dim_fecha    fe ON f.fecha_id    = fe.fecha_id
    JOIN dim_sucursal s  ON f.sucursal_sk = s.sucursal_sk
    JOIN dim_comercio c  ON s.comercio_sk = c.comercio_sk
    WHERE fe.fecha = (
        SELECT MAX(fecha) FROM dim_fecha
        JOIN agg_precios_diarios USING (fecha_id)
    )
    GROUP BY c.comercio_bandera_nombre
    ORDER BY pct_promo DESC
    LIMIT 1
""")

if not df_best_chain.empty:
    chain = df_best_chain.iloc[0]
    st.success(
        f"**Cadena con mas promos hoy:** {chain['cadena']} "
        f"({chain['pct_promo']:.1f}% de sus productos en promocion)"
    )

# ── Quick links ──────────────────────────────────────────────────────────────

st.divider()
st.header("Explorar")

col1, col2 = st.columns(2)

with col1:
    st.page_link("pages/1_buscador.py", label="Buscador de Precios", icon="🔍")
    st.caption("Busca un producto y compara precios entre locales.")

    st.page_link("pages/2_evolucion.py", label="Evolucion de Precios", icon="📈")
    st.caption("Seguimiento del precio de un producto a lo largo del tiempo.")

    st.page_link("pages/5_locales.py", label="Locales Cercanos", icon="📍")
    st.caption("Encuentra supermercados cerca tuyo y ve sus ofertas.")

with col2:
    st.page_link("pages/3_variaciones.py", label="Variaciones de Precios", icon="🔀")
    st.caption("Productos con mayor cambio de precio en los ultimos 7 dias.")

    st.page_link("pages/4_ofertas.py", label="Ofertas del dia", icon="🏷️")
    st.caption("Los mejores descuentos y promociones vigentes hoy.")
