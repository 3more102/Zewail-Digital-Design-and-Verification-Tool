from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any

from zddv.config import ProjectConfig


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    project TEXT NOT NULL,
    simulator TEXT NOT NULL,
    simulator_version TEXT NOT NULL,
    top TEXT NOT NULL,
    test_name TEXT,
    seed INTEGER,
    status TEXT NOT NULL,
    returncode INTEGER NOT NULL,
    duration_ms REAL,
    run_dir TEXT NOT NULL,
    log_path TEXT NOT NULL,
    waveform_path TEXT,
    coverage_path TEXT,
    timeout_s REAL,
    command_json TEXT NOT NULL,
    plusargs_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_created_at
    ON runs(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_runs_status
    ON runs(status);

CREATE INDEX IF NOT EXISTS idx_runs_test_seed
    ON runs(test_name, seed);

CREATE TABLE IF NOT EXISTS coverage_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    project TEXT NOT NULL,
    simulator TEXT NOT NULL,
    input_count INTEGER NOT NULL,
    total_points INTEGER NOT NULL,
    hit_points INTEGER NOT NULL,
    hit_rate REAL NOT NULL,
    by_type_json TEXT NOT NULL,
    merged_path TEXT NOT NULL,
    summary_path TEXT NOT NULL,
    metrics_path TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_coverage_created_at
    ON coverage_snapshots(created_at DESC);

CREATE TABLE IF NOT EXISTS assertion_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    event_index INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    project TEXT NOT NULL,
    simulator TEXT NOT NULL,
    severity TEXT NOT NULL,
    sim_time TEXT,
    source_path TEXT NOT NULL,
    source_line INTEGER NOT NULL,
    source_column INTEGER,
    scope TEXT,
    assertion_name TEXT,
    message TEXT NOT NULL,
    UNIQUE(run_id, event_index)
);

CREATE INDEX IF NOT EXISTS idx_assertion_run
    ON assertion_events(run_id);

CREATE INDEX IF NOT EXISTS idx_assertion_name
    ON assertion_events(assertion_name);
"""


def database_path(project: ProjectConfig) -> Path:
    return (project.root / ".zddv" / "results.db").resolve()


def _connect(project: ProjectConfig) -> sqlite3.Connection:
    path = database_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=30000")
    connection.executescript(SCHEMA)
    return connection


def record_run(project: ProjectConfig, record: dict[str, Any]) -> Path:
    path = database_path(project)
    with _connect(project) as db:
        db.execute(
            """
            INSERT OR REPLACE INTO runs (
                run_id, created_at, project, simulator, simulator_version,
                top, test_name, seed, status, returncode, duration_ms,
                run_dir, log_path, waveform_path, coverage_path, timeout_s,
                command_json, plusargs_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["run_id"],
                record["created_at"],
                record["project"],
                record["simulator"],
                record["simulator_version"],
                record["top"],
                record.get("test"),
                record.get("seed"),
                record["status"],
                int(record["returncode"]),
                record.get("duration_ms"),
                record["run_dir"],
                record["log"],
                record.get("waveform"),
                record.get("coverage"),
                record.get("timeout_s"),
                json.dumps(record.get("command", [])),
                json.dumps(record.get("plusargs", [])),
            ),
        )
    return path


def list_runs(
    project: ProjectConfig,
    *,
    limit: int = 20,
    status: str | None = None,
) -> list[dict[str, Any]]:
    if limit < 1:
        raise ValueError("limit must be >= 1")

    query = """
        SELECT run_id, created_at, test_name, seed, status, returncode,
               duration_ms, simulator, run_dir
        FROM runs
    """
    params: list[Any] = []
    if status:
        query += " WHERE status = ?"
        params.append(status)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with _connect(project) as db:
        rows = db.execute(query, params).fetchall()

    return [dict(row) for row in rows]


def list_run_records(
    project: ProjectConfig,
    *,
    limit: int = 100,
    statuses: tuple[str, ...] | list[str] | None = None,
) -> list[dict[str, Any]]:
    if limit < 1:
        raise ValueError("limit must be >= 1")

    allowed = {"PASS", "FAIL", "TIMEOUT"}
    normalized = tuple(statuses or ())
    invalid = sorted(set(normalized) - allowed)
    if invalid:
        raise ValueError(f"Unsupported run status: {', '.join(invalid)}")

    query = """
        SELECT run_id, created_at, project, simulator, simulator_version,
               top, test_name, seed, status, returncode, duration_ms,
               run_dir, log_path, waveform_path, coverage_path, timeout_s,
               command_json, plusargs_json
        FROM runs
    """
    params: list[Any] = []
    if normalized:
        placeholders = ", ".join("?" for _ in normalized)
        query += f" WHERE status IN ({placeholders})"
        params.extend(normalized)

    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with _connect(project) as db:
        rows = db.execute(query, params).fetchall()

    records: list[dict[str, Any]] = []
    for row in rows:
        record = dict(row)
        record["command"] = json.loads(record.pop("command_json"))
        record["plusargs"] = json.loads(record.pop("plusargs_json"))
        records.append(record)
    return records


