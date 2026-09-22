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

CREATE TABLE IF NOT EXISTS assertion_events (
    run_id TEXT NOT NULL,
    event_index INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    assertion_name TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT,
    log_path TEXT NOT NULL,
    log_line INTEGER,
    PRIMARY KEY (run_id, event_index)
);

CREATE INDEX IF NOT EXISTS idx_assertions_created_at
    ON assertion_events(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_assertions_status
    ON assertion_events(status);

CREATE INDEX IF NOT EXISTS idx_assertions_name
    ON assertion_events(assertion_name);

CREATE TABLE IF NOT EXISTS uvm_log_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    project TEXT NOT NULL,
    source TEXT NOT NULL,
    test_name TEXT,
    status TEXT NOT NULL,
    count_source TEXT NOT NULL,
    info_count INTEGER NOT NULL,
    warning_count INTEGER NOT NULL,
    error_count INTEGER NOT NULL,
    fatal_count INTEGER NOT NULL,
    total_reports INTEGER NOT NULL,
    input_path TEXT NOT NULL,
    normalized_path TEXT NOT NULL,
    report_path TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_uvm_snapshots_created_at
    ON uvm_log_snapshots(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_uvm_snapshots_status
    ON uvm_log_snapshots(status);

CREATE INDEX IF NOT EXISTS idx_uvm_snapshots_test
    ON uvm_log_snapshots(test_name);

CREATE TABLE IF NOT EXISTS uvm_report_messages (
    snapshot_id TEXT NOT NULL,
    event_index INTEGER NOT NULL,
    severity TEXT NOT NULL,
    report_id TEXT,
    component TEXT,
    message TEXT,
    time_text TEXT,
    source_location TEXT,
    log_line INTEGER NOT NULL,
    raw TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, event_index)
);

CREATE INDEX IF NOT EXISTS idx_uvm_messages_severity
    ON uvm_report_messages(severity);

CREATE INDEX IF NOT EXISTS idx_uvm_messages_report_id
    ON uvm_report_messages(report_id);

CREATE TABLE IF NOT EXISTS uvm_run_links (
    snapshot_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_uvm_run_links_run
    ON uvm_run_links(run_id);

CREATE TABLE IF NOT EXISTS uvm_phase_events (
    snapshot_id TEXT NOT NULL,
    event_index INTEGER NOT NULL,
    phase_name TEXT NOT NULL,
    phase_instance_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    detail TEXT,
    time_text TEXT,
    report_id TEXT NOT NULL,
    message_event_index INTEGER NOT NULL,
    log_line INTEGER NOT NULL,
    raw TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, event_index)
);

CREATE INDEX IF NOT EXISTS idx_uvm_phase_events_phase
    ON uvm_phase_events(phase_name);

CREATE INDEX IF NOT EXISTS idx_uvm_phase_events_action
    ON uvm_phase_events(action);

CREATE TABLE IF NOT EXISTS uvm_objection_events (
    snapshot_id TEXT NOT NULL,
    event_index INTEGER NOT NULL,
    objection_name TEXT,
    object_name TEXT NOT NULL,
    source_object TEXT NOT NULL,
    action TEXT NOT NULL,
    delta INTEGER NOT NULL,
    object_count INTEGER NOT NULL,
    total_count INTEGER NOT NULL,
    time_text TEXT,
    message_event_index INTEGER NOT NULL,
    log_line INTEGER NOT NULL,
    raw TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, event_index)
);

CREATE INDEX IF NOT EXISTS idx_uvm_objection_events_action
    ON uvm_objection_events(action);

CREATE INDEX IF NOT EXISTS idx_uvm_objection_events_source
    ON uvm_objection_events(source_object);

CREATE TABLE IF NOT EXISTS uvm_sequence_events (
    snapshot_id TEXT NOT NULL,
    event_index INTEGER NOT NULL,
    sequencer TEXT NOT NULL,
    sequence_name TEXT NOT NULL,
    action TEXT NOT NULL,
    evidence TEXT NOT NULL,
    time_text TEXT,
    report_id TEXT,
    message TEXT,
    message_event_index INTEGER NOT NULL,
    log_line INTEGER NOT NULL,
    raw TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, event_index)
);

CREATE INDEX IF NOT EXISTS idx_uvm_sequence_events_sequence
    ON uvm_sequence_events(sequence_name);

CREATE INDEX IF NOT EXISTS idx_uvm_sequence_events_sequencer
    ON uvm_sequence_events(sequencer);

CREATE TABLE IF NOT EXISTS functional_coverage_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    project TEXT NOT NULL,
    source TEXT NOT NULL,
    total_bins INTEGER NOT NULL,
    covered_bins INTEGER NOT NULL,
    coverage_rate REAL NOT NULL,
    input_path TEXT NOT NULL,
    normalized_path TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fcov_created_at
    ON functional_coverage_snapshots(created_at DESC);

