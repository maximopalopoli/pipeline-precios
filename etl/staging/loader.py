"""
loader.py — Staging loader for SEPA ZIP files.

Opens the main ZIP → iterates ~18 inner ZIPs → extracts and consolidates
comercio.csv / sucursales.csv / productos.csv → loads into stg_* tables
via COPY.

All columns are kept as strings (VARCHAR) — no type coercion at this stage.
Returns a LoadResult with row counts per table and the number of inner ZIPs
processed.
"""

import io
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import psycopg2
import structlog

log = structlog.get_logger(__name__)

# Columns expected per CSV type — must match staging table column order
# (run_id is injected by the loader, not present in the source CSV)

_COMERCIOS_COLS = [
    "id_comercio",
    "id_bandera",
    "comercio_cuit",
    "comercio_razon_social",
    "comercio_bandera_nombre",
    "comercio_bandera_url",
    "comercio_ultima_actualizacion",
    "comercio_version_sepa",
]

_SUCURSALES_COLS = [
    "id_comercio",
    "id_bandera",
    "id_sucursal",
    "sucursales_nombre",
    "sucursales_tipo",
    "sucursales_calle",
    "sucursales_numero",
    "sucursales_latitud",
    "sucursales_longitud",
    "sucursales_observaciones",
    "sucursales_barrio",
    "sucursales_codigo_postal",
    "sucursales_localidad",
    "sucursales_provincia",
    "sucursales_lunes_horario_atencion",
    "sucursales_martes_horario_atencion",
    "sucursales_miercoles_horario_atencion",
    "sucursales_jueves_horario_atencion",
    "sucursales_viernes_horario_atencion",
    "sucursales_sabado_horario_atencion",
    "sucursales_domingo_horario_atencion",
]

_PRODUCTOS_COLS = [
    "id_comercio",
    "id_bandera",
    "id_sucursal",
    "id_producto",
    "productos_ean",
    "productos_descripcion",
    "productos_cantidad_presentacion",
    "productos_unidad_medida_presentacion",
    "productos_marca",
    "productos_precio_lista",
    "productos_precio_referencia",
    "productos_cantidad_referencia",
    "productos_unidad_medida_referencia",
    "productos_precio_unitario_promo1",
    "productos_leyenda_promo1",
    "productos_precio_unitario_promo2",
    "productos_leyenda_promo2",
]


# ── Result dataclass ───────────────────────────────────────────────────────────


@dataclass
class LoadResult:
    inner_zips_count: int
    rows_comercios: int
    rows_sucursales: int
    rows_productos: int


# ── Public API ─────────────────────────────────────────────────────────────────


def load(
    conn: "psycopg2.connection",
    zip_path: Path,
    run_id: int,
) -> LoadResult:
    """
    Parse *zip_path* and load all three staging tables for *run_id*.

    Idempotent: existing rows for *run_id* are removed before loading so the
    function can be safely retried.

    Parameters
    ----------
    conn:
        Open psycopg2 connection (caller manages the outer transaction scope).
    zip_path:
        Path to the main SEPA ZIP downloaded by the extract stage.
    run_id:
        ingestion_runs.id for this pipeline run.

    Returns
    -------
    LoadResult with per-table row counts and inner_zips_count.
    """
    logger = log.bind(run_id=run_id, zip_path=str(zip_path))
    logger.info("staging.load.start")

    comercios_frames: list[pd.DataFrame] = []
    sucursales_frames: list[pd.DataFrame] = []
    productos_frames: list[pd.DataFrame] = []

    with zipfile.ZipFile(zip_path) as main_zip:
        inner_names = sorted(
            name for name in main_zip.namelist() if name.lower().endswith(".zip")
        )
        logger.info("staging.inner_zips_found", count=len(inner_names))

        for inner_name in inner_names:
            inner_bytes = main_zip.read(inner_name)
            with zipfile.ZipFile(io.BytesIO(inner_bytes)) as inner_zip:
                # Build a flat lookup: basename → full path inside inner ZIP
                csv_index = {
                    entry.split("/")[-1].lower(): entry
                    for entry in inner_zip.namelist()
                }
                for csv_name, cols, frames in [
                    ("comercio.csv", _COMERCIOS_COLS, comercios_frames),
                    ("sucursales.csv", _SUCURSALES_COLS, sucursales_frames),
                    ("productos.csv", _PRODUCTOS_COLS, productos_frames),
                ]:
                    if csv_name not in csv_index:
                        logger.warning(
                            "staging.csv_missing",
                            inner_zip=inner_name,
                            csv=csv_name,
                        )
                        continue
                    raw = inner_zip.read(csv_index[csv_name])
                    df = _parse_csv(raw, cols, source=inner_name)
                    frames.append(df)

    empty_comercios = pd.DataFrame(columns=_COMERCIOS_COLS)
    empty_sucursales = pd.DataFrame(columns=_SUCURSALES_COLS)
    empty_productos = pd.DataFrame(columns=_PRODUCTOS_COLS)

    comercios = pd.concat(comercios_frames, ignore_index=True) if comercios_frames else empty_comercios
    sucursales = pd.concat(sucursales_frames, ignore_index=True) if sucursales_frames else empty_sucursales
    productos = pd.concat(productos_frames, ignore_index=True) if productos_frames else empty_productos

    logger.info(
        "staging.consolidated",
        rows_comercios=len(comercios),
        rows_sucursales=len(sucursales),
        rows_productos=len(productos),
    )

    _truncate_staging(conn, run_id, logger)
    _ensure_productos_partition(conn, run_id, logger)

    _copy_into(conn, "stg_comercios", _COMERCIOS_COLS, comercios, run_id)
    _copy_into(conn, "stg_sucursales", _SUCURSALES_COLS, sucursales, run_id)
    _copy_into(conn, "stg_productos", _PRODUCTOS_COLS, productos, run_id)

    conn.commit()

    result = LoadResult(
        inner_zips_count=len(inner_names),
        rows_comercios=len(comercios),
        rows_sucursales=len(sucursales),
        rows_productos=len(productos),
    )
    logger.info("staging.load.done", **result.__dict__)
    return result


