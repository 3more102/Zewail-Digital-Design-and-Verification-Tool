from pathlib import Path

from zddv.assertions import ingest_assertion_log
from zddv.cli import main
from zddv.config import initialize_project, save_project
from zddv.root_cause import rank_root_cause_candidates, write_root_cause_report
from zddv.storage import record_run


VCD = """$timescale 1ns $end
$scope module TOP $end
$scope module tb_top $end
$var wire 1 ! clk $end
$scope module dut $end
$var wire 4 # count [3:0] $end
$upscope $end
$upscope $end
$upscope $end
$enddefinitions $end
#0
0!
b0000 #
"""


def _project(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    rtl = project.root / "rtl"
    tb = project.root / "tb"
    rtl.mkdir(exist_ok=True)
    tb.mkdir(exist_ok=True)

    (rtl / "counter.sv").write_text(
        """module counter(
    input logic clk,
    output logic [3:0] count
);
    always_ff @(posedge clk)
        count <= count + 1'b1;
endmodule
""",
        encoding="utf-8",
    )
    (tb / "tb_top.sv").write_text(
        """module tb_top;
    logic clk;
    logic [3:0] count;
    counter dut(
        .clk(clk),
        .count(count)
    );
endmodule
""",
        encoding="utf-8",
    )
    project.rtl = ["rtl/*.sv"]
    project.tb = ["tb/*.sv"]
    project.top = "tb_top"
    save_project(project)
    return project


def _record(project, run_id: str, *, status: str, waveform: Path | None, timeout_s=10.0):
    run_dir = project.root / ".zddv" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return {
        "run_id": run_id,
        "created_at": "2026-09-22T19:00:00+00:00",
        "project": project.name,
        "simulator": "verilator",
        "simulator_version": "Verilator test",
        "top": project.top,
        "test": "smoke",
        "seed": 17,
        "status": status,
        "returncode": 124 if status == "TIMEOUT" else 1,
        "duration_ms": 25.0,
        "run_dir": str(run_dir),
        "log": str(run_dir / "simulation.log"),
        "waveform": str(waveform) if waveform else None,
        "coverage": None,
        "timeout_s": timeout_s,
        "command": ["zddv_sim"],
        "plusargs": [],
    }


def _failing_assertion_run(project, run_id: str = "run-fail"):
    run_dir = project.root / ".zddv" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    waveform = run_dir / "waveform.vcd"
    waveform.write_text(VCD, encoding="utf-8")
    log = run_dir / "simulation.log"
    log.write_text(
        "ZDDV_ASSERT count_guard FAIL count mismatch expected=7 actual=8\n",
        encoding="utf-8",
    )
    record_run(
        project,
        _record(project, run_id, status="FAIL", waveform=waveform),
    )
    ingest_assertion_log(
        project,
        run_id=run_id,
        log_path=log,
        created_at="2026-09-22T19:00:00+00:00",
    )
    return run_id


def test_root_cause_ranks_explicit_rtl_driver_evidence_first(tmp_path: Path):
    project = _project(tmp_path)
    run_id = _failing_assertion_run(project)

    result = rank_root_cause_candidates(project, run_id=run_id)

    assert result["analysis"] == "root_cause_candidates"
    assert result["summary"]["assertion_events"] == 1
    assert result["summary"]["rtl_driver_candidates"] >= 1
    assert "not by causal probability" in result["semantics"]

    top = result["candidates"][0]
    assert top["kind"] == "rtl_driver"
    assert top["evidence_score"] == 95
    assert "rtl/counter.sv" in top["subject"]
    assert top["evidence"][0]["signal"].endswith(".count")
    assert top["evidence"][0]["driver"]["kind"] == "procedural_assignment"


def test_root_cause_keeps_assertion_anchor_without_waveform(tmp_path: Path):
    project = _project(tmp_path)
    run_id = "run-no-wave"
    record = _record(project, run_id, status="FAIL", waveform=None)
    record_run(project, record)
    log = Path(record["log"])
    log.write_text("ZDDV_ASSERT state_guard FAIL state=bad\n", encoding="utf-8")
    ingest_assertion_log(
        project,
        run_id=run_id,
        log_path=log,
        created_at="2026-09-22T19:00:00+00:00",
    )

    result = rank_root_cause_candidates(project, run_id=run_id)

    assert result["summary"]["rtl_driver_candidates"] == 0
    assert result["candidates"][0]["kind"] == "assertion_anchor"
    assert result["candidates"][0]["subject"] == "state_guard"


def test_root_cause_uses_timeout_as_explicit_runtime_evidence(tmp_path: Path):
    project = _project(tmp_path)
    run_id = "run-timeout"
    record = _record(
        project,
        run_id,
        status="TIMEOUT",
        waveform=None,
        timeout_s=3.5,
    )
    record_run(project, record)
    Path(record["log"]).write_text("simulation timeout after 3.5 seconds\n", encoding="utf-8")

    result = rank_root_cause_candidates(project, run_id=run_id)

    assert result["candidates"][0]["kind"] == "runtime_timeout"
    assert result["candidates"][0]["evidence_score"] == 80
    assert result["candidates"][0]["evidence"][0]["timeout_s"] == 3.5


def test_write_root_cause_report_and_cli(tmp_path: Path, capsys):
    project = _project(tmp_path)
    run_id = _failing_assertion_run(project, "run-cli")

    result = write_root_cause_report(project, run_id=run_id)
    assert Path(result["path"]).is_file()

    rc = main(
        [
            "--project",
            str(project.root),
            "root-cause",
            "--run",
            run_id,
            "--show",
            "2",
        ]
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "ROOT CAUSE CANDIDATES:" in output
    assert "evidence richness" in output
    assert (project.root / ".zddv" / "debug" / "root-cause.json").is_file()
