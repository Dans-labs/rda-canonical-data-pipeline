# python
from fastapi import APIRouter, Query
from datetime import datetime
from typing import Optional

router = APIRouter(prefix="", tags=["metrics"])

# GET /metrics/sync/status
# Purpose: overall sync status for DB, External API and Elasticsearch
# Query params: ?target=(db|api|es|all)
# Example response:
# {
#   "timestamp": "2026-01-05T12:00:00Z",
#   "status": "OK",
#   "components": {
#     "database": {"status": "OK", "last_success": "2026-01-05T11:59:50Z"},
#     "external_api": {"status": "DEGRADED", "last_fetch": "2026-01-05T11:58:00Z", "error_rate": 0.12},
#     "elasticsearch": {"status": "OK", "last_index": "2026-01-05T11:59:55Z", "cluster_health": "green"}
#   }
# }
@router.get("/sync/status")
def get_sync_status(target: str = Query("all")):
    # Replace with actual checks: DB ping, last job timestamps, ES cluster health, API health
    now = datetime.utcnow().isoformat() + "Z"
    return {
        "timestamp": now,
        "status": "OK",
        "components": {
            "database": {"status": "OK", "last_success": now},
            "external_api": {"status": "OK", "last_fetch": now},
            "elasticsearch": {"status": "OK", "last_index": now, "cluster_health": "green"}
        }
    }

# GET /metrics/sync/counts
# Purpose: compare counts between DB source table(s), deduplicated table, and ES index
# Query params: ?source_table=...&dedup_table=...&es_index=...&since=ISO8601
# Example response:
# {
#   "source_count": 12000,
#   "dedup_count": 11800,
#   "es_count": 11800,
#   "delta_source_to_dedup": 200,
#   "delta_dedup_to_es": 0,
#   "snapshot_time": "2026-01-05T11:59:00Z"
# }
@router.get("/sync/counts")
def get_sync_counts(
    source_table: Optional[str] = None,
    dedup_table: Optional[str] = None,
    es_index: Optional[str] = None,
    since: Optional[str] = None,
):
    # Query DB and ES for counts; return diffs and percentages
    return {
        "source_count": 12000,
        "dedup_count": 11800,
        "es_count": 11800,
        "delta_source_to_dedup": 200,
        "delta_dedup_to_es": 0,
        "snapshot_time": datetime.utcnow().isoformat() + "Z"
    }

# GET /metrics/sync/lag
# Purpose: show ingestion lag (time since source change -> indexed in ES)
# Query params: ?window_minutes=60
# Example response:
# {
#   "avg_lag_seconds": 45.2,
#   "p95_lag_seconds": 120,
#   "max_lag_seconds": 3600,
#   "samples": [...]
# }
@router.get("/sync/lag")
def get_sync_lag(window_minutes: int = Query(60, ge=1)):
    # Compute lag using last-modified timestamps in DB vs ES `_timestamp` or job logs
    return {
        "avg_lag_seconds": 45.2,
        "p95_lag_seconds": 120,
        "max_lag_seconds": 3600,
        "window_minutes": window_minutes
    }

# GET /metrics/ingest/throughput
# Purpose: ingestion throughput and error rates for recent time windows
# Query params: ?window_minutes=60&granularity=minute
# Example response:
# {
#   "window_minutes": 60,
#   "total_indexed": 3600,
#   "avg_per_minute": 60,
#   "error_rate": 0.005,
#   "buckets": [{"t":"2026-01-05T11:00:00Z","count":60,"errors":0}, ...]
# }
@router.get("/ingest/throughput")
def get_ingest_throughput(window_minutes: int = Query(60, ge=1), granularity: str = Query("minute")):
    # Use job logs or ES ingest stats to build metrics
    return {
        "window_minutes": window_minutes,
        "total_indexed": 3600,
        "avg_per_minute": 60,
        "error_rate": 0.005,
        "buckets": []
    }

# GET /metrics/dedup/stats
# Purpose: deduplication metrics: how many rows were deduplicated, when, per-source
# Example response:
# {
#   "total_rows": 12000,
#   "deduplicated_rows": 200,
#   "dedup_rate": 0.0167,
#   "last_dedup_run": "2026-01-05T11:50:00Z",
#   "per_country": {"NL": 120, "US": 80}
# }
@router.get("/dedup/stats")
def get_dedup_stats():
    # Query deduplicated table and mapping tables for counts
    return {
        "total_rows": 12000,
        "deduplicated_rows": 200,
        "dedup_rate": 0.0167,
        "last_dedup_run": datetime.utcnow().isoformat() + "Z"
    }

# GET /metrics/errors
# Purpose: recent pipeline errors, failures and counts (paginated)
# Query params: ?since=ISO8601&limit=50
# Example response:
# {
#   "total_errors": 5,
#   "errors": [{"time":"...","component":"external_api","message":"timeout", "job_id":"..."}]
# }
@router.get("/errors")
def get_errors(since: Optional[str] = None, limit: int = Query(50, ge=1, le=1000)):
    return {
        "total_errors": 0,
        "errors": []
    }

# GET /metrics/health
# Purpose: lightweight health check for automation (returns 200 when healthy)
# Example response:
# {"status":"OK","checks":{"db":true,"es":true,"queue":true}}
@router.get("/health")
def health():
    # Perform quick DB and ES pings
    return {"status": "OK", "checks": {"db": True, "es": True, "queue": True}}