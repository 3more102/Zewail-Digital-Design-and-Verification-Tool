from pathlib import Path

from zddv.cli import main
from zddv.config import initialize_project
from zddv.storage import (
    list_uvm_log_snapshots,
    list_uvm_objection_events,
    list_uvm_phase_events,
    list_uvm_report_messages,
)
from zddv.uvm import analyze_uvm_log, parse_uvm_log_text


def test_parses_uvm_summary_and_test_metadata():
    result = parse_uvm_log_text(
        """
# UVM_INFO @ 0: reporter [RNTST] Running test axi_smoke...
# UVM_WARNING monitor.sv(42) @ 15 ns: uvm_test_top.env.mon [LATENCY] delayed response
# UVM_INFO test.sv(99) @ 20 ns: uvm_test_top [DONE] test completed
# --- UVM Report Summary ---
# ** Report counts by severity
# UVM_INFO : 3
# UVM_WARNING : 1
# UVM_ERROR : 0
# UVM_FATAL : 0
"""
    )

    assert result["status"] == "PASS"
    assert result["test_name"] == "axi_smoke"
    assert result["count_source"] == "uvm_report_summary"
    assert result["report_summary_complete"] is True
    assert result["summary"] == {
        "infos": 3,
        "warnings": 1,
        "errors": 0,
        "fatals": 0,
        "total_reports": 4,
        "observed_messages": 3,
    }
    warning = next(
        event for event in result["messages"]
        if event["severity"] == "UVM_WARNING"
    )
    assert warning["report_id"] == "LATENCY"
    assert warning["component"] == "uvm_test_top.env.mon"
    assert warning["message"] == "delayed response"
    assert warning["time"] == "15 ns"
    assert warning["source_location"] == "monitor.sv(42)"


def test_complete_summary_is_authoritative_over_visible_messages():
    result = parse_uvm_log_text(
        """
UVM_ERROR @ 10: reporter [CAUGHT] this visible report is later demoted
--- UVM Report Summary ---
** Report counts by severity
UVM_INFO : 1
UVM_WARNING : 1
UVM_ERROR : 0
UVM_FATAL : 0
"""
    )

    assert result["status"] == "PASS"
    assert result["observed_severity_counts"]["UVM_ERROR"] == 1
    assert result["severity_counts"]["UVM_ERROR"] == 0
    assert result["count_source"] == "uvm_report_summary"


def test_falls_back_to_observed_messages_without_complete_summary():
    result = parse_uvm_log_text(
        """
UVM_INFO @ 0: reporter [RNTST] Running test fallback_case...
UVM_WARNING @ 2: reporter [WARN] warning only
UVM_ERROR @ 4: reporter [BAD] scoreboard mismatch
"""
    )

    assert result["status"] == "FAIL"
    assert result["test_name"] == "fallback_case"
    assert result["count_source"] == "observed_messages"
    assert result["report_summary_detected"] is False
    assert result["summary"]["warnings"] == 1
    assert result["summary"]["errors"] == 1
    assert result["summary"]["fatals"] == 0


def test_fatal_summary_fails():
    result = parse_uvm_log_text(
        """
--- UVM Report Summary ---
** Report counts by severity
UVM_INFO : 5
UVM_WARNING : 0
UVM_ERROR : 0
UVM_FATAL : 1
"""
    )

    assert result["status"] == "FAIL"
    assert result["summary"]["fatals"] == 1
    assert result["summary"]["total_reports"] == 6


