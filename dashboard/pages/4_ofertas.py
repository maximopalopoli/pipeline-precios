"""Screen 4 — Ofertas del dia: top discounted products right now."""

import plotly.express as px
import streamlit as st

from dashboard.db import run_query
from dashboard.filters import best_price_expr, render_consumer_filters

st.set_page_config(page_title="Ofertas del dia", layout="wide")
st.title("Ofertas del dia")

cf = render_consumer_filters()

if cf["latest_fecha"] is None:
    st.warning("No hay datos de precios cargados.")
    st.stop()

st.caption(f"Promociones vigentes al {cf['latest_fecha']:%d/%m/%Y}")

# ── Build query filters ─────────────────────────────────────────────────────

mejor_precio = best_price_expr()

where_clauses: list[str] = []
params: list = [str(cf["latest_fecha"])]

if cf["provincia"]:
    where_clauses.append("pr.nombre = %s")
    params.append(cf["provincia"])

if cf["localidad"]:
    where_clauses.append("s.sucursales_localidad = %s")
    params.append(cf["localidad"])

if cf["cadenas"]:
    chain_ph = ", ".join(["%s"] * len(cf["cadenas"]))
    where_clauses.append(f"c.comercio_bandera_nombre IN ({chain_ph})")
    params.extend(cf["cadenas"])

extra_where = ""
if where_clauses:
    extra_where = "AND " + " AND ".join(where_clauses)

# ── Main query: top discounted products ──────────────────────────────────────

df_ofertas = run_query(
    f"""
    SELECT
        p.productos_descripcion AS producto,
        p.productos_marca       AS marca,
        c.comercio_bandera_nombre AS cadena,
        ROUND(AVG(f.precio_lista), 2) AS precio_lista,
        ROUND(AVG({mejor_precio}), 2) AS precio_promo,
        ROUND(AVG(
            (f.precio_lista - {mejor_precio})
            / NULLIF(f.precio_lista, 0) * 100
        ), 1) AS descuento_pct
    FROM fact_precios f
    JOIN dim_producto  p  ON f.producto_sk  = p.producto_sk
    JOIN dim_fecha     fe ON f.fecha_id     = fe.fecha_id
    JOIN dim_sucursal  s  ON f.sucursal_sk  = s.sucursal_sk
    JOIN dim_comercio  c  ON s.comercio_sk  = c.comercio_sk
    JOIN dim_provincia pr ON s.provincia_sk  = pr.provincia_sk
    WHERE fe.fecha = %s
      AND f.tiene_promo = TRUE
      {extra_where}
    GROUP BY p.productos_descripcion, p.productos_marca,
             c.comercio_bandera_nombre
    HAVING AVG(f.precio_lista - {mejor_precio}) > 0
    ORDER BY descuento_pct DESC
    LIMIT 50
    """,
    tuple(params),
)

if df_ofertas.empty:
    st.info("No hay promociones activas para los filtros seleccionados.")
    st.stop()

# ── Summary ──────────────────────────────────────────────────────────────────

c1, c2, c3 = st.columns(3)
c1.metric("Productos en oferta", len(df_ofertas))
c2.metric("Descuento promedio", f"{df_ofertas['descuento_pct'].mean():.1f}%")
c3.metric("Mayor descuento", f"{df_ofertas['descuento_pct'].max():.1f}%")

# ── Table ────────────────────────────────────────────────────────────────────

st.header("Mejores ofertas")

st.dataframe(
    df_ofertas.rename(
        columns={
            "producto": "Producto",
            "marca": "Marca",
            "cadena": "Cadena",
            "precio_lista": "Precio lista",
            "precio_promo": "Precio promo",
            "descuento_pct": "Descuento %",
        }
    ),
    column_config={
        "Precio lista": st.column_config.NumberColumn(format="$%.2f"),
        "Precio promo": st.column_config.NumberColumn(format="$%.2f"),
        "Descuento %": st.column_config.NumberColumn(format="%.1f%%"),
    },
    use_container_width=True,
    hide_index=True,
)

# ── Bar chart: top discounts ─────────────────────────────────────────────────

st.header("Top descuentos")

df_chart = df_ofertas.head(20).copy()
df_chart["label"] = (
    df_chart["producto"].str[:35] + " — " + df_chart["cadena"].str[:15]
)

fig = px.bar(
    df_chart.sort_values("descuento_pct"),
    y="label",
    x="descuento_pct",
    orientation="h",
    color="descuento_pct",
    color_continuous_scale="Greens",
    labels={"label": "", "descuento_pct": "Descuento (%)"},
)
fig.update_layout(showlegend=False, yaxis_title="")
st.plotly_chart(fig, use_container_width=True)
