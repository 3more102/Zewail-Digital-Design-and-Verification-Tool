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
