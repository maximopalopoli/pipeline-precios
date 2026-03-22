"""Screen 2 — Price Evolution."""

import plotly.express as px
import streamlit as st

from dashboard.db import run_query
from dashboard.filters import (
    COLOR_PRECIO_LISTA,
    COLOR_PRECIO_PROMO,
    product_search,
    render_global_filters,
)

st.set_page_config(page_title="Evolucion de Precios", layout="wide")
st.title("Evolucion de Precios")

gf = render_global_filters()

# ── 1. Product search ───────────────────────────────────────────────────────

producto_sk = product_search()
if producto_sk is None:
    st.stop()

# ── 2. Price line chart (list vs promo) ─────────────────────────────────────

st.header("Precio de lista vs mejor precio promo")

# Build optional province filter from global filters
prov_clause = ""
params: list = [producto_sk]
if gf["provincias"]:
    prov_ph = ", ".join(["%s"] * len(gf["provincias"]))
    prov_clause = f"AND pr.nombre IN ({prov_ph})"
    params.extend(gf["provincias"])

df_prices = run_query(
    f"""
    SELECT
        f.fecha,
        a.precio_lista_avg,
        a.precio_promo_avg,
        a.cantidad_obs,
        pr.nombre AS provincia
    FROM agg_precios_diarios a
    JOIN dim_fecha     f  ON a.fecha_id     = f.fecha_id
    JOIN dim_provincia pr ON a.provincia_sk  = pr.provincia_sk
    WHERE a.producto_sk = %s
    {prov_clause}
    ORDER BY f.fecha, pr.nombre
    """,
    tuple(params),
)

if df_prices.empty:
    st.warning("No hay datos de precios para este producto.")
    st.stop()

# National average (all provinces)
df_national = (
    df_prices.groupby("fecha", as_index=False)
    .agg(
        precio_lista=("precio_lista_avg", "mean"),
        precio_promo=("precio_promo_avg", "mean"),
    )
)

fig_national = px.line(
    df_national,
    x="fecha",
    y=["precio_lista", "precio_promo"],
    labels={"fecha": "Fecha", "value": "Precio ($)", "variable": "Tipo"},
    color_discrete_map={
        "precio_lista": COLOR_PRECIO_LISTA,
        "precio_promo": COLOR_PRECIO_PROMO,
    },
)
fig_national.update_layout(legend_title_text="")
st.plotly_chart(fig_national, use_container_width=True)

# ── 3. Breakdown by province ────────────────────────────────────────────────

st.header("Desglose por provincia")

provinces = sorted(df_prices["provincia"].unique())
selected_provinces = st.multiselect(
    "Provincia",
    options=provinces,
    default=provinces[:5],
    key="evol_provincias",
)

if not selected_provinces:
    st.info("Selecciona al menos una provincia.")
    st.stop()

df_filtered = df_prices[df_prices["provincia"].isin(selected_provinces)]

fig_prov = px.line(
    df_filtered,
    x="fecha",
    y="precio_lista_avg",
    color="provincia",
    labels={
        "fecha": "Fecha",
        "precio_lista_avg": "Precio lista prom. ($)",
        "provincia": "Provincia",
    },
    markers=True,
)
st.plotly_chart(fig_prov, use_container_width=True)
