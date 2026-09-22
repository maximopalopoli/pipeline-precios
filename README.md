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
make dev                  # start DB + API + frontend for local development
```

That's it. The dashboard will be at http://localhost:5173 and the API at http://localhost:8000.

The checked-in development defaults are sufficient for a local run. To use
different database or Airflow credentials, copy `.env.example` to `.env` and
edit it before running the commands above.

### Ingest a specific date

```bash
make ingest-date DATE=2026-03-17
```

The pipeline downloads the daily ZIP (~300 MB), processes ~12M price observations
(staging → quality checks → dimensions → pending facts → atomic publication), and
loads them into PostgreSQL.

### Ingestion results and failures

The CLI exits with code `0` when it completes an ingestion or skips a raw file
that already has a completed publication. It exits with a non-zero code when a
critical quality check or an operational error aborts the run.

Every run opened after a download is recorded in `ingestion_runs`. A failed run
has `status = 'failed'`, `finished_at`, and an error message. The same raw file
remains eligible for a later retry until a run completes successfully. Critical
quality failures retain their structured check details in the error message.

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