# ── Private helpers ────────────────────────────────────────────────────────────


def _parse_csv(
    raw_bytes: bytes,
    expected_cols: list[str],
    source: str = "",
) -> pd.DataFrame:
    """
    Decode, strip the trailer line, and parse a SEPA CSV.

    All values are kept as strings (dtype=str). The trailer line — last
    non-empty line starting with "Ultima" or "Última" — is discarded before
    parsing so pandas never sees it.
    """
    text = raw_bytes.decode("utf-8", errors="replace")
    lines = text.splitlines()

    # Drop the trailer and any trailing blank lines
    while lines:
        stripped = lines[-1].strip()
        if stripped == "" or stripped.lower().startswith("ultima") or stripped.lower().startswith("última"):
            lines.pop()
        else:
            break

    if not lines:
        return pd.DataFrame(columns=expected_cols)

    df = pd.read_csv(
        io.StringIO("\n".join(lines)),
        sep="|",
        dtype=str,
        keep_default_na=False,
    )
    df.columns = [c.strip() for c in df.columns]

    # Warn about unexpected schema and ensure all expected columns are present
    missing = [c for c in expected_cols if c not in df.columns]
    if missing:
        log.warning(
            "staging.csv_columns_missing",
            source=source,
            missing=missing,
        )
        for col in missing:
            df[col] = ""

    return df[expected_cols]


def _truncate_staging(
    conn: "psycopg2.connection",
    run_id: int,
    logger: structlog.BoundLogger,
) -> None:
    """Remove prior rows for *run_id* from the non-partitioned staging tables."""
    with conn.cursor() as cur:
        for table in ("stg_comercios", "stg_sucursales"):
            cur.execute(f"DELETE FROM {table} WHERE run_id = %s", (run_id,))
            logger.info("staging.deleted", table=table, rows=cur.rowcount)


def _ensure_productos_partition(
    conn: "psycopg2.connection",
    run_id: int,
    logger: structlog.BoundLogger,
) -> None:
    """
    Drop and recreate the stg_productos partition for *run_id*.

    This is the idempotent truncate for the partitioned table: dropping the
    entire child table is cheaper than DELETE on a large products dataset.
    """
    partition_name = f"stg_productos_{run_id}"
    with conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {partition_name}")
        cur.execute(
            f"""
            CREATE TABLE {partition_name}
                PARTITION OF stg_productos
                FOR VALUES IN ({run_id})
            """
        )
    logger.info("staging.partition_ready", partition=partition_name)


def _copy_into(
    conn: "psycopg2.connection",
    table: str,
    cols: list[str],
    df: pd.DataFrame,
    run_id: int,
) -> None:
    """
    Bulk-load *df* into *table* using PostgreSQL COPY.

    Prepends run_id to every row. Empty DataFrames are skipped.
    """
    if df.empty:
        return

    buf = io.StringIO()
    df_out = df.copy()
    df_out.insert(0, "run_id", str(run_id))
    # Use CSV format: pandas handles quoting of values that contain the delimiter
    df_out.to_csv(buf, index=False, header=False)
    buf.seek(0)

    col_list = ", ".join(["run_id"] + cols)
    with conn.cursor() as cur:
        cur.copy_expert(
            f"COPY {table} ({col_list}) FROM STDIN WITH (FORMAT csv)",
            buf,
        )
