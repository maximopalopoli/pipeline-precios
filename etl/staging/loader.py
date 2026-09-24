"""
loader.py — Staging loader for SEPA ZIP files.

Opens the main ZIP → iterates ~18 inner ZIPs → extracts and consolidates
comercio.csv / sucursales.csv / productos.csv → loads into stg_* tables
via COPY.

All columns are kept as strings (VARCHAR) — no type coercion at this stage.
Returns a LoadResult with row counts and inner-archive processing metrics.
"""

import io
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pandas as pd
import psycopg2
import structlog

from etl.development_ingestion import DevelopmentIngestionPolicy

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

_INNER_ZIP_CHAIN_KEY = re.compile(
    r"(?:^|/)sepa_(?P<id_bandera>\d+)_comercio-sepa-(?P<id_comercio>\d+)_"
)


# ── Result dataclass ───────────────────────────────────────────────────────────


@dataclass
class LoadResult:
    inner_zips_count: int
    rows_comercios: int
    rows_sucursales: int
    rows_productos: int
    rows_productos_source: int
    rows_productos_selected: int
    inner_zips_invalid_count: int = 0


# ── Public API ─────────────────────────────────────────────────────────────────


def load(
    conn: "psycopg2.connection",
    zip_path: Path,
    run_id: int,
    heartbeat: Callable[[], None] | None = None,
    development_policy: DevelopmentIngestionPolicy | None = None,
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
    heartbeat:
        Optional callback invoked before each inner ZIP, for long-running
        callers that need to refresh their execution lease.
    development_policy:
        Optional explicit bounds used by the future local partial-ingestion
        entrypoint. Callers must pass this value deliberately; ambient
        environment settings never change a normal staging load.

    Returns
    -------
    LoadResult with per-table row counts and inner-archive metrics.
    """
    development_policy = development_policy or DevelopmentIngestionPolicy()
    logger = log.bind(
        run_id=run_id,
        zip_path=str(zip_path),
        development_ingestion=development_policy.enabled,
        development_chain_keys=development_policy.chain_keys,
        development_max_product_rows=development_policy.max_product_rows,
    )
    logger.info("staging.load.start")

    _truncate_staging(conn, run_id, logger)
    _ensure_productos_partition(conn, run_id, logger)

    rows_comercios = 0
    rows_sucursales = 0
    rows_productos = 0
    rows_productos_source = 0
    rows_productos_selected = 0
    inner_zips_invalid_count = 0
    remaining_product_rows = development_policy.max_product_rows

    with zipfile.ZipFile(zip_path) as main_zip:
        inner_names = sorted(
            name for name in main_zip.namelist() if name.lower().endswith(".zip")
        )
        if development_policy.chain_keys:
            inner_names = [
                name
                for name in inner_names
                if _inner_zip_matches_chain_key(name, development_policy.chain_keys)
            ]
        logger.info("staging.inner_zips_found", count=len(inner_names))

        for inner_name in inner_names:
            if heartbeat is not None:
                heartbeat()
            # Inner archives can be large. Spill them to disk rather than
            # materialising them in memory before their product CSV is read.
            with (
                main_zip.open(inner_name) as compressed_inner,
                tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as inner_file,
            ):
                shutil.copyfileobj(compressed_inner, inner_file)
                inner_file.seek(0)
                try:
                    with zipfile.ZipFile(inner_file) as inner_zip:
                        # Build a flat lookup: basename → full path inside inner ZIP
                        csv_index = {
                            entry.split("/")[-1].lower(): entry
                            for entry in inner_zip.namelist()
                        }
                        for csv_name, cols, table, counter_name in [
                            (
                                "comercio.csv",
                                _COMERCIOS_COLS,
                                "stg_comercios",
                                "comercios",
                            ),
                            (
                                "sucursales.csv",
                                _SUCURSALES_COLS,
                                "stg_sucursales",
                                "sucursales",
                            ),
                            (
                                "productos.csv",
                                _PRODUCTOS_COLS,
                                "stg_productos",
                                "productos",
                            ),
                        ]:
                            if csv_name not in csv_index:
                                logger.warning(
                                    "staging.csv_missing",
                                    inner_zip=inner_name,
                                    csv=csv_name,
                                )
                                continue
                            with inner_zip.open(csv_index[csv_name]) as raw:
                                loaded_rows, source_rows, selected_rows = (
                                    _stream_copy_into(
                                        conn,
                                        table,
                                        cols,
                                        raw,
                                        run_id,
                                        source=inner_name,
                                        chain_keys=development_policy.chain_keys,
                                        max_rows=(
                                            remaining_product_rows
                                            if counter_name == "productos"
                                            else None
                                        ),
                                    )
                                )
                            if counter_name == "comercios":
                                rows_comercios += loaded_rows
                            elif counter_name == "sucursales":
                                rows_sucursales += loaded_rows
                            else:
                                rows_productos += loaded_rows
                                rows_productos_source += source_rows
                                rows_productos_selected += selected_rows
                                if remaining_product_rows is not None:
                                    remaining_product_rows -= loaded_rows
                except zipfile.BadZipFile as exc:
                    inner_zips_invalid_count += 1
                    logger.warning(
                        "staging.inner_zip_invalid",
                        inner_zip=inner_name,
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )

    logger.info(
        "staging.consolidated",
        rows_comercios=rows_comercios,
        rows_sucursales=rows_sucursales,
        rows_productos=rows_productos,
        rows_productos_source=rows_productos_source,
        rows_productos_selected=rows_productos_selected,
        inner_zips_invalid_count=inner_zips_invalid_count,
    )

    conn.commit()

    result = LoadResult(
        inner_zips_count=len(inner_names),
        rows_comercios=rows_comercios,
        rows_sucursales=rows_sucursales,
        rows_productos=rows_productos,
        rows_productos_source=rows_productos_source,
        rows_productos_selected=rows_productos_selected,
        inner_zips_invalid_count=inner_zips_invalid_count,
    )
    logger.info("staging.load.done", **result.__dict__)
    return result


def _inner_zip_matches_chain_key(
    inner_name: str, chain_keys: tuple[tuple[int, int], ...]
) -> bool:
    """Use the SEPA inner-ZIP name to exclude known unrelated chains.

    An unrecognised name is retained so a source naming change cannot silently
    drop data.  Normal (unbounded) ingestion always processes every inner ZIP.
    """
    match = _INNER_ZIP_CHAIN_KEY.search(inner_name)
    if match is None:
        return True
    chain_key = (int(match["id_comercio"]), int(match["id_bandera"]))
    return chain_key in chain_keys


def register_counts(
    conn: "psycopg2.connection",
    run_id: int,
    result: LoadResult,
) -> None:
    """
    Persist staging counts from *result* into ingestion_runs.

    Should be called after load() completes successfully.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ingestion_runs
               SET inner_zips_count   = %s,
                   inner_zips_invalid_count = %s,
                   rows_comercios     = %s,
                   rows_sucursales    = %s,
                   rows_productos_raw = %s,
                   rows_productos_selected = %s,
                   rows_productos_source = %s
             WHERE id = %s
            """,
            (
                result.inner_zips_count,
                result.inner_zips_invalid_count,
                result.rows_comercios,
                result.rows_sucursales,
                result.rows_productos,
                result.rows_productos_selected,
                result.rows_productos_source,
                run_id,
            ),
        )
    conn.commit()
    log.info(
        "staging.counts_registered",
        run_id=run_id,
        **result.__dict__,
    )


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
    stream = io.BytesIO(raw_bytes)
    # Strip BOM if present
    bom = stream.read(3)
    if bom != b"\xef\xbb\xbf":
        stream.seek(0)

    # Read all lines, drop the trailer and trailing blanks
    raw_lines = stream.read().decode("utf-8", errors="replace").splitlines()
    del stream
    while raw_lines:
        stripped = raw_lines[-1].strip()
        if stripped == "" or stripped.lower().startswith("ultima") or stripped.lower().startswith("última"):
            raw_lines.pop()
        else:
            break

    if not raw_lines:
        return pd.DataFrame(columns=expected_cols)

    df = pd.read_csv(
        io.StringIO("\n".join(raw_lines)),
        sep="|",
        dtype=str,
        keep_default_na=False,
    )
    del raw_lines
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


def _stream_copy_into(
    conn: "psycopg2.connection",
    table: str,
    expected_cols: list[str],
    raw_file: "zipfile.ZipExtFile",
    run_id: int,
    source: str = "",
    chain_keys: tuple[tuple[int, int], ...] = (),
    max_rows: int | None = None,
) -> tuple[int, int, int]:
    """
    Stream a CSV into a staging table via chunked COPY,
    bypassing pandas to keep memory usage low even for 400+ MB CSVs.

    Returns ``(loaded_rows, source_rows, selected_rows)``. Source rows are
    counted before chain bounds, selected rows after chain bounds but before a
    row cap, and loaded rows after all bounds. Input order is stable because
    callers process sorted inner ZIP names and this function preserves each
    CSV's source order.
    """
    import csv as csv_mod

    _CHUNK = 100_000  # rows per COPY batch

    text_stream = io.TextIOWrapper(
        raw_file, encoding="utf-8-sig", errors="replace", newline=""
    )
    reader = csv_mod.reader(text_stream, delimiter="|")

    try:
        header = [column.replace("\x00", "").strip() for column in next(reader)]
    except StopIteration:
        return 0, 0, 0
    if not any(header):
        return 0, 0, 0

    col_indices: list[int | None] = []
    missing = []
    for col in expected_cols:
        if col in header:
            col_indices.append(header.index(col))
        else:
            col_indices.append(None)
            missing.append(col)

    if missing:
        log.warning("staging.csv_columns_missing", source=source, missing=missing)

    col_list = ", ".join(["run_id"] + expected_cols)
    sql = f"COPY {table} ({col_list}) FROM STDIN WITH (FORMAT csv)"
    run_id_str = str(run_id)
    row_count = 0
    source_row_count = 0
    selected_row_count = 0

    def _flush_chunk(buf: io.StringIO) -> None:
        buf.seek(0)
        with conn.cursor() as cur:
            if hasattr(cur, "copy_expert"):
                cur.copy_expert(sql, buf)
            else:
                with cur.copy(sql) as copy:
                    copy.write(buf.read())

    buf = io.StringIO()
    writer = csv_mod.writer(buf)
    chunk_rows = 0

    comercio_idx = header.index("id_comercio") if "id_comercio" in header else None
    bandera_idx = header.index("id_bandera") if "id_bandera" in header else None

    for parsed in reader:
        parsed = [value.replace("\x00", "") for value in parsed]
        if not any(value.strip() for value in parsed):
            continue
        # Stop at trailer line
        lower = parsed[0].strip().lower()
        if lower.startswith("ultima") or lower.startswith("última"):
            break
        source_row_count += 1
        if chain_keys and not _matches_chain_key(parsed, comercio_idx, bandera_idx, chain_keys):
            continue
        selected_row_count += 1
        if max_rows is not None and row_count >= max_rows:
            continue
        row = [run_id_str]
        for idx in col_indices:
            row.append(parsed[idx] if idx is not None and idx < len(parsed) else "")
        writer.writerow(row)
        row_count += 1
        chunk_rows += 1

        if chunk_rows >= _CHUNK:
            _flush_chunk(buf)
            buf = io.StringIO()
            writer = csv_mod.writer(buf)
            chunk_rows = 0

    # Flush remaining rows
    if chunk_rows > 0:
        _flush_chunk(buf)
    del buf

    return row_count, source_row_count, selected_row_count


def _matches_chain_key(
    row: list[str],
    comercio_idx: int | None,
    bandera_idx: int | None,
    chain_keys: tuple[tuple[int, int], ...],
) -> bool:
    """Return whether a parsed source row belongs to an allowed chain."""
    if comercio_idx is None or bandera_idx is None:
        return False
    try:
        key = (int(row[comercio_idx]), int(row[bandera_idx]))
    except (IndexError, ValueError):
        return False
    return key in chain_keys


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
    df.insert(0, "run_id", str(run_id))
    # Use CSV format: pandas handles quoting of values that contain the delimiter
    df.to_csv(buf, index=False, header=False)
    df.drop(columns=["run_id"], inplace=True)
    buf.seek(0)

    col_list = ", ".join(["run_id"] + cols)
    sql = f"COPY {table} ({col_list}) FROM STDIN WITH (FORMAT csv)"

    # psycopg2: cursor.copy_expert(); psycopg3: cursor.copy()
    with conn.cursor() as cur:
        if hasattr(cur, "copy_expert"):
            cur.copy_expert(sql, buf)
        else:
            with cur.copy(sql) as copy:
                copy.write(buf.read())
