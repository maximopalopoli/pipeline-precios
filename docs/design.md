# Design

Why the project looks the way it does: what the data showed, what was decided
from it, the rules the ingestion never breaks, and the measurements behind
each choice.

## Product decision

The product is a B2C comparator: a shopper picks a location and radius,
searches products with clear names, builds a list and sees what it costs at
each nearby store. It was decided in September 2026, after measuring the
source (next section), with three choices:

- **Unambiguous product identity.** Every offer maps to one canonical product:
  a valid EAN is shared across chains, an internal code stays chain-scoped.
  Products are never merged by name similarity.
- **Freshness per price.** Every price shows the date it was observed.
- **AMBA first.** Only CABA and Buenos Aires province within 60 km of the
  Obelisco are ingested and published.

Consequences for the design:

- **The hot data is the current snapshot, not a history.** PostgreSQL holds
  the latest published day plus a short rollback window, so its size stays
  roughly fixed. History lives in the archived source ZIPs.
- **Analytics is out of scope.** Weekly variations, price evolution and
  inflation-style indicators were the features that forced an expensive
  online history, and they serve a different user (an analyst, not a
  shopper). They were removed from the schema and the API.
- **Ingestion performance work is frozen** while a run fits its daily window.
  From this point retention and the data model drive cost, not small speedups.

## What the source showed

Measured with DuckDB directly over six archived daily ZIPs (March and
September 2026). The measurement scripts are not versioned.

| Question | Finding | Decision |
|---|---|---|
| How much changes per day? | 1.9-3.1% of product × store rows change price; about 10% per week, 84% over six months | A change log would cost 5-8% of a snapshot per day. Not needed for a shopping list, so only the ZIPs are kept |
| Are chains complete every day? | No: Cencosud missing on one day, Farmacity on 3 of 6, invalid inner ZIPs | A store missing from a day keeps its previous prices, labeled with their date |
| Can products be compared across chains? | 22,700 EANs are sold by 2 or more chains; EAN products are 67% of all rows. Fresh products mostly use internal codes | Identity by EAN; internal codes stay inside their chain |
| Do prices vary per store? | Only 6.1% of rows are distinct per (chain, product, prices); DIA and Coto are almost uniform | Chain-level storage would need 25% of the rows. Kept as an option, not built |
| How many stores are nearby? | In CABA 4-7 chains within 1 km; in parts of GBA none | The search radius grows until enough chains are inside it |
| Are bank discounts in the data? | No; promotions are store discounts on 3% of rows | Shown as published, explained as such |
| How big is AMBA? | 1,834 stores, 12 chain companies, 7.67 M rows (52% of the day) | AMBA scope, applied while staging |

Existing comparators were reviewed too. The official
[Precios Claros](https://www.preciosclaros.gob.ar/) uses the same data and
already offers list totals per store; others add bank discounts or online
prices. The differentiator chosen was product identity and per-price
freshness, not features those sites already had.

## Ingestion invariants

Rules every change to the pipeline must keep:

- The CLI is the only ingestion entry point; the scheduler only calls it.
- Product rows are processed in chunks, never all in memory.
- A day becomes visible only through an atomic publication. A failure keeps
  the previous day visible.
- Only a successfully completed file is skipped; failed runs stay retryable.
- Every row is accounted for. For a completed run:
  `source = out of scope + store unlisted + raw`,
  `raw = valid + rejected`,
  `valid = without keys + deduplicated + pending`,
  `snapshot = published + carried forward`.
  A new filter adds its own bucket instead of hiding rows.
- Trust the date inside the archive, never a file name: the portal
  overwrites each weekday URL every week.
- Applied migrations are never edited; a change is a new migration.

## Measurements

All numbers are from full local replays, not a production capacity promise.

| | National, before the snapshot decision (2026-09-22) | AMBA snapshot (2026-03-24) |
|---|---:|---:|
| Source product rows | 14,346,842 | 14,784,862 |
| Facts published | 13,899,272 | 7,669,934 |
| Staging | 243.5 s | 117.8 s |
| Quality | 83.7 s | 30.9 s |
| Dimensions | 234.1 s | 122.7 s |
| Pending facts | 906.3 s | 399.6 s |
| Publication | 105.8 s (with aggregates) | 38.5 s |
| **Total** | **~26 min** | **~11.8 min** |
| Daily fact partition | 2,218 MB | 1,243 MB |
| Peak memory | | 1.39 GB |

Earlier work moved publication from replacing rows to swapping a daily
partition, which took it from about 818 s to about 109 s on the national day.

With the AMBA scope, three online days plus a run in flight fit a small VM.
An ephemeral ETL worker would not save money, because the API needs the
database all day.

## Decided against

- **Moving staging out of PostgreSQL** (reading the ZIP with DuckDB or
  Polars). An AMBA run writes about 1.1 GB of staging, which retention
  already drops. The change would save that transient space and about
  2 minutes per run, against about 2,500 lines of ingestion code, a new
  dependency and risk to reconciliation, retries and bounded memory.
- **Online detailed history, Parquet cold export and SCD2 dimensions.**
  Dropped with the analytics scope.
- **Unlogged staging tables.** Less WAL, but no crash recovery. Only with
  measured WAL pressure.
- **Separate ETL and serving databases, Redis, a managed database.** Not
  justified at this size.

## Smaller implementation choices

- **Untyped staging.** Staging tables are all `VARCHAR`, so the raw values
  survive for debugging and the cleaner is the only place that casts.
- **SCD Type 1 dimensions.** Dimensions update last-write-wins; there is no
  change history to keep.
- **Two-level quality checks.** Critical checks abort the run; warnings are
  logged and the run continues.
- **Bounded rejections.** Counts per reason are complete and reconcile with
  the run counters; only a sample of rejected rows is stored, and the archived
  ZIP holds the rest.
- **Structured logs.** `structlog` events carry `run_id`, file name and row
  counts, and the benchmarks read their numbers from those events.
