# Operations

How to run, operate and inspect the pipeline locally. The README has the
short quick start; this page has every option.

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
│   ├── ingestion_run.py, ingestion_lock.py, reconciliation.py  # Run records, per-date lock, counts
│   ├── rejection_counts.py           # Rejection counts per reason + bounded samples
│   ├── development_ingestion.py      # Bounded partial development mode
│   ├── regional_scope.py             # Opt-in regional scope (AMBA) applied while staging
│   ├── raw_archive.py                # Verified source ZIP archive and restore
│   ├── *_retention.py                # Opt-in fact, staging and raw ZIP retention
│   └── run_pipeline.py               # End-to-end orchestration (CLI)
├── models/
│   ├── comercio.py                   # ComercioRecord (Pydantic v2)
│   ├── sucursal.py                   # SucursalRecord
│   └── producto.py                   # ProductoRecord
├── db/
│   ├── migrations/                   # ordered SQL migrations, checksum-verified
│   └── seeds/                        # Provinces, calendar, curated product overrides, types and variants
├── tests/
│   ├── unit/                         # focused module and lifecycle tests
│   ├── integration/                  # database-backed ETL, publication and entrypoint tests
│   └── fixtures/sample.zip           # Sample ZIP (2 chains, 7 products)
├── benchmarks/                       # Baselines, chunk sweeps and query audits read from run logs
├── deploy/                           # systemd timer and retrying ingestion wrapper
├── api/                              # FastAPI backend (REST endpoints, asyncpg)
├── web/                              # React frontend (Vite + TypeScript + Tailwind)
├── notebooks/exploration.ipynb       # Exploratory analysis
├── docker-compose.yml                # PostgreSQL + API + Web
├── pyproject.toml                    # Dependencies and configuration
└── Makefile                          # Targets: install, test, test-unit, test-integration
```


## Requirements

- Python 3.11+
- Node.js 18+ (for the frontend)
- Docker and Docker Compose
- PostgreSQL 16 (via Docker)

## Migration integrity

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

## Ingest a specific date

```bash
make ingest-date DATE=2026-03-17
```

The pipeline downloads the daily ZIP (~300 MB), processes ~12M price observations
(staging → quality checks → dimensions → pending facts → atomic publication), and
loads them into PostgreSQL.

## Run a bounded local ingestion

Use the explicit Make target for a deterministic local walk-through without
publishing a daily snapshot:

```bash
make ingest-bounded DATE=2026-03-17 DEV_CHAIN_KEYS=1:1 DEV_MAX_PRODUCT_ROWS=5000
```

At least one bound is required. `DEV_CHAIN_KEYS` follows the same
comma-separated `id_comercio:id_bandera` format as `SEPA_DEV_CHAIN_KEYS`; the
row cap is optional when chains alone are enough.

## Regional scope (AMBA)

`SEPA_REGIONAL_SCOPE=amba` limits a published ingestion to the stores of the
Buenos Aires metropolitan area and their product rows. Unlike the development
bounds, a scoped run is a production run: it publishes its snapshot, and every
row it leaves out is counted. Unset or blank keeps every store; an unknown
name fails the run before anything is staged.

Stores are classified while `sucursales.csv` is staged:

| Store | Result |
|-------|--------|
| `sucursales_provincia` other than `AR-C` or `AR-B`, including empty or invalid values | Out of scope |
| Plausible coordinates within 60 km of the Obelisco (`AR-C` or `AR-B`) | In scope |
| Plausible coordinates farther than 60 km | Out of scope |
| `AR-C` without plausible coordinates (all of CABA lies inside the radius) | In scope |
| `AR-B` without plausible coordinates | Unlocated: counted and left out, never guessed |

Store discovery (`/api/locales` and the province, chain and locality filters)
includes only stores with prices on the latest published day.
`GET /api/filters/chain-options` lists the chains as `{valor, nombre,
sucursales}`, most stores first: `valor` is the published banner name that the
`cadenas` filter takes, and `nombre` is the name shoppers know ("Coto",
"Carrefour Market"), from `api/chain_names.py`. With `lat`, `lon` and `radius`
it lists only the chains with a store in that radius and counts those stores.
Every page that takes a location (Buscador, Ofertas, Locales, Lista) shows the
same one-line location bar, and Buscador, Ofertas and Locales offer these chains
as chips under it; the web has no province or locality filter.

Every located route takes `lat`, `lon` and `radius` (km). The point must lie
in Argentina, both coordinates come together, and the radius is 0.5–50 km.
Distance is measured before any limit. `GET /api/location/radius` returns
`radio_km`, the chosen radius grown through 1, 2, 5, 10, 20 and 50 km until
grocery stores of three chain companies are within it. It also returns
`cadenas` (the companies reached), `ampliado` and `suficiente`. Gas-station
shops and pharmacies do not count. The web grows only the first radius around an
address; a radius the shopper sets on the slider is kept as set. Older
national facts may remain under fact retention without appearing as current
stores.

Search results and the product detail carry `cobertura`: `fraccionado_en_local`
(a 13-digit code with GS1 prefix 2, weighed or cut in the store and numbered
by each chain), `codigo_interno` (no valid EAN) and, on search results,
`pocos_locales` (one or two of the chosen stores carry it).

Prices per kg or l (`precio_unitario_desde` on search results,
`precio_unitario` on store offers) are computed from the canonical package
size in g or ml, never from `productos_precio_referencia`, which some chains
fill with the list price. Products sold by the unit have none.

`GET /api/lista/totales` takes the list as repeated `producto_sk` keys (up to
50, a repeated product counts once) and a location. For each store within the
radius with a price on the latest published day, it returns `total` (prices
paid without any condition), `total_condicional` (meeting every conditional
promotion, or null), `cubiertos` of `productos`, the missing `faltantes`
and each item's price and observation date. Stores are ranked by coverage
first, then by total, so an incomplete basket never ranks above a complete
one; it returns at most 50. Pharmacies and gas-station shops (`id_comercio`
4, 19, 23 and 24) are left out of list totals but stay in single-product
search. The list page picks each item's product from `GET
/api/products/search`, ranked and paged like the search page, with its
"from" price at nearby stores.

The product-date, daily-offer and store-promotion routes read published
facts and run metadata directly. The analytics aggregates (weekly variation
KPI, Evolución, Variaciones) were retired in migration 035; price history
lives in the archived source ZIPs.

When a store has no valid price rows in a later file, publication carries its
last prices from the newest available snapshot of the same regional scope.
The fact's `fecha_id` is the snapshot day; `fecha_observada_id` is the day the
price was actually reported. API offers include `fecha_precio`, and the web
client labels each price with that date. Carried prices keep the list and
reference prices, while unverified older promotions are removed. A store with
new price rows replaces its previous offers in full; individual product rows
missing from an otherwise present store are not carried. Retained facts from a
different scope are never used as a carry source.

Coordinates are plausible when both parse as dot-decimal numbers inside
Argentina's bounding box. Product rows are then kept only when the same file
placed their store in scope. The store key is `id_comercio`, `id_bandera` and
`id_sucursal` with leading zeros removed, because `id_sucursal` padding differs
between the files. Product rows whose store is missing from `sucursales.csv`
(for example, a chain whose store file is truncated or absent that day) cannot
be placed; `dim_sucursal` may still hold that store from an earlier unscoped
ingestion, so they are counted as `rows_productos_store_unlisted` and not
staged rather than published outside the scope. The scope applies after the
development chain filter and before the development row cap.

The scope is recorded on each run (`ingestion_runs.regional_scope`). A raw file
is skipped only when a completed run used the same scope, so changing
`SEPA_REGIONAL_SCOPE` re-ingests the current file on the next run.

## Source ZIP archive and restore

After each full publication, the pipeline archives every completed ZIP
not archived yet, earlier days included, under `SEPA_ARCHIVE_DIR` (default:
an `archive` folder inside the raw directory):

```
archive/2026/09/2026-09-25_<sha256>.zip
```

The key comes from the date declared inside the ZIP, as validated by its
completed run, and the content hash, never from the source file name or an
earlier mistaken requested date. A copy is a hard link when both folders share a file system,
so archiving costs no extra space; on another disk the bytes are copied. The
copy is checked against the registered SHA-256 and size before it is recorded in
`raw_files.archive_key` and `raw_files.archived_at`. A failed or missing copy is
logged, never fails the publication, and is retried by the next run.

`SEPA_RAW_RETENTION_DAYS` deletes a local ZIP only when its archived copy is
recorded and still present with the registered size. With the default archive
on the same disk the archived hard link keeps the bytes, so this retention frees
no space; it only reclaims space when `SEPA_ARCHIVE_DIR` is on another disk. The
archive folder is the historical backup: point `SEPA_ARCHIVE_DIR` at another disk, or sync it off the
server, if the history must survive a disk failure.

To re-ingest a day from the archive, for example after fact retention dropped
it or to reprocess it after a fix:

```bash
make restore-date DATE=2026-09-25
```

The restore picks the archived version with the latest full publication (a
10:00 rectification wins), verifies its hash, and reprocesses it even if it had
already completed. With no registry row, as after rebuilding the database, it
searches the archive folder by date and asks for `--zip-path` if several
versions exist. Fact retention drops a restored day again once it falls outside
`SEPA_FACT_RETENTION_DAYS`. A ZIP replayed with `--zip-path` from outside the raw
directory is not archived. A restore always publishes, so it fails if
`SEPA_DEV_CHAIN_KEYS` or `SEPA_DEV_MAX_PRODUCT_ROWS` is set.

## Ingestion results and failures

The CLI exits with code `0` when it completes an ingestion or skips a raw file
that already has a completed publication. It exits with a non-zero code when a
critical quality check or an operational error aborts the run.

Every run opened after a download is recorded in `ingestion_runs`. A failed run
has `status = 'failed'`, `finished_at`, and an error message. The same raw file
remains eligible for a later retry until a run completes successfully. Critical
quality failures retain their structured check details in the error message.

`GET /api/health` also reports `ultima_publicacion`, the newest day that still
has visible facts from a completed full ingestion. `aviso_ingesta` identifies a
later full ingestion that failed or is still running; bounded development runs
do not replace the publication or hide that warning. The home page shows the
published date and the later attempt separately. Every offer displays its own
observation date, and dates more than three Argentine calendar days old are
marked as outdated.

## Product-row reconciliation

Completed runs created by the current pipeline account for every staged product
row through publication. The product-specific counters on `ingestion_runs` are:

| Counter | Meaning |
|---------|---------|
| `rows_productos_source` | Product rows inspected in source archives before development chain bounds and the regional scope. |
| `rows_productos_selected` | Source rows selected by development chain bounds, before the regional scope and an optional row cap. |
| `rows_productos_out_of_scope` | Product rows of stores left out by the regional scope (out of scope or unlocated); `NULL` for runs before the scope existed. |
| `rows_productos_store_unlisted` | Scoped runs only: product rows whose store is not listed in the source `sucursales.csv`, so the scope cannot place them. |
| `rows_productos_raw` | Rows staged after the regional scope and all development bounds; the input to product cleaning. |
| `rows_productos_valid` | Rows accepted by product cleaning. |
| `rows_productos_rejected` | Rows rejected by product cleaning, counted per reason in `rejection_counts` (source `clean_productos`). |
| `rows_productos_without_keys` | Valid rows that could not resolve the dimensions required for a fact, counted per reason in `rejection_counts` (source `fact_key_resolution`). |
| `rows_productos_deduplicated` | Valid rows superseded by another row at the product × store fact grain. |
| `rows_fact_pending` | Unique facts held in the provisional set before publication. |
| `rows_fact_published` | New source facts made visible by the completed atomic publication; equals pending facts. |
| `rows_fact_carried_forward` | Earlier offers copied into the visible day because their store had no new prices. |
| `rows_fact_snapshot` | Total visible facts for the day: published source facts plus carried facts. |
| `inner_zips_invalid_count` | Nested SEPA ZIPs that were malformed and skipped; these contain no product rows to reconcile. |
| `rows_sucursales_out_of_scope`, `rows_sucursales_unlocated` | Store rows left out by the regional scope, by reason; diagnostic, not terms of the product equations. |

For a completed run, the following invariants hold:

```text
rows_productos_source >= rows_productos_selected >= rows_productos_raw
rows_productos_source = rows_productos_out_of_scope + rows_productos_store_unlisted + rows_productos_raw  # full runs only
rows_productos_raw = rows_productos_valid + rows_productos_rejected
rows_productos_valid = rows_productos_without_keys + rows_productos_deduplicated + rows_fact_pending
rows_fact_pending = rows_fact_published  # full runs only
rows_fact_snapshot = rows_fact_published + rows_fact_carried_forward  # new full runs
rows_productos_rejected = SUM(rejection_counts.row_count) for clean_productos
rows_productos_without_keys = SUM(rejection_counts.row_count) for fact_key_resolution
```

For an explicit partial development run, `rows_fact_published` is zero and its
bounds leave source rows out on purpose; those are the intended exceptions to
the full-run invariants. Its pending rows remain isolated for local inspection.
Only the newest completed partial run keeps them: opening any run deletes the
pending rows of older completed partial runs
(`ingestion_run.partial_pending_deleted`). Running runs keep theirs.

A failed run keeps its pending rows for diagnosis until a later full run of the
same declared date completes; the next run to open then deletes them
(`ingestion_run.failed_pending_deleted`), since a retry writes its own rows
under a new run. A missing `rows_productos_out_of_scope` or
`rows_productos_store_unlisted` counts as zero.

## Quality limits

Reconciliation proves that every row is accounted for; configurable limits
decide whether the losses are acceptable. Before the partition swap, a full
run fails (and the previous snapshot stays visible) when any share exceeds its
limit:

| Share | Limit (default) |
|-------|-----------------|
| `rows_productos_rejected / rows_productos_raw` | `SEPA_MAX_REJECTED_SHARE` (0.01) |
| `rows_productos_without_keys / rows_productos_valid` | `SEPA_MAX_WITHOUT_KEYS_SHARE` (0.01) |
| `rows_productos_store_unlisted / (rows_productos_store_unlisted + rows_productos_raw)` | `SEPA_MAX_STORE_UNLISTED_SHARE` (0.05) |

The 2026-03-23 and 03-24 AMBA replays measured 0.03%, 0% and 0.75%. The error
message names each exceeded share; the event `quality.rejection_thresholds_checked`
logs all three. Partial development runs are not checked. Staging quality
checks also read `SEPA_MIN_PRODUCT_ROWS` (critical, default 100) and
`SEPA_MAX_ROW_COUNT_DRIFT` (warning, default 0.20 against the previous full run
of the same regional scope).

## Rejected rows

Every rejection is counted in `rejection_counts` by run, source (a staging
table or a processing step) and a stable reason code such as
`productos_precio_lista_not_positive` or `producto_not_found`. The detailed
reason, including the offending value, is kept only with the sampled rows:
`rejected_rows` stores at most `SEPA_REJECTED_SAMPLE_ROWS` rows (default 100)
per run, source and code, so its size no longer grows with bad source data.
Each sampled row keeps its code in `rejected_rows.rejection_code`. Only the
`clean_productos` and `fact_key_resolution` sources are terms of the equations
above; the `stg_*` sources come from warning-level quality checks and are
diagnostic, so a row can appear there and in `clean_productos`. To
inspect every rejected row of a day, replay its archived ZIP with
`make restore-date`. Each step clears its own counts and samples before
recomputing them, so a retried task does not count rows twice. Rows written
by earlier versions of the pipeline are not removed.

```sql
SELECT source_file, rejection_reason, row_count
FROM rejection_counts
WHERE run_id = 42
ORDER BY row_count DESC;

