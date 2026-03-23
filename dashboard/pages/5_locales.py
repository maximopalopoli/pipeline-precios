"""Screen 5 — Locales cercanos: find nearby stores on a map."""

import folium
import streamlit as st
from folium.plugins import MarkerCluster
from geopy.exc import GeocoderTimedOut, GeocoderUnavailable
from geopy.geocoders import Nominatim
from streamlit_folium import st_folium

from dashboard.db import run_query
from dashboard.filters import best_price_expr, render_consumer_filters

st.set_page_config(page_title="Locales Cercanos", layout="wide")
st.title("Locales Cercanos")

cf = render_consumer_filters()


@st.cache_data(show_spinner=False, ttl=3600)
def geocode_address(address: str) -> tuple[float, float] | None:
    """Geocode an address string to (lat, lon) using Nominatim."""
    geolocator = Nominatim(user_agent="pipeline-precios-dashboard")
    try:
        location = geolocator.geocode(address, country_codes="ar", timeout=10)
    except (GeocoderTimedOut, GeocoderUnavailable):
        return None
    if location is None:
        return None
    return (location.latitude, location.longitude)


# ── 1. User location input ───────────────────────────────────────────────────

st.subheader("Tu ubicacion")

modo = st.radio(
    "Como queres indicar tu ubicacion?",
    ["Direccion (calle y altura)", "Coordenadas"],
    horizontal=True,
)

user_lat: float | None = None
user_lon: float | None = None

if modo == "Direccion (calle y altura)":
    col_dir, col_loc = st.columns([2, 1])
    with col_dir:
        direccion_input = st.text_input(
            "Direccion",
            placeholder="Ej: Av. Corrientes 1234",
        )
    with col_loc:
        localidad_input = st.text_input(
            "Localidad / Ciudad",
            value="Buenos Aires",
            placeholder="Ej: Buenos Aires, Rosario, Cordoba",
        )

    if direccion_input:
        full_address = f"{direccion_input}, {localidad_input}, Argentina"
        with st.spinner("Buscando direccion..."):
            coords = geocode_address(full_address)
        if coords:
            user_lat, user_lon = coords
            st.caption(f"Ubicacion encontrada: {user_lat:.4f}, {user_lon:.4f}")
        else:
            st.error(
                "No se pudo encontrar la direccion. "
                "Intenta ser mas especifico (ej: 'Av. Corrientes 1234, CABA')."
            )
    else:
        st.info("Ingresa una direccion para buscar locales cercanos.")

else:
    col_lat, col_lon = st.columns(2)
    with col_lat:
        user_lat = st.number_input(
            "Latitud",
            value=-34.6037,
            format="%.4f",
            min_value=-90.0,
            max_value=90.0,
        )
    with col_lon:
        user_lon = st.number_input(
            "Longitud",
            value=-58.3816,
            format="%.4f",
            min_value=-180.0,
            max_value=180.0,
        )

radius_km = st.slider("Radio (km)", min_value=1, max_value=50, value=5)

if user_lat is None or user_lon is None:
    st.stop()

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

m = folium.Map(
    location=[user_lat, user_lon],
    zoom_start=13,
    tiles="CartoDB Positron",
)

# User location marker
folium.Marker(
    location=[user_lat, user_lon],
    tooltip="Tu ubicacion",
    icon=folium.Icon(color="red", icon="home", prefix="fa"),
).add_to(m)

# Radius circle
folium.Circle(
    location=[user_lat, user_lon],
    radius=radius_km * 1000,
    color="#e74c3c",
    fill=True,
    fill_opacity=0.05,
    weight=1,
).add_to(m)

# Store markers with clustering
marker_cluster = MarkerCluster(name="Locales").add_to(m)

for _, row in df_map.iterrows():
    popup_html = (
        f"<b>{row['cadena']}</b><br>"
        f"{row['nombre']}<br>"
        f"{row['direccion']}, {row['localidad'] or ''}<br>"
        f"<b>{row['distancia_km']} km</b>"
    )
    folium.Marker(
        location=[row["lat"], row["lon"]],
        tooltip=f"{row['cadena']} — {row['distancia_km']} km",
        popup=folium.Popup(popup_html, max_width=250),
        icon=folium.Icon(color="blue", icon="shopping-cart", prefix="fa"),
    ).add_to(marker_cluster)

st_folium(m, use_container_width=True, height=500)

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
