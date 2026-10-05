-- Retire the province-level analytics aggregates. Shopper routes read the
-- published snapshot directly, and price history lives in archived source
-- ZIPs, so publication no longer rebuilds a daily aggregate.

DROP VIEW IF EXISTS v_variacion_precios;
DROP TABLE IF EXISTS agg_precios_diarios;

ALTER TABLE ingestion_publication_metrics
    DROP COLUMN IF EXISTS aggregate_delete_seconds,
    DROP COLUMN IF EXISTS aggregate_insert_seconds;
