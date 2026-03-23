"""Screen 2 — Price Evolution: track a product's price over time."""

from datetime import timedelta

import plotly.express as px
import streamlit as st

from dashboard.db import run_query
from dashboard.filters import (
    COLOR_PRECIO_LISTA,
    COLOR_PRECIO_PROMO,
    product_search,
    render_consumer_filters,
)

st.set_page_config(page_title="Evolucion de Precios", layout="wide")
st.title("Evolucion de Precios")

cf = render_consumer_filters()

# ── 1. Product search ────────────────────────────────────────────────────────

producto_sk = product_search()
if producto_sk is None:
    st.stop()

# ── 2. Query price history ───────────────────────────────────────────────────

where_clauses: list[str] = []
params: list = [producto_sk]

if cf["provincia"]:
    where_clauses.append("pr.nombre = %s")
    params.append(cf["provincia"])

extra_where = ""
if where_clauses:
    extra_where = "AND " + " AND ".join(where_clauses)

df_prices = run_query(
    f"""
    SELECT
        f.fecha,
        ROUND(AVG(a.precio_lista_avg), 2) AS precio_lista,
        ROUND(AVG(a.precio_promo_avg), 2) AS precio_promo,
        SUM(a.cantidad_obs)               AS observaciones
    FROM agg_precios_diarios a
    JOIN dim_fecha     f  ON a.fecha_id     = f.fecha_id
    JOIN dim_provincia pr ON a.provincia_sk  = pr.provincia_sk
    WHERE a.producto_sk = %s
    {extra_where}
    GROUP BY f.fecha
    ORDER BY f.fecha
    """,
    tuple(params),
)

if df_prices.empty:
    st.warning("No hay datos de precios para este producto y filtros.")
    st.stop()

# ── 3. Price delta (current vs 7 days ago) ───────────────────────────────────

current_price = df_prices.iloc[-1]["precio_lista"]
week_ago = df_prices[df_prices["fecha"] <= df_prices.iloc[-1]["fecha"] - timedelta(days=7)]

c1, c2, c3 = st.columns(3)
c1.metric("Precio actual (lista)", f"${current_price:,.2f}")

if not week_ago.empty:
    prev_price = week_ago.iloc[-1]["precio_lista"]
    delta = current_price - prev_price
    delta_pct = (delta / prev_price * 100) if prev_price else 0
    c2.metric(
        "Hace 7 dias",
        f"${prev_price:,.2f}",
        f"{delta_pct:+.1f}%",
        delta_color="inverse",
    )

current_promo = df_prices.iloc[-1]["precio_promo"]
if current_promo and current_promo < current_price:
    c3.metric("Precio promo actual", f"${current_promo:,.2f}")

# ── 4. Price chart ───────────────────────────────────────────────────────────

st.header("Precio de lista vs mejor promo")

scope_label = cf["provincia"] if cf["provincia"] else "Nacional"
st.caption(f"Promedio — {scope_label}")

fig = px.line(
    df_prices,
    x="fecha",
    y=["precio_lista", "precio_promo"],
    labels={"fecha": "Fecha", "value": "Precio ($)", "variable": "Tipo"},
    color_discrete_map={
        "precio_lista": COLOR_PRECIO_LISTA,
        "precio_promo": COLOR_PRECIO_PROMO,
    },
)
fig.update_layout(legend_title_text="")
st.plotly_chart(fig, use_container_width=True)
