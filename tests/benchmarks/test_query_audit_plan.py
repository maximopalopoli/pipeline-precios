from benchmarks.query_audit import AuditQuery, _summarize_plan


def test_summarize_plan_keeps_cost_timing_rows_and_buffer_evidence():
    query = AuditQuery("product_offer_lookup", "GET /api/buscador", "SELECT 1")
    summary = _summarize_plan(
        query,
        [{
            "Plan": {
                "Node Type": "Limit", "Total Cost": 12.34, "Actual Rows": 8,
                "Shared Hit Blocks": 5, "Shared Read Blocks": 2,
            },
            "Planning Time": 0.5, "Execution Time": 1.25,
        }],
    )

    assert summary.as_dict() == {
        "name": "product_offer_lookup", "endpoint": "GET /api/buscador",
        "execution_time_ms": 1.25, "planning_time_ms": 0.5,
        "root_node": "Limit", "total_cost": 12.34, "actual_rows": 8,
        "shared_hit_blocks": 5, "shared_read_blocks": 2,
    }
