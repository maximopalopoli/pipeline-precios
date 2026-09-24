-- 030_drop_redundant_pending_run_date_index.sql
-- The pending-fact primary key begins with run_id, so it already serves every
-- run-scoped read and delete.  Keeping this duplicate index makes each streamed
-- provisional fact update an unnecessary second B-tree.

DROP INDEX IF EXISTS idx_pending_fact_precios_run_fecha;
