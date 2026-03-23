"""Screen 5 — Locales cercanos: find nearby stores on a map."""

import plotly.express as px
import streamlit as st

from dashboard.db import run_query
from dashboard.filters import best_price_expr, render_consumer_filters

st.set_page_config(page_title="Locales Cercanos", layout="wide")
st.title("Locales Cercanos")

cf = render_consumer_filters()

# ── 1. User location input ───────────────────────────────────────────────────

st.subheader("Tu ubicacion")

col_lat, col_lon, col_radio = st.columns(3)
with col_lat:
    user_lat = st.number_input(
        "Latitud",
        value=-34.6037,
        format="%.4f",
        min_value=-90.0,
        max_value=90.0,
        help="Ej: -34.6037 (Buenos Aires)",
    )
with col_lon:
    user_lon = st.number_input(
        "Longitud",
        value=-58.3816,
        format="%.4f",
        min_value=-180.0,
        max_value=180.0,
        help="Ej: -58.3816 (Buenos Aires)",
    )
with col_radio:
    radius_km = st.slider("Radio (km)", min_value=1, max_value=50, value=5)

st.caption(
    "Podes obtener tus coordenadas desde Google Maps: "
    "click derecho en tu ubicacion y copia los numeros."
)

# ── 2. Query nearby stores ──────────────────────────────────────────────────

# Approximate degree-to-km conversion for Argentina latitudes
# 1 degree latitude ~ 111 km, 1 degree longitude ~ 85-95 km at -34 to -55 lat
lat_range = radius_km / 111.0
lon_range = radius_km / 85.0  # conservative estimate

where_clauses: list[str] = []
params: list = [
    user_lat - lat_range,
    user_lat + lat_range,
    user_lon - lon_range,
    user_lon + lon_range,
]

if cf["cadenas"]:
    chain_ph = ", ".join(["%s"] * len(cf["cadenas"]))
    where_clauses.append(f"c.comercio_bandera_nombre IN ({chain_ph})")
    params.extend(cf["cadenas"])

extra_where = ""
if where_clauses:
    extra_where = "AND " + " AND ".join(where_clauses)

df_stores = run_query(
    f"""
    SELECT
        s.sucursal_sk,
        s.sucursales_nombre       AS nombre,
        c.comercio_bandera_nombre AS cadena,
        s.sucursales_tipo         AS tipo,
        s.sucursales_calle        AS calle,
        s.sucursales_numero       AS numero,
        s.sucursales_localidad    AS localidad,
        s.sucursales_latitud      AS lat,
        s.sucursales_longitud     AS lon,
        pr.nombre                 AS provincia,
        -- Approximate distance in km using Euclidean on degrees
        ROUND(
            SQRT(
                POW((s.sucursales_latitud - {user_lat}) * 111.0, 2) +
                POW((s.sucursales_longitud - {user_lon}) * 85.0, 2)
            )::numeric, 1
        ) AS distancia_km
    FROM dim_sucursal s
    JOIN dim_comercio  c  ON s.comercio_sk  = c.comercio_sk
    JOIN dim_provincia pr ON s.provincia_sk  = pr.provincia_sk
    WHERE s.sucursales_latitud  IS NOT NULL
      AND s.sucursales_longitud IS NOT NULL
      AND s.sucursales_latitud  BETWEEN %s AND %s
      AND s.sucursales_longitud BETWEEN %s AND %s
      {extra_where}
    ORDER BY distancia_km ASC
    LIMIT 100
    """,
    tuple(params),
)

if df_stores.empty:
    st.warning(
        f"No se encontraron locales en un radio de {radius_km} km. "
        "Intenta aumentar el radio o verificar las coordenadas."
    )
    st.stop()

# Filter by actual distance (bounding box may include corners beyond radius)
df_stores = df_stores[df_stores["distancia_km"] <= radius_km]

if df_stores.empty:
    st.warning(f"No se encontraron locales dentro de {radius_km} km.")
    st.stop()

st.success(f"**{len(df_stores)} locales** encontrados en un radio de {radius_km} km")

# ── 3. Map ───────────────────────────────────────────────────────────────────

