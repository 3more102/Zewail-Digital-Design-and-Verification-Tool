from __future__ import annotations

from pathlib import Path

from zddv.assertions import ingest_assertion_log
from zddv.cli import main
from zddv.config import initialize_project, save_project
from zddv.debug_probes import suggest_debug_probes, write_debug_probe_report
from zddv.storage import record_run


VCD = """$timescale 1ns $end
$scope module TOP $end
$scope module tb_top $end
$var wire 1 ! clk $end
$scope module dut $end
$var wire 4 # count [3:0] $end
$var wire 4 $ data [3:0] $end
$upscope $end
$upscope $end
$upscope $end
$enddefinitions $end
#0
0!
b0000 #
b0011 $
#5
1!
b0100 #
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
    input logic [3:0] data,
    output logic [3:0] count
);
    always_ff @(posedge clk)
        count <= data + 1'b1;
endmodule
""",
        encoding="utf-8",
    )
    (tb / "tb_top.sv").write_text(
        """module tb_top;
    logic clk;
    logic [3:0] data;
    logic [3:0] count;
    counter dut(
        .clk(clk),
        .data(data),
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


def _record(
    project,
    *,
    run_id: str,
    status: str,
    waveform: Path | None,
    test: str = "smoke",
    seed: int = 17,
    plusargs: list[str] | None = None,
    timeout_s: float = 10.0,
) -> dict:
    run_dir = project.root / ".zddv" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "run_id": run_id,
        "created_at": "2026-09-22T19:10:00+00:00",
        "project": project.name,
        "simulator": "verilator",
        "simulator_version": "Verilator test",
        "top": project.top,
        "test": test,
        "seed": seed,
        "status": status,
        "returncode": 124 if status == "TIMEOUT" else 1,
        "duration_ms": 25.0,
        "run_dir": str(run_dir),
        "log": str(run_dir / "simulation.log"),
        "waveform": str(waveform) if waveform else None,
        "coverage": None,
        "timeout_s": timeout_s,
        "command": ["zddv_sim"],
        "plusargs": plusargs or [],
    }
    record_run(project, record)
    return record


def _failed_assertion_run(project, run_id: str = "run-fail") -> str:
    run_dir = project.root / ".zddv" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    waveform = run_dir / "waveform.vcd"
    waveform.write_text(VCD, encoding="utf-8")
    log = run_dir / "simulation.log"
    log.write_text(
        "ZDDV_ASSERT count_guard FAIL count mismatch expected=3 actual=4\n",
        encoding="utf-8",
    )
    _record(project, run_id=run_id, status="FAIL", waveform=waveform)
    ingest_assertion_log(
        project,
        run_id=run_id,
        log_path=log,
        created_at="2026-09-22T19:10:00+00:00",
    )
    return run_id


def test_suggests_waveform_probe_for_driver_and_resolved_fanin(tmp_path: Path):
    project = _project(tmp_path)
    run_id = _failed_assertion_run(project)

    result = suggest_debug_probes(project, run_id=run_id)

    assert result["analysis"] == "debug_probe_suggestions"
    assert result["policy"]["automatic_execution"] is False
    first = result["suggestions"][0]
    assert first["priority"] == 1
    assert first["kind"] == "waveform_driver_cone"
    assert first["target"] == "TOP.tb_top.dut.count"
    assert first["signals"] == [
        "TOP.tb_top.dut.count",
        "TOP.tb_top.dut.data",
    ]
    assert first["execution_mode"] == "read_only_existing_artifact"
    assert first["evidence"]["driver"]["kind"] == "procedural_assignment"
    assert first["evidence"]["fanin"][0]["signal"] == "data"
    assert first["command"][-4:] == [
        "--run",
        run_id,
        "--max-changes",
        "500",
    ]


def test_missing_waveform_suggests_opt_in_reproduction(tmp_path: Path):
    project = _project(tmp_path)
    run_id = "run-no-wave"
    record = _record(
        project,
        run_id=run_id,
        status="FAIL",
        waveform=None,
        test="corner",
        seed=99,
        plusargs=["+MODE=2"],
        timeout_s=7.5,
    )
    log = Path(record["log"])
    log.write_text(
        "ZDDV_ASSERT state_guard FAIL state mismatch\n",
        encoding="utf-8",
    )
    ingest_assertion_log(
        project,
        run_id=run_id,
        log_path=log,
        created_at="2026-09-22T19:11:00+00:00",
    )

    result = suggest_debug_probes(project, run_id=run_id)

    rerun = next(
        item
        for item in result["suggestions"]
        if item["kind"] == "rerun_with_waveform_capture"
    )
    assert rerun["execution_mode"] == "manual_opt_in_new_run"
    assert rerun["command"][-10:] == [
        "run",
        "--test",
        "corner",
        "--seed",
        "99",
        "--plusarg",
        "+MODE=2",
        "--timeout",
        "7.5",
    ]
    assert result["summary"]["manual_opt_in_new_runs"] == 1


def test_timeout_with_waveform_suggests_index_review(tmp_path: Path):
    project = _project(tmp_path)
    run_id = "run-timeout"
    run_dir = project.root / ".zddv" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    waveform = run_dir / "waveform.vcd"
    waveform.write_text(VCD, encoding="utf-8")
    record = _record(
        project,
        run_id=run_id,
        status="TIMEOUT",
        waveform=waveform,
        timeout_s=3.5,
    )
    Path(record["log"]).write_text(
        "simulation timeout after 3.5 seconds\n",
        encoding="utf-8",
    )

    result = suggest_debug_probes(project, run_id=run_id)

    assert result["suggestions"][0]["kind"] == "waveform_index_review"
    assert result["suggestions"][0]["command"][-3:] == [
        "waveform-index",
        "--run",
        run_id,
    ]
    assert result["suggestions"][0]["execution_mode"] == "read_only_existing_artifact"


def test_debug_probes_report_and_cli(tmp_path: Path, capsys):
    project = _project(tmp_path)
    run_id = _failed_assertion_run(project, "run-cli")

    report = write_debug_probe_report(
        project,
        run_id=run_id,
        output=".zddv/debug/custom-probes.json",
    )
    assert Path(report["path"]).is_file()

    rc = main(
        [
            "--project",
            str(project.root),
            "debug-probes",
            "--run",
            run_id,
            "--show",
            "2",
        ]
    )
    assert rc == 0
    output = capsys.readouterr().out
    assert "DEBUG PROBES: run=run-cli" in output
    assert "waveform_driver_cone" in output
    assert "Suggestions are not executed automatically." in output
    assert (project.root / ".zddv" / "debug" / "probes.json").is_file()