CREATE TABLE IF NOT EXISTS functional_coverage_bins (
    snapshot_id TEXT NOT NULL,
    bin_index INTEGER NOT NULL,
    scope TEXT NOT NULL,
    coverpoint TEXT NOT NULL,
    bin_name TEXT NOT NULL,
    hits INTEGER NOT NULL,
    goal INTEGER NOT NULL,
    status TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, bin_index)
);

CREATE INDEX IF NOT EXISTS idx_fcov_bins_status
    ON functional_coverage_bins(status);

CREATE INDEX IF NOT EXISTS idx_fcov_bins_coverpoint
    ON functional_coverage_bins(coverpoint);

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


def get_run_record(
    project: ProjectConfig,
    run_id: str,
) -> dict[str, Any] | None:
    with _connect(project) as db:
        row = db.execute(
            """
            SELECT run_id, created_at, project, simulator, simulator_version,
                   top, test_name, seed, status, returncode, duration_ms,
                   run_dir, log_path, waveform_path, coverage_path, timeout_s,
                   command_json, plusargs_json
            FROM runs
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()

    if row is None:
        return None

    record = dict(row)
    record["command"] = json.loads(record.pop("command_json"))
    record["plusargs"] = json.loads(record.pop("plusargs_json"))
    return record


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


def record_assertion_events(
    project: ProjectConfig,
    events: list[dict[str, Any]],
) -> Path:
    path = database_path(project)
    if not events:
        return path

    with _connect(project) as db:
        db.executemany(
            """
            INSERT OR REPLACE INTO assertion_events (
                run_id, event_index, created_at, assertion_name, status,
                message, log_path, log_line
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    event["run_id"],
                    int(event["event_index"]),
                    event["created_at"],
                    event["assertion_name"],
                    event["status"],
                    event.get("message"),
                    event["log_path"],
                    event.get("log_line"),
                )
                for event in events
            ],
        )
    return path