def run_statistics(project: ProjectConfig) -> dict[str, Any]:
    with _connect(project) as db:
        totals = db.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'PASS' THEN 1 ELSE 0 END) AS passed,
                SUM(CASE WHEN status = 'FAIL' THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN status = 'TIMEOUT' THEN 1 ELSE 0 END) AS timed_out
            FROM runs
            """
        ).fetchone()

        tests = db.execute(
            """
            SELECT
                COALESCE(test_name, '(default)') AS test_name,
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'PASS' THEN 1 ELSE 0 END) AS passed,
                SUM(CASE WHEN status = 'FAIL' THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN status = 'TIMEOUT' THEN 1 ELSE 0 END) AS timed_out
            FROM runs
            GROUP BY COALESCE(test_name, '(default)')
            ORDER BY test_name
            """
        ).fetchall()

    total = int(totals["total"] or 0)
    passed = int(totals["passed"] or 0)
    failed = int(totals["failed"] or 0)
    timed_out = int(totals["timed_out"] or 0)

    test_rows: list[dict[str, Any]] = []
    for row in tests:
        row_total = int(row["total"] or 0)
        row_passed = int(row["passed"] or 0)
        test_rows.append(
            {
                "test_name": str(row["test_name"]),
                "total": row_total,
                "passed": row_passed,
                "failed": int(row["failed"] or 0),
                "timed_out": int(row["timed_out"] or 0),
                "pass_rate": (
                    100.0 * row_passed / row_total if row_total else 0.0
                ),
            }
        )

    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "timed_out": timed_out,
        "pass_rate": 100.0 * passed / total if total else 0.0,
        "tests": test_rows,
    }


def record_coverage_snapshot(
    project: ProjectConfig,
    record: dict[str, Any],
) -> Path:
    path = database_path(project)
    with _connect(project) as db:
        db.execute(
            """
            INSERT OR REPLACE INTO coverage_snapshots (
                snapshot_id, created_at, project, simulator, input_count,
                total_points, hit_points, hit_rate, by_type_json,
                merged_path, summary_path, metrics_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["snapshot_id"],
                record["created_at"],
                record["project"],
                record["simulator"],
                int(record["input_count"]),
                int(record["total_points"]),
                int(record["hit_points"]),
                float(record["hit_rate"]),
                json.dumps(record.get("by_type", {}), sort_keys=True),
                record["merged"],
                record["summary"],
                record["metrics_path"],
            ),
        )
    return path


def list_coverage_snapshots(
    project: ProjectConfig,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    if limit < 1:
        raise ValueError("limit must be >= 1")

    with _connect(project) as db:
        rows = db.execute(
            """
            SELECT snapshot_id, created_at, project, simulator, input_count,
                   total_points, hit_points, hit_rate, by_type_json,
                   merged_path, summary_path, metrics_path
            FROM coverage_snapshots
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["by_type"] = json.loads(item.pop("by_type_json"))
        result.append(item)
    return result


def record_assertion_events(
    project: ProjectConfig,
    run_record: dict[str, Any],
    events: list[dict[str, Any]],
) -> Path:
    path = database_path(project)
    with _connect(project) as db:
        db.execute(
            "DELETE FROM assertion_events WHERE run_id = ?",
            (run_record["run_id"],),
        )
        db.executemany(
            """
            INSERT INTO assertion_events (
                run_id, event_index, created_at, project, simulator, severity,
                sim_time, source_path, source_line, source_column, scope,
                assertion_name, message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    run_record["run_id"],
                    int(event["event_index"]),
                    run_record["created_at"],
                    run_record["project"],
                    run_record["simulator"],
                    event["severity"],
                    event.get("time"),
                    event["source_path"],
                    int(event["source_line"]),
                    event.get("source_column"),
                    event.get("scope"),
                    event.get("assertion_name"),
                    event.get("message", ""),
                )
                for event in events
            ],
        )
    return path


def list_assertion_events(
    project: ProjectConfig,
    *,
    limit: int = 100,
    run_id: str | None = None,
    severity: str | None = None,
) -> list[dict[str, Any]]:
    if limit < 1:
        raise ValueError("limit must be >= 1")

    normalized_severity = severity.upper() if severity else None
    allowed = {"WARNING", "ERROR", "FATAL"}
    if normalized_severity and normalized_severity not in allowed:
        raise ValueError(f"Unsupported assertion severity: {severity}")

    query = """
        SELECT run_id, event_index, created_at, project, simulator, severity,
               sim_time, source_path, source_line, source_column, scope,
               assertion_name, message
        FROM assertion_events
    """
    clauses: list[str] = []
    params: list[Any] = []
    if run_id:
        clauses.append("run_id = ?")
        params.append(run_id)
    if normalized_severity:
        clauses.append("severity = ?")
        params.append(normalized_severity)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_at DESC, event_index ASC LIMIT ?"
    params.append(limit)

    with _connect(project) as db:
        rows = db.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def assertion_statistics(project: ProjectConfig) -> dict[str, int]:
    with _connect(project) as db:
        row = db.execute(
            """
            SELECT
                COUNT(*) AS total_events,
                COUNT(DISTINCT run_id) AS affected_runs,
                COUNT(DISTINCT COALESCE(scope, assertion_name, source_path || ':' || source_line))
                    AS unique_assertions
            FROM assertion_events
            """
        ).fetchone()

    return {
        "total_events": int(row["total_events"] or 0),
        "affected_runs": int(row["affected_runs"] or 0),
        "unique_assertions": int(row["unique_assertions"] or 0),
    }
