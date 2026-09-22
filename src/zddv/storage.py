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
    sequence_name TEXT NOT NULL,
    sequencer_name TEXT,
    component TEXT NOT NULL,
    action TEXT NOT NULL,
    report_id TEXT,
    message TEXT,
    time_text TEXT,
    message_event_index INTEGER NOT NULL,
    log_line INTEGER NOT NULL,
    raw TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, event_index)
);

CREATE INDEX IF NOT EXISTS idx_uvm_sequence_events_sequence
    ON uvm_sequence_events(sequence_name);

CREATE INDEX IF NOT EXISTS idx_uvm_sequence_events_sequencer
    ON uvm_sequence_events(sequencer_name);

CREATE TABLE IF NOT EXISTS uvm_sequence_lifecycle_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    project TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    sequence_count INTEGER NOT NULL,
    event_count INTEGER NOT NULL,
    violation_count INTEGER NOT NULL,
    finished_count INTEGER NOT NULL,
    stopped_count INTEGER NOT NULL,
    active_count INTEGER NOT NULL,
    run_id TEXT,
    input_path TEXT NOT NULL,
    normalized_path TEXT NOT NULL,
    report_path TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_uvm_sequence_lifecycle_created
    ON uvm_sequence_lifecycle_snapshots(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_uvm_sequence_lifecycle_status
    ON uvm_sequence_lifecycle_snapshots(status);

CREATE INDEX IF NOT EXISTS idx_uvm_sequence_lifecycle_run
    ON uvm_sequence_lifecycle_snapshots(run_id);

CREATE TABLE IF NOT EXISTS uvm_sequence_state_events (
    snapshot_id TEXT NOT NULL,
    event_index INTEGER NOT NULL,
    sequence_id TEXT NOT NULL,
    sequence_name TEXT NOT NULL,
    sequencer TEXT,
    parent_sequence_id TEXT,
    state TEXT NOT NULL,
    time_text TEXT,
    metadata_json TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, event_index)
);

CREATE INDEX IF NOT EXISTS idx_uvm_sequence_state_events_sequence
    ON uvm_sequence_state_events(snapshot_id, sequence_id);

CREATE INDEX IF NOT EXISTS idx_uvm_sequence_state_events_state
    ON uvm_sequence_state_events(state);

CREATE TABLE IF NOT EXISTS uvm_item_handshake_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    project TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    item_count INTEGER NOT NULL,
    event_count INTEGER NOT NULL,
    violation_count INTEGER NOT NULL,
    granted_count INTEGER NOT NULL,
    requested_count INTEGER NOT NULL,
    completed_count INTEGER NOT NULL,
    responded_count INTEGER NOT NULL,
    active_count INTEGER NOT NULL,
    partial_count INTEGER NOT NULL,
    run_id TEXT,
    input_path TEXT NOT NULL,
    normalized_path TEXT NOT NULL,
    report_path TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_uvm_item_handshake_created
    ON uvm_item_handshake_snapshots(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_uvm_item_handshake_status
    ON uvm_item_handshake_snapshots(status);

CREATE INDEX IF NOT EXISTS idx_uvm_item_handshake_run
    ON uvm_item_handshake_snapshots(run_id);

CREATE TABLE IF NOT EXISTS uvm_item_handshake_events (
    snapshot_id TEXT NOT NULL,
    event_index INTEGER NOT NULL,
    item_id TEXT NOT NULL,
    event TEXT NOT NULL,
    sequence_id TEXT,
    sequence_name TEXT,
    sequencer TEXT,
    item_name TEXT,
    transaction_id TEXT,
    time_text TEXT,
    metadata_json TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, event_index)
);

CREATE INDEX IF NOT EXISTS idx_uvm_item_handshake_events_item
    ON uvm_item_handshake_events(snapshot_id, item_id);

CREATE INDEX IF NOT EXISTS idx_uvm_item_handshake_events_event
    ON uvm_item_handshake_events(event);

CREATE TABLE IF NOT EXISTS uvm_item_handshake_violations (
    snapshot_id TEXT NOT NULL,
    violation_index INTEGER NOT NULL,
    code TEXT NOT NULL,
    event_index INTEGER NOT NULL,
    item_id TEXT NOT NULL,
    event TEXT NOT NULL,
    message TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, violation_index)
);

CREATE INDEX IF NOT EXISTS idx_uvm_item_handshake_violations_code
    ON uvm_item_handshake_violations(code);

