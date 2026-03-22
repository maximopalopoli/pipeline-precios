"""Screen 6 — Analisis de promociones."""

import plotly.express as px
import streamlit as st

from dashboard.db import run_query
from dashboard.filters import COLOR_BAJA, COLOR_PROMO1, COLOR_PROMO2, render_global_filters

st.set_page_config(page_title="Analisis de Promociones", layout="wide")
st.title("Analisis de Promociones")

gf = render_global_filters()

# ── Resolve dates ────────────────────────────────────────────────────────────

if gf["date_from"] is None or gf["date_to"] is None:
    st.warning("No hay datos de precios cargados.")
    st.stop()

date_from = gf["date_from"]
date_to = gf["date_to"]

# Build shared WHERE fragments from global filters
prov_clause = ""
prov_params: list = []
if gf["provincias"]:
    prov_ph = ", ".join(["%s"] * len(gf["provincias"]))
    prov_clause = f"AND pr.nombre IN ({prov_ph})"
    prov_params = list(gf["provincias"])

chain_clause = ""
chain_params: list = []
if gf["cadenas"]:
    chain_ph = ", ".join(["%s"] * len(gf["cadenas"]))
    chain_clause = f"AND c.comercio_bandera_nombre IN ({chain_ph})"
    chain_params = list(gf["cadenas"])

base_params = tuple([str(date_from), str(date_to)] + prov_params + chain_params)

# == 1. Porcentaje de productos con promo por cadena ===========================

st.header("Productos con promocion por cadena")

df_promo_pct = run_query(
    f"""
    SELECT
        c.comercio_bandera_nombre                          AS cadena,
        COUNT(*)                                           AS total_obs,
        SUM(CASE WHEN f.precio_promo1 > 0 THEN 1 ELSE 0 END)  AS con_promo1,
        SUM(CASE WHEN f.precio_promo2 > 0 THEN 1 ELSE 0 END)  AS con_promo2,
        SUM(CASE WHEN f.tiene_promo THEN 1 ELSE 0 END)    AS con_cualquier_promo,
        ROUND(100.0 * SUM(CASE WHEN f.precio_promo1 > 0 THEN 1 ELSE 0 END)
              / COUNT(*), 1)                               AS pct_promo1,
        ROUND(100.0 * SUM(CASE WHEN f.precio_promo2 > 0 THEN 1 ELSE 0 END)
              / COUNT(*), 1)                               AS pct_promo2,
        ROUND(100.0 * SUM(CASE WHEN f.tiene_promo THEN 1 ELSE 0 END)
              / COUNT(*), 1)                               AS pct_cualquiera
    FROM fact_precios f
    JOIN dim_fecha     fe ON f.fecha_id    = fe.fecha_id
    JOIN dim_sucursal  s  ON f.sucursal_sk = s.sucursal_sk
    JOIN dim_comercio  c  ON s.comercio_sk = c.comercio_sk
    JOIN dim_provincia pr ON s.provincia_sk = pr.provincia_sk
    WHERE fe.fecha BETWEEN %s AND %s
      {prov_clause}
      {chain_clause}
    GROUP BY c.comercio_bandera_nombre
    ORDER BY pct_cualquiera DESC
    """,
    base_params,
)

if df_promo_pct.empty:
    st.warning("No hay datos para los filtros seleccionados.")
    st.stop()

df_melt = df_promo_pct.melt(
    id_vars="cadena",
    value_vars=["pct_promo1", "pct_promo2", "pct_cualquiera"],
    var_name="tipo",
    value_name="porcentaje",
)
df_melt["tipo"] = df_melt["tipo"].map({
    "pct_promo1": "Promo 1",
    "pct_promo2": "Promo 2",
    "pct_cualquiera": "Cualquiera",
})

fig_pct = px.bar(
    df_melt,
    y="cadena",
    x="porcentaje",
    color="tipo",
    orientation="h",
    barmode="group",
    color_discrete_map={
        "Promo 1": COLOR_PROMO1,
        "Promo 2": COLOR_PROMO2,
        "Cualquiera": COLOR_BAJA,
    },
    labels={"cadena": "", "porcentaje": "% de productos", "tipo": "Tipo promo"},
)
fig_pct.update_layout(yaxis_title="", legend_title_text="")
st.plotly_chart(fig_pct, use_container_width=True)

# == 2. Descuento efectivo promedio por cadena =================================

st.header("Descuento efectivo promedio por cadena")

