# API

FastAPI backend (asyncpg) that reads only the latest published day. Every
route is under `/api`. Located routes take `lat`, `lon` and `radius` (km); when
the radius is too small the search grows it until enough chains are inside
(see `GET /api/location/radius`). Store filters `provincia`, `localidad` and
repeated `cadenas` keys work the same way on every route that takes them.

Paged routes return one page plus `total` (or `hay_mas`) computed over every
row the filters choose, not only the page. Ties are broken by a stable key,
so pages of one published day never repeat or skip a row.

## Products

### `GET /api/products/search`

Products with a price in the latest published day.

- `q`: every query word must appear, in any order, ignoring case and accents.
- Ranking: an exact EAN first, then products of the type the query names,
  then products listed by more chains.
- Filters: `tipo`, `marca` (repeatable: any of those brands), `contenido`,
  `contenido_min`, `contenido_max`, `unidad` (`g`, `ml`, `u`), plus the
  location and store filters. With a location only nearby stores count; the
  order stays the same.
- Each result carries `precio_desde` (lowest store price, unconditional
  promotions in force only), the observation date of that price and
  `sucursales` (how many stores carry it).
- Paging: `limit` (default 50, at most 100), `offset` (at most 1000).
- Response: `{"productos": [...], "hay_mas": bool}`.

The display name (`v_producto.nombre`) joins type, brand, variant and size,
for example "Leche La Serenisima entera 1 l"; an untyped product keeps its
description.

### `GET /api/products/search/facets`

Same query and attribute filters as the search. Returns `tipos`, `marcas` and
`tamanos` with product counts; each facet ignores its own filter and applies
the others. Types and brands list the 20 most common; sizes are all listed.
`tipo_nombrado` is the type the query names ("leche" for "leche entera"), when
it is among the listed types; the web selects it before listing results.

### `GET /api/products/{producto_sk}`

Product detail: catalog name and attributes, the description, brand and size
as published (`publicado`), whether a curated correction applies
(`corregido`), and the source codes (`codigos`: the EAN, or the chain code
with its `id_comercio` and `id_bandera`).

## Store prices

### `GET /api/buscador`

One page of a product's store prices, cheapest first, each with the store
address and its `id_comercio`, `id_bandera` and `id_sucursal`.

- Takes `producto_sk` plus the location and store filters.
- Paging: `limit` (default 50, at most 200), `offset` (at most 10,000).
- Response: `{"sucursales", "total", "hay_mas", "precio_min", "precio_max",
  "precio_promedio"}`.

### `GET /api/buscador/dates`

Published dates with prices for a product (`producto_sk`).

### `GET /api/lista/totales`

Each nearby store's total for a shopping list (repeated `producto_sk`; a
location is required), with its coverage and the items it does not carry.

## Offers

### `GET /api/ofertas`

The latest day's promotions, largest discount first: one row per product and
chain promotion, with average prices and `sucursales` (the chain's stores that
carry it).

- Takes the location and store filters.
- Paging: `limit` (default 50, at most 200), `offset` (at most 10,000).
- Response: `{"fecha", "items", "total", "hay_mas", "descuento_promedio",
  "descuento_maximo"}`.

### `GET /api/locales/{sucursal_sk}/promos`

Promotions of one store.

## Stores and location

### `GET /api/locales`

Stores with a price in the latest published day within the location, nearest
first.

- Takes the location and repeated `cadenas` keys.
- Paging: `limit` (default 100, at most 500), `offset` (at most 10,000).
- Response: `{"sucursales", "total", "hay_mas"}`.

### `GET /api/location/radius`

The radius to search around a point: the requested one, grown if needed.

### `GET /api/geocode`

Address to coordinates (`address`, optional `city`).

### Filters

- `GET /api/filters/provinces`, `GET /api/filters/localities?province=`
- `GET /api/filters/chains`
- `GET /api/filters/chain-options`: chains to choose among, with the stores
  each has near the location.

## Health

### `GET /api/health`

Latest published day and ingestion state. The web uses it to warn when there
are no published prices, when the latest day is older than three Argentine
calendar days, or when a later ingestion failed or is still running.
