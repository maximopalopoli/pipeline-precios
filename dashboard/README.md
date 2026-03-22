# SEPA Pipeline Dashboard

Streamlit dashboard for monitoring the ETL pipeline and exploring SEPA retail price data.

## Prerequisites

- Python 3.11+
- Docker and Docker Compose
- Git clone of this repo

## Setup

### 1. Start PostgreSQL

```bash
docker compose up -d postgres
```

Wait until healthy:

```bash
docker compose ps
```

You should see `postgres` with status `healthy`.

### 2. Configure environment variables

Copy the example `.env` file if you haven't already:

```bash
cp .env.example .env
```

Verify it has `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB` set (see `.env.example` for the template).

### 3. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

This installs `streamlit`, `plotly`, and all other project dependencies.

### 4. Run the dashboard

```bash
make dashboard
```

Or directly:

```bash
streamlit run dashboard/app.py
```

The app opens at [http://localhost:8501](http://localhost:8501).

## Pages

| Page | What it shows |
|------|---------------|
| Home | Welcome screen, last ingestion status, links to all pages |
| Salud del Pipeline | Last runs, loaded vs rejected rows, QC alerts, execution time |
| Evolucion de Precios | Product search, price trends, province breakdown |
| Mapa de Precios | Geographic map of prices by store, filters by type and promo |
| Comparador por Cadena | Price comparison table across chains for a given product |
| Ranking de Variaciones | Top products with highest price variation in last 7 days |
| Analisis de Promociones | Promo coverage by chain, effective discounts, promo legends |

## Running with Docker

If you prefer running everything containerized:

```bash
docker compose up -d postgres streamlit
```

The dashboard will be available at [http://localhost:8501](http://localhost:8501).

## Notes

- If the database is empty (no pipeline runs yet), the dashboard shows informational messages instead of charts.
- Run the ETL pipeline at least once (`python -m etl.run_pipeline`) to populate the data.
- Query results are cached for 5 minutes to avoid hitting the database on every Streamlit rerun.
