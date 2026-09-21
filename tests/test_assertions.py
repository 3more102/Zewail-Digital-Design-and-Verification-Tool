from pathlib import Path

from zddv.assertions import ingest_assertion_log, parse_assertion_log
from zddv.config import initialize_project
from zddv.storage import assertion_statistics, list_assertion_events


def test_parse_normalized_assertion_markers(tmp_path: Path):
    log = tmp_path / "simulation.log"
    log.write_text(
        "noise\n"
        "ZDDV_ASSERT fifo_no_overflow PASS depth=4\n"
        "ZDDV_ASSERT fifo_no_underflow FAIL read_when_empty\n",
        encoding="utf-8",
    )

    events = parse_assertion_log(log)

    assert len(events) == 2
    assert events[0] == {
        "event_index": 0,
        "assertion_name": "fifo_no_overflow",
        "status": "PASS",
        "message": "depth=4",
        "log_line": 2,
    }
    assert events[1]["assertion_name"] == "fifo_no_underflow"
    assert events[1]["status"] == "FAIL"


def test_ingest_and_query_assertion_events(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    log = tmp_path / "simulation.log"
    log.write_text(
        "ZDDV_ASSERT counter_sequence PASS count=8\n"
        "ZDDV_ASSERT protocol_guard FAIL bad_response\n",
        encoding="utf-8",
    )

    ingested = ingest_assertion_log(
        project,
        run_id="run-assert",
        log_path=log,
        created_at="2026-09-21T21:00:00+00:00",
    )

    assert len(ingested) == 2
    rows = list_assertion_events(project, limit=10)
    assert len(rows) == 2
    assert rows[0]["run_id"] == "run-assert"

    failed = list_assertion_events(project, limit=10, status="FAIL")
    assert len(failed) == 1
    assert failed[0]["assertion_name"] == "protocol_guard"

    named = list_assertion_events(
        project,
        limit=10,
        assertion_name="counter_sequence",
    )
    assert len(named) == 1
    assert named[0]["status"] == "PASS"

    stats = assertion_statistics(project)
    assert stats == {
        "total": 2,
        "passed": 1,
        "failed": 1,
        "pass_rate": 50.0,
    }
