-- Convert each monthly fact partition into a monthly parent with daily leaf
-- partitions. Existing rows are copied transactionally before the legacy
-- monthly table is removed.

DO $$
DECLARE
    monthly RECORD;
    day_row RECORD;
    legacy_name TEXT;
    day_name TEXT;
BEGIN
    FOR monthly IN
        SELECT child.relname, pg_get_expr(child.relpartbound, child.oid) AS bounds
        FROM pg_inherits inheritance
        JOIN pg_class parent ON parent.oid = inheritance.inhparent
        JOIN pg_class child ON child.oid = inheritance.inhrelid
        JOIN pg_namespace namespace ON namespace.oid = child.relnamespace
        WHERE parent.relname = 'fact_precios'
          AND namespace.nspname = 'public'
          AND child.relname ~ '^fact_precios_[0-9]{6}$'
    LOOP
        legacy_name := monthly.relname || '_legacy';
        EXECUTE format('ALTER TABLE %I DETACH PARTITION %I', 'fact_precios', monthly.relname);
        EXECUTE format('ALTER TABLE %I RENAME TO %I', monthly.relname, legacy_name);
        EXECUTE format(
            'CREATE TABLE %I PARTITION OF fact_precios %s PARTITION BY RANGE (fecha_id)',
            monthly.relname, monthly.bounds
        );
        FOR day_row IN EXECUTE format('SELECT DISTINCT fecha_id FROM %I ORDER BY fecha_id', legacy_name)
        LOOP
            day_name := format('fact_precios_%s', day_row.fecha_id);
            EXECUTE format(
                'CREATE TABLE %I PARTITION OF %I FOR VALUES FROM (%s) TO (%s)',
                day_name, monthly.relname, day_row.fecha_id,
                to_char(to_date(day_row.fecha_id::text, 'YYYYMMDD') + 1, 'YYYYMMDD')
            );
        END LOOP;
        EXECUTE format('INSERT INTO fact_precios SELECT * FROM %I', legacy_name);
        EXECUTE format('DROP TABLE %I', legacy_name);
    END LOOP;
END
$$;
