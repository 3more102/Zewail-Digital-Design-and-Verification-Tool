from pathlib import Path

from zddv.assertions import parse_verilator_assertions
from zddv.config import initialize_project
from zddv.simulator.verilator import VerilatorBackend
from zddv.storage import (
    assertion_statistics,
    list_assertion_events,
    record_assertion_events,
)


def test_parse_named_verilator_assertion():
    text = (
        "[3] %Error: top.v:42: Assertion failed in "
        "TOP.top.a_valid: 'assert property' failed.\n"
        "%Error: top.v:42: Verilog $stop\n"
    )

    events = parse_verilator_assertions(text)

    assert len(events) == 1
    event = events[0]
    assert event["time"] == "3"
    assert event["severity"] == "ERROR"
    assert event["source_path"] == "top.v"
    assert event["source_line"] == 42
    assert event["scope"] == "TOP.top.a_valid"
    assert event["assertion_name"] == "a_valid"
    assert event["message"] == "'assert property' failed."


def test_parse_multiline_and_windows_path():
    text = (
        "[0] %Fatal: C:\\work\\checker.sv:294:7: Assertion failed in "
        "TOP.tb.u_chk.a_latency:\n"
        "            SVA: read data did not arrive on time\n"
        "%Error: C:\\work\\checker.sv:294: Verilog $stop\n"
    )

    events = parse_verilator_assertions(text)

    assert len(events) == 1
    event = events[0]
    assert event["severity"] == "FATAL"
    assert event["source_path"] == r"C:\work\checker.sv"
    assert event["source_line"] == 294
    assert event["source_column"] == 7
    assert event["assertion_name"] == "a_latency"
    assert event["message"] == "SVA: read data did not arrive on time"


def test_assertion_parser_ignores_non_assertion_errors():
    text = "%Error: top.v:12: Verilog $stop\nAborting...\n"
    assert parse_verilator_assertions(text) == []


def test_assertion_storage_and_statistics(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    run_record = {
        "run_id": "run-1",
        "created_at": "2026-09-21T20:00:00+00:00",
        "project": "demo",
        "simulator": "verilator",
    }
    events = [
        {
            "event_index": 0,
            "time": "12",
            "severity": "ERROR",
            "source_path": "checker.sv",
            "source_line": 55,
            "source_column": None,
            "scope": "TOP.tb.a_ready",
            "assertion_name": "a_ready",
            "message": "ready protocol violated",
        },
        {
            "event_index": 1,
            "time": "19",
            "severity": "ERROR",
            "source_path": "checker.sv",
            "source_line": 55,
            "source_column": None,
            "scope": "TOP.tb.a_ready",
            "assertion_name": "a_ready",
            "message": "ready protocol violated",
        },
    ]

    record_assertion_events(project, run_record, events)

    rows = list_assertion_events(project, limit=10, run_id="run-1")
    assert len(rows) == 2
    assert rows[0]["assertion_name"] == "a_ready"
    assert rows[1]["sim_time"] == "19"

    stats = assertion_statistics(project)
    assert stats == {
        "total_events": 2,
        "affected_runs": 1,
        "unique_assertions": 1,
    }


def test_verilator_version_parser():
    assert VerilatorBackend._version_number("Verilator 5.020 2024-01-01") == (5, 20)
    assert VerilatorBackend._version_number("Verilator 5.052 devel") == (5, 52)
    assert VerilatorBackend._version_number("unknown") is None
