"""Screen 4 — Comparador por cadena."""

import plotly.express as px
import streamlit as st

from dashboard.db import run_query

st.set_page_config(page_title="Comparador por Cadena", layout="wide")
st.title("Comparador por Cadena")

# ── 1. Product search ────────────────────────────────────────────────────────

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

df = run_query(
    """
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
    GROUP BY c.comercio_bandera_nombre, s.sucursales_tipo
    ORDER BY precio_lista_avg
    """,
    (producto_sk, fecha_id),
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
        f"**Más barata:** {cheapest['cadena']}  \n"
        f"Precio lista prom: ${cheapest['precio_lista_avg']:,.2f}"
    )
with col_exp:
    st.error(
        f"**Más cara:** {most_expensive['cadena']}  \n"
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
        "precio_lista_min": "Lista mín.",
        "precio_lista_max": "Lista máx.",
        "mejor_promo_avg": "Promo prom.",
        "con_promo": "Con promo",
        "pct_con_promo": "% con promo",
    }),
    column_config={
        "Lista prom.": st.column_config.NumberColumn(format="$%.2f"),
        "Lista mín.": st.column_config.NumberColumn(format="$%.2f"),
        "Lista máx.": st.column_config.NumberColumn(format="$%.2f"),
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
    labels={"cadena": "Cadena", "pct_con_promo": "% con promo"},
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
    color_discrete_map={"Precio lista": "#3498db", "Mejor promo": "#e67e22"},
    labels={"cadena": "Cadena", "precio": "Precio ($)", "tipo": "Tipo"},
)
fig_vs.update_layout(legend_title_text="", xaxis_tickangle=-45)
st.plotly_chart(fig_vs, use_container_width=True)
