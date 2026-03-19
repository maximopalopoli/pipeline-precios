# SEPA Precios — Pipeline de Datos

Pipeline ETL que ingesta, valida, transforma y almacena los registros diarios de precios del
[Sistema Electrónico de Publicidad de Precios Argentinos (SEPA)](https://datos.produccion.gob.ar/dataset/sepa-precios),
publicados por la Secretaría de Comercio Interior de Argentina.

El dataset contiene precios observados a nivel **sucursal × producto × día**, reportados
por las cadenas de retail bajo la Resolución 678/2020. Cada día se publican ~12 millones
de observaciones de precio distribuidas en ~18 comercios.

## Arquitectura

```
ZIP diario (~300 MB)
  └── ~18 sub-ZIPs (uno por comercio)
        └── 3 CSVs: comercio.csv | sucursales.csv | productos.csv

          ┌─────────────┐
          │  Descarga    │  etl/extract/downloader.py
          │  + SHA-256   │  → raw_files (registro de descargas)
          └──────┬───────┘
                 │
          ┌──────▼───────┐
          │   Staging     │  etl/staging/loader.py
          │  (VARCHAR)    │  → stg_comercios, stg_sucursales, stg_productos
          └──────┬───────┘
                 │
          ┌──────▼───────┐
          │   Quality     │  etl/quality/checks.py + reporters.py
          │   Checks      │  8 críticos + 7 warnings → rejected_rows
          └──────┬───────┘
                 │
          ┌──────▼───────┐
          │  Transform    │  etl/transform/cleaner.py
          │  + Resolve    │  etl/transform/dimension_resolver.py + fact_builder.py
          └──────┬───────┘
                 │
          ┌──────▼───────┐
          │    Load       │  etl/load/warehouse_loader.py
          │  (Upsert)     │  → dim_*, fact_precios (particionada por mes)
          └──────┬───────┘
                 │
          ┌──────▼───────┐
          │  Aggregate    │  etl/load/aggregator.py
          │               │  → agg_precios_diarios, v_variacion_precios
          └───────────────┘
```

### Esquema de base de datos (Star Schema — PostgreSQL 16)

| Capa | Tablas |
|------|--------|
| **Metadata** | `raw_files`, `ingestion_runs` |
| **Staging** | `stg_comercios`, `stg_sucursales`, `stg_productos` |
| **Dimensiones** | `dim_comercio`, `dim_sucursal`, `dim_producto`, `dim_provincia`, `dim_fecha` |
| **Hechos** | `fact_precios` (particionada por mes, grano: producto × sucursal × día) |
| **Agregaciones** | `agg_precios_diarios`, vista `v_variacion_precios` |
| **Calidad** | `rejected_rows` |

## Estructura del proyecto

```
pipeline-precios/
├── etl/
│   ├── extract/downloader.py         # Descarga ZIP + hash SHA-256
│   ├── staging/loader.py             # Parseo de ZIPs anidados → staging
│   ├── quality/checks.py             # 8 checks críticos + 7 warnings
│   ├── quality/reporters.py          # Logging estructurado + persistencia
│   ├── transform/cleaner.py          # Cast de tipos + normalización
│   ├── transform/dimension_resolver.py  # Resolución de surrogate keys (SCD Type 1)
│   ├── transform/fact_builder.py     # Construcción del DataFrame de hechos
│   ├── load/warehouse_loader.py      # COPY masivo + upserts + auto-particiones
│   ├── load/aggregator.py            # Recálculo de agregados diarios
│   └── run_pipeline.py               # Orquestación end-to-end
├── models/
│   ├── comercio.py                   # ComercioRecord (Pydantic v2)
│   ├── sucursal.py                   # SucursalRecord
│   └── producto.py                   # ProductoRecord
├── db/
│   ├── migrations/                   # 6 migraciones SQL numeradas
│   └── seeds/                        # Provincias (ISO 3166-2) + calendario
├── tests/
│   ├── unit/                         # test_cleaner, test_quality_reporters
│   ├── integration/                  # 7 tests: staging → quality → dimensions → fact → warehouse → aggregator → rerun
│   └── fixtures/sample.zip           # ZIP de muestra (2 comercios, 7 productos)
├── scripts/benchmark_queries.py      # Benchmark: agg vs fact directo
├── dags/                             # Airflow DAGs (pendiente)
├── dashboard/                        # Streamlit (pendiente)
├── notebooks/exploration.ipynb       # Análisis exploratorio
├── docker-compose.yml                # PostgreSQL 16
├── pyproject.toml                    # Dependencias y configuración
└── Makefile                          # Targets: install, test, test-unit, test-integration
```

## Dataset SEPA

El portal publica un ZIP por día de la semana (~300 MB cada uno). El gobierno sobreescribe
el ZIP de cada día semanalmente — no hay archivo histórico.

### Estructura del ZIP

```
sepa_martes.zip
└── 2026-03-17/
    ├── sepa_1_comercio-sepa-8_2026-03-17_09-05-11.zip
    │   ├── comercio.csv      (~1 fila)
    │   ├── sucursales.csv    (~10-1000 filas)
    │   └── productos.csv     (~1K-5M filas)
    └── ... (18 comercios)
```

- **Separador:** pipe `|`
- **Encoding:** UTF-8
- **Precios:** punto `.` como separador decimal
- **Trailer:** cada CSV termina con `Ultima actualizacion: YYYY-MM-DDTHH:MM:SS-03:00` (descartada al parsear)

Ver [sepa_datasets.md](sepa_datasets.md) para el esquema completo de cada CSV.

## Requisitos

- Python 3.11+
- Docker y Docker Compose
- PostgreSQL 16 (via Docker)

## Instalación

```bash
# Levantar PostgreSQL
docker compose up -d

# Instalar dependencias
pip install -e ".[dev]"

# Ejecutar tests
pytest                    # todos
make test-unit            # solo unitarios
make test-integration     # solo integración (requiere PostgreSQL)

# Ejecutar pipeline
python -m etl.run_pipeline --fecha 2026-03-17

# Exploración de datos
jupyter notebook notebooks/exploration.ipynb
```

Variables de entorno en `.env` (ver `.env.example`):
- `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`

## Decisiones técnicas

- **Idempotencia:** SHA-256 para skip de descargas duplicadas; `ON CONFLICT DO UPDATE` en dimensiones y hechos; re-ejecución recalcula agregados
- **Staging sin tipos:** todo VARCHAR para preservar datos raw y facilitar debugging de quality checks
- **SCD Type 1:** las dimensiones se actualizan con last-write-wins (sin historial de cambios)
- **Particionamiento mensual:** `fact_precios` particionada por `fecha_id` para queries eficientes por rango de fechas
- **Quality checks en dos niveles:** críticos (abortan pipeline) vs warnings (loguean y continúan)
- **structlog:** logging estructurado con contexto (run_id, filename, counts) para trazabilidad

## Estado del proyecto

| Etapa | Estado |
|-------|--------|
| 1. Exploración de datos | Completa |
| 2. Infraestructura Docker | Completa |
| 3. ETL Core | Completa |
| 4. Agregaciones | Completa |
| 5. Airflow | Pendiente |
| 6. Dashboard Streamlit | Pendiente |
| 7. Testing y docs | Parcial (tests completos, docs en progreso) |

Ver [ROADMAP.md](ROADMAP.md) para el plan detallado.

## Referencias

- [Dataset SEPA](https://datos.produccion.gob.ar/dataset/sepa-precios) — Portal de datos abiertos
- Resolución 678/2020, Anexo II — Especificación oficial del formato
- [sepa-pipeline-spec.md](sepa-pipeline-spec.md) — Especificación técnica completa
- [sepa_datasets.md](sepa_datasets.md) — Esquema detallado de los 3 CSVs
