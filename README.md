# SEPA Precios

A shopping-list price comparator for nearby supermarkets in AMBA (CABA and
Greater Buenos Aires), built on the daily retail price data from
[Argentina's SEPA price transparency system](https://datos.produccion.gob.ar/dataset/sepa-precios),
published by the Secretaría de Comercio Interior.

The source reports prices at the **product × store × day** level under
Resolución 678/2020: about 12–15 million observations per day from roughly 20
chains. The product goals are:

- **Unambiguous products.** Every offer maps to one product identity, so the
  user compares the same item across chains without guessing.
- **Visible freshness.** Every price shows the date it was reported.
- **Nearby and focused.** AMBA only for now; about half of the national rows.

This repository holds the ETL pipeline that ingests, validates and publishes
that data, plus the API and web client that serve it.

## Architecture

```
Daily ZIP (~300 MB)
  └── ~18 sub-ZIPs (one per chain)
        └── 3 CSVs: comercio.csv | sucursales.csv | productos.csv

          ┌─────────────┐
          │  Download    │  etl/extract/downloader.py
          │  + SHA-256   │  → raw_files (download registry)
          └──────┬───────┘
                 │
          ┌──────▼───────┐
          │   Staging     │  etl/staging/loader.py
          │  (VARCHAR)    │  → stg_comercios, stg_sucursales, stg_productos
          └──────┬───────┘
                 │
          ┌──────▼───────┐
          │   Quality     │  etl/quality/checks.py + reporters.py
          │   Checks      │  8 critical + 7 warnings → rejected_rows
          └──────┬───────┘
                 │
          ┌──────▼───────┐
          │  Transform    │  etl/transform/cleaner.py
          │  + Resolve    │  etl/transform/dimension_resolver.py + fact_builder.py
          └──────┬───────┘
                 │
          ┌──────▼───────┐
          │    Load       │  etl/load/warehouse_loader.py
          │  (Upsert)     │  → dim_*, fact_precios (month → day partitions)
          └──────┬───────┘
                 │
          ┌──────▼───────┐
          │  Aggregate    │  etl/load/aggregator.py
          │               │  → agg_precios_diarios, v_variacion_precios
          └───────────────┘
```

### Database Schema (Star Schema — PostgreSQL 16)

| Layer | Tables |
|-------|--------|
| **Metadata** | `raw_files`, `ingestion_runs` |
| **Staging** | `stg_comercios`, `stg_sucursales`, `stg_productos` |
| **Dimensions** | `dim_comercio`, `dim_sucursal`, `dim_producto`, `dim_provincia`, `dim_fecha` |
| **Fact** | `fact_precios` (monthly parents with daily leaves; grain: product × store × day) |
| **Aggregations** | `agg_precios_diarios`, view `v_variacion_precios` |
| **Quality** | `rejected_rows` |

## Project Structure

```
pipeline-precios/
├── etl/
│   ├── extract/downloader.py         # ZIP download + SHA-256 hash
│   ├── source_archive.py             # Declared-date validation from inner ZIP names
│   ├── ingestion_date.py             # Logical date in the Buenos Aires time zone
│   ├── staging/loader.py             # Nested ZIP parsing → staging
│   ├── quality/checks.py             # 8 critical checks + 7 warnings
│   ├── quality/reporters.py          # Structured logging + persistence
│   ├── transform/cleaner.py          # Type casting + normalization
│   ├── transform/product_identity.py # EAN vs chain-scoped product identity
│   ├── transform/dimension_resolver.py  # Surrogate key resolution (SCD Type 1)
│   ├── transform/fact_builder.py     # Fact DataFrame construction
│   ├── load/warehouse_loader.py      # Bulk COPY + upserts + pending facts
│   ├── load/publisher.py             # Atomic daily publication
│   ├── load/aggregator.py            # Daily aggregate recalculation
│   ├── ingestion_run.py, ingestion_lock.py, reconciliation.py  # Run records, per-date lock, counts
│   ├── development_ingestion.py      # Bounded partial development mode
│   ├── *_retention.py                # Opt-in fact, staging and raw ZIP retention
│   └── run_pipeline.py               # End-to-end orchestration (shared with Airflow)
├── models/
│   ├── comercio.py                   # ComercioRecord (Pydantic v2)
│   ├── sucursal.py                   # SucursalRecord
│   └── producto.py                   # ProductoRecord
├── db/
│   ├── migrations/                   # ordered SQL migrations, checksum-verified
│   └── seeds/                        # Provinces (ISO 3166-2) + calendar
├── tests/
│   ├── unit/                         # focused module and lifecycle tests
│   ├── integration/                  # database-backed ETL, publication and entrypoint tests
│   └── fixtures/sample.zip           # Sample ZIP (2 chains, 7 products)
├── scripts/                          # Baseline, benchmark and regression-check commands
├── dags/dag_sepa_ingest.py           # Airflow DAG (7 tasks, dual daily schedule)
├── api/                              # FastAPI backend (REST endpoints, asyncpg)
├── web/                              # React frontend (Vite + TypeScript + Tailwind)
├── notebooks/exploration.ipynb       # Exploratory analysis
├── docker-compose.yml                # PostgreSQL + Airflow + API + Web
├── pyproject.toml                    # Dependencies and configuration
└── Makefile                          # Targets: install, test, test-unit, test-integration
```

## SEPA Dataset

Source: [datos.produccion.gob.ar — Base SEPA](https://datos.produccion.gob.ar/dataset/sepa-precios),
under Resolución 678/2020, Annex II. The same data feeds the official
[Precios Claros](https://www.preciosclaros.gob.ar/) site.

The portal publishes one ZIP per weekday (~300–330 MB) at a fixed URL,
`.../download/sepa_{dia}.zip` (`lunes` … `domingo`). Each URL is overwritten
weekly — there is no historical archive, so keep local copies (by hash) if you
need to replay a day. Chains must deliver by 06:00 on the effective day and may
rectify until 10:00, which is why the DAG runs at 06:30 and 10:30.

### ZIP Structure

```
sepa_martes.zip
└── 2026-03-17/
    ├── sepa_1_comercio-sepa-8_2026-03-17_09-05-11.zip
    │   ├── comercio.csv      (one row per banner)
    │   ├── sucursales.csv    (one row per store)
    │   └── productos.csv     (one row per product per store)
    └── ... (one inner ZIP per chain delivery)
```

- **Delimiter:** pipe `|`
- **Encoding:** UTF-8
- **Prices:** dot `.` as decimal separator
- **Quoting:** none; header row present
- **Trailer:** each CSV ends with a `&#032;` line and
  `Última actualización:YYYY-MM-DDTHH:MM:SS-03:00`. The staging loader discards
  the `Última` line and blank lines; any other reader must also drop the
  `&#032;` line (or keep only rows with a numeric `id_comercio`).
- **Types:** staging reads every value as text; `etl/transform/cleaner.py` casts.

### CSV schemas

`comercio.csv` — one row per banner (`bandera`):

| Field | Type / notes | Example |
|-------|--------------|---------|
| `id_comercio` | int | 123 |
| `id_bandera` | int, from 1 | 1 |
| `comercio_cuit` | str, no dashes | 30123456787 |
| `comercio_razon_social` | str | Supermercado S.R.L. |
| `comercio_bandera_nombre` | str, may have trailing spaces | El Rey del Super |
| `comercio_bandera_url` | str (`n/a` observed) | http://www.el-rey-del-super.com.ar |
| `comercio_ultima_actualizacion` | ISO 8601 | 2016-02-29T14:04:00-03:00 |
| `comercio_version_sepa` | float | 1.0 |

`sucursales.csv` — one row per store, web stores included:

| Field | Type / notes | Example |
|-------|--------------|---------|
| `id_comercio`, `id_bandera` | chain key | 123, 1 |
| `id_sucursal` | digits; stable, unique only within the chain, leading zeros vary | 7, 00866 |
| `sucursales_nombre` | str | Sucursal Pompeya |
| `sucursales_tipo` | Hipermercado / Supermercado / Autoservicio / Tradicional / Web | Supermercado |
| `sucursales_calle`, `sucursales_numero` | str, int (empty or 0 when unknown) | Avenida Hipólito Yrigoyen, 250 |
| `sucursales_latitud`, `sucursales_longitud` | float, empty for web stores | -34.603722, -58.381592 |
| `sucursales_observaciones`, `sucursales_barrio` | str, optional | Local 123, Villa Lugano |
| `sucursales_codigo_postal`, `sucursales_localidad` | int, str | 1426, Mar de Ajó |
| `sucursales_provincia` | ISO 3166-2 | AR-B |
| `sucursales_{lunes…domingo}_horario_atencion` | `HH:mm a HH:mm` or `cerrado`; not reliable (`6:00 a 22:00 hs.` observed) | 08:00 a 21:00 |

`productos.csv` — one row per product per store (~13–15 M rows/day):

| Field | Type / notes | Example |
|-------|--------------|---------|
| `id_comercio`, `id_bandera`, `id_sucursal` | store key | 123, 1, 7 |
| `id_producto` | 13 digits, EAN/UPC or internal code, zero-padded | 0779027200550 |
| `productos_ean` | 1 = standard EAN, 0 = internal code | 1 |
| `productos_descripcion`, `productos_marca` | str | Aceite oliva Freir aerosol 120ml, Freir |
| `productos_cantidad_presentacion` | number | 120 |
| `productos_unidad_medida_presentacion` | ml / l / kg / g / unidad …, case varies | ml |
| `productos_precio_lista` | 2 decimals, price without discounts | 111.66 |
| `productos_precio_referencia` | price per reference unit | 200.00 |
| `productos_cantidad_referencia`, `productos_unidad_medida_referencia` | int, unit | 1, l |
| `productos_precio_unitario_promo1`, `productos_leyenda_promo1` | optional general discount (3x2, brand) | 73.70, 3x2 - Vigencia: … |
| `productos_precio_unitario_promo2`, `productos_leyenda_promo2` | optional special discount (card, retiree, loyalty) | 78.16, 50% 2da unidad con tarjeta … |

A few duplicate product × store keys exist in the source (63 on 2026-03-17);
they are deduplicated deterministically.

Data rules the pipeline relies on:

- **Trust the date inside the archive.** The date folder and inner ZIP names
  must match the logical date; a mismatch aborts the run. Local file names are
  not evidence.
- **Product identity.** A product is `ean:<id>` only when the source flags it as
  an EAN and it passes the EAN-8/13 checksum; otherwise it is scoped to its
  chain. Products are never merged by name similarity.
- **Store identity.** `id_sucursal` is unique only within a chain.
- **Prices.** The list price must be > 0; a promotion counts only when it is
  > 0 and lower than the list price. Bank and wallet discounts are not published.
- **Completeness varies.** Whole chains can be missing on a given day, and an
  inner ZIP can be malformed (it is skipped and counted). A chain absent from
  one file has not closed its stores.
- **Not only supermarkets.** SEPA also includes pharmacies and gas-station
  shops (see the table below).

### Chains observed

`id_comercio` values seen in 2026-03 and 2026-09 files; rows in thousands for
2026-03-24.

| `id_comercio` | Banners | Kind | Rows (k) |
|---:|---|---|---:|
| 2 | La Anónima, Topsy, Bomba | Supermarket | 872 |
| 3 | Deheza | Supermarket | 11 |
| 4 | Estación Lima | Gas-station shop | < 1 |
| 5 | California Supermercados | Supermarket | 24 |
| 6 | Comodín, Maxi Comodín | Supermarket | 19 |
| 8 | Mariano Max | Supermarket | 43 |
| 9 | Jumbo, Vea, Disco (Cencosud) | Supermarket | 1,325 |
| 10 | Carrefour Hiper, Market, Express, Maxi | Supermarket | 3,551 |
| 11 | Changomás (Hiper, Super) | Supermarket | 2,704 |
| 12 | Coto | Supermarket | 1,005 |
| 13 | Cooperativa Obrera | Supermarket | 685 |
| 15 | DIA | Supermarket | 3,883 |
| 16 | Libertad (Hiper, Petit, Mini) | Supermarket | 120 |
| 19 | FULL | Gas-station shop | 37 |
| 20 | La Agrícola Regional | Supermarket | 7 |
| 21 | Toledo | Supermarket | 96 |
| 23 | Axion Energy | Gas-station shop | 6 |
| 24 | Farmacity, Simplicity | Pharmacy / drugstore | 371 |
| 36 | SuperCLC | Supermarket | invalid ZIP on 09-22 |
| 47 | Unicoop | Supermarket | 19 |

### Observed behavior

Measured over the files for 2026-03-17, 03-19, 03-20, 03-23, 03-24 and 09-22:

- Whole chains go missing on some days: Cencosud on 03-17, Farmacity on 3 of
  the 6 days, Libertad and FULL on 09-22.
- 1.9–3.1% of product × store rows change price (list or promo) per day, about
  10% per week and 84% over six months.
- Prices are mostly uniform within a chain: only 6.1% of rows have a distinct
  (chain, banner, product, prices) combination.
- About 83 k EAN products and 4.3 k internal-code products per day; EANs sold by
  2 or more chains cover 67% of all rows. Fresh products are largely absent or
  use internal codes.
- Promo 1 appears on 3% of rows and promo 2 on 0.9%; legends are store
  discounts, not bank or wallet promotions.
- About 3,160 physical stores with prices per day, nearly all with plausible
  coordinates. AMBA (CABA + Buenos Aires province within 60 km of the Obelisco)
  is 1,834 stores and 52% of the rows.

## Requirements

- Python 3.11+
- Node.js 18+ (for the frontend)
- Docker and Docker Compose
- PostgreSQL 16 (via Docker)

## Quick Start

```bash
make setup                # create venv + install Python & Node deps
make db                   # start PostgreSQL and apply pending migrations and seeds
make ingest               # download today's SEPA ZIP and run the full ETL pipeline
make ingest-bounded DATE=2026-03-17 DEV_CHAIN_KEYS=1:1 DEV_MAX_PRODUCT_ROWS=5000
make dev                  # start DB + API + frontend for local development
```

That's it. The web app will be at http://localhost:5173 and the API at http://localhost:8000.

The checked-in development defaults are sufficient for a local run. To use
different database or Airflow credentials, copy `.env.example` to `.env` and
edit it before running the commands above.

### Migration integrity

`make db` runs `db/run-migrations.sh`. The runner records the SHA-256 checksum
of every applied migration in `schema_migrations` and verifies it before each
subsequent run. This prevents a database from silently drifting after a
historical migration has been edited.

When upgrading a database created before checksum support, its currently
applied migration files are recorded as a one-time baseline. After that first
run, a changed applied migration stops the runner and reports its filename.

If this happens, do not update the stored checksum or edit the historical SQL
to make the error disappear. Restore the applied migration from version
control and add a new, ordered migration for the intended schema/data change.

### Ingest a specific date

```bash
make ingest-date DATE=2026-03-17
```

The pipeline downloads the daily ZIP (~300 MB), processes ~12M price observations
(staging → quality checks → dimensions → pending facts → atomic publication), and
loads them into PostgreSQL.

### Run a bounded local ingestion

Use the explicit Make target for a deterministic local walk-through without
publishing a daily snapshot:

```bash
make ingest-bounded DATE=2026-03-17 DEV_CHAIN_KEYS=1:1 DEV_MAX_PRODUCT_ROWS=5000
```

At least one bound is required. `DEV_CHAIN_KEYS` follows the same
comma-separated `id_comercio:id_bandera` format as `SEPA_DEV_CHAIN_KEYS`; the
row cap is optional when chains alone are enough.

### Ingestion results and failures

The CLI exits with code `0` when it completes an ingestion or skips a raw file
that already has a completed publication. It exits with a non-zero code when a
critical quality check or an operational error aborts the run.

Every run opened after a download is recorded in `ingestion_runs`. A failed run
has `status = 'failed'`, `finished_at`, and an error message. The same raw file
remains eligible for a later retry until a run completes successfully. Critical
quality failures retain their structured check details in the error message.

### Product-row reconciliation

Completed runs created by the current pipeline account for every staged product
row through publication. The product-specific counters on `ingestion_runs` are:

| Counter | Meaning |
|---------|---------|
| `rows_productos_source` | Product rows inspected in source archives before development chain bounds. |
| `rows_productos_selected` | Source rows selected by development chain bounds, before an optional row cap. |
| `rows_productos_raw` | Rows staged after all development bounds; the input to product cleaning. |
| `rows_productos_valid` | Rows accepted by product cleaning. |
| `rows_productos_rejected` | Rows rejected by product cleaning; their reason and payload, along with missing-key rows, are kept in `rejected_rows`. |
| `rows_productos_without_keys` | Valid rows that could not resolve the dimensions required for a fact. |
| `rows_productos_deduplicated` | Valid rows superseded by another row at the product × store fact grain. |
| `rows_fact_pending` | Unique facts held in the provisional set before publication. |
| `rows_fact_published` | Facts made visible by the completed atomic publication. |
| `inner_zips_invalid_count` | Nested SEPA ZIPs that were malformed and skipped; these contain no product rows to reconcile. |

For a completed run, the following invariants hold:

```text
rows_productos_source >= rows_productos_selected >= rows_productos_raw
rows_productos_raw = rows_productos_valid + rows_productos_rejected
rows_productos_valid = rows_productos_without_keys + rows_productos_deduplicated + rows_fact_pending
rows_fact_pending = rows_fact_published  # full runs only
```

For an explicit partial development run, `rows_fact_published` is zero and its
pending rows remain isolated for local inspection; that is the intended
exception to the last full-run invariant.

The API exposes these fields as `reconciliacion` in `GET /api/health`; the home
page displays the latest available summary. Older runs predate these counters,
so their reconciliation is `null` rather than a failed or incomplete status.

### Performance telemetry

The CLI and Airflow emit structured events for `download`, `staging`, `quality`,
`dimensions`, `fact_load`, and `publication`. Each completed or failed stage
includes `elapsed_seconds` and `peak_rss_bytes`; stage-specific events also
include their relevant row counts. Product fact processing adds one pair of
events per chunk: `fact_chunk_preparation` measures cleaning and key resolution,
while `pending_fact_write` measures the provisional PostgreSQL write.

The CLI's `pipeline.completed` event summarizes the end-to-end `elapsed_total`,
`peak_rss_bytes`, reconciliation counts, and `fact_rows_per_second`. Airflow
tasks may run in separate processes, so their per-stage `peak_rss_bytes` values
are intentionally not combined into a single run-level number.

To establish a comparable baseline, capture one bounded ingestion's output:

```bash
make ingest-date DATE=2026-03-17 2>&1 | tee ingestion-2026-03-17.log
```

Compare the same source size and row counts before using the timings to choose
an optimization. `peak_rss_bytes` is the peak resident memory of the ETL process,
not PostgreSQL's memory or the full container/host usage; collect those separately
when diagnosing database or infrastructure capacity.

For a bounded run, terminal events always include `is_partial=true`, pending
fact count, and zero published facts. Treat its throughput as a local flow
measurement, never as a published snapshot or a full-SEPA capacity result.

After `staging` and `publication`, they also emit a storage snapshot with the
database total plus `fact_precios`, pending facts, and staging relation sizes.
These PostgreSQL relation sizes include indexes; compare snapshots from the
same source and bounds before drawing conclusions about capacity.

Two read-only commands provide repeatable evidence before changing indexes or
database infrastructure:

```bash
# Latest completed run, or a specific local run.
make etl-baseline RUN_ID=5

# Compare only completed replays that share source ZIP and bounded policy.
make compare-etl-baselines RUN_ID=5

# Representative /api/kpis, /api/buscador and /api/ofertas plans.
# Requires at least one full published snapshot; bounded runs publish no facts.
make audit-queries FECHA_ID=20260922
```

`etl-baseline` reads durable run-level timing, source size, chunk size, fact-load
duration, fact-load process-memory high-water mark and
reconciliation counts. `compare-etl-baselines` excludes runs that do not share
the source archive and bounds, so its JSON rows differ only in the benchmark
configuration such as `fact_chunk_size`; it also excludes historical runs that
predate durable fact-load telemetry. `audit-queries` runs `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` and
emits planning/execution time, row counts and shared-buffer reads as JSON.
Capture a baseline before and after a candidate index migration; do not infer
full-scale capacity from a bounded run.

### Bounded development ingestion

The local bounded-ingestion mode is opt-in and uses `SEPA_DEV_CHAIN_KEYS`
(comma-separated `id_comercio:id_bandera` values) and/or
`SEPA_DEV_MAX_PRODUCT_ROWS` (a positive integer). Both settings are validated
strictly and are disabled when unset. When either is set, CLI and Airflow mark
the run as partial, retain
its staging and pending facts only for local inspection, and publish zero facts
and aggregates. They may add dimension keys required by their pending facts but
never overwrite attributes already visible from a full run. Partial runs skip
retention and never make the source look fully ingested, so a later full
ingestion of the same ZIP remains eligible. Their audit records retain both
source product rows and the subset selected into staging; `/health` exposes
that distinction and whether the latest run is partial.

For one-off CLI runs, the equivalent flags override the matching environment
bound: `--dev-chain-key ID_COMERCIO:ID_BANDERA` (repeatable) and
`--dev-max-product-rows N`. For example:

```bash
python -m etl.run_pipeline --fecha 2026-03-17 --dev-chain-key 1:1 --dev-max-product-rows 5000
```

`SEPA_FACT_CHUNK_SIZE` controls how many staged product rows are prepared and
written per fact batch (default: 300000). It changes only memory/throughput
partitioning, not the published fact grain. Use the same value when comparing
baselines; for example, `make ingest-bounded DATE=... DEV_MAX_PRODUCT_ROWS=5000
FACT_CHUNK_SIZE=50000`.

To benchmark multiple chunk sizes against an already-downloaded source, replay
the exact ZIP instead of requesting the mutable weekday URL again:

```bash
make replay-bounded \
  ZIP=data/2026-09-22_martes_<sha256>.zip \
  DATE=2026-09-22 DEV_CHAIN_KEYS=12:1 DEV_MAX_PRODUCT_ROWS=50000 \
  FACT_CHUNK_SIZE=10000
```

Replay streams the local archive to calculate its SHA-256, registers or reuses
the same `raw_files` record, and performs the normal declared-date validation.
It does not copy the ZIP or contact the SEPA URL.

For a reproducible sizing experiment, run each candidate multiple times. The
command below performs runs sequentially, remains partial, and prints the
individual durable samples plus per-size medians and the Python/PostgreSQL/OS
versions used for the measurement:

```bash
make benchmark-chunks \
  ZIP=data/2026-09-22_martes_<sha256>.zip DATE=2026-09-22 \
  DEV_CHAIN_KEYS=12:1 DEV_MAX_PRODUCT_ROWS=50000 \
  CHUNK_SIZES=5000,10000,25000 REPEATS=3
```

Use its median durations and memory range to choose a local benchmark setting.
Stage logs go to standard error and the final report is JSON on standard output,
so it can be saved without mixing output streams: append
`> benchmark-chunks.json` to the command.
It deliberately does not alter `SEPA_FACT_CHUNK_SIZE`'s production default;
validate any production-sized change on a machine that can run a full source.

To use a checked-in or CI-provided reference report, compare an equivalent
candidate with tolerant limits (15% elapsed time and 20% RSS by default). The
command exits nonzero only for a material regression, not normal timing noise:

```bash
make check-benchmark-regression \
  BASELINE=benchmark-baseline.json CANDIDATE=benchmark-chunks.json
```

### Run with Docker Compose (all services)

```bash
make up
```

| Service | URL |
|---------|-----|
| Frontend | http://localhost:3000 |
| API | http://localhost:8000 |
| Airflow | http://localhost:8080 |

### Run tests

```bash
make test                 # all tests
make test-unit            # unit tests only
make test-integration     # integration tests (requires PostgreSQL)
make lint                 # run linter
```

### All available commands

```bash
make help
```

## Environment Variables

All variables have development defaults. Set them in `.env` (see
`.env.example`) when credentials or database names need to differ:

| Variable | Description |
|----------|-------------|
| `POSTGRES_USER` | PostgreSQL username |
| `POSTGRES_PASSWORD` | PostgreSQL password |
| `POSTGRES_DB` | Database name |
| `AIRFLOW_ADMIN_USER` | Airflow web UI username |
| `AIRFLOW_ADMIN_PASSWORD` | Airflow web UI password |
| `AIRFLOW_SECRET_KEY` | Airflow secret key |
| `SEPA_FACT_CHUNK_SIZE` | Staged product rows per fact batch (default 300000) |
| `SEPA_PENDING_FACT_COMMIT_EVERY_CHUNKS` | Pending-fact chunks written per commit (default 5) |
| `SEPA_FACT_RETENTION_DAYS`, `SEPA_STAGING_RETENTION_DAYS`, `SEPA_RAW_RETENTION_DAYS` | Opt-in retention windows; off when unset |
| `SEPA_DEV_CHAIN_KEYS`, `SEPA_DEV_MAX_PRODUCT_ROWS` | Bounded development ingestion (see above) |

### Airflow

The DAG `dag_sepa_ingest` runs automatically at 06:30 and 10:30 daily (two runs
to capture government rectifications). Its tasks run download → duplicate check
→ staging → quality checks → dimensions → pending facts → atomic publication.
If a task fails after opening an ingestion run, the DAG records the terminal
failure before Airflow retries or marks the task as failed.

### Web app

Pages: Home, Buscador (price finder with map), BuscadorEAN (lookup by barcode),
Lista (shopping list), Ofertas (daily promotions), Locales (nearby store
locator), Evolución and Variaciones. The last two, along with the KPIs on Home,
are analytics views being retired: detailed price history is out of scope for
the comparator. Built with React + TypeScript, Recharts for charts, React-Leaflet for maps and
TanStack Table/Query for data.

## Technical Decisions

- **Idempotency:** SHA-256 plus a completed ingestion to skip duplicate downloads; failed runs remain retryable; `ON CONFLICT DO UPDATE` on dimensions and facts; re-runs recalculate aggregates
- **Untyped staging:** all VARCHAR to preserve raw data and simplify quality check debugging
- **SCD Type 1:** dimensions update with last-write-wins (no change history)
- **Daily fact leaves:** `fact_precios` uses monthly parents with daily leaves, so a prepared day can be atomically replaced while monthly retention remains efficient.
- **Two-level quality checks:** critical (abort pipeline) vs warnings (log and continue)
- **structlog:** structured logging with context (run_id, filename, counts) for traceability

## Project Status

| Area | Status |
|------|--------|
| Ingestion (download, staging, quality, atomic publication, reconciliation) | Done |
| Airflow orchestration and bounded development mode | Done |
| Source measurements (price change rate, AMBA share, coverage) | Done |
| Storage: current snapshot in PostgreSQL, history in archived ZIPs | Next |
| AMBA scope, carry-forward of missing chains, price date on every offer | Next |
| Canonical product catalog and shopping-list comparison UI | Planned |
| Promotions and discounts beyond SEPA's list/promo prices | Later |

## References

- [SEPA Dataset](https://datos.produccion.gob.ar/dataset/sepa-precios) — Open data portal
- Resolución 678/2020, Anexo II — Official format specification
