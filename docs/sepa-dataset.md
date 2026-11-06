# SEPA dataset

Source: [datos.produccion.gob.ar — Base SEPA](https://datos.produccion.gob.ar/dataset/sepa-precios),
under Resolución 678/2020, Annex II. The same data feeds the official
[Precios Claros](https://www.preciosclaros.gob.ar/) site.

The portal publishes one ZIP per weekday (~300–330 MB) at a fixed URL,
`.../download/sepa_{dia}.zip` (`lunes` … `domingo`). Each URL is overwritten
weekly and the portal keeps no history, so the pipeline archives every
completed ZIP (see [Source ZIP archive](operations.md#source-zip-archive-and-restore)).
Chains must deliver by 06:00 on the effective day and may
rectify until 10:00, which is why the ingestion runs at 06:30 and 10:30.

## ZIP Structure

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

## CSV schemas

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
- **Product attributes.** Chains spell a shared EAN differently ("20 gr",
  "0.02 KG", "1 UNI"). Each attribute is chosen by a vote of chains, one vote
  per chain whatever its store count: the description with most chains, then
  the longest text; the brand with most chains, then most rows; and the size
  normalized to grams, millilitres or units (`contenido`, `contenido_unidad`),
  preferring a measured size over a unit count. Unknown units stay empty. Full
  runs record products whose chains disagree on brand or measured size in
  `producto_conflictos` for review.
- **Product type.** SEPA has no category, but descriptions name the product
  first, often abbreviated ("YOG BEBIBLE", "SH SEDAL"). The curated dictionary
  in `db/seeds/seed_producto_tipos.sql` maps one to three normalized words to a
  type (`YOG` → yogur, `DULCE DE LECHE` → dulce de leche). A description takes
  the type of the term at its first word, the longest when several start
  there; terms flagged `tras_marca` (SH, DEO, YOG) also match behind a brand,
  at the second or third word. Each chain's description is classified and the
  chains vote. A description with no known term leaves `dim_producto.tipo`
  empty; it is never guessed. A dictionary change (`make db`) reaches the
  products of the next full ingestion.
- **Product variant.** Products of the same type, brand and size still differ
  by flavor, fat content or recipe. The curated dictionary in
  `db/seeds/seed_producto_variantes.sql` maps one to three normalized words to a
  variant value (`VAI` → vainilla, `PARC DESC` → parcialmente descremada,
  `S G` → sin gas). A description's variant is the sorted set of the values it
  names at any word, joined with ", " ("firme, vainilla"); a term inside a
  longer matched term does not count ("LIMA LIMON" is lima limón only), and a
  value equal to the product's type is left out. The chains vote: a named
  variant beats none, then most chains, then most values. A description with
  no known term leaves `dim_producto.variante` empty; it is never guessed, and
  a dictionary change also reaches the next full ingestion.
- **Curated overrides.** When the vote is wrong, a row in
  `db/seeds/seed_producto_overrides.sql` corrects a product's description,
  brand, size, type or variant, with a note saying why. The seed is the source of truth:
  every migration run (`make db`) replaces `producto_overrides` with it. The
  API reads products through `v_producto`, which applies overrides when
  reading, so removing one restores the voted value. List what is left to
  review with
  `SELECT * FROM v_producto_conflictos_pendientes ORDER BY cadenas_fuente DESC`.
- **Display name and search.** `v_producto.nombre` joins type, brand, variant
  and size ("Leche La Serenisima entera 1 l"); an untyped product keeps its
  description. Search, ranking and paging are described in [API](api.md).
- **Store identity.** `id_sucursal` is unique only within a chain.
- **Prices.** The list price must be > 0; a promotion counts only when it is
  > 0 and lower than the list price. Bank and wallet discounts are not published.
- **Completeness varies.** Whole chains can be missing on a given day, and an
  inner ZIP can be malformed (it is skipped and counted). A chain absent from
  one file has not closed its stores.
- **Not only supermarkets.** SEPA also includes pharmacies and gas-station
  shops (see the table below).

## Chains observed

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

## Observed behavior

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
  discounts, not bank or wallet promotions. Legends carry the promotion's
  validity dates and sometimes a requirement (mostly several units); the API
  keeps conditional promotions out of the store price (migration 037).
- About 3,160 physical stores with prices per day, nearly all with plausible
  coordinates. AMBA (CABA + Buenos Aires province within 60 km of the Obelisco)
  is 1,834 stores and 52% of the rows.
