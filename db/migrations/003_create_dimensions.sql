-- 003_create_dimensions.sql
-- Warehouse dimension tables

CREATE TABLE IF NOT EXISTS dim_provincia (
    provincia_sk    SERIAL PRIMARY KEY,
    codigo_iso      VARCHAR(6)  NOT NULL UNIQUE,   -- ISO 3166-2, e.g.: AR-B
    nombre          TEXT        NOT NULL,
    region          TEXT        NOT NULL
                        CHECK (region IN ('NOA', 'NEA', 'Pampeana', 'Cuyo', 'Patagonia', 'AMBA'))
);

-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS dim_comercio (
    comercio_sk             SERIAL PRIMARY KEY,
    -- natural key
    id_comercio             INT         NOT NULL,
    id_bandera              INT         NOT NULL,
    UNIQUE (id_comercio, id_bandera),
    -- attributes (SCD Type 1: overwritten)
    comercio_cuit           VARCHAR(20),
    comercio_razon_social   TEXT,
    comercio_bandera_nombre TEXT,
    comercio_bandera_url    TEXT,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dim_comercio_natural ON dim_comercio (id_comercio, id_bandera);

-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS dim_sucursal (
    sucursal_sk         SERIAL PRIMARY KEY,
    -- natural key
    id_comercio         INT         NOT NULL,
    id_bandera          INT         NOT NULL,
    id_sucursal         INT         NOT NULL,
    UNIQUE (id_comercio, id_bandera, id_sucursal),
    -- FK
    comercio_sk         INT         NOT NULL REFERENCES dim_comercio (comercio_sk),
    provincia_sk        INT         REFERENCES dim_provincia (provincia_sk),
    -- attributes (SCD Type 1)
    sucursales_nombre           TEXT,
    sucursales_tipo             TEXT,
    sucursales_calle            TEXT,
    sucursales_numero           TEXT,
    sucursales_barrio           TEXT,
    sucursales_codigo_postal    TEXT,
    sucursales_localidad        TEXT,
    sucursales_latitud          DECIMAL(9, 6),
    sucursales_longitud         DECIMAL(9, 6),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dim_sucursal_natural   ON dim_sucursal (id_comercio, id_bandera, id_sucursal);
CREATE INDEX IF NOT EXISTS idx_dim_sucursal_comercio  ON dim_sucursal (comercio_sk);
CREATE INDEX IF NOT EXISTS idx_dim_sucursal_provincia ON dim_sucursal (provincia_sk);

-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS dim_producto (
    producto_sk                         SERIAL PRIMARY KEY,
    -- natural key: id_producto (EAN code or chain-internal code)
    id_producto                         TEXT        NOT NULL UNIQUE,
    -- attributes (SCD Type 1)
    es_ean                              BOOLEAN,
    productos_descripcion               TEXT,
    productos_marca                     TEXT,
    productos_cantidad_presentacion     TEXT,
    productos_unidad_medida_presentacion TEXT,
    productos_cantidad_referencia       TEXT,
    productos_unidad_medida_referencia  TEXT,
    updated_at                          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS dim_fecha (
    fecha_id        INT PRIMARY KEY,   -- YYYYMMDD format
    fecha           DATE        NOT NULL UNIQUE,
    anio            SMALLINT    NOT NULL,
    mes             SMALLINT    NOT NULL,
    trimestre       SMALLINT    NOT NULL,
    semana_iso      SMALLINT    NOT NULL,
    dia_semana      SMALLINT    NOT NULL,   -- 1=Monday … 7=Sunday (ISO)
    es_fin_semana   BOOLEAN     NOT NULL
);