def list_assertion_events(
    project: ProjectConfig,
    *,
    limit: int = 100,
    status: str | None = None,
    assertion_name: str | None = None,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    if limit < 1:
        raise ValueError("limit must be >= 1")
    if status is not None and status not in {"PASS", "FAIL"}:
        raise ValueError(f"Unsupported assertion status: {status}")

    query = """
        SELECT run_id, event_index, created_at, assertion_name, status,
               message, log_path, log_line
        FROM assertion_events
    """
    clauses: list[str] = []
    params: list[Any] = []
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if assertion_name is not None:
        clauses.append("assertion_name = ?")
        params.append(assertion_name)
    if run_id is not None:
        clauses.append("run_id = ?")
        params.append(run_id)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_at DESC, run_id DESC, event_index ASC LIMIT ?"
    params.append(limit)

    with _connect(project) as db:
        rows = db.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def assertion_statistics(project: ProjectConfig) -> dict[str, Any]:
    with _connect(project) as db:
        totals = db.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'PASS' THEN 1 ELSE 0 END) AS passed,
                SUM(CASE WHEN status = 'FAIL' THEN 1 ELSE 0 END) AS failed
            FROM assertion_events
            """
        ).fetchone()

    total = int(totals["total"] or 0)
    passed = int(totals["passed"] or 0)
    failed = int(totals["failed"] or 0)
    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": 100.0 * passed / total if total else 0.0,
    }


def record_uvm_log_snapshot(
    project: ProjectConfig,
    record: dict[str, Any],
) -> Path:
    path = database_path(project)
    summary = record["summary"]
    with _connect(project) as db:
        db.execute(
            """
            INSERT OR REPLACE INTO uvm_log_snapshots (
                snapshot_id, created_at, project, source, test_name, status,
                count_source, info_count, warning_count, error_count,
                fatal_count, total_reports, input_path, normalized_path,
                report_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["snapshot_id"],
                record["created_at"],
                record["project"],
                record["source"],
                record.get("test_name"),
                record["status"],
                record["count_source"],
                int(summary["infos"]),
                int(summary["warnings"]),
                int(summary["errors"]),
                int(summary["fatals"]),
                int(summary["total_reports"]),
                record["input_path"],
                record["normalized_path"],
                record["report_path"],
            ),
        )
        run_id = record.get("run_id")
        if run_id is not None:
            db.execute(
                """
                INSERT OR REPLACE INTO uvm_run_links (snapshot_id, run_id)
                VALUES (?, ?)
                """,
                (record["snapshot_id"], str(run_id)),
            )
        else:
            db.execute(
                "DELETE FROM uvm_run_links WHERE snapshot_id = ?",
                (record["snapshot_id"],),
            )
        db.execute(
            "DELETE FROM uvm_report_messages WHERE snapshot_id = ?",
            (record["snapshot_id"],),
        )
        db.executemany(
            """
            INSERT INTO uvm_report_messages (
                snapshot_id, event_index, severity, report_id, component,
                message, time_text, source_location, log_line, raw
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    record["snapshot_id"],
                    int(item["event_index"]),
                    item["severity"],
                    item.get("report_id"),
                    item.get("component"),
                    item.get("message"),
                    item.get("time"),
                    item.get("source_location"),
                    int(item["log_line"]),
                    item["raw"],
                )
                for item in record["messages"]
            ],
        )

        lifecycle = record.get("lifecycle", {})
        phase_events = lifecycle.get("phase_events", [])
        objection_events = lifecycle.get("objection_events", [])
        sequence_events = lifecycle.get("sequence_events", [])

        db.execute(
            "DELETE FROM uvm_phase_events WHERE snapshot_id = ?",
            (record["snapshot_id"],),
        )
        db.executemany(
            """
            INSERT INTO uvm_phase_events (
                snapshot_id, event_index, phase_name, phase_instance_id,
                action, detail, time_text, report_id, message_event_index,
                log_line, raw
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    record["snapshot_id"],
                    int(item["event_index"]),
                    item["phase"],
                    int(item["phase_id"]),
                    item["action"],
                    item.get("detail"),
                    item.get("time"),
                    item["report_id"],
                    int(item["message_event_index"]),
                    int(item["log_line"]),
                    item["raw"],
                )
                for item in phase_events
            ],
        )

        db.execute(
            "DELETE FROM uvm_objection_events WHERE snapshot_id = ?",
            (record["snapshot_id"],),
        )
        db.executemany(
            """
            INSERT INTO uvm_objection_events (
                snapshot_id, event_index, objection_name, object_name,
                source_object, action, delta, object_count, total_count,
                time_text, message_event_index, log_line, raw
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    record["snapshot_id"],
                    int(item["event_index"]),
                    item.get("objection"),
                    item["object"],
                    item["source_object"],
                    item["action"],
                    int(item["delta"]),
                    int(item["count"]),
                    int(item["total"]),
                    item.get("time"),
                    int(item["message_event_index"]),
                    int(item["log_line"]),
                    item["raw"],
                )
                for item in objection_events
            ],
        )

        db.execute(
            "DELETE FROM uvm_sequence_events WHERE snapshot_id = ?",
            (record["snapshot_id"],),
        )
        db.executemany(
            """
            INSERT INTO uvm_sequence_events (
                snapshot_id, event_index, sequencer, sequence_name,
                action, evidence, time_text, report_id, message,
                message_event_index, log_line, raw
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    record["snapshot_id"],
                    int(item["event_index"]),
                    item["sequencer"],
                    item["sequence"],
                    item["action"],
                    item["evidence"],
                    item.get("time"),
                    item.get("report_id"),
                    item.get("message"),
                    int(item["message_event_index"]),
                    int(item["log_line"]),
                    item["raw"],
                )
                for item in sequence_events
            ],
        )
    return path


