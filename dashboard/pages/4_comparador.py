"""Screen 4 — Comparador por cadena."""

import plotly.express as px
import streamlit as st

from dashboard.db import run_query
from dashboard.filters import (
    COLOR_PRECIO_LISTA,
    COLOR_PRECIO_PROMO,
    product_search,
    render_global_filters,
)

st.set_page_config(page_title="Comparador por Cadena", layout="wide")
st.title("Comparador por Cadena")

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

# ── 3. Main query ────────────────────────────────────────────────────────────

# Optional chain filter from global filters
chain_clause = ""
extra_params: list = []
if gf["cadenas"]:
    chain_ph = ", ".join(["%s"] * len(gf["cadenas"]))
    chain_clause = f"AND c.comercio_bandera_nombre IN ({chain_ph})"
    extra_params.extend(gf["cadenas"])

df = run_query(
    f"""
    SELECT
        c.comercio_bandera_nombre AS cadena,
        s.sucursales_tipo         AS tipo_sucursal,
        COUNT(*)                  AS sucursales,
        ROUND(AVG(f.precio_lista), 2)  AS precio_lista_avg,
        ROUND(MIN(f.precio_lista), 2)  AS precio_lista_min,
        ROUND(MAX(f.precio_lista), 2)  AS precio_lista_max,
        ROUND(AVG(COALESCE(
            LEAST(NULLIF(f.precio_promo1, 0), NULLIF(f.precio_promo2, 0)),
            f.precio_lista
        )), 2) AS mejor_promo_avg,
        SUM(CASE WHEN f.tiene_promo THEN 1 ELSE 0 END) AS con_promo,
        ROUND(100.0 * SUM(CASE WHEN f.tiene_promo THEN 1 ELSE 0 END)
              / COUNT(*), 1) AS pct_con_promo
    FROM fact_precios f
    JOIN dim_sucursal s ON f.sucursal_sk = s.sucursal_sk
    JOIN dim_comercio c ON s.comercio_sk = c.comercio_sk
    WHERE f.producto_sk = %s AND f.fecha_id = %s
    {chain_clause}
    GROUP BY c.comercio_bandera_nombre, s.sucursales_tipo
    ORDER BY precio_lista_avg
    """,
    (producto_sk, fecha_id, *extra_params),
)

if df.empty:
    st.warning("No hay datos para este producto y fecha.")
    st.stop()

# ── 4. Chain-level summary ───────────────────────────────────────────────────

df_chain = (
    df.groupby("cadena", as_index=False)
    .agg(
        sucursales=("sucursales", "sum"),
        precio_lista_avg=("precio_lista_avg", "mean"),
        mejor_promo_avg=("mejor_promo_avg", "mean"),
        con_promo=("con_promo", "sum"),
        pct_con_promo=("pct_con_promo", "mean"),
    )
    .sort_values("precio_lista_avg")
)

# ── 5. Highlight cards ───────────────────────────────────────────────────────

cheapest = df_chain.iloc[0]
most_expensive = df_chain.iloc[-1]

col_cheap, col_exp = st.columns(2)
with col_cheap:
    st.success(
        f"**Mas barata:** {cheapest['cadena']}  \n"
        f"Precio lista prom: ${cheapest['precio_lista_avg']:,.2f}"
    )
with col_exp:
    st.error(
        f"**Mas cara:** {most_expensive['cadena']}  \n"
        f"Precio lista prom: ${most_expensive['precio_lista_avg']:,.2f}"
    )

# ── 6. Detail table ──────────────────────────────────────────────────────────

st.header("Detalle por cadena y tipo de sucursal")

st.dataframe(
    df.rename(columns={
        "cadena": "Cadena",
        "tipo_sucursal": "Tipo",
        "sucursales": "Sucursales",
        "precio_lista_avg": "Lista prom.",
        "precio_lista_min": "Lista min.",
        "precio_lista_max": "Lista max.",
        "mejor_promo_avg": "Promo prom.",
        "con_promo": "Con promo",
        "pct_con_promo": "% con promo",
    }),
    column_config={
        "Lista prom.": st.column_config.NumberColumn(format="$%.2f"),
        "Lista min.": st.column_config.NumberColumn(format="$%.2f"),
        "Lista max.": st.column_config.NumberColumn(format="$%.2f"),
        "Promo prom.": st.column_config.NumberColumn(format="$%.2f"),
        "% con promo": st.column_config.NumberColumn(format="%.1f%%"),
    },
    use_container_width=True,
    hide_index=True,
)

# ── 7. Bar chart: promo availability ─────────────────────────────────────────

st.header("Disponibilidad de promos por cadena")

fig_promo = px.bar(
    df_chain.sort_values("pct_con_promo"),
    y="cadena",
    x="pct_con_promo",
    orientation="h",
    color="pct_con_promo",
    color_continuous_scale="Greens",
    labels={"cadena": "", "pct_con_promo": "% con promo"},
)
fig_promo.update_layout(showlegend=False, yaxis_title="")
st.plotly_chart(fig_promo, use_container_width=True)

# ── 8. Bar chart: list vs promo ──────────────────────────────────────────────

st.header("Precio de lista vs mejor promo por cadena")

df_melt = df_chain.melt(
    id_vars="cadena",
    value_vars=["precio_lista_avg", "mejor_promo_avg"],
    var_name="tipo",
    value_name="precio",
)
df_melt["tipo"] = df_melt["tipo"].map({
    "precio_lista_avg": "Precio lista",
    "mejor_promo_avg": "Mejor promo",
})

fig_vs = px.bar(
    df_melt,
    x="cadena",
    y="precio",
    color="tipo",
    barmode="group",
    color_discrete_map={
        "Precio lista": COLOR_PRECIO_LISTA,
        "Mejor promo": COLOR_PRECIO_PROMO,
    },
    labels={"cadena": "Cadena", "precio": "Precio ($)", "tipo": "Tipo"},
)
fig_vs.update_layout(legend_title_text="", xaxis_tickangle=-45)
st.plotly_chart(fig_vs, use_container_width=True)
