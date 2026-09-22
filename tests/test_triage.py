from pathlib import Path

from zddv.assertions import ingest_assertion_log
from zddv.cli import main
from zddv.config import initialize_project
from zddv.storage import record_run
from zddv.triage import (
    build_failure_triage_report,
    group_failure_records,
    signature_from_text,
    write_failure_triage_report,
)


VCD = """$timescale 1ns $end
$scope module tb_top $end
$scope module dut $end
$var wire 4 # count [3:0] $end
$upscope $end
$upscope $end
$enddefinitions $end
#0
b0000 #
#10
b1000 #
"""


def test_signature_normalizes_volatile_values():
    a = signature_from_text(
        "ASSERTION FAILED at /tmp/run-1/tb.sv:42 expected=0x10 got=17",
        status="FAIL",
    )
    b = signature_from_text(
        "ASSERTION FAILED at /tmp/run-2/tb.sv:99 expected=0x20 got=31",
        status="FAIL",
    )

    assert a == b
    assert "0x#" in a
    assert "<path>" in a


def test_timeout_has_stable_signature():
    assert signature_from_text("anything", status="TIMEOUT") == "TIMEOUT"


def test_group_failure_records(tmp_path: Path):
    log1 = tmp_path / "a.log"
    log2 = tmp_path / "b.log"
    log3 = tmp_path / "c.log"

    log1.write_text("FAIL mismatch expected 10 got 11\n", encoding="utf-8")
    log2.write_text("FAIL mismatch expected 20 got 21\n", encoding="utf-8")
    log3.write_text("fatal protocol violation on channel 3\n", encoding="utf-8")

    records = [
        {
            "run_id": "r1",
            "created_at": "2026-09-21T20:00:00+00:00",
            "test_name": "smoke",
            "seed": 1,
            "status": "FAIL",
            "log_path": str(log1),
        },
        {
            "run_id": "r2",
            "created_at": "2026-09-21T20:01:00+00:00",
            "test_name": "smoke",
            "seed": 2,
            "status": "FAIL",
            "log_path": str(log2),
        },
        {
            "run_id": "r3",
            "created_at": "2026-09-21T20:02:00+00:00",
            "test_name": "corner",
            "seed": 3,
            "status": "FAIL",
            "log_path": str(log3),
        },
    ]

    groups = group_failure_records(records)

    assert len(groups) == 2
    assert groups[0]["count"] == 2
    assert groups[0]["tests"] == ["smoke"]
    assert groups[0]["seeds"] == [1, 2]


def _record_failed_run(project, run_id: str, seed: int, created_at: str) -> None:
    run_dir = project.root / ".zddv" / "runs" / run_id
    run_dir.mkdir(parents=True)
    waveform = run_dir / "waveform.vcd"
    waveform.write_text(VCD, encoding="utf-8")
    log = run_dir / "simulation.log"
    log.write_text(
        "ERROR count mismatch expected=7 got=8\n"
        "ZDDV_ASSERT counter_sequence FAIL count mismatch\n",
        encoding="utf-8",
    )
    record_run(
        project,
        {
            "run_id": run_id,
            "created_at": created_at,
            "project": project.name,
            "simulator": "verilator",
            "simulator_version": "Verilator test",
            "top": "tb_top",
            "test": "smoke",
            "seed": seed,
            "status": "FAIL",
            "returncode": 1,
            "duration_ms": 12.0,
            "run_dir": str(run_dir),
            "log": str(log),
            "waveform": str(waveform),
            "coverage": None,
            "timeout_s": 10.0,
            "command": ["zddv_sim"],
            "plusargs": [],
        },
    )
    ingest_assertion_log(
        project,
        run_id=run_id,
        log_path=log,
        created_at=created_at,
    )


def test_failure_triage_ranks_direct_assertion_and_signal_evidence(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    _record_failed_run(project, "run-a", 1, "2026-09-22T18:00:00+00:00")
    _record_failed_run(project, "run-b", 2, "2026-09-22T18:05:00+00:00")

    report = build_failure_triage_report(project)

    assert report["summary"]["failed_runs"] == 2
    assert report["summary"]["failure_groups"] == 1
    group = report["groups"][0]
    assert group["evidence_summary"]["correlated_assertion_events"] == 2

    assertion = next(
        item
        for item in group["candidates"]
        if item["kind"] == "assertion" and item["name"] == "counter_sequence"
    )
    assert assertion["supporting_runs"] == 2
    assert assertion["event_count"] == 2

    signal = next(
        item
        for item in group["candidates"]
        if item["kind"] == "signal" and item["name"] == "tb_top.dut.count"
    )
    assert signal["supporting_runs"] == 2
    assert signal["matches"] == ["exact-name"]

    probe = group["suggested_probes"][0]
    assert probe["run_id"] == "run-b"
    assert probe["signal"] == "tb_top.dut.count"
    assert probe["command"][-3:] == ["tb_top.dut.count", "--run", "run-b"]
    assert "not a claim of causality" in report["ranking_semantics"]


def test_failure_triage_report_and_cli(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    _record_failed_run(project, "run-cli", 7, "2026-09-22T18:10:00+00:00")

    result = write_failure_triage_report(project)
    assert Path(result["path"]).is_file()

    rc = main([
        "--project",
        str(project.root),
        "triage",
        "--show",
        "5",
    ])

    assert rc == 0
    output = capsys.readouterr().out
    assert "TRIAGE: 1 failure group(s)" in output
    assert "counter_sequence" in output
    assert "tb_top.dut.count" in output
    assert (project.root / ".zddv" / "debug" / "failure-triage.json").is_file()
