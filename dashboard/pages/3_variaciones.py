"""Screen 3 — Ranking de variaciones de precios."""

import plotly.express as px
import streamlit as st

from dashboard.db import run_query
from dashboard.filters import COLOR_BAJA, COLOR_SUBA, render_consumer_filters

st.set_page_config(page_title="Variaciones de Precios", layout="wide")
st.title("Variaciones de Precios")

cf = render_consumer_filters()

# ── Filters ──────────────────────────────────────────────────────────────────

price_mode = st.radio(
    "Tipo de precio", ["Precio de lista", "Precio promo"], horizontal=True
)

selected_prov = cf["provincia"]
selected_chains = cf["cadenas"]

# ── Resolve latest date range (7 days) ───────────────────────────────────────

df_range = run_query(
    """
    SELECT MAX(f.fecha) AS max_fecha, MAX(f.fecha) - INTERVAL '7 days' AS min_fecha
    FROM dim_fecha f
    JOIN agg_precios_diarios a ON a.fecha_id = f.fecha_id
    """
)

if df_range.empty or df_range.iloc[0]["max_fecha"] is None:
    st.warning("No hay datos de precios cargados.")
    st.stop()

max_fecha = df_range.iloc[0]["max_fecha"]
min_fecha = df_range.iloc[0]["min_fecha"]

st.caption(
    f"Variaciones en los ultimos 7 dias ({min_fecha:%Y-%m-%d} a {max_fecha:%Y-%m-%d})"
)

# ── Determine columns based on price mode ────────────────────────────────────

if price_mode == "Precio de lista":
    variation_col = "variacion_pct"
    price_col = "precio_lista_avg"
    prev_col = "precio_lista_avg_anterior"
else:
    variation_col = "variacion_promo_pct"
    price_col = "precio_promo_avg"
    prev_col = "precio_promo_avg_anterior"

# ── Build query ──────────────────────────────────────────────────────────────

if selected_chains:
    chain_ph = ", ".join(["%s"] * len(selected_chains))
    prov_clause = ""
    params: list = []

    if selected_prov:
        prov_clause = "AND pr.nombre = %s"
        params.append(selected_prov)

    params.extend(selected_chains)

    df_var = run_query(
        f"""
        WITH daily AS (
            SELECT
                p.producto_sk,
                p.id_producto,
                p.productos_descripcion,
                p.productos_marca,
                fe.fecha_id,
                fe.fecha,
                pr.nombre AS provincia,
                ROUND(AVG(f.precio_lista), 2) AS precio_lista_avg,
                ROUND(AVG(
                    COALESCE(
                        LEAST(NULLIF(f.precio_promo1, 0), NULLIF(f.precio_promo2, 0)),
                        f.precio_lista
                    )
                ), 2) AS precio_promo_avg
            FROM fact_precios f
            JOIN dim_producto  p  ON f.producto_sk  = p.producto_sk
            JOIN dim_fecha     fe ON f.fecha_id     = fe.fecha_id
            JOIN dim_sucursal  s  ON f.sucursal_sk  = s.sucursal_sk
            JOIN dim_comercio  c  ON s.comercio_sk  = c.comercio_sk
            JOIN dim_provincia pr ON s.provincia_sk  = pr.provincia_sk
            WHERE fe.fecha >= (
                    SELECT MAX(fecha) - INTERVAL '7 days'
                    FROM dim_fecha
                    JOIN agg_precios_diarios USING (fecha_id)
                  )
              {prov_clause}
              AND c.comercio_bandera_nombre IN ({chain_ph})
            GROUP BY p.producto_sk, p.id_producto, p.productos_descripcion,
                     p.productos_marca, fe.fecha_id, fe.fecha, pr.nombre
        ),
        with_lag AS (
            SELECT *,
                LAG(precio_lista_avg) OVER w AS precio_lista_avg_anterior,
                LAG(precio_promo_avg) OVER w AS precio_promo_avg_anterior,
                ROUND(
                    (precio_lista_avg - LAG(precio_lista_avg) OVER w)
                    / NULLIF(LAG(precio_lista_avg) OVER w, 0) * 100, 2
                ) AS variacion_pct,
                ROUND(
                    (precio_promo_avg - LAG(precio_promo_avg) OVER w)
                    / NULLIF(LAG(precio_promo_avg) OVER w, 0) * 100, 2
                ) AS variacion_promo_pct
            FROM daily
            WINDOW w AS (PARTITION BY producto_sk, provincia ORDER BY fecha_id)
        )
        SELECT *
        FROM with_lag
        WHERE {variation_col} IS NOT NULL
        ORDER BY ABS({variation_col}) DESC
        LIMIT 20
        """,
        tuple(params),
    )
