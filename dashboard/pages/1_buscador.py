"""Screen 1 — Buscador de precios: find the cheapest store for a product."""

import branca.colormap as cm
import folium
import streamlit as st
from streamlit_folium import st_folium

from dashboard.db import run_query
from dashboard.filters import (
    best_price_expr,
    product_search,
    render_consumer_filters,
)

st.set_page_config(page_title="Buscador de Precios", layout="wide")
st.title("Buscador de Precios")

cf = render_consumer_filters()

# ── 1. Product search ────────────────────────────────────────────────────────

producto_sk = product_search()
if producto_sk is None:
    st.stop()

# ── 2. Resolve latest fecha_id for this product ─────────────────────────────

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

# ── 3. Build store-level price query ─────────────────────────────────────────

mejor_precio = best_price_expr()

where_clauses: list[str] = []
params: list = [producto_sk, fecha_id]

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

df_stores = run_query(
    f"""
    SELECT
        c.comercio_bandera_nombre AS cadena,
        s.sucursales_tipo         AS tipo,
        s.sucursales_localidad    AS localidad,
        pr.nombre                 AS provincia,
        f.precio_lista,
        {mejor_precio}            AS mejor_precio,
        f.precio_lista - {mejor_precio} AS ahorro,
        s.sucursales_latitud      AS lat,
        s.sucursales_longitud     AS lon,
        s.sucursales_nombre       AS sucursal
    FROM fact_precios f
    JOIN dim_sucursal  s  ON f.sucursal_sk  = s.sucursal_sk
    JOIN dim_comercio  c  ON s.comercio_sk  = c.comercio_sk
    JOIN dim_provincia pr ON s.provincia_sk  = pr.provincia_sk
    WHERE f.producto_sk = %s
      AND f.fecha_id = %s
      {extra_where}
    ORDER BY mejor_precio ASC
    LIMIT 200
    """,
    tuple(params),
)

if df_stores.empty:
    st.warning("No se encontraron resultados para los filtros seleccionados.")
    st.stop()

# ── 4. Highlight cheapest ────────────────────────────────────────────────────

cheapest = df_stores.iloc[0]
st.success(
    f"**Mejor precio:** {cheapest['cadena']} — {cheapest['sucursal']} "
    f"({cheapest['localidad']})  \n"
    f"**${cheapest['mejor_precio']:,.2f}**"
    + (
        f"  (ahorro ${cheapest['ahorro']:,.2f} vs lista)"
        if cheapest["ahorro"] > 0
        else ""
    )
)

# ── 5. Summary metrics ──────────────────────────────────────────────────────

c1, c2, c3, c4 = st.columns(4)
c1.metric("Locales", len(df_stores))
c2.metric("Precio min.", f"${df_stores['mejor_precio'].min():,.2f}")
c3.metric("Precio max.", f"${df_stores['mejor_precio'].max():,.2f}")
c4.metric("Precio prom.", f"${df_stores['mejor_precio'].mean():,.2f}")

# ── 6. Results table ────────────────────────────────────────────────────────

st.header("Precios por local")

st.dataframe(
    df_stores[
        ["cadena", "tipo", "localidad", "provincia", "precio_lista", "mejor_precio", "ahorro"]
    ].rename(
        columns={
            "cadena": "Cadena",
            "tipo": "Tipo",
            "localidad": "Localidad",
            "provincia": "Provincia",
            "precio_lista": "Precio lista",
            "mejor_precio": "Mejor precio",
            "ahorro": "Ahorro",
        }
    ),
    column_config={
        "Precio lista": st.column_config.NumberColumn(format="$%.2f"),
        "Mejor precio": st.column_config.NumberColumn(format="$%.2f"),
        "Ahorro": st.column_config.NumberColumn(format="$%.2f"),
    },
    use_container_width=True,
    hide_index=True,
)

# ── 7. Optional map view ────────────────────────────────────────────────────

df_with_coords = df_stores.dropna(subset=["lat", "lon"])
df_with_coords = df_with_coords[
    (df_with_coords["lat"] != 0) & (df_with_coords["lon"] != 0)
]

if not df_with_coords.empty:
    show_map = st.toggle("Ver en mapa", value=False)

    if show_map:
        df_with_coords = df_with_coords.copy()

        # Color scale: green (cheap) to red (expensive)
        vmin = df_with_coords["mejor_precio"].min()
        vmax = df_with_coords["mejor_precio"].max()
        colormap = cm.LinearColormap(
            colors=["#27ae60", "#f1c40f", "#e74c3c"],
            vmin=vmin,
            vmax=vmax,
            caption="Precio ($)",
        )

        m = folium.Map(
            location=[-38, -64],
            zoom_start=4,
            tiles="CartoDB Positron",
        )

        for _, row in df_with_coords.iterrows():
            popup_html = (
                f"<b>{row['cadena']}</b> — {row['sucursal']}<br>"
                f"{row['localidad'] or ''}<br>"
                f"Precio: <b>${row['mejor_precio']:,.2f}</b><br>"
                f"Tipo: {row['tipo']}"
            )
            folium.CircleMarker(
                location=[row["lat"], row["lon"]],
                radius=8,
                color=colormap(row["mejor_precio"]),
                fill=True,
                fill_color=colormap(row["mejor_precio"]),
                fill_opacity=0.8,
                tooltip=f"{row['cadena']} — ${row['mejor_precio']:,.2f}",
                popup=folium.Popup(popup_html, max_width=250),
            ).add_to(m)

        colormap.add_to(m)
        st_folium(m, use_container_width=True, height=600)
