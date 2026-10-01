"""Repeatable ``EXPLAIN ANALYZE`` audits for the warehouse's hot reads."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class AuditQuery:
    name: str
    endpoint: str
    sql: str
    params: tuple[object, ...] = ()


@dataclass(frozen=True)
class QueryPlanSummary:
    name: str
    endpoint: str
    execution_time_ms: float
    planning_time_ms: float
    root_node: str
    total_cost: float
    actual_rows: int
    shared_hit_blocks: int
    shared_read_blocks: int

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def representative_queries(conn: Any, *, fecha_id: int | None = None) -> list[AuditQuery]:
    """Build product-offer audits using a real published fact sample."""
    sample = _latest_product_day(conn, fecha_id=fecha_id)
    if sample is None:
        raise ValueError(
            "fact_precios is empty; publish a full ingestion before auditing API queries"
        )
    selected_fecha_id, producto_sk = sample
    return [
        AuditQuery(
            "product_offer_lookup", "GET /api/buscador",
            """
                SELECT f.precio_lista, f.precio_promo1, f.precio_promo2,
                       s.sucursales_localidad,
                       c.comercio_bandera_nombre
                FROM fact_precios AS f
                JOIN dim_sucursal AS s ON s.sucursal_sk = f.sucursal_sk
                JOIN dim_comercio AS c ON c.comercio_sk = s.comercio_sk
                WHERE f.producto_sk = %s AND f.fecha_id = %s
                ORDER BY LEAST(
                    f.precio_lista,
                    COALESCE(NULLIF(f.precio_promo1, 0), f.precio_lista),
                    COALESCE(NULLIF(f.precio_promo2, 0), f.precio_lista)
                ) ASC
                LIMIT 200
            """,
            (producto_sk, selected_fecha_id),
        ),
        AuditQuery(
            "daily_promotion_scan", "GET /api/ofertas",
            """
                SELECT f.producto_sk, f.sucursal_sk, f.precio_lista,
                       f.precio_promo1, f.precio_promo2
                FROM fact_precios AS f
                WHERE f.fecha_id = %s AND f.tiene_promo = TRUE
                ORDER BY f.producto_sk
                LIMIT 50
            """,
            (selected_fecha_id,),
        ),
    ]


def audit_queries(conn: Any, queries: list[AuditQuery]) -> list[QueryPlanSummary]:
    """Execute and summarize JSON plans for *queries*."""
    summaries: list[QueryPlanSummary] = []
    with conn.cursor() as cur:
        for query in queries:
            cur.execute(
                f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {query.sql}", query.params
            )
            summaries.append(_summarize_plan(query, cur.fetchone()[0]))
    return summaries


def _latest_product_day(conn: Any, *, fecha_id: int | None) -> tuple[int, int] | None:
    with conn.cursor() as cur:
        if fecha_id is None:
            cur.execute(
                """
                SELECT fecha_id, producto_sk FROM fact_precios
                ORDER BY fecha_id DESC, producto_sk LIMIT 1
                """
            )
        else:
            cur.execute(
                """
                SELECT fecha_id, producto_sk FROM fact_precios
                WHERE fecha_id = %s ORDER BY producto_sk LIMIT 1
                """,
                (fecha_id,),
            )
        row = cur.fetchone()
    return (int(row[0]), int(row[1])) if row is not None else None


def _summarize_plan(query: AuditQuery, raw_plan: object) -> QueryPlanSummary:
    plan_document = raw_plan[0] if isinstance(raw_plan, list) else raw_plan
    plan = plan_document["Plan"]
    return QueryPlanSummary(
        name=query.name, endpoint=query.endpoint,
        execution_time_ms=float(plan_document["Execution Time"]),
        planning_time_ms=float(plan_document["Planning Time"]),
        root_node=str(plan["Node Type"]), total_cost=float(plan["Total Cost"]),
        actual_rows=int(plan["Actual Rows"]),
        shared_hit_blocks=int(plan.get("Shared Hit Blocks", 0)),
        shared_read_blocks=int(plan.get("Shared Read Blocks", 0)),
    )
