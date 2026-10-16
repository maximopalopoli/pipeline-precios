-- 043_drop_ingestion_benchmark_metrics.sql
-- Benchmarks now read stage timings from each run's JSON log (benchmarks/),
-- so the pipeline no longer writes these tables.

DROP TABLE IF EXISTS ingestion_fact_load_metrics;
DROP TABLE IF EXISTS ingestion_publication_metrics;
