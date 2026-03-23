"""Shared filters and widgets for the SEPA dashboard."""

import streamlit as st

from dashboard.db import run_query

# ── Color palette ────────────────────────────────────────────────────────────

COLOR_PRECIO_LISTA = "#3498db"
COLOR_PRECIO_PROMO = "#e67e22"
COLOR_SUBA = "#e74c3c"
COLOR_BAJA = "#2ecc71"
COLOR_PROMO1 = "#3498db"
COLOR_PROMO2 = "#9b59b6"
COLOR_VALID = "#2ecc71"
COLOR_REJECTED = "#e74c3c"


def render_global_filters() -> dict:
    """Render global filters in the sidebar and return current selections.

    Returns a dict with keys:
        - date_from, date_to (date or None)
        - provincias (list[str])
        - cadenas (list[str])
    """
    with st.sidebar:
        st.header("Filtros globales")

        # Date range
        df_range = run_query(
            """
            SELECT MIN(fecha) AS min_fecha, MAX(fecha) AS max_fecha
            FROM dim_fecha
            JOIN agg_precios_diarios USING (fecha_id)
            """
        )

        date_from = None
        date_to = None
        if not df_range.empty and df_range.iloc[0]["max_fecha"] is not None:
            min_date = df_range.iloc[0]["min_fecha"]
            max_date = df_range.iloc[0]["max_fecha"]
            date_val = st.date_input(
                "Rango de fechas",
                value=(max_date, max_date),
                min_value=min_date,
                max_value=max_date,
                key="global_dates",
            )
            if isinstance(date_val, tuple) and len(date_val) == 2:
                date_from, date_to = date_val
            elif isinstance(date_val, tuple) and len(date_val) == 1:
                date_from = date_to = date_val[0]

        # Province
        df_provs = run_query(
            "SELECT nombre FROM dim_provincia ORDER BY nombre"
        )
        provincias = st.multiselect(
            "Provincia",
            options=df_provs["nombre"].tolist() if not df_provs.empty else [],
            key="global_provincias",
        )

        # Chain
        df_chains = run_query(
            "SELECT DISTINCT comercio_bandera_nombre FROM dim_comercio ORDER BY 1"
        )
        cadenas = st.multiselect(
            "Cadena",
            options=(
                df_chains["comercio_bandera_nombre"].tolist()
                if not df_chains.empty
                else []
            ),
            key="global_cadenas",
        )

    return {
        "date_from": date_from,
        "date_to": date_to,
        "provincias": provincias,
        "cadenas": cadenas,
    }


def best_price_expr(alias: str = "f") -> str:
    """Return SQL expression for the best available price (promo or list)."""
    return (
        f"COALESCE(LEAST(NULLIF({alias}.precio_promo1, 0), "
        f"NULLIF({alias}.precio_promo2, 0)), {alias}.precio_lista)"
    )


def render_consumer_filters() -> dict:
    """Render consumer-oriented filters in the sidebar.

    Returns a dict with keys:
        - provincia (str or None) — single province
        - localidad (str or None) — single locality (depends on provincia)
        - cadenas (list[str])
        - latest_fecha (date or None) — most recent date with data
    """
    with st.sidebar:
        st.header("Filtros")

        # Latest available date
        df_range = run_query(
            """
            SELECT MAX(fecha) AS max_fecha
            FROM dim_fecha
            JOIN agg_precios_diarios USING (fecha_id)
            """
        )
        latest_fecha = (
            df_range.iloc[0]["max_fecha"]
            if not df_range.empty and df_range.iloc[0]["max_fecha"] is not None
            else None
        )

        # Province (single select)
        df_provs = run_query(
            "SELECT nombre FROM dim_provincia ORDER BY nombre"
        )
        prov_options = df_provs["nombre"].tolist() if not df_provs.empty else []
        provincia = st.selectbox(
            "Provincia",
            options=["Todas"] + prov_options,
            index=0,
            key="consumer_provincia",
        )
        if provincia == "Todas":
            provincia = None

        # Locality (depends on province)
        localidad = None
        if provincia:
            df_locs = run_query(
                """
                SELECT DISTINCT s.sucursales_localidad
                FROM dim_sucursal s
                JOIN dim_provincia pr ON s.provincia_sk = pr.provincia_sk
                WHERE pr.nombre = %s
                  AND s.sucursales_localidad IS NOT NULL
                ORDER BY 1
                """,
                (provincia,),
            )
            loc_options = (
                df_locs["sucursales_localidad"].tolist()
                if not df_locs.empty
                else []
            )
            if loc_options:
                loc_selection = st.selectbox(
                    "Localidad",
                    options=["Todas"] + loc_options,
                    index=0,
                    key="consumer_localidad",
                )
                if loc_selection != "Todas":
                    localidad = loc_selection

        # Chain (multi-select)
        df_chains = run_query(
            "SELECT DISTINCT comercio_bandera_nombre FROM dim_comercio ORDER BY 1"
        )
        cadenas = st.multiselect(
            "Cadena",
            options=(
                df_chains["comercio_bandera_nombre"].tolist()
                if not df_chains.empty
                else []
            ),
            key="consumer_cadenas",
        )

    return {
        "provincia": provincia,
        "localidad": localidad,
        "cadenas": cadenas,
        "latest_fecha": latest_fecha,
    }


def product_search() -> int | None:
    """Render a product search widget and return the selected producto_sk.

    Returns None if no product is selected yet (caller should st.stop()).
    """
    search = st.text_input(
        "Buscar producto",
        placeholder="Nombre, marca o EAN (ej: leche entera, Marolio, 7790001234567)",
    )

    if not search:
        st.info("Ingresa un termino de busqueda para comenzar.")
        return None

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
        return None

    df_products["label"] = (
        df_products["productos_descripcion"]
        + " — "
        + df_products["productos_marca"].fillna("")
        + " ("
        + df_products["id_producto"]
        + ")"
    )

    selected_label = st.selectbox("Producto", df_products["label"])
    selected_row = df_products[df_products["label"] == selected_label].iloc[0]
    return int(selected_row["producto_sk"])