SELECT raw_data, rejection_reason
FROM rejected_rows
WHERE run_id = 42 AND rejection_code = 'producto_not_found';
```

The API exposes these fields as `reconciliacion` in `GET /api/health`, next to
the run's `regional_scope`; the home page displays the latest available summary. Older runs predate these counters,
so their reconciliation is `null` rather than a failed or incomplete status.

## Log output

The CLI prints one line per milestone: the run header with the path of its
full log, each stage with its row counts and duration, fact progress every 20%
with the estimated time left, the loss limits and the outcome. Warnings and
errors that no milestone summarizes are printed as they happen. Every event,
including the per-chunk ones described below, is written to
`logs/ingest-<timestamp>.log` (not versioned) as one JSON object per line, so
it can be filtered with `jq` and read by the benchmark commands. `make ingest VERBOSE=1` (or
`--verbose`) prints every event to the terminal instead.

## Performance telemetry

The pipeline emits structured events for `download`, `staging`, `quality`,
`dimensions`, `fact_load`, and `publication`. Each completed or failed stage
includes `elapsed_seconds` and `peak_rss_bytes`; stage-specific events also
include their relevant row counts. Product fact processing adds one pair of
events per chunk: `fact_chunk_preparation` measures cleaning and key resolution,
while `pending_fact_write` measures the provisional PostgreSQL write.

The CLI's `pipeline.completed` event summarizes the end-to-end `elapsed_total`,
`peak_rss_bytes`, reconciliation counts, and `fact_rows_per_second`.

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

The pipeline itself stores no benchmark data. The commands below live in
`benchmarks/`, outside the ETL: they read counts and end-to-end timing from
`ingestion_runs` and stage timings from the run's full log, so a run is
comparable only while its log is kept. Logs written before the JSON format
provide no stage timings.

Three read-only commands provide repeatable evidence before changing indexes or
database infrastructure:

```bash
# Latest completed run, or a specific local run.
make etl-baseline RUN_ID=5