def test_analyze_uvm_log_persists_snapshot_and_messages(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    log = project.root / "uvm.log"
    log.write_text(
        "UVM_INFO @ 0: reporter [RNTST] Running test persist_case...\n"
        "UVM_WARNING @ 5: uvm_test_top [WARN] slow response\n"
        "--- UVM Report Summary ---\n"
        "** Report counts by severity\n"
        "UVM_INFO : 1\n"
        "UVM_WARNING : 1\n"
        "UVM_ERROR : 0\n"
        "UVM_FATAL : 0\n",
        encoding="utf-8",
    )

    result = analyze_uvm_log(project, log, source="unit-test")

    assert result["status"] == "PASS"
    assert result["source"] == "unit-test"
    assert Path(result["normalized_path"]).is_file()
    assert Path(result["report_path"]).is_file()

    snapshots = list_uvm_log_snapshots(project, limit=10)
    assert len(snapshots) == 1
    assert snapshots[0]["snapshot_id"] == result["snapshot_id"]
    assert snapshots[0]["test_name"] == "persist_case"
    assert snapshots[0]["warning_count"] == 1

    warnings = list_uvm_report_messages(
        project,
        result["snapshot_id"],
        severity="UVM_WARNING",
    )
    assert len(warnings) == 1
    assert warnings[0]["report_id"] == "WARN"
    assert warnings[0]["message"] == "slow response"


def test_uvm_cli_and_history(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    log = project.root / "uvm.log"
    log.write_text(
        "UVM_INFO @ 0: reporter [RNTST] Running test cli_case...\n"
        "--- UVM Report Summary ---\n"
        "** Report counts by severity\n"
        "UVM_INFO : 1\n"
        "UVM_WARNING : 0\n"
        "UVM_ERROR : 0\n"
        "UVM_FATAL : 0\n",
        encoding="utf-8",
    )

    rc = main(
        [
            "--project",
            str(project.root),
            "uvm-analyze",
            str(log),
            "--source",
            "questa",
        ]
    )
    assert rc == 0
    output = capsys.readouterr().out
    assert "UVM PASS: test=cli_case" in output
    assert "I/W/E/F=1/0/0/0" in output

    rc = main(
        [
            "--project",
            str(project.root),
            "uvm-history",
            "--limit",
            "10",
        ]
    )
    assert rc == 0
    history = capsys.readouterr().out
    assert "cli_case" in history
    assert "uvm_report_summary" in history


def test_uvm_cli_returns_failure_for_error_summary(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    log = project.root / "failing.log"
    log.write_text(
        "--- UVM Report Summary ---\n"
        "** Report counts by severity\n"
        "UVM_INFO : 2\n"
        "UVM_WARNING : 0\n"
        "UVM_ERROR : 1\n"
        "UVM_FATAL : 0\n",
        encoding="utf-8",
    )

    rc = main(
        [
            "--project",
            str(project.root),
            "uvm-analyze",
            str(log),
        ]
    )
    assert rc == 1
    assert "UVM FAIL" in capsys.readouterr().out


def test_parses_standard_uvm_phase_and_objection_traces():
    result = parse_uvm_log_text(
        """
UVM_INFO @ 0: reporter [RNTST] Running test lifecycle_case...
UVM_INFO @ 1 ns: reporter [PH/TRC/SCHEDULED] Phase 'common.run' (id=42) Scheduled from phase common
UVM_INFO @ 2 ns: reporter [PH/TRC/STRT] Phase 'common.run' (id=42) Starting phase
UVM_INFO @ 3 ns: reporter [OBJTN_TRC] Object uvm_test_top raised 1 run_objection objection(s) (starting sequence): count=1  total=1
UVM_INFO @ 3 ns: reporter [OBJTN_TRC] Object uvm_top added 1 run_objection objection(s) to its total (raised from source object uvm_test_top, starting sequence): count=0  total=1
UVM_INFO @ 8 ns: reporter [OBJTN_TRC] Object uvm_test_top dropped 1 run_objection objection(s) (sequence done): count=0  total=0
UVM_INFO @ 8 ns: reporter [OBJTN_TRC] Object uvm_top subtracted 1 run_objection objection(s) from its total (dropped from source object uvm_test_top, sequence done): count=0  total=0
UVM_INFO @ 9 ns: reporter [PH_READY_TO_END] Phase 'common.run' (id=42) PHASE READY TO END
UVM_INFO @ 10 ns: reporter [PH_END] Phase 'common.run' (id=42) ENDING PHASE
UVM_INFO @ 11 ns: reporter [PH/TRC/DONE] Phase 'common.run' (id=42) Completed phase
--- UVM Report Summary ---
** Report counts by severity
UVM_INFO : 10
UVM_WARNING : 0
UVM_ERROR : 0
UVM_FATAL : 0
"""
    )

    lifecycle = result["lifecycle"]
    assert lifecycle["phase_trace_detected"] is True
    assert lifecycle["objection_trace_detected"] is True
    assert lifecycle["phase_event_count"] == 5
    assert lifecycle["objection_event_count"] == 4
    assert lifecycle["objection_raise_events"] == 2
    assert lifecycle["objection_drop_events"] == 2
    assert lifecycle["phases"] == ["common.run"]
    assert [event["state"] for event in lifecycle["phase_events"]] == [
        "SCHEDULED",
        "STARTED",
        "READY_TO_END",
        "ENDED",
        "DONE",
    ]

    direct_raise = lifecycle["objection_events"][0]
    assert direct_raise["action"] == "raised"
    assert direct_raise["object"] == "uvm_test_top"
    assert direct_raise["source_object"] == "uvm_test_top"
    assert direct_raise["objection"] == "run_objection"
    assert direct_raise["delta"] == 1
    assert direct_raise["count"] == 1
    assert direct_raise["total"] == 1
    assert direct_raise["description"] == "starting sequence"
    assert direct_raise["propagated"] is False

    propagated_raise = lifecycle["objection_events"][1]
    assert propagated_raise["action"] == "raised"
    assert propagated_raise["object"] == "uvm_top"
    assert propagated_raise["source_object"] == "uvm_test_top"
    assert propagated_raise["propagated"] is True


def test_persists_uvm_lifecycle_events(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    log = project.root / "uvm-lifecycle.log"
    log.write_text(
        "UVM_INFO @ 1 ns: reporter [PH/TRC/STRT] Phase 'common.run' (id=17) Starting phase\n"
        "UVM_INFO @ 2 ns: reporter [OBJTN_TRC] Object uvm_test_top raised 1 run_objection objection(s): count=1  total=1\n"
        "UVM_INFO @ 3 ns: reporter [OBJTN_TRC] Object uvm_test_top dropped 1 run_objection objection(s): count=0  total=0\n"
        "UVM_INFO @ 4 ns: reporter [PH_END] Phase 'common.run' (id=17) ENDING PHASE\n"
        "UVM_INFO @ 5 ns: reporter [PH/TRC/DONE] Phase 'common.run' (id=17) Completed phase\n"
        "--- UVM Report Summary ---\n"
        "** Report counts by severity\n"
        "UVM_INFO : 5\n"
        "UVM_WARNING : 0\n"
        "UVM_ERROR : 0\n"
        "UVM_FATAL : 0\n",
        encoding="utf-8",
    )

    result = analyze_uvm_log(project, log, source="uvm-2020-trace")
    phase_events = list_uvm_phase_events(project, result["snapshot_id"])
    objection_events = list_uvm_objection_events(project, result["snapshot_id"])

    assert [event["state"] for event in phase_events] == [
        "STARTED",
        "ENDED",
        "DONE",
    ]
    assert phase_events[0]["phase"] == "common.run"
    assert phase_events[0]["phase_id"] == 17
    assert phase_events[0]["time_text"] == "1 ns"
    assert phase_events[1]["premature"] is False

    assert [event["action"] for event in objection_events] == [
        "raised",
        "dropped",
    ]
    assert objection_events[0]["object_name"] == "uvm_test_top"
    assert objection_events[0]["source_object"] == "uvm_test_top"
    assert objection_events[0]["source_count"] == 1
    assert objection_events[0]["total_count"] == 1
    assert objection_events[0]["propagated"] is False


def test_marks_premature_phase_end():
    result = parse_uvm_log_text(
        "UVM_INFO @ 20 ns: reporter [PH_END] "
        "Phase 'common.run' (id=8) ENDING PHASE PREMATURELY\n"
    )

    assert result["lifecycle"]["phase_event_count"] == 1
    event = result["lifecycle"]["phase_events"][0]
    assert event["state"] == "ENDED"
    assert event["premature"] is True
