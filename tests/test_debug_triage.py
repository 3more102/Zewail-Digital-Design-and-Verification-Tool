from __future__ import annotations

from pathlib import Path

import pytest

from zddv.assertions import ingest_assertion_log
from zddv.cli import main
from zddv.config import initialize_project, save_project
from zddv.debug_triage import build_debug_triage, write_debug_triage_report
from zddv.formal.base import FormalCheckRequest, FormalCheckResult, FormalPropertyResult
from zddv.formal.results import persist_formal_result
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
#5
1!
b1000 #
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


def _record(
    project,
    *,
    run_id: str,
    log: Path,
    waveform: Path | None,
    seed: int,
    status: str = "FAIL",
) -> None:
    run_dir = project.root / ".zddv" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    record_run(
        project,
        {
            "run_id": run_id,
            "created_at": f"2026-09-22T18:{seed:02d}:00+00:00",
            "project": project.name,
            "simulator": "verilator",
            "simulator_version": "Verilator test",
            "top": project.top,
            "test": "counter_test",
            "seed": seed,
            "status": status,
            "returncode": 1 if status == "FAIL" else 0,
            "duration_ms": 12.0,
            "run_dir": str(run_dir),
            "log": str(log),
            "waveform": str(waveform) if waveform is not None else None,
            "coverage": None,
            "timeout_s": 10.0,
            "command": ["sim"],
            "plusargs": [],
        },
    )


def _prepare_failed_run(project) -> str:
    run_id = "run-fail"
    run_dir = project.root / ".zddv" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    waveform = run_dir / "waveform.vcd"
    waveform.write_text(VCD, encoding="utf-8")
    log = run_dir / "simulation.log"
    log.write_text(
        "ERROR mismatch expected 7 got 8\n"
        "ZDDV_ASSERT counter_sequence FAIL count mismatch expected=7 got=8\n",
        encoding="utf-8",
    )
    _record(
        project,
        run_id=run_id,
        log=log,
        waveform=waveform,
        seed=7,
    )
    ingest_assertion_log(
        project,
        run_id=run_id,
        log_path=log,
        created_at="2026-09-22T18:07:00+00:00",
    )

    other_id = "run-fail-2"
    other_dir = project.root / ".zddv" / "runs" / other_id
    other_dir.mkdir(parents=True, exist_ok=True)
    other_log = other_dir / "simulation.log"
    other_log.write_text(
        "ERROR mismatch expected 17 got 18\n"
        "ZDDV_ASSERT counter_sequence FAIL count mismatch expected=17 got=18\n",
        encoding="utf-8",
    )
    _record(
        project,
        run_id=other_id,
        log=other_log,
        waveform=None,
        seed=8,
    )

    formal_dir = project.root / ".zddv" / "formal" / "test"
    formal_dir.mkdir(parents=True, exist_ok=True)
    formal_log = formal_dir / "formal.log"
    formal_log.write_text("formal evidence\n", encoding="utf-8")
    persist_formal_result(
        project,
        FormalCheckResult(
            backend="sby",
            engine="smtbmc",
            request=FormalCheckRequest(mode="bmc", depth=12),
            command=("sby",),
            returncode=2,
            status="FAIL",
            run_dir=formal_dir,
            log_path=formal_log,
            properties=(
                FormalPropertyResult(
                    name="counter_sequence",
                    kind="assert",
                    status="FAIL",
                    depth=7,
                ),
            ),
        ),
        input_path=formal_log,
        output=formal_dir / "result.json",
    )
    return run_id


def test_debug_triage_ranks_only_retained_evidence(tmp_path: Path):
    project = _project(tmp_path)
    run_id = _prepare_failed_run(project)

    report = build_debug_triage(project, run_id=run_id)

    assert report["analysis"] == "debug_triage"
    assert report["summary"]["failed_assertion_events"] == 1
    assert report["summary"]["signal_candidates"] == 1
    assert report["summary"]["source_matched_candidates"] == 1
    assert report["summary"]["formal_exact_name_matches"] == 1
    assert report["summary"]["failure_group_occurrences"] == 2
    assert report["ranking_policy"]["formal_history_affects_rank"] is False

    candidate = report["candidates"][0]
    assert candidate["priority_rank"] == 1
    assert candidate["signal"]["path"] == "TOP.tb_top.dut.count"
    assert candidate["assertion_names"] == ["counter_sequence"]
    assert candidate["crossprobe"]["status"] == "MATCHED"
    assert candidate["crossprobe"]["source"]["file"] == "rtl/counter.sv"
    assert candidate["crossprobe"]["source"]["declaration"]["line"] == 3
    assert candidate["rank_basis"]["failed_assertion_events"] == 1
    assert candidate["rank_basis"]["rtl_declaration_match"] is True
    assert candidate["rank_basis"]["structural_driver_count"] == 1

    event = report["assertions"]["events"][0]
    assert len(event["formal_matches"]) == 1
    assert event["formal_matches"][0]["match"] == "exact-property-name"
    assert event["formal_matches"][0]["property_status"] == "FAIL"


def test_debug_triage_cli_writes_report(tmp_path: Path, capsys):
    project = _project(tmp_path)
    run_id = _prepare_failed_run(project)

    rc = main(
        [
            "--project",
            str(project.root),
            "debug-triage",
            "--run",
            run_id,
            "--show",
            "5",
        ]
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "DEBUG TRIAGE: run=run-fail status=FAIL candidates=1" in output
    assert "#1 TOP.tb_top.dut.count -> rtl/counter.sv:3" in output
    assert "not causal probability" in output
    assert (project.root / ".zddv" / "debug" / "triage.json").is_file()


def test_debug_triage_rejects_passing_run(tmp_path: Path):
    project = _project(tmp_path)
    run_dir = project.root / ".zddv" / "runs" / "run-pass"
    run_dir.mkdir(parents=True)
    log = run_dir / "simulation.log"
    log.write_text("PASS\n", encoding="utf-8")
    _record(
        project,
        run_id="run-pass",
        log=log,
        waveform=None,
        seed=1,
        status="PASS",
    )

    with pytest.raises(RuntimeError, match="requires a FAIL or TIMEOUT run"):
        build_debug_triage(project, run_id="run-pass")


def test_write_debug_triage_report_custom_path(tmp_path: Path):
    project = _project(tmp_path)
    run_id = _prepare_failed_run(project)

    result = write_debug_triage_report(
        project,
        run_id=run_id,
        output=".zddv/debug/custom-triage.json",
    )

    assert Path(result["report_path"]).is_file()
    assert result["summary"]["signal_candidates"] == 1
