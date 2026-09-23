# SEPA Precios — Data Pipeline

ETL pipeline that ingests, validates, transforms, and warehouses daily retail price data from
[Argentina's SEPA price transparency system](https://datos.produccion.gob.ar/dataset/sepa-precios),
published by the Secretaría de Comercio Interior.

The dataset contains prices observed at the **store × product × day** level, reported
by retail chains under Resolución 678/2020. Each day publishes ~12 million
price observations across ~18 commercial chains.

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
          │  (Upsert)     │  → dim_*, fact_precios (partitioned by month)
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
| **Fact** | `fact_precios` (partitioned by month, grain: product × store × day) |
| **Aggregations** | `agg_precios_diarios`, view `v_variacion_precios` |
| **Quality** | `rejected_rows` |

## Project Structure

```
pipeline-precios/
├── etl/
│   ├── extract/downloader.py         # ZIP download + SHA-256 hash
│   ├── staging/loader.py             # Nested ZIP parsing → staging
│   ├── quality/checks.py             # 8 critical checks + 7 warnings
│   ├── quality/reporters.py          # Structured logging + persistence
│   ├── transform/cleaner.py          # Type casting + normalization
│   ├── transform/dimension_resolver.py  # Surrogate key resolution (SCD Type 1)
│   ├── transform/fact_builder.py     # Fact DataFrame construction
│   ├── load/warehouse_loader.py      # Bulk COPY + upserts + auto-partitioning
│   ├── load/aggregator.py            # Daily aggregate recalculation
│   └── run_pipeline.py               # End-to-end orchestration
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
├── scripts/benchmark_queries.py      # Benchmark: agg vs direct fact queries
├── dags/dag_sepa_ingest.py           # Airflow DAG (7 tasks, dual daily schedule)
├── api/                              # FastAPI backend (REST endpoints, asyncpg)
├── web/                              # React frontend (Vite + TypeScript + Tailwind)
├── notebooks/exploration.ipynb       # Exploratory analysis
├── docker-compose.yml                # PostgreSQL + Airflow + API + Web
├── pyproject.toml                    # Dependencies and configuration
└── Makefile                          # Targets: install, test, test-unit, test-integration
```

## SEPA Dataset

The government portal publishes one ZIP per weekday (~300 MB each). The ZIP for each day
is overwritten weekly — there is no historical archive.

### ZIP Structure

```
sepa_martes.zip
└── 2026-03-17/
    ├── sepa_1_comercio-sepa-8_2026-03-17_09-05-11.zip
    │   ├── comercio.csv      (~1 row)
    │   ├── sucursales.csv    (~10-1000 rows)
    │   └── productos.csv     (~1K-5M rows)
    └── ... (18 chains)
```

- **Delimiter:** pipe `|`
- **Encoding:** UTF-8
- **Prices:** dot `.` as decimal separator
- **Trailer:** each CSV ends with `Ultima actualizacion: YYYY-MM-DDTHH:MM:SS-03:00` (discarded during parsing)

See [sepa_datasets.md](sepa_datasets.md) for the full schema of each CSV.

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

That's it. The dashboard will be at http://localhost:5173 and the API at http://localhost:8000.

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
| `rows_productos_raw` | Product rows received from the source ZIP before development bounds. |
| `rows_productos_selected` | Product rows selected into staging after optional development bounds. Equal to `rows_productos_raw` for a full run. |
| `rows_productos_valid` | Rows accepted by product cleaning. |
| `rows_productos_rejected` | Rows rejected by product cleaning; their reason and payload, along with missing-key rows, are kept in `rejected_rows`. |
| `rows_productos_without_keys` | Valid rows that could not resolve the dimensions required for a fact. |
| `rows_productos_deduplicated` | Valid rows superseded by another row at the product × store fact grain. |
| `rows_fact_pending` | Unique facts held in the provisional set before publication. |
| `rows_fact_published` | Facts made visible by the completed atomic publication. |

For a completed run, the following invariants hold:

```text
rows_productos_raw = rows_productos_valid + rows_productos_rejected
rows_productos_valid = rows_productos_without_keys + rows_productos_deduplicated + rows_fact_pending
rows_fact_pending = rows_fact_published
```

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

### Bounded development ingestion (configuration contract)

The next local-ingestion mode is opt-in and will use `SEPA_DEV_CHAIN_KEYS`
(comma-separated `id_comercio:id_bandera` values) and/or
`SEPA_DEV_MAX_PRODUCT_ROWS` (a positive integer). Both settings are validated
strictly and are disabled when unset. This commit defines the configuration
contract. When either is set, CLI and Airflow mark the run as partial, retain
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

### Airflow

The DAG `dag_sepa_ingest` runs automatically at 06:30 and 10:30 daily (two runs
to capture government rectifications). Its tasks run download → duplicate check
→ staging → quality checks → dimensions → pending facts → atomic publication.
If a task fails after opening an ingestion run, the DAG records the terminal
failure before Airflow retries or marks the task as failed.

### Dashboard

6 screens: Home (KPIs + overview), Buscador (price finder with map), Evolución (price history charts), Variaciones (top price changes), Ofertas (daily promotions), Locales (nearby store locator). Built with React + TypeScript, Recharts for charts, React-Leaflet for maps, TanStack Table/Query for data.

## Technical Decisions

- **Idempotency:** SHA-256 plus a completed ingestion to skip duplicate downloads; failed runs remain retryable; `ON CONFLICT DO UPDATE` on dimensions and facts; re-runs recalculate aggregates
- **Untyped staging:** all VARCHAR to preserve raw data and simplify quality check debugging
- **SCD Type 1:** dimensions update with last-write-wins (no change history)
- **Monthly partitioning:** `fact_precios` partitioned by `fecha_id` for efficient date-range queries
- **Two-level quality checks:** critical (abort pipeline) vs warnings (log and continue)
- **structlog:** structured logging with context (run_id, filename, counts) for traceability

## Project Status

| Stage | Status |
|-------|--------|
| 1. Data Exploration | Done |
| 2. Docker Infrastructure | Done |
| 3. ETL Core | Done |
| 4. Aggregations | Done |
| 5. Airflow Orchestration | Done |
| 6. Dashboard (React + FastAPI) | ~95% |
| 7. Testing & Quality | ~95% |

See [ROADMAP.md](ROADMAP.md) for the detailed plan.

## References

- [SEPA Dataset](https://datos.produccion.gob.ar/dataset/sepa-precios) — Open data portal
- Resolución 678/2020, Anexo II — Official format specification
- [sepa-pipeline-spec.md](sepa-pipeline-spec.md) — Full technical specification
- [sepa_datasets.md](sepa_datasets.md) — Detailed schema for all 3 CSVs