def list_uvm_log_snapshots(
    project: ProjectConfig,
    *,
    limit: int = 20,
    status: str | None = None,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    if limit < 1:
        raise ValueError("limit must be >= 1")
    if status is not None and status not in {"PASS", "FAIL"}:
        raise ValueError(f"Unsupported UVM status: {status}")

    query = """
        SELECT s.snapshot_id, s.created_at, s.project, s.source, s.test_name,
               s.status, s.count_source, s.info_count, s.warning_count,
               s.error_count, s.fatal_count, s.total_reports, s.input_path,
               s.normalized_path, s.report_path, l.run_id,
               (
                   SELECT COUNT(*) FROM uvm_phase_events AS p
                   WHERE p.snapshot_id = s.snapshot_id
               ) AS phase_events,
               (
                   SELECT COUNT(*) FROM uvm_objection_events AS o
                   WHERE o.snapshot_id = s.snapshot_id
               ) AS objection_events,
               (
                   SELECT COUNT(*) FROM uvm_sequence_events AS q
                   WHERE q.snapshot_id = s.snapshot_id
               ) AS sequence_events
        FROM uvm_log_snapshots AS s
        LEFT JOIN uvm_run_links AS l ON l.snapshot_id = s.snapshot_id
    """
    clauses: list[str] = []
    params: list[Any] = []
    if status is not None:
        clauses.append("s.status = ?")
        params.append(status)
    if run_id is not None:
        clauses.append("l.run_id = ?")
        params.append(run_id)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY s.created_at DESC LIMIT ?"
    params.append(limit)

    with _connect(project) as db:
        rows = db.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def list_uvm_report_messages(
    project: ProjectConfig,
    snapshot_id: str,
    *,
    severity: str | None = None,
) -> list[dict[str, Any]]:
    if severity is not None and severity not in {
        "UVM_INFO",
        "UVM_WARNING",
        "UVM_ERROR",
        "UVM_FATAL",
    }:
        raise ValueError(f"Unsupported UVM severity: {severity}")

    query = """
        SELECT snapshot_id, event_index, severity, report_id, component,
               message, time_text, source_location, log_line, raw
        FROM uvm_report_messages
        WHERE snapshot_id = ?
    """
    params: list[Any] = [snapshot_id]
    if severity is not None:
        query += " AND severity = ?"
        params.append(severity)
    query += " ORDER BY event_index"

    with _connect(project) as db:
        rows = db.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def list_uvm_phase_events(
    project: ProjectConfig,
    snapshot_id: str,
) -> list[dict[str, Any]]:
    with _connect(project) as db:
        rows = db.execute(
            """
            SELECT snapshot_id, event_index, phase_name, phase_instance_id,
                   action, detail, time_text, report_id, message_event_index,
                   log_line, raw
            FROM uvm_phase_events
            WHERE snapshot_id = ?
            ORDER BY event_index
            """,
            (snapshot_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_uvm_objection_events(
    project: ProjectConfig,
    snapshot_id: str,
) -> list[dict[str, Any]]:
    with _connect(project) as db:
        rows = db.execute(
            """
            SELECT snapshot_id, event_index, objection_name, object_name,
                   source_object, action, delta, object_count, total_count,
                   time_text, message_event_index, log_line, raw
            FROM uvm_objection_events
            WHERE snapshot_id = ?
            ORDER BY event_index
            """,
            (snapshot_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_uvm_sequence_events(
    project: ProjectConfig,
    snapshot_id: str,
) -> list[dict[str, Any]]:
    with _connect(project) as db:
        rows = db.execute(
            """
            SELECT snapshot_id, event_index, sequencer, sequence_name,
                   action, evidence, time_text, report_id, message,
                   message_event_index, log_line, raw
            FROM uvm_sequence_events
            WHERE snapshot_id = ?
            ORDER BY event_index
            """,
            (snapshot_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def record_functional_coverage_snapshot(
    project: ProjectConfig,
    record: dict[str, Any],
) -> Path:
    path = database_path(project)
    with _connect(project) as db:
        db.execute(
            """
            INSERT OR REPLACE INTO functional_coverage_snapshots (
                snapshot_id, created_at, project, source, total_bins,
                covered_bins, coverage_rate, input_path, normalized_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["snapshot_id"],
                record["created_at"],
                record["project"],
                record["source"],
                int(record["total_bins"]),
                int(record["covered_bins"]),
                float(record["coverage_rate"]),
                record["input_path"],
                record["normalized_path"],
            ),
        )
        db.execute(
            "DELETE FROM functional_coverage_bins WHERE snapshot_id = ?",
            (record["snapshot_id"],),
        )
        db.executemany(
            """
            INSERT INTO functional_coverage_bins (
                snapshot_id, bin_index, scope, coverpoint, bin_name,
                hits, goal, status, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    record["snapshot_id"],
                    int(item["bin_index"]),
                    item["scope"],
                    item["coverpoint"],
                    item["bin_name"],
                    int(item["hits"]),
                    int(item["goal"]),
                    item["status"],
                    json.dumps(item.get("metadata", {}), sort_keys=True),
                )
                for item in record["bins"]
            ],
        )
    return path


def list_functional_coverage_snapshots(
    project: ProjectConfig,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    if limit < 1:
        raise ValueError("limit must be >= 1")

    with _connect(project) as db:
        rows = db.execute(
            """
            SELECT snapshot_id, created_at, project, source, total_bins,
                   covered_bins, coverage_rate, input_path, normalized_path
            FROM functional_coverage_snapshots
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_functional_coverage_bins(
    project: ProjectConfig,
    snapshot_id: str,
    *,
    status: str | None = None,
) -> list[dict[str, Any]]:
    if status is not None and status not in {"COVERED", "UNCOVERED"}:
        raise ValueError(f"Unsupported functional coverage status: {status}")

    query = """
        SELECT snapshot_id, bin_index, scope, coverpoint, bin_name,
               hits, goal, status, metadata_json
        FROM functional_coverage_bins
        WHERE snapshot_id = ?
    """
    params: list[Any] = [snapshot_id]
    if status is not None:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY scope, coverpoint, bin_name, bin_index"

    with _connect(project) as db:
        rows = db.execute(query, params).fetchall()

    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["metadata"] = json.loads(item.pop("metadata_json"))
        result.append(item)
    return result


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
