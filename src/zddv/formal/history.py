from __future__ import annotations

import json
import sqlite3
from typing import Any

from zddv.config import ProjectConfig
from zddv.storage import database_path


FORMAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS formal_result_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    project TEXT NOT NULL,
    backend TEXT NOT NULL,
    engine TEXT,
    status TEXT NOT NULL,
    mode TEXT NOT NULL,
    scope TEXT NOT NULL,
    request_depth INTEGER,
    timeout_s REAL,
    returncode INTEGER NOT NULL,
    runtime_ms REAL,
    run_dir TEXT NOT NULL,
    log_path TEXT NOT NULL,
    input_path TEXT NOT NULL,
    report_path TEXT NOT NULL,
    command_json TEXT NOT NULL,
    artifacts_json TEXT NOT NULL,
    property_count INTEGER NOT NULL,
    assertion_count INTEGER NOT NULL,
    cover_count INTEGER NOT NULL,
    counterexample_count INTEGER NOT NULL,
    bounded_safe_count INTEGER NOT NULL,
    proved_count INTEGER NOT NULL,
    covered_goal_count INTEGER NOT NULL,
    unreached_goal_count INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_formal_snapshots_created
    ON formal_result_snapshots(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_formal_snapshots_backend
    ON formal_result_snapshots(backend);

CREATE INDEX IF NOT EXISTS idx_formal_snapshots_status
    ON formal_result_snapshots(status);

CREATE INDEX IF NOT EXISTS idx_formal_snapshots_mode
    ON formal_result_snapshots(mode);

CREATE TABLE IF NOT EXISTS formal_property_results (
    snapshot_id TEXT NOT NULL,
    property_index INTEGER NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    interpretation TEXT NOT NULL,
    depth INTEGER,
    effective_depth INTEGER,
    message TEXT,
    trace_path TEXT,
    trace_role TEXT,
    PRIMARY KEY (snapshot_id, property_index)
);

CREATE INDEX IF NOT EXISTS idx_formal_properties_name
    ON formal_property_results(name);

CREATE INDEX IF NOT EXISTS idx_formal_properties_kind
    ON formal_property_results(kind);

CREATE INDEX IF NOT EXISTS idx_formal_properties_status
    ON formal_property_results(status);

CREATE INDEX IF NOT EXISTS idx_formal_properties_interpretation
    ON formal_property_results(interpretation);
"""


def _connect(project: ProjectConfig) -> sqlite3.Connection:
    path = database_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=30000")
    connection.executescript(FORMAL_SCHEMA)
    return connection


def record_formal_result(
    project: ProjectConfig,
    record: dict[str, Any],
):
    """Persist one normalized formal snapshot and its property outcomes."""

    request = record["request"]
    execution = record["execution"]
    summary = record["summary"]

    with _connect(project) as db:
        db.execute(
            """
            INSERT OR REPLACE INTO formal_result_snapshots (
                snapshot_id, created_at, project, backend, engine, status,
                mode, scope, request_depth, timeout_s, returncode, runtime_ms,
                run_dir, log_path, input_path, report_path, command_json,
                artifacts_json, property_count, assertion_count, cover_count,
                counterexample_count, bounded_safe_count, proved_count,
                covered_goal_count, unreached_goal_count
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                record["snapshot_id"],
                record["created_at"],
                record["project"],
                record["backend"],
                record.get("engine"),
                record["status"],
                request["mode"],
                request["scope"],
                request.get("depth"),
                request.get("timeout_s"),
                int(execution["returncode"]),
                execution.get("runtime_ms"),
                execution["run_dir"],
                execution["log_path"],
                record["input_path"],
                record["report_path"],
                json.dumps(execution.get("command", [])),
                json.dumps(record.get("artifacts", [])),
                int(summary["properties"]),
                int(summary["assertions"]),
                int(summary["covers"]),
                int(summary["counterexamples"]),
                int(summary["bounded_safe_assertions"]),
                int(summary["proved_assertions"]),
                int(summary["covered_goals"]),
                int(summary["unreached_goals"]),
            ),
        )

        db.execute(
            "DELETE FROM formal_property_results WHERE snapshot_id = ?",
            (record["snapshot_id"],),
        )
        db.executemany(
            """
            INSERT INTO formal_property_results (
                snapshot_id, property_index, name, kind, status,
                interpretation, depth, effective_depth, message,
                trace_path, trace_role
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    record["snapshot_id"],
                    index,
                    item["name"],
                    item["kind"],
                    item["status"],
                    item["interpretation"],
                    item.get("depth"),
                    item.get("effective_depth"),
                    item.get("message"),
                    None if item.get("trace") is None else item["trace"].get("path"),
                    None if item.get("trace") is None else item["trace"].get("role"),
                )
                for index, item in enumerate(record["properties"])
            ],
        )

    return database_path(project)


def list_formal_result_snapshots(
    project: ProjectConfig,
    *,
    limit: int = 20,
    status: str | None = None,
    backend: str | None = None,
    mode: str | None = None,
) -> list[dict[str, Any]]:
    """Return newest formal snapshots with optional exact filters."""

    if limit < 1:
        raise ValueError("limit must be >= 1")

    query = """
        SELECT snapshot_id, created_at, project, backend, engine, status,
               mode, scope, request_depth, timeout_s, returncode, runtime_ms,
               run_dir, log_path, input_path, report_path, command_json,
               artifacts_json, property_count, assertion_count, cover_count,
               counterexample_count, bounded_safe_count, proved_count,
               covered_goal_count, unreached_goal_count
        FROM formal_result_snapshots
    """
    clauses: list[str] = []
    params: list[Any] = []

    for column, value in (
        ("status", status),
        ("backend", backend),
        ("mode", mode),
    ):
        if value is not None:
            clauses.append(f"{column} = ?")
            params.append(value)

    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_at DESC, snapshot_id DESC LIMIT ?"
    params.append(limit)

    with _connect(project) as db:
        rows = db.execute(query, params).fetchall()

    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["command"] = json.loads(item.pop("command_json"))
        item["artifacts"] = json.loads(item.pop("artifacts_json"))
        result.append(item)
    return result


def list_formal_property_results(
    project: ProjectConfig,
    snapshot_id: str,
    *,
    kind: str | None = None,
    status: str | None = None,
    interpretation: str | None = None,
) -> list[dict[str, Any]]:
    """Return normalized property outcomes for one formal snapshot."""

    query = """
        SELECT snapshot_id, property_index, name, kind, status,
               interpretation, depth, effective_depth, message,
               trace_path, trace_role
        FROM formal_property_results
        WHERE snapshot_id = ?
    """
    params: list[Any] = [snapshot_id]

    for column, value in (
        ("kind", kind),
        ("status", status),
        ("interpretation", interpretation),
    ):
        if value is not None:
            query += f" AND {column} = ?"
            params.append(value)

    query += " ORDER BY property_index"

    with _connect(project) as db:
        rows = db.execute(query, params).fetchall()
    return [dict(row) for row in rows]
