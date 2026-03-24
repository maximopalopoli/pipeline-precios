-- 002_create_staging.sql
-- Staging tables: exact mirror of CSVs, all VARCHAR

CREATE TABLE IF NOT EXISTS stg_comercios (
    run_id                          INT         NOT NULL REFERENCES ingestion_runs (id),
    id_comercio                     VARCHAR,
    id_bandera                      VARCHAR,
    comercio_cuit                   VARCHAR,
    comercio_razon_social           VARCHAR,
    comercio_bandera_nombre         VARCHAR,
    comercio_bandera_url            VARCHAR,
    comercio_ultima_actualizacion   VARCHAR,
    comercio_version_sepa           VARCHAR
);

CREATE INDEX IF NOT EXISTS idx_stg_comercios_run ON stg_comercios (run_id);

-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS stg_sucursales (
    run_id                                  INT         NOT NULL REFERENCES ingestion_runs (id),
    id_comercio                             VARCHAR,
    id_bandera                              VARCHAR,
    id_sucursal                             VARCHAR,
    sucursales_nombre                       VARCHAR,
    sucursales_tipo                         VARCHAR,
    sucursales_calle                        VARCHAR,
    sucursales_numero                       VARCHAR,
    sucursales_latitud                      VARCHAR,
    sucursales_longitud                     VARCHAR,
    sucursales_observaciones                VARCHAR,
    sucursales_barrio                       VARCHAR,
    sucursales_codigo_postal                VARCHAR,
    sucursales_localidad                    VARCHAR,
    sucursales_provincia                    VARCHAR,
    sucursales_lunes_horario_atencion       VARCHAR,
    sucursales_martes_horario_atencion      VARCHAR,
    sucursales_miercoles_horario_atencion   VARCHAR,
    sucursales_jueves_horario_atencion      VARCHAR,
    sucursales_viernes_horario_atencion     VARCHAR,
    sucursales_sabado_horario_atencion      VARCHAR,
    sucursales_domingo_horario_atencion     VARCHAR
);

CREATE INDEX IF NOT EXISTS idx_stg_sucursales_run ON stg_sucursales (run_id);

-- ------------------------------------------------------------
-- stg_productos: partitioned by run_id (LIST) for efficient
-- truncation per run without affecting other runs

CREATE TABLE IF NOT EXISTS stg_productos (
    run_id                              INT         NOT NULL REFERENCES ingestion_runs (id),
    id_comercio                         VARCHAR,
    id_bandera                          VARCHAR,
    id_sucursal                         VARCHAR,
    id_producto                         VARCHAR,
    productos_ean                       VARCHAR,
    productos_descripcion               VARCHAR,
    productos_cantidad_presentacion     VARCHAR,
    productos_unidad_medida_presentacion VARCHAR,
    productos_marca                     VARCHAR,
    productos_precio_lista              VARCHAR,
    productos_precio_referencia         VARCHAR,
    productos_cantidad_referencia       VARCHAR,
    productos_unidad_medida_referencia  VARCHAR,
    productos_precio_unitario_promo1    VARCHAR,
    productos_leyenda_promo1            VARCHAR,
    productos_precio_unitario_promo2    VARCHAR,
    productos_leyenda_promo2            VARCHAR
) PARTITION BY LIST (run_id);

-- Catch-all partition for run_ids not explicitly declared.
-- The loader will create run-specific partitions before loading.
CREATE TABLE IF NOT EXISTS stg_productos_default
    PARTITION OF stg_productos DEFAULT;
