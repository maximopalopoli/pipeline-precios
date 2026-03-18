# SEPA Precios — Pipeline de Datos

Pipeline de datos que ingesta, transforma y almacena los registros diarios de precios del
[Sistema Electrónico de Publicidad de Precios Argentinos (SEPA)](https://datos.produccion.gob.ar/dataset/sepa-precios),
publicados por la Secretaría de Comercio Interior de Argentina.

El dataset contiene precios observados a nivel **sucursal x producto x día**, reportados
por las cadenas de retail bajo la Resolución 678/2020. Cada día se publican ~12 millones
de observaciones de precio distribuidas en ~18 comercios.

## Dataset

El portal publica un ZIP por día de la semana (~300 MB cada uno). El gobierno sobreescribe
el ZIP de cada día semanalmente — no hay archivo histórico.

### Estructura del ZIP

El ZIP principal contiene una carpeta con la fecha del día, y dentro de ella **un sub-ZIP
por comercio**, cada uno con 3 archivos CSV:

```
sepa_martes.zip
└── 2026-03-17/
    ├── sepa_1_comercio-sepa-8_2026-03-17_09-05-11.zip
    │   ├── comercio.csv      (~1 fila: datos del comercio/bandera)
    │   ├── sucursales.csv    (~10-1000 filas: sucursales con ubicación)
    │   └── productos.csv     (~1K-5M filas: precios por producto x sucursal)
    ├── sepa_1_comercio-sepa-15_2026-03-17_09-05-11.zip
    │   ├── comercio.csv
    │   ├── sucursales.csv
    │   └── productos.csv
    └── ... (18 comercios en total)
```

El nombre de cada sub-ZIP sigue el patrón: `sepa_{version}_comercio-sepa-{id}_{fecha}_{hora}.zip`

### Formato de los CSV

- **Separador:** pipe `|` (no coma)
- **Encoding:** UTF-8
- **Precios:** punto `.` como separador decimal (no formato argentino)
- **Trailer:** cada CSV termina con una línea en blanco + `Ultima actualizacion: YYYY-MM-DDTHH:MM:SS-03:00`

### Archivos dentro de cada sub-ZIP

| Archivo | Descripción | Clave compuesta |
|---------|-------------|-----------------|
| `comercio.csv` | Datos del comercio y bandera (CUIT, razón social, URL) | `(id_comercio, id_bandera)` |
| `sucursales.csv` | Sucursales con tipo, dirección, lat/lon, horarios | `(id_comercio, id_bandera, id_sucursal)` |
| `productos.csv` | Precios por producto x sucursal (tabla grande) | `(id_comercio, id_bandera, id_sucursal, id_producto)` |

#### Campos de precio en productos.csv

| Campo | Obligatorio | Descripción |
|-------|-------------|-------------|
| `productos_precio_lista` | Si | Precio de lista sin descuentos |
| `productos_precio_referencia` | Si | Precio por unidad de medida estándar (por kg, por litro) |
| `productos_precio_unitario_promo1` | No | Promo de alcance general (3x2, descuento por marca, etc.) |
| `productos_precio_unitario_promo2` | No | Promo con condiciones (tarjeta de fidelidad, jubilados, etc.) |

Ver [sepa_datasets.md](sepa_datasets.md) para el esquema completo de cada CSV.

## Requisitos

- Python 3.11+
- Jupyter (para el notebook de exploración)

```bash
pip install pandas polars requests jupyter
```

## Exploración de datos

```bash
jupyter notebook notebooks/exploration.ipynb
```

El notebook descarga un ZIP del día, extrae los sub-ZIPs por comercio, y explora
la estructura y contenido de los 3 CSVs.

## Estructura del proyecto

```
pipeline-precios/
├── README.md
├── ROADMAP.md                  # Plan de desarrollo día por día
├── sepa-pipeline-spec.md       # Especificación técnica completa
├── sepa_datasets.md            # Esquema detallado de los 3 CSVs
├── notebooks/
│   └── exploration.ipynb       # Análisis exploratorio del dataset
└── data/                       # Datos descargados (no versionado)
    └── martes.zip
```

## Referencias

- [Dataset SEPA](https://datos.produccion.gob.ar/dataset/sepa-precios) — Portal de datos abiertos
- Resolución 678/2020, Anexo II — Especificación oficial del formato
- `maestro-provincias.xlsx` — Mapeo de códigos ISO 3166-2 a nombres de provincia