# Compare only completed replays that share source ZIP and bounded policy.
make compare-etl-baselines RUN_ID=5

# Representative /api/buscador and /api/ofertas plans.
# Requires at least one full published snapshot; bounded runs publish no facts.
make audit-queries FECHA_ID=20260922
```

`etl-baseline` reports run-level timing, source size, chunk size, reconciliation
counts, the logged fact-load duration, process-memory high-water mark and
publication timings, plus the current database and relation sizes (measured
when the command runs; they include indexes). `compare-etl-baselines` excludes
runs that do not share the source archive and bounds, so its JSON rows differ
only in the benchmark configuration such as `fact_chunk_size`; it also excludes
runs whose log holds no fact-load timing. `audit-queries` runs `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` and
emits planning/execution time, row counts and shared-buffer reads as JSON.
Capture a baseline before and after a candidate index migration; do not infer
full-scale capacity from a bounded run.

## Bounded development ingestion

The local bounded-ingestion mode is opt-in and uses `SEPA_DEV_CHAIN_KEYS`
(comma-separated `id_comercio:id_bandera` values) and/or
`SEPA_DEV_MAX_PRODUCT_ROWS` (a positive integer). Both settings are validated
strictly and are disabled when unset. When either is set, the pipeline marks
the run as partial, retain
its staging and pending facts only for local inspection, and publish zero
facts. They may add dimension keys required by their pending facts but
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
individual samples plus per-size medians and the Python/PostgreSQL/OS
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

## Run with Docker Compose (all services)

```bash
make up
```

| Service | URL |
|---------|-----|
| Frontend | http://localhost:3000 |
| API | http://localhost:8000 |

## Run tests

```bash
make test                 # all tests
make test-unit            # unit tests only
make test-integration     # integration tests (requires PostgreSQL)
make lint                 # run linter
```

## All available commands

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
| `SEPA_FACT_CHUNK_SIZE` | Staged product rows per fact batch (default 300000) |
| `SEPA_PENDING_FACT_COMMIT_EVERY_CHUNKS` | Pending-fact chunks written per commit (default 5) |
| `SEPA_FACT_RETENTION_DAYS` | Opt-in; keeps the ingested date plus the previous N days of facts and drops older daily partitions (and months left empty). 3 covers rectifications and rollback. Off when unset |
| `SEPA_STAGING_RETENTION_DAYS` | Opt-in; removes staging rows of terminal runs older than N days. Off when unset |
| `SEPA_RAW_RETENTION_DAYS` | Opt-in; removes local ZIPs older than N days only when a verified archived copy exists. Frees space only when the archive is on another disk. Off when unset |
| `SEPA_ARCHIVE_DIR` | Source ZIP archive folder (default: `archive` inside the raw directory) |
| `SEPA_REJECTED_SAMPLE_ROWS` | Rejected rows kept in `rejected_rows` per run, source and reason (default 100; 0 keeps only counts) |
| `SEPA_MAX_REJECTED_SHARE`, `SEPA_MAX_WITHOUT_KEYS_SHARE`, `SEPA_MAX_STORE_UNLISTED_SHARE` | Largest share of rejected, keyless and unlisted-store product rows a full run may lose before publication fails (defaults 0.01, 0.01, 0.05; see Quality limits) |
| `SEPA_MIN_PRODUCT_ROWS`, `SEPA_MAX_ROW_COUNT_DRIFT` | Critical minimum of staged product rows (default 100) and warning drift against the previous full run of the same scope (default 0.20) |
| `SEPA_REGIONAL_SCOPE` | Opt-in regional scope of a published ingestion; `amba` is the only value. Changing it re-ingests the current file. Off when unset |
| `SEPA_DEV_CHAIN_KEYS`, `SEPA_DEV_MAX_PRODUCT_ROWS` | Bounded development ingestion (see above) |

## Scheduled ingestion

A systemd timer (`deploy/systemd/sepa-ingest.timer`) runs the ingestion at
06:30 and 10:30 daily, Argentine time (two runs to capture government
rectifications), through `deploy/run-ingest.sh`. The wrapper fixes the
Argentine calendar day once and runs `etl.run_pipeline --fecha <day>` up to
three times, 15 minutes apart. Before a chain delivers, the weekday URL still
serves last week's ZIP: its hash is already published, so the 06:30 run stops
at the duplicate check, and the 10:30 run ingests the new or rectified file.
A completed file is skipped, so a retry never publishes twice; a failed attempt
keeps the previous snapshot visible and sends the pipeline's failure alert.

The service reads the production settings from `.env`. The wrapper unsets the
partial development bounds `SEPA_DEV_*`, so a local `.env` cannot make the
scheduled ingestion publish nothing. To install it, adjust `User`,
`WorkingDirectory`, `EnvironmentFile` and `ExecStart` in
`deploy/systemd/sepa-ingest.service`, then:

```bash
sudo cp deploy/systemd/sepa-ingest.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sepa-ingest.timer
systemctl list-timers sepa-ingest.timer   # next run
journalctl -u sepa-ingest.service         # attempts and milestones
```

The full log of every attempt is in `logs/`, and `/estado` shows the latest
publication.

## Web app

Pages: Buscador, the home page (location first, then a price finder with map;
it also finds a product by its barcode, EAN-8 and UPC-A included; a product
lists its stores cheapest first and can be added to the list), Lista
(shopping list, kept in the browser with its chosen products), Ofertas (daily promotions), Locales (nearby store locator) and
Estado (`/estado`, linked at the foot of the sidebar on wide screens), which shows the latest
publication and the quality of the last ingestion. The location (an address or
the device's, plus the radius the shopper sets) is one for every page and is
kept in the browser. Pages fit any width: on phones the navigation becomes a
top bar and every page uses the full width. Built with React + TypeScript, Recharts for charts, React-Leaflet for maps and
TanStack Table/Query for data.
