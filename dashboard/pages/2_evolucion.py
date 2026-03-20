"""Screen 2 — Price Evolution."""

import plotly.express as px
import streamlit as st

from dashboard.db import run_query

st.set_page_config(page_title="Evolución de Precios", layout="wide")
st.title("Evolución de Precios")

# ── 1. Product search ───────────────────────────────────────────────────────

search = st.text_input(
    "Buscar producto por nombre, marca o EAN",
    placeholder="Ej: leche entera, Marolio, 7790001234567",
)

if not search:
    st.info("Ingresá un término de búsqueda para comenzar.")
    st.stop()

df_products = run_query(
    """
    SELECT producto_sk, id_producto, productos_descripcion, productos_marca
    FROM dim_producto
    WHERE productos_descripcion ILIKE %s
       OR productos_marca        ILIKE %s
       OR id_producto             = %s
    LIMIT 50
    """,
    (f"%{search}%", f"%{search}%", search),
)

if df_products.empty:
    st.warning("No se encontraron productos.")
    st.stop()

df_products["label"] = (
    df_products["productos_descripcion"]
    + " — "
    + df_products["productos_marca"].fillna("")
    + " ("
    + df_products["id_producto"]
    + ")"
)

selected_label = st.selectbox("Seleccioná un producto", df_products["label"])
selected_row = df_products[df_products["label"] == selected_label].iloc[0]
producto_sk = int(selected_row["producto_sk"])

# ── 2. Price line chart (list vs promo) ─────────────────────────────────────

st.header("Precio de lista vs mejor precio promo")

df_prices = run_query(
    """
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
    ORDER BY f.fecha, pr.nombre
    """,
    (producto_sk,),
)

if df_prices.empty:
    st.warning("No hay datos de precios para este producto.")
    st.stop()

# National average (all provinces)
df_national = (
    df_prices.groupby("fecha", as_index=False)
    .agg(precio_lista=("precio_lista_avg", "mean"), precio_promo=("precio_promo_avg", "mean"))
)

fig_national = px.line(
    df_national,
    x="fecha",
    y=["precio_lista", "precio_promo"],
    labels={"fecha": "Fecha", "value": "Precio ($)", "variable": "Tipo"},
    color_discrete_map={"precio_lista": "#3498db", "precio_promo": "#e67e22"},
)
fig_national.update_layout(legend_title_text="")
st.plotly_chart(fig_national, use_container_width=True)

# ── 3. Breakdown by province ────────────────────────────────────────────────

st.header("Desglose por provincia")

provinces = sorted(df_prices["provincia"].unique())
selected_provinces = st.multiselect(
    "Provincias a mostrar",
    options=provinces,
    default=provinces[:5],
)

if not selected_provinces:
    st.info("Seleccioná al menos una provincia.")
    st.stop()

df_filtered = df_prices[df_prices["provincia"].isin(selected_provinces)]

fig_prov = px.line(
    df_filtered,
    x="fecha",
    y="precio_lista_avg",
    color="provincia",
    labels={"fecha": "Fecha", "precio_lista_avg": "Precio lista prom. ($)", "provincia": "Provincia"},
    markers=True,
)
st.plotly_chart(fig_prov, use_container_width=True)