df_map = df_stores.copy()
df_map["direccion"] = (
    df_map["calle"].fillna("") + " " + df_map["numero"].fillna("")
).str.strip()
df_map["hover"] = (
    df_map["cadena"]
    + " — "
    + df_map["nombre"]
    + "<br>"
    + df_map["direccion"]
    + ", "
    + df_map["localidad"].fillna("")
    + "<br>"
    + df_map["distancia_km"].astype(str)
    + " km"
)

import plotly.graph_objects as go

fig = go.Figure()

# User location marker
fig.add_trace(go.Scattermapbox(
    lat=[user_lat],
    lon=[user_lon],
    mode="markers+text",
    marker=dict(size=15, color="#e74c3c", symbol="circle"),
    text=["Tu ubicacion"],
    textposition="top center",
    name="Tu ubicacion",
    hoverinfo="text",
))

# Store markers
fig.add_trace(go.Scattermapbox(
    lat=df_map["lat"],
    lon=df_map["lon"],
    mode="markers",
    marker=dict(
        size=10,
        color="#3498db",
    ),
    text=df_map["hover"],
    customdata=df_map["sucursal_sk"],
    name="Locales",
    hoverinfo="text",
))

fig.update_layout(
    mapbox=dict(
        style="open-street-map",
        center=dict(lat=user_lat, lon=user_lon),
        zoom=12,
    ),
    margin={"r": 0, "t": 0, "l": 0, "b": 0},
    height=500,
    showlegend=True,
    legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01),
)

st.plotly_chart(fig, use_container_width=True)

# ── 4. Store list ────────────────────────────────────────────────────────────

st.header("Listado de locales")

st.dataframe(
    df_stores[["cadena", "nombre", "tipo", "localidad", "distancia_km"]]
    .rename(columns={
        "cadena": "Cadena",
        "nombre": "Sucursal",
        "tipo": "Tipo",
        "localidad": "Localidad",
        "distancia_km": "Distancia (km)",
    }),
    use_container_width=True,
    hide_index=True,
)

# ── 5. Store detail expanders ────────────────────────────────────────────────

st.header("Detalle por local")
st.caption("Selecciona un local para ver productos en oferta")

mejor_precio = best_price_expr()

# Get latest fecha_id
df_fecha = run_query("""
    SELECT MAX(fe.fecha_id) AS fecha_id, MAX(fe.fecha) AS fecha
    FROM dim_fecha fe
    JOIN agg_precios_diarios a ON a.fecha_id = fe.fecha_id
""")

if df_fecha.empty or df_fecha.iloc[0]["fecha_id"] is None:
    st.info("No hay datos de precios cargados.")
    st.stop()

latest_fecha_id = int(df_fecha.iloc[0]["fecha_id"])

for _, store in df_stores.head(20).iterrows():
    direccion = f"{store['calle'] or ''} {store['numero'] or ''}".strip()
    label = (
        f"{store['cadena']} — {store['nombre']} "
        f"({direccion}, {store['localidad']}) — {store['distancia_km']} km"
    )
    with st.expander(label):
        df_promos = run_query(
            f"""
            SELECT
                p.productos_descripcion AS producto,
                p.productos_marca       AS marca,
                f.precio_lista,
                {mejor_precio}          AS precio_promo,
                ROUND(
                    (f.precio_lista - {mejor_precio})
                    / NULLIF(f.precio_lista, 0) * 100, 1
                ) AS descuento_pct
            FROM fact_precios f
            JOIN dim_producto p ON f.producto_sk = p.producto_sk
            WHERE f.sucursal_sk = %s
              AND f.fecha_id = %s
              AND f.tiene_promo = TRUE
            ORDER BY descuento_pct DESC
            LIMIT 10
            """,
            (int(store["sucursal_sk"]), latest_fecha_id),
        )

        if df_promos.empty:
            st.info("Sin promociones activas en este local.")
        else:
            st.dataframe(
                df_promos.rename(columns={
                    "producto": "Producto",
                    "marca": "Marca",
                    "precio_lista": "Precio lista",
                    "precio_promo": "Precio promo",
                    "descuento_pct": "Descuento %",
                }),
                column_config={
                    "Precio lista": st.column_config.NumberColumn(format="$%.2f"),
                    "Precio promo": st.column_config.NumberColumn(format="$%.2f"),
                    "Descuento %": st.column_config.NumberColumn(format="%.1f%%"),
                },
                use_container_width=True,
                hide_index=True,
            )