else:
    prov_clause = ""
    params = []

    if selected_prov:
        prov_clause = "AND provincia = %s"
        params.append(selected_prov)

    if price_mode == "Precio promo":
        df_var = run_query(
            f"""
            WITH base AS (
                SELECT
                    producto_sk, id_producto, productos_descripcion,
                    productos_marca, fecha_id, fecha, provincia,
                    precio_promo_avg,
                    LAG(precio_promo_avg) OVER (
                        PARTITION BY producto_sk, provincia ORDER BY fecha_id
                    ) AS precio_promo_avg_anterior,
                    ROUND(
                        (precio_promo_avg - LAG(precio_promo_avg) OVER (
                            PARTITION BY producto_sk, provincia ORDER BY fecha_id
                        ))
                        / NULLIF(LAG(precio_promo_avg) OVER (
                            PARTITION BY producto_sk, provincia ORDER BY fecha_id
                        ), 0) * 100, 2
                    ) AS variacion_promo_pct
                FROM v_variacion_precios
                WHERE fecha >= (
                    SELECT MAX(fecha) FROM v_variacion_precios
                ) - INTERVAL '7 days'
                {prov_clause}
            )
            SELECT * FROM base
            WHERE variacion_promo_pct IS NOT NULL
            ORDER BY ABS(variacion_promo_pct) DESC
            LIMIT 20
            """,
            tuple(params),
        )
    else:
        df_var = run_query(
            f"""
            SELECT *
            FROM v_variacion_precios
            WHERE variacion_pct IS NOT NULL
              AND fecha >= (
                  SELECT MAX(fecha) FROM v_variacion_precios
              ) - INTERVAL '7 days'
              {prov_clause}
            ORDER BY ABS(variacion_pct) DESC
            LIMIT 20
            """,
            tuple(params),
        )

if df_var.empty:
    st.warning("No se encontraron variaciones para los filtros seleccionados.")
    st.stop()

# ── Display ──────────────────────────────────────────────────────────────────

st.header(f"Top {len(df_var)} productos con mayor variacion")

display_cols = {
    "productos_descripcion": "Producto",
    "productos_marca": "Marca",
    "provincia": "Provincia",
    prev_col: "Precio anterior",
    price_col: "Precio actual",
    variation_col: "Variacion %",
}
available = {k: v for k, v in display_cols.items() if k in df_var.columns}
df_display = df_var[list(available.keys())].rename(columns=available)

st.dataframe(
    df_display,
    column_config={
        "Precio anterior": st.column_config.NumberColumn(format="$%.2f"),
        "Precio actual": st.column_config.NumberColumn(format="$%.2f"),
        "Variacion %": st.column_config.NumberColumn(format="%.2f%%"),
    },
    use_container_width=True,
    hide_index=True,
)

# Bar chart
df_var["label"] = (
    df_var["productos_descripcion"].str[:40]
    + " (" + df_var["provincia"].str[:10] + ")"
)
df_var["color"] = df_var[variation_col].apply(lambda x: "Suba" if x > 0 else "Baja")

fig = px.bar(
    df_var.sort_values(variation_col),
    y="label",
    x=variation_col,
    orientation="h",
    color="color",
    color_discrete_map={"Suba": COLOR_SUBA, "Baja": COLOR_BAJA},
    labels={"label": "", variation_col: "Variacion (%)"},
)
fig.update_layout(showlegend=True, legend_title_text="", yaxis_title="")
st.plotly_chart(fig, use_container_width=True)
