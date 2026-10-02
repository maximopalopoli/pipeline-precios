-- seed_producto_overrides.sql
-- Curated product attribute overrides (migration 039). This file is the source
-- of truth: every migration run replaces producto_overrides with these rows,
-- so edit here, never in the database.
--
-- Add a row only when the chain vote is wrong; find candidates with
--   SELECT * FROM v_producto_conflictos_pendientes ORDER BY cadenas_fuente DESC;
-- Leave a column NULL to keep the voted value. contenido and contenido_unidad
-- (g, ml or u) go together. nota says why the vote is wrong and what was checked.
--
-- Example:
--   ('ean:<code>', NULL, 'LA SERENISIMA', 1000, 'ml',
--    'Two chains each: SERENISIMA and LA SERENISIMA; 1 l checked on the package'),

BEGIN;

DELETE FROM producto_overrides;

-- INSERT INTO producto_overrides
--     (producto_identity, descripcion, marca, contenido, contenido_unidad, nota)
-- VALUES
--     ...;

COMMIT;
