from pathlib import Path

from zddv.cli import main
from zddv.config import initialize_project
from zddv.storage import (
    list_uvm_log_snapshots,
    list_uvm_report_messages,
    record_run,
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


def _record_simulation_run(
    project,
    *,
    run_id: str,
    log_path: Path,
    status: str = "PASS",
    returncode: int = 0,
) -> None:
    run_dir = log_path.parent
    record_run(
        project,
        {
            "run_id": run_id,
            "created_at": "2026-09-22T04:30:00+00:00",
            "project": project.name,
            "simulator": "verilator",
            "simulator_version": "Verilator test",
            "top": "tb_top",
            "test": "run_link_case",
            "seed": 9,
            "status": status,
            "returncode": returncode,
            "duration_ms": 12.0,
            "run_dir": str(run_dir),
            "log": str(log_path),
            "waveform": None,
            "coverage": None,
            "timeout_s": 10.0,
            "command": ["zddv_sim"],
            "plusargs": [],
        },
    )


def test_uvm_analysis_can_resolve_and_link_recorded_run(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    run_dir = project.root / ".zddv" / "runs" / "run-uvm"
    run_dir.mkdir(parents=True)
    log = run_dir / "simulation.log"
    log.write_text(
        "UVM_INFO @ 0: reporter [RNTST] Running test run_link_case...\n"
        "--- UVM Report Summary ---\n"
        "** Report counts by severity\n"
        "UVM_INFO : 1\n"
        "UVM_WARNING : 0\n"
        "UVM_ERROR : 0\n"
        "UVM_FATAL : 0\n",
        encoding="utf-8",
    )
    _record_simulation_run(project, run_id="run-uvm", log_path=log)

    result = analyze_uvm_log(project, None, run_id="run-uvm")

    assert result["status"] == "PASS"
    assert result["run_id"] == "run-uvm"
    assert result["run_status"] == "PASS"
    assert result["run_returncode"] == 0
    assert result["simulator"] == "verilator"
    assert result["source"] == "verilator"
    assert result["input_path"] == str(log.resolve())

    rows = list_uvm_log_snapshots(project, limit=10, run_id="run-uvm")
    assert len(rows) == 1
    assert rows[0]["snapshot_id"] == result["snapshot_id"]
    assert rows[0]["run_id"] == "run-uvm"


def test_uvm_cli_can_analyze_recorded_run_without_path(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    run_dir = project.root / ".zddv" / "runs" / "run-cli"
    run_dir.mkdir(parents=True)
    log = run_dir / "simulation.log"
    log.write_text(
        "UVM_INFO @ 0: reporter [RNTST] Running test cli_run_case...\n"
        "--- UVM Report Summary ---\n"
        "** Report counts by severity\n"
        "UVM_INFO : 1\n"
        "UVM_WARNING : 0\n"
        "UVM_ERROR : 0\n"
        "UVM_FATAL : 0\n",
        encoding="utf-8",
    )
    _record_simulation_run(project, run_id="run-cli", log_path=log)

    rc = main(
        [
            "--project",
            str(project.root),
            "uvm-analyze",
            "--run",
            "run-cli",
        ]
    )
    assert rc == 0
    output = capsys.readouterr().out
    assert "UVM PASS: test=cli_run_case" in output
    assert "Run: run-cli (simulator-status=PASS, returncode=0)" in output

    rc = main(
        [
            "--project",
            str(project.root),
            "uvm-history",
            "--run",
            "run-cli",
        ]
    )
    assert rc == 0
    history = capsys.readouterr().out
    assert "run-cli" in history
    assert "cli_run_case" in history


def test_uvm_run_link_rejects_unknown_run(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")

    try:
        analyze_uvm_log(project, None, run_id="missing-run")
    except ValueError as exc:
        assert "Unknown run ID" in str(exc)
    else:
        raise AssertionError("Expected ValueError for unknown run ID")


def test_uvm_analysis_requires_path_or_run(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")

    try:
        analyze_uvm_log(project, None)
    except ValueError as exc:
        assert "path or --run" in str(exc)
    else:
        raise AssertionError("Expected ValueError when no path or run is supplied")