CREATE INDEX IF NOT EXISTS idx_uvm_item_handshake_violations_item
    ON uvm_item_handshake_violations(snapshot_id, item_id);

CREATE TABLE IF NOT EXISTS uvm_arbitration_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    project TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    decision_count INTEGER NOT NULL,
    request_count INTEGER NOT NULL,
    grant_count INTEGER NOT NULL,
    pending_count INTEGER NOT NULL,
    violation_count INTEGER NOT NULL,
    fairness_violation_count INTEGER NOT NULL,
    max_wait_decisions INTEGER NOT NULL,
    fairness_bound INTEGER,
    run_id TEXT,
    input_path TEXT NOT NULL,
    normalized_path TEXT NOT NULL,
    report_path TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_uvm_arbitration_created
    ON uvm_arbitration_snapshots(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_uvm_arbitration_status
    ON uvm_arbitration_snapshots(status);

CREATE INDEX IF NOT EXISTS idx_uvm_arbitration_run
    ON uvm_arbitration_snapshots(run_id);

CREATE TABLE IF NOT EXISTS uvm_arbitration_requests (
    snapshot_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    sequence_id TEXT NOT NULL,
    sequence_name TEXT NOT NULL,
    item_id TEXT,
    priority INTEGER,
    sequencer TEXT NOT NULL,
    exposure_count INTEGER NOT NULL,
    lost_decisions INTEGER NOT NULL,
    granted INTEGER NOT NULL,
    grant_decision_index INTEGER,
    grant_decision_id TEXT,
    pending INTEGER NOT NULL,
    PRIMARY KEY (snapshot_id, request_id)
);

CREATE INDEX IF NOT EXISTS idx_uvm_arbitration_requests_sequence
    ON uvm_arbitration_requests(snapshot_id, sequence_id);

CREATE INDEX IF NOT EXISTS idx_uvm_arbitration_requests_pending
    ON uvm_arbitration_requests(pending);

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

CREATE TABLE IF NOT EXISTS coverage_score_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    project TEXT NOT NULL,
    simulator TEXT NOT NULL,
    input_count INTEGER NOT NULL,
    score REAL NOT NULL,
    by_metric_json TEXT NOT NULL,
    merged_path TEXT NOT NULL,
    summary_path TEXT NOT NULL,
    metrics_path TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_coverage_score_created_at
    ON coverage_score_snapshots(created_at DESC);

CREATE TABLE IF NOT EXISTS coverage_score_metric_counts (
    snapshot_id TEXT NOT NULL,
    metric TEXT NOT NULL,
    covered INTEGER NOT NULL,
    total INTEGER NOT NULL,
    hit_rate REAL,
    PRIMARY KEY (snapshot_id, metric)
);

CREATE INDEX IF NOT EXISTS idx_coverage_score_metric_counts_snapshot
    ON coverage_score_metric_counts(snapshot_id);

CREATE TABLE IF NOT EXISTS formal_result_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    project TEXT NOT NULL,
    backend TEXT NOT NULL,
    engine TEXT,
    status TEXT NOT NULL,
    mode TEXT NOT NULL,
    proof_scope TEXT NOT NULL,
    request_depth INTEGER,
    returncode INTEGER NOT NULL,
    runtime_ms REAL,
    property_count INTEGER NOT NULL,
    assertion_count INTEGER NOT NULL,
    cover_count INTEGER NOT NULL,
    counterexample_count INTEGER NOT NULL,
    bounded_safe_count INTEGER NOT NULL,
    proved_count INTEGER NOT NULL,
    covered_goal_count INTEGER NOT NULL,
    unreached_goal_count INTEGER NOT NULL,
    input_path TEXT NOT NULL,
    report_path TEXT NOT NULL,
    run_dir TEXT NOT NULL,
    log_path TEXT NOT NULL,
    artifacts_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_formal_result_created
    ON formal_result_snapshots(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_formal_result_status
    ON formal_result_snapshots(status);

CREATE INDEX IF NOT EXISTS idx_formal_result_mode
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

CREATE INDEX IF NOT EXISTS idx_formal_property_name
    ON formal_property_results(name);

CREATE INDEX IF NOT EXISTS idx_formal_property_status
    ON formal_property_results(status);

CREATE INDEX IF NOT EXISTS idx_formal_property_interpretation
    ON formal_property_results(interpretation);

CREATE TABLE IF NOT EXISTS formal_trace_snapshots (
    trace_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    project TEXT NOT NULL,
    property_name TEXT NOT NULL,
    property_kind TEXT NOT NULL,
    trace_kind TEXT NOT NULL,
    source TEXT NOT NULL,
    time_unit TEXT,
    signal_count INTEGER NOT NULL,
    step_count INTEGER NOT NULL,
    input_path TEXT NOT NULL,
    input_sha256 TEXT NOT NULL,
    normalized_path TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    limitations_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_formal_trace_created
    ON formal_trace_snapshots(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_formal_trace_property
    ON formal_trace_snapshots(property_name);

CREATE INDEX IF NOT EXISTS idx_formal_trace_kind
    ON formal_trace_snapshots(trace_kind);

CREATE TABLE IF NOT EXISTS formal_trace_signals (
    trace_id TEXT NOT NULL,
    signal_index INTEGER NOT NULL,
    name TEXT NOT NULL,
    width INTEGER,
    metadata_json TEXT NOT NULL,
    PRIMARY KEY (trace_id, signal_index)
);

CREATE INDEX IF NOT EXISTS idx_formal_trace_signal_name
    ON formal_trace_signals(name);

CREATE TABLE IF NOT EXISTS formal_trace_steps (
    trace_id TEXT NOT NULL,
    step_position INTEGER NOT NULL,
    step_index INTEGER NOT NULL,
    time_json TEXT NOT NULL,
    cycle INTEGER,
    values_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    PRIMARY KEY (trace_id, step_position)
);

CREATE INDEX IF NOT EXISTS idx_formal_trace_steps_index
    ON formal_trace_steps(trace_id, step_index);

CREATE TABLE IF NOT EXISTS formal_property_trace_links (
    snapshot_id TEXT NOT NULL,
    property_index INTEGER NOT NULL,
    trace_id TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, property_index, trace_id)
);

CREATE INDEX IF NOT EXISTS idx_formal_property_trace_links_trace
    ON formal_property_trace_links(trace_id);
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
                snapshot_id, event_index, sequence_name, sequencer_name,
                component, action, report_id, message, time_text,
                message_event_index, log_line, raw
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    record["snapshot_id"],
                    int(item["event_index"]),
                    item["sequence"],
                    item.get("sequencer"),
                    item["component"],
                    item["action"],
                    item.get("report_id"),
                    item.get("message"),
                    item.get("time"),
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
            SELECT snapshot_id, event_index, sequence_name, sequencer_name,
                   component, action, report_id, message, time_text,
                   message_event_index, log_line, raw
            FROM uvm_sequence_events
            WHERE snapshot_id = ?
            ORDER BY event_index
            """,
            (snapshot_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def record_uvm_sequence_lifecycle_snapshot(
    project: ProjectConfig,
    record: dict[str, Any],
) -> Path:
    path = database_path(project)
    summary = record["summary"]
    with _connect(project) as db:
        db.execute(
            """
            INSERT OR REPLACE INTO uvm_sequence_lifecycle_snapshots (
                snapshot_id, created_at, project, source, status,
                sequence_count, event_count, violation_count,
                finished_count, stopped_count, active_count, run_id,
                input_path, normalized_path, report_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["snapshot_id"],
                record["created_at"],
                record["project"],
                record["source"],
                record["status"],
                int(summary["sequences"]),
                int(summary["events"]),
                int(summary["violations"]),
                int(summary["finished"]),
                int(summary["stopped"]),
                int(summary["active"]),
                record.get("run_id"),
                record["input_path"],
                record["normalized_path"],
                record["report_path"],
            ),
        )
        db.execute(
            "DELETE FROM uvm_sequence_state_events WHERE snapshot_id = ?",
            (record["snapshot_id"],),
        )
        db.executemany(
            """
            INSERT INTO uvm_sequence_state_events (
                snapshot_id, event_index, sequence_id, sequence_name,
                sequencer, parent_sequence_id, state, time_text, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    record["snapshot_id"],
                    int(item["event_index"]),
                    item["sequence_id"],
                    item["sequence"],
                    item.get("sequencer"),
                    item.get("parent_sequence_id"),
                    item["state"],
                    item.get("time"),
                    json.dumps(item.get("metadata", {}), sort_keys=True),
                )
                for item in record["events"]
            ],
        )
    return path


def list_uvm_sequence_lifecycle_snapshots(
    project: ProjectConfig,
    *,
    limit: int = 20,
    status: str | None = None,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    if limit < 1:
        raise ValueError("limit must be >= 1")
    if status is not None and status not in {"PASS", "FAIL"}:
        raise ValueError(f"Unsupported UVM sequence status: {status}")

    query = """
        SELECT snapshot_id, created_at, project, source, status,
               sequence_count, event_count, violation_count,
               finished_count, stopped_count, active_count, run_id,
               input_path, normalized_path, report_path
        FROM uvm_sequence_lifecycle_snapshots
    """
    clauses: list[str] = []
    params: list[Any] = []
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if run_id is not None:
        clauses.append("run_id = ?")
        params.append(run_id)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with _connect(project) as db:
        rows = db.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def list_uvm_sequence_state_events(
    project: ProjectConfig,
    snapshot_id: str,
    *,
    sequence_id: str | None = None,
) -> list[dict[str, Any]]:
    query = """
        SELECT snapshot_id, event_index, sequence_id, sequence_name,
               sequencer, parent_sequence_id, state, time_text, metadata_json
        FROM uvm_sequence_state_events
        WHERE snapshot_id = ?
    """
    params: list[Any] = [snapshot_id]
    if sequence_id is not None:
        query += " AND sequence_id = ?"
        params.append(sequence_id)
    query += " ORDER BY event_index"

    with _connect(project) as db:
        rows = db.execute(query, params).fetchall()

    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["metadata"] = json.loads(item.pop("metadata_json"))
        result.append(item)
    return result


def record_uvm_item_handshake_snapshot(
    project: ProjectConfig,
    record: dict[str, Any],
) -> Path:
    path = database_path(project)
    summary = record["summary"]
    with _connect(project) as db:
        db.execute(
            """
            INSERT OR REPLACE INTO uvm_item_handshake_snapshots (
                snapshot_id, created_at, project, source, status,
                item_count, event_count, violation_count, granted_count,
                requested_count, completed_count, responded_count,
                active_count, partial_count, run_id, input_path,
                normalized_path, report_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["snapshot_id"],
                record["created_at"],
                record["project"],
                record["source"],
                record["status"],
                int(summary["items"]),
                int(summary["events"]),
                int(summary["violations"]),
                int(summary["granted"]),
                int(summary["requested"]),
                int(summary["completed"]),
                int(summary["responded"]),
                int(summary["active"]),
                int(summary["partial"]),
                record.get("run_id"),
                record["input_path"],
                record["normalized_path"],
                record["report_path"],
            ),
        )
        db.execute(
            "DELETE FROM uvm_item_handshake_events WHERE snapshot_id = ?",
            (record["snapshot_id"],),
        )
        db.executemany(
            """
            INSERT INTO uvm_item_handshake_events (
                snapshot_id, event_index, item_id, event, sequence_id,
                sequence_name, sequencer, item_name, transaction_id,
                time_text, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    record["snapshot_id"],
                    int(item["event_index"]),
                    item["item_id"],
                    item["event"],
                    item.get("sequence_id"),
                    item.get("sequence"),
                    item.get("sequencer"),
                    item.get("item"),
                    item.get("transaction_id"),
                    item.get("time"),
                    json.dumps(item.get("metadata", {}), sort_keys=True),
                )
                for item in record["events"]
            ],
        )
        db.execute(
            "DELETE FROM uvm_item_handshake_violations WHERE snapshot_id = ?",
            (record["snapshot_id"],),
        )
        db.executemany(
            """
            INSERT INTO uvm_item_handshake_violations (
                snapshot_id, violation_index, code, event_index,
                item_id, event, message
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    record["snapshot_id"],
                    int(item["violation_index"]),
                    item["code"],
                    int(item["event_index"]),
                    item["item_id"],
                    item["event"],
                    item["message"],
                )
                for item in record["violations"]
            ],
        )
    return path


def list_uvm_item_handshake_snapshots(
    project: ProjectConfig,
    *,
    limit: int = 20,
    status: str | None = None,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    if limit < 1:
        raise ValueError("limit must be >= 1")
    if status is not None and status not in {"PASS", "FAIL"}:
        raise ValueError(f"Unsupported UVM item status: {status}")

    query = """
        SELECT snapshot_id, created_at, project, source, status,
               item_count, event_count, violation_count, granted_count,
               requested_count, completed_count, responded_count,
               active_count, partial_count, run_id, input_path,
               normalized_path, report_path
        FROM uvm_item_handshake_snapshots
    """
    clauses: list[str] = []
    params: list[Any] = []
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if run_id is not None:
        clauses.append("run_id = ?")
        params.append(run_id)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with _connect(project) as db:
        rows = db.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def list_uvm_item_handshake_events(
    project: ProjectConfig,
    snapshot_id: str,
    *,
    item_id: str | None = None,
) -> list[dict[str, Any]]:
    query = """
        SELECT snapshot_id, event_index, item_id, event, sequence_id,
               sequence_name, sequencer, item_name, transaction_id,
               time_text, metadata_json
        FROM uvm_item_handshake_events
        WHERE snapshot_id = ?
    """
    params: list[Any] = [snapshot_id]
    if item_id is not None:
        query += " AND item_id = ?"
        params.append(item_id)
    query += " ORDER BY event_index"

    with _connect(project) as db:
        rows = db.execute(query, params).fetchall()

    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["metadata"] = json.loads(item.pop("metadata_json"))
        result.append(item)
    return result


def list_uvm_item_handshake_violations(
    project: ProjectConfig,
    snapshot_id: str,
    *,
    limit: int = 100,
    code: str | None = None,
    item_id: str | None = None,
) -> list[dict[str, Any]]:
    if limit < 1:
        raise ValueError("limit must be >= 1")

    query = """
        SELECT snapshot_id, violation_index, code, event_index,
               item_id, event, message
        FROM uvm_item_handshake_violations
        WHERE snapshot_id = ?
    """
    params: list[Any] = [snapshot_id]
    if code is not None:
        query += " AND code = ?"
        params.append(code)
    if item_id is not None:
        query += " AND item_id = ?"
        params.append(item_id)
    query += " ORDER BY violation_index LIMIT ?"
    params.append(limit)

    with _connect(project) as db:
        rows = db.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def record_uvm_arbitration_snapshot(
    project: ProjectConfig,
    record: dict[str, Any],
) -> Path:
    path = database_path(project)
    summary = record["summary"]
    with _connect(project) as db:
        db.execute(
            """
            INSERT OR REPLACE INTO uvm_arbitration_snapshots (
                snapshot_id, created_at, project, source, status,
                decision_count, request_count, grant_count, pending_count,
                violation_count, fairness_violation_count, max_wait_decisions,
                fairness_bound, run_id, input_path, normalized_path, report_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["snapshot_id"],
                record["created_at"],
                record["project"],
                record["source"],
                record["status"],
                int(summary["decisions"]),
                int(summary["requests"]),
                int(summary["grants"]),
                int(summary["pending"]),
                int(summary["violations"]),
                int(summary["fairness_violations"]),
                int(summary["max_wait_decisions"]),
                record.get("fairness_bound"),
                record.get("run_id"),
                record["input_path"],
                record["normalized_path"],
                record["report_path"],
            ),
        )
        db.execute(
            "DELETE FROM uvm_arbitration_requests WHERE snapshot_id = ?",
            (record["snapshot_id"],),
        )
        db.executemany(
            """
            INSERT INTO uvm_arbitration_requests (
                snapshot_id, request_id, sequence_id, sequence_name,
                item_id, priority, sequencer, exposure_count, lost_decisions,
                granted, grant_decision_index, grant_decision_id, pending
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    record["snapshot_id"],
                    item["request_id"],
                    item["sequence_id"],
                    item["sequence"],
                    item.get("item_id"),
                    item.get("priority"),
                    item["sequencer"],
                    int(item["exposure_count"]),
                    int(item["lost_decisions"]),
                    int(bool(item["granted"])),
                    item.get("grant_decision_index"),
                    item.get("grant_decision_id"),
                    int(bool(item["pending"])),
                )
                for item in record["requests"]
            ],
        )
    return path


def list_uvm_arbitration_snapshots(
    project: ProjectConfig,
    *,
    limit: int = 20,
    status: str | None = None,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    if limit < 1:
        raise ValueError("limit must be >= 1")
    if status is not None and status not in {"PASS", "FAIL"}:
        raise ValueError(f"Unsupported UVM arbitration status: {status}")

    query = """
        SELECT snapshot_id, created_at, project, source, status,
               decision_count, request_count, grant_count, pending_count,
               violation_count, fairness_violation_count, max_wait_decisions,
               fairness_bound, run_id, input_path, normalized_path, report_path
        FROM uvm_arbitration_snapshots
    """
    clauses: list[str] = []
    params: list[Any] = []
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if run_id is not None:
        clauses.append("run_id = ?")
        params.append(run_id)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with _connect(project) as db:
        rows = db.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def list_uvm_arbitration_requests(
    project: ProjectConfig,
    snapshot_id: str,
    *,
    pending: bool | None = None,
) -> list[dict[str, Any]]:
    query = """
        SELECT snapshot_id, request_id, sequence_id, sequence_name,
               item_id, priority, sequencer, exposure_count, lost_decisions,
               granted, grant_decision_index, grant_decision_id, pending
        FROM uvm_arbitration_requests
        WHERE snapshot_id = ?
    """
    params: list[Any] = [snapshot_id]
    if pending is not None:
        query += " AND pending = ?"
        params.append(int(pending))
    query += " ORDER BY request_id"

    with _connect(project) as db:
        rows = db.execute(query, params).fetchall()

    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["granted"] = bool(item["granted"])
        item["pending"] = bool(item["pending"])
        result.append(item)
    return result


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


def record_coverage_score_snapshot(
    project: ProjectConfig,
    record: dict[str, Any],
) -> Path:
    """Persist percentage-native coverage scores plus explicitly reported counts."""
    path = database_path(project)
    with _connect(project) as db:
        db.execute(
            """
            INSERT OR REPLACE INTO coverage_score_snapshots (
                snapshot_id, created_at, project, simulator, input_count,
                score, by_metric_json, merged_path, summary_path, metrics_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["snapshot_id"],
                record["created_at"],
                record["project"],
                record["simulator"],
                int(record["input_count"]),
                float(record["score"]),
                json.dumps(record.get("by_metric", {}), sort_keys=True),
                record["merged"],
                record["summary"],
                record["metrics_path"],
            ),
        )
        db.execute(
            "DELETE FROM coverage_score_metric_counts WHERE snapshot_id = ?",
            (record["snapshot_id"],),
        )
        for metric, values in sorted(
            (record.get("by_metric_counts") or {}).items()
        ):
            covered = int(values["covered"])
            total = int(values["total"])
            if covered < 0 or total < 0 or covered > total:
                raise ValueError(
                    f"invalid coverage count for {metric}: {covered}/{total}"
                )
            hit_rate = values.get("hit_rate")
            db.execute(
                """
                INSERT INTO coverage_score_metric_counts (
                    snapshot_id, metric, covered, total, hit_rate
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    record["snapshot_id"],
                    str(metric),
                    covered,
                    total,
                    None if hit_rate is None else float(hit_rate),
                ),
            )
    return path


def list_coverage_score_snapshots(
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
                   score, by_metric_json, merged_path, summary_path, metrics_path
            FROM coverage_score_snapshots
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        counts_by_snapshot: dict[str, dict[str, dict[str, int | float]]] = {}
        for row in rows:
            snapshot_id = str(row["snapshot_id"])
            count_rows = db.execute(
                """
                SELECT metric, covered, total, hit_rate
                FROM coverage_score_metric_counts
                WHERE snapshot_id = ?
                ORDER BY metric
                """,
                (snapshot_id,),
            ).fetchall()
            counts: dict[str, dict[str, int | float]] = {}
            for count_row in count_rows:
                values: dict[str, int | float] = {
                    "covered": int(count_row["covered"]),
                    "total": int(count_row["total"]),
                }
                if count_row["hit_rate"] is not None:
                    values["hit_rate"] = float(count_row["hit_rate"])
                counts[str(count_row["metric"])] = values
            counts_by_snapshot[snapshot_id] = counts

    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["by_metric"] = json.loads(item.pop("by_metric_json"))
        item["by_metric_counts"] = counts_by_snapshot.get(
            str(item["snapshot_id"]), {}
        )
        result.append(item)
    return result


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

def record_formal_result_snapshot(
    project: ProjectConfig,
    record: dict[str, Any],
) -> Path:
    """Persist one normalized formal result snapshot and its property rows."""
    path = database_path(project)
    summary = record["summary"]
    request = record["request"]
    execution = record["execution"]

    with _connect(project) as db:
        db.execute(
            """
            INSERT OR REPLACE INTO formal_result_snapshots (
                snapshot_id, created_at, project, backend, engine, status,
                mode, proof_scope, request_depth, returncode, runtime_ms,
                property_count, assertion_count, cover_count,
                counterexample_count, bounded_safe_count, proved_count,
                covered_goal_count, unreached_goal_count,
                input_path, report_path, run_dir, log_path, artifacts_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                int(execution["returncode"]),
                execution.get("runtime_ms"),
                int(summary["properties"]),
                int(summary["assertions"]),
                int(summary["covers"]),
                int(summary["counterexamples"]),
                int(summary["bounded_safe_assertions"]),
                int(summary["proved_assertions"]),
                int(summary["covered_goals"]),
                int(summary["unreached_goals"]),
                record["input_path"],
                record["report_path"],
                execution["run_dir"],
                execution["log_path"],
                json.dumps(record.get("artifacts", []), sort_keys=True),
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
                    (
                        item["trace"]["path"]
                        if item.get("trace") is not None
                        else None
                    ),
                    (
                        item["trace"]["role"]
                        if item.get("trace") is not None
                        else None
                    ),
                )
                for index, item in enumerate(record["properties"])
            ],
        )
        db.execute(
            "DELETE FROM formal_property_trace_links WHERE snapshot_id = ?",
            (record["snapshot_id"],),
        )
        trace_links: list[tuple[str, int, str]] = []
        for index, item in enumerate(record["properties"]):
            trace = item.get("trace")
            if not isinstance(trace, dict):
                continue
            normalization = trace.get("normalization")
            if not isinstance(normalization, dict):
                continue
            trace_id = normalization.get("trace_id")
            if normalization.get("status") == "NORMALIZED" and trace_id:
                trace_links.append((record["snapshot_id"], index, str(trace_id)))
        db.executemany(
            """
            INSERT INTO formal_property_trace_links (
                snapshot_id, property_index, trace_id
            ) VALUES (?, ?, ?)
            """,
            trace_links,
        )
    return path


def list_formal_result_snapshots(
    project: ProjectConfig,
    *,
    limit: int = 20,
    status: str | None = None,
    mode: str | None = None,
) -> list[dict[str, Any]]:
    if limit < 1:
        raise ValueError("limit must be >= 1")

    allowed_statuses = {"PASS", "FAIL", "UNKNOWN", "ERROR"}
    if status is not None:
        status = status.strip().upper()
        if status not in allowed_statuses:
            raise ValueError(f"Unsupported formal result status: {status}")

    allowed_modes = {"bmc", "prove", "cover"}
    if mode is not None:
        mode = mode.strip().lower()
        if mode not in allowed_modes:
            raise ValueError(f"Unsupported formal mode: {mode}")

    query = """
        SELECT snapshot_id, created_at, project, backend, engine, status,
               mode, proof_scope, request_depth, returncode, runtime_ms,
               property_count, assertion_count, cover_count,
               counterexample_count, bounded_safe_count, proved_count,
               covered_goal_count, unreached_goal_count,
               input_path, report_path, run_dir, log_path, artifacts_json
        FROM formal_result_snapshots
    """
    clauses: list[str] = []
    params: list[Any] = []
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if mode is not None:
        clauses.append("mode = ?")
        params.append(mode)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with _connect(project) as db:
        rows = db.execute(query, params).fetchall()

    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["artifacts"] = json.loads(item.pop("artifacts_json"))
        result.append(item)
    return result


def list_formal_property_results(
    project: ProjectConfig,
    snapshot_id: str,
    *,
    status: str | None = None,
    interpretation: str | None = None,
) -> list[dict[str, Any]]:
    query = """
        SELECT p.snapshot_id, p.property_index, p.name, p.kind, p.status,
               p.interpretation, p.depth, p.effective_depth, p.message,
               p.trace_path, p.trace_role, l.trace_id
        FROM formal_property_results AS p
        LEFT JOIN formal_property_trace_links AS l
          ON l.snapshot_id = p.snapshot_id
         AND l.property_index = p.property_index
        WHERE p.snapshot_id = ?
    """
    params: list[Any] = [snapshot_id]
    if status is not None:
        query += " AND p.status = ?"
        params.append(status.strip().upper())
    if interpretation is not None:
        query += " AND p.interpretation = ?"
        params.append(interpretation.strip().upper())
    query += " ORDER BY p.property_index"

    with _connect(project) as db:
        rows = db.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def record_formal_trace_snapshot(
    project: ProjectConfig,
    record: dict[str, Any],
) -> Path:
    """Persist one normalized formal trace plus its signal catalog and timed steps."""

    path = database_path(project)
    summary = record["summary"]
    with _connect(project) as db:
        db.execute(
            """
            INSERT OR REPLACE INTO formal_trace_snapshots (
                trace_id, created_at, project, property_name, property_kind,
                trace_kind, source, time_unit, signal_count, step_count,
                input_path, input_sha256, normalized_path, summary_json,
                metadata_json, limitations_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["trace_id"],
                record["created_at"],
                record["project"],
                record["property"],
                record["property_kind"],
                record["trace_kind"],
                record["source"],
                record.get("time_unit"),
                int(summary["signals"]),
                int(summary["steps"]),
                record["input_path"],
                record["input_sha256"],
                record["normalized_path"],
                json.dumps(summary, sort_keys=True),
                json.dumps(record.get("metadata", {}), sort_keys=True),
                json.dumps(record.get("limitations", []), sort_keys=True),
            ),
        )

        db.execute(
            "DELETE FROM formal_trace_signals WHERE trace_id = ?",
            (record["trace_id"],),
        )
        db.executemany(
            """
            INSERT INTO formal_trace_signals (
                trace_id, signal_index, name, width, metadata_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    record["trace_id"],
                    index,
                    signal["name"],
                    signal.get("width"),
                    json.dumps(signal.get("metadata", {}), sort_keys=True),
                )
                for index, signal in enumerate(record["signals"])
            ],
        )

        db.execute(
            "DELETE FROM formal_trace_steps WHERE trace_id = ?",
            (record["trace_id"],),
        )
        db.executemany(
            """
            INSERT INTO formal_trace_steps (
                trace_id, step_position, step_index, time_json, cycle,
                values_json, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    record["trace_id"],
                    position,
                    int(step["step"]),
                    json.dumps(step.get("time")),
                    step.get("cycle"),
                    json.dumps(step["values"], sort_keys=True),
                    json.dumps(step.get("metadata", {}), sort_keys=True),
                )
                for position, step in enumerate(record["steps"])
            ],
        )
    return path


def list_formal_trace_snapshots(
    project: ProjectConfig,
    *,
    limit: int = 20,
    property_name: str | None = None,
    trace_kind: str | None = None,
) -> list[dict[str, Any]]:
    if limit < 1:
        raise ValueError("limit must be >= 1")

    if trace_kind is not None:
        trace_kind = trace_kind.strip().lower()
        if trace_kind not in {"counterexample", "witness"}:
            raise ValueError(f"Unsupported formal trace kind: {trace_kind}")

    query = """
        SELECT trace_id, created_at, project, property_name, property_kind,
               trace_kind, source, time_unit, signal_count, step_count,
               input_path, input_sha256, normalized_path, summary_json,
               metadata_json, limitations_json
        FROM formal_trace_snapshots
    """
    clauses: list[str] = []
    params: list[Any] = []
    if property_name is not None:
        clauses.append("property_name = ?")
        params.append(property_name)
    if trace_kind is not None:
        clauses.append("trace_kind = ?")
        params.append(trace_kind)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with _connect(project) as db:
        rows = db.execute(query, params).fetchall()

    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["summary"] = json.loads(item.pop("summary_json"))
        item["metadata"] = json.loads(item.pop("metadata_json"))
        item["limitations"] = json.loads(item.pop("limitations_json"))
        result.append(item)
    return result


def list_formal_trace_signals(
    project: ProjectConfig,
    trace_id: str,
) -> list[dict[str, Any]]:
    with _connect(project) as db:
        rows = db.execute(
            """
            SELECT trace_id, signal_index, name, width, metadata_json
            FROM formal_trace_signals
            WHERE trace_id = ?
            ORDER BY signal_index
            """,
            (trace_id,),
        ).fetchall()

    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["metadata"] = json.loads(item.pop("metadata_json"))
        result.append(item)
    return result


def list_formal_trace_steps(
    project: ProjectConfig,
    trace_id: str,
    *,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    if limit < 1:
        raise ValueError("limit must be >= 1")
    if offset < 0:
        raise ValueError("offset must be >= 0")

    with _connect(project) as db:
        rows = db.execute(
            """
            SELECT trace_id, step_position, step_index, time_json, cycle,
                   values_json, metadata_json
            FROM formal_trace_steps
            WHERE trace_id = ?
            ORDER BY step_position
            LIMIT ? OFFSET ?
            """,
            (trace_id, limit, offset),
        ).fetchall()

    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["time"] = json.loads(item.pop("time_json"))
        item["values"] = json.loads(item.pop("values_json"))
        item["metadata"] = json.loads(item.pop("metadata_json"))
        result.append(item)
    return result

