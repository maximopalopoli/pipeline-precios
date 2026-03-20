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

Verify it has these variables set:

```
POSTGRES_USER=sepa
POSTGRES_PASSWORD=sepa_secret
POSTGRES_DB=sepa_pipeline
```

### 3. Install dependencies

```bash
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

| Page | URL | What it shows |
|------|-----|---------------|
| Home | `/` | Welcome screen |
| Salud del Pipeline | `/salud` | Last runs, loaded vs rejected rows, QC alerts, execution time |
| Evolución de Precios | `/evolucion` | Product search, price trends, province breakdown |

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