df_discount = run_query(
    f"""
    SELECT
        c.comercio_bandera_nombre AS cadena,
        ROUND(AVG(
            (f.precio_lista - COALESCE(
                LEAST(NULLIF(f.precio_promo1, 0), NULLIF(f.precio_promo2, 0)),
                f.precio_lista
            )) / NULLIF(f.precio_lista, 0) * 100
        ), 1) AS descuento_pct
    FROM fact_precios f
    JOIN dim_fecha     fe ON f.fecha_id    = fe.fecha_id
    JOIN dim_sucursal  s  ON f.sucursal_sk = s.sucursal_sk
    JOIN dim_comercio  c  ON s.comercio_sk = c.comercio_sk
    JOIN dim_provincia pr ON s.provincia_sk = pr.provincia_sk
    WHERE fe.fecha BETWEEN %s AND %s
      AND f.tiene_promo = TRUE
      {prov_clause}
      {chain_clause}
    GROUP BY c.comercio_bandera_nombre
    ORDER BY descuento_pct DESC
    """,
    base_params,
)

if not df_discount.empty:
    fig_disc = px.bar(
        df_discount.sort_values("descuento_pct"),
        y="cadena",
        x="descuento_pct",
        orientation="h",
        color="descuento_pct",
        color_continuous_scale="Oranges",
        labels={"cadena": "", "descuento_pct": "Descuento promedio (%)"},
    )
    fig_disc.update_layout(showlegend=False, yaxis_title="")
    st.plotly_chart(fig_disc, use_container_width=True)
else:
    st.info("No hay productos con promocion en este periodo.")

# == 3. Productos con promos mas agresivas =====================================

st.header("Top 20 productos con mayor descuento")

df_top = run_query(
    f"""
    SELECT
        p.productos_descripcion AS producto,
        p.productos_marca       AS marca,
        c.comercio_bandera_nombre AS cadena,
        ROUND(AVG(f.precio_lista), 2) AS precio_lista,
        ROUND(AVG(
            COALESCE(
                LEAST(NULLIF(f.precio_promo1, 0), NULLIF(f.precio_promo2, 0)),
                f.precio_lista
            )
        ), 2) AS mejor_promo,
        ROUND(AVG(
            (f.precio_lista - COALESCE(
                LEAST(NULLIF(f.precio_promo1, 0), NULLIF(f.precio_promo2, 0)),
                f.precio_lista
            )) / NULLIF(f.precio_lista, 0) * 100
        ), 1) AS descuento_pct
    FROM fact_precios f
    JOIN dim_producto  p  ON f.producto_sk  = p.producto_sk
    JOIN dim_fecha     fe ON f.fecha_id     = fe.fecha_id
    JOIN dim_sucursal  s  ON f.sucursal_sk  = s.sucursal_sk
    JOIN dim_comercio  c  ON s.comercio_sk  = c.comercio_sk
    JOIN dim_provincia pr ON s.provincia_sk  = pr.provincia_sk
    WHERE fe.fecha BETWEEN %s AND %s
      AND f.tiene_promo = TRUE
      {prov_clause}
      {chain_clause}
    GROUP BY p.productos_descripcion, p.productos_marca, c.comercio_bandera_nombre
    ORDER BY descuento_pct DESC
    LIMIT 20
    """,
    base_params,
)

if not df_top.empty:
    st.dataframe(
        df_top.rename(columns={
            "producto": "Producto",
            "marca": "Marca",
            "cadena": "Cadena",
            "precio_lista": "Precio lista",
            "mejor_promo": "Mejor promo",
            "descuento_pct": "Descuento %",
        }),
        column_config={
            "Precio lista": st.column_config.NumberColumn(format="$%.2f"),
            "Mejor promo": st.column_config.NumberColumn(format="$%.2f"),
            "Descuento %": st.column_config.NumberColumn(format="%.1f%%"),
        },
        use_container_width=True,
        hide_index=True,
    )
else:
    st.info("No hay productos con promocion en este periodo.")

# == 4. Tabla de leyendas de promo =============================================

st.header("Leyendas de promocion")

df_leyendas = run_query(
    f"""
    SELECT
        COALESCE(f.leyenda_promo1, '') AS leyenda_promo1,
        COALESCE(f.leyenda_promo2, '') AS leyenda_promo2,
        COUNT(*) AS ocurrencias
    FROM fact_precios f
    JOIN dim_fecha     fe ON f.fecha_id    = fe.fecha_id
    JOIN dim_sucursal  s  ON f.sucursal_sk = s.sucursal_sk
    JOIN dim_provincia pr ON s.provincia_sk = pr.provincia_sk
    WHERE fe.fecha BETWEEN %s AND %s
      AND f.tiene_promo = TRUE
      AND (f.leyenda_promo1 IS NOT NULL OR f.leyenda_promo2 IS NOT NULL)
      {prov_clause}
      {chain_clause}
    GROUP BY f.leyenda_promo1, f.leyenda_promo2
    ORDER BY ocurrencias DESC
    LIMIT 50
    """,
    base_params,
)

if not df_leyendas.empty:
    st.dataframe(
        df_leyendas.rename(columns={
            "leyenda_promo1": "Leyenda promo 1",
            "leyenda_promo2": "Leyenda promo 2",
            "ocurrencias": "Ocurrencias",
        }),
        use_container_width=True,
        hide_index=True,
    )
else:
    st.info("No hay leyendas de promocion en este periodo.")
