"""Screen 3 — Mapa de precios."""

import plotly.express as px
import streamlit as st

from dashboard.db import run_query
from dashboard.filters import product_search, render_global_filters

st.set_page_config(page_title="Mapa de Precios", layout="wide")
st.title("Mapa de Precios")

gf = render_global_filters()

# ── 1. Product search ────────────────────────────────────────────────────────

producto_sk = product_search()
if producto_sk is None:
    st.stop()

# ── 2. Date selector ─────────────────────────────────────────────────────────

df_dates = run_query(
    """
    SELECT DISTINCT a.fecha_id, fe.fecha
    FROM agg_precios_diarios a
    JOIN dim_fecha fe ON a.fecha_id = fe.fecha_id
    WHERE a.producto_sk = %s
    ORDER BY fe.fecha DESC
    """,
    (producto_sk,),
)

if df_dates.empty:
    st.warning("No hay datos de precios para este producto.")
    st.stop()

date_options = df_dates["fecha"].tolist()
selected_date = st.selectbox("Fecha", date_options, index=0)
fecha_id = int(df_dates[df_dates["fecha"] == selected_date].iloc[0]["fecha_id"])

# ── 3. Controls ──────────────────────────────────────────────────────────────

col_tipo, col_precio = st.columns(2)

with col_tipo:
    df_tipos = run_query(
        """
        SELECT DISTINCT s.sucursales_tipo
        FROM fact_precios f
        JOIN dim_sucursal s ON f.sucursal_sk = s.sucursal_sk
        WHERE f.producto_sk = %s AND f.fecha_id = %s
        ORDER BY s.sucursales_tipo
        """,
        (producto_sk, fecha_id),
    )
    all_tipos = df_tipos["sucursales_tipo"].tolist()
    selected_tipos = st.multiselect(
        "Tipo de sucursal",
        options=all_tipos,
        default=all_tipos,
    )

with col_precio:
    precio_mode = st.radio(
        "Precio a mostrar",
        options=["Precio de lista", "Mejor precio promo"],
        horizontal=True,
    )

if not selected_tipos:
    st.warning("Selecciona al menos un tipo de sucursal.")
    st.stop()

# ── 4. Map query ─────────────────────────────────────────────────────────────

if precio_mode == "Precio de lista":
    precio_expr = "f.precio_lista"
else:
    precio_expr = (
        "COALESCE(LEAST(NULLIF(f.precio_promo1, 0), NULLIF(f.precio_promo2, 0)), "
        "f.precio_lista)"
    )

tipo_placeholders = ", ".join(["%s"] * len(selected_tipos))

# Optional chain filter from global filters
chain_clause = ""
extra_params: list = []
if gf["cadenas"]:
    chain_ph = ", ".join(["%s"] * len(gf["cadenas"]))
    chain_clause = f"AND c.comercio_bandera_nombre IN ({chain_ph})"
    extra_params.extend(gf["cadenas"])

df_map = run_query(
    f"""
    SELECT
        s.sucursales_nombre, s.sucursales_tipo,
        s.sucursales_latitud  AS lat,
        s.sucursales_longitud AS lon,
        s.sucursales_localidad,
        c.comercio_bandera_nombre,
        f.precio_lista, f.tiene_promo,
        {precio_expr} AS precio_display
    FROM fact_precios f
    JOIN dim_sucursal s ON f.sucursal_sk = s.sucursal_sk
    JOIN dim_comercio c ON s.comercio_sk = c.comercio_sk
    WHERE f.producto_sk = %s
      AND f.fecha_id = %s
      AND s.sucursales_latitud  IS NOT NULL
      AND s.sucursales_longitud IS NOT NULL
      AND s.sucursales_tipo IN ({tipo_placeholders})
      {chain_clause}
    LIMIT 5000
    """,
    (producto_sk, fecha_id, *selected_tipos, *extra_params),
)

if df_map.empty:
    st.warning("No hay sucursales con coordenadas para este producto y fecha.")
    st.stop()

# ── 5. Summary metrics ───────────────────────────────────────────────────────

c1, c2, c3, c4 = st.columns(4)
c1.metric("Sucursales", len(df_map))
c2.metric("Precio min.", f"${df_map['precio_display'].min():,.2f}")
c3.metric("Precio max.", f"${df_map['precio_display'].max():,.2f}")
c4.metric("Precio prom.", f"${df_map['precio_display'].mean():,.2f}")

# ── 6. Render map ────────────────────────────────────────────────────────────

df_map["hover"] = (
    df_map["comercio_bandera_nombre"]
    + " — "
    + df_map["sucursales_nombre"]
    + "<br>"
    + df_map["sucursales_localidad"].fillna("")
)

fig = px.scatter_mapbox(
    df_map,
    lat="lat",
    lon="lon",
    color="precio_display",
    color_continuous_scale="RdYlGn_r",
    hover_name="hover",
    hover_data={
        "precio_display": ":.2f",
        "sucursales_tipo": True,
        "lat": False,
        "lon": False,
    },
    zoom=4,
    center={"lat": -38, "lon": -64},
    mapbox_style="open-street-map",
    labels={"precio_display": "Precio ($)", "sucursales_tipo": "Tipo"},
)
fig.update_layout(margin={"r": 0, "t": 0, "l": 0, "b": 0}, height=600)
st.plotly_chart(fig, use_container_width=True)
