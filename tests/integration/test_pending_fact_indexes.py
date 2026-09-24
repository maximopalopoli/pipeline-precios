"""Schema invariants for indexes used while facts are pending publication."""


def test_pending_fact_primary_key_covers_run_scoped_access_without_redundant_index(pg_conn):
    """The PK starts with run_id; a separate (run_id, fecha_id) index is redundant."""
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT array_agg(attribute.attname ORDER BY key_position.ordinality)
            FROM pg_constraint AS constraint_row
            JOIN unnest(constraint_row.conkey) WITH ORDINALITY
                AS key_position(attribute_number, ordinality) ON TRUE
            JOIN pg_attribute AS attribute
                ON attribute.attrelid = constraint_row.conrelid
                AND attribute.attnum = key_position.attribute_number
            WHERE constraint_row.conrelid = 'pending_fact_precios'::regclass
              AND constraint_row.contype = 'p'
            """
        )
        assert cur.fetchone()[0] == [
            "run_id",
            "producto_sk",
            "sucursal_sk",
            "fecha_id",
        ]

        cur.execute(
            """
            SELECT to_regclass('idx_pending_fact_precios_run_fecha')
            """
        )
        assert cur.fetchone()[0] is None
