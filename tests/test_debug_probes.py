from pathlib import Path

from zddv.assertions import ingest_assertion_log
from zddv.cli import main
from zddv.config import initialize_project, save_project
from zddv.debug_probes import suggest_debug_probes, write_debug_probe_suggestions
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
b0001 #
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
    counter dut(.clk(clk), .count(count));
endmodule
""",
        encoding="utf-8",
    )
    project.rtl = ["rtl/*.sv"]
    project.tb = ["tb/*.sv"]
    project.top = "tb_top"
    save_project(project)
    return project


def _record(project, run_id: str, waveform: Path | None):
    run_dir = project.root / ".zddv" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return {
        "run_id": run_id,
        "created_at": "2026-09-22T19:10:00+00:00",
        "project": project.name,
        "simulator": "verilator",
        "simulator_version": "Verilator test",
        "top": project.top,
        "test": "smoke",
        "seed": 21,
        "status": "FAIL",
        "returncode": 1,
        "duration_ms": 30.0,
        "run_dir": str(run_dir),
        "log": str(run_dir / "simulation.log"),
        "waveform": str(waveform) if waveform else None,
        "coverage": None,
        "timeout_s": 10.0,
        "command": ["zddv_sim"],
        "plusargs": [],
    }


def _failure(project, run_id: str, *, waveform_enabled: bool = True):
    run_dir = project.root / ".zddv" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    waveform = run_dir / "waveform.vcd" if waveform_enabled else None
    if waveform is not None:
        waveform.write_text(VCD, encoding="utf-8")
    log = run_dir / "simulation.log"
    log.write_text(
        "ZDDV_ASSERT count_guard FAIL count mismatch expected=1 actual=2\n",
        encoding="utf-8",
    )
    record_run(project, _record(project, run_id, waveform))
    ingest_assertion_log(
        project,
        run_id=run_id,
        log_path=log,
        created_at="2026-09-22T19:10:00+00:00",
    )


def test_suggests_only_explicit_waveform_signal_probe(tmp_path: Path):
    project = _project(tmp_path)
    _failure(project, "run-probe")

    result = suggest_debug_probes(project, run_id="run-probe")

    assert result["summary"]["suggestions"] == 1
    assert result["summary"]["unique_signals"] == 1
    assert result["blockers"] == []
    suggestion = result["suggestions"][0]
    assert suggestion["kind"] == "waveform_probe"
    assert suggestion["signal"].endswith(".count")
    assert suggestion["source_candidate_kind"] == "rtl_driver"
    assert suggestion["argv"] == [
        "waveform-probe",
        suggestion["signal"],
        "--run",
        "run-probe",
    ]
    assert "does not invent" in result["semantics"]


def test_keeps_exact_signal_probe_when_rtl_crossprobe_is_unavailable(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    _failure(project, "run-partial")

    def fail_crossprobe(*args, **kwargs):
        raise RuntimeError("source cross-probe unavailable")

    monkeypatch.setattr("zddv.root_cause.build_crossprobe", fail_crossprobe)

    result = suggest_debug_probes(project, run_id="run-partial")

    assert result["summary"]["suggestions"] == 1
    assert result["blockers"] == []
    suggestion = result["suggestions"][0]
    assert suggestion["signal"].endswith(".count")
    assert suggestion["source_candidate_kind"] == "assertion_anchor"
    assert suggestion["evidence"]["signal_hint_match"] == "exact-name"


def test_withholds_probe_when_run_has_no_waveform(tmp_path: Path):
    project = _project(tmp_path)
    _failure(project, "run-no-wave", waveform_enabled=False)

    result = suggest_debug_probes(project, run_id="run-no-wave")

    assert result["suggestions"] == []
    codes = {item["code"] for item in result["blockers"]}
    assert "NO_WAVEFORM_ARTIFACT" in codes
    assert "NO_EXPLICIT_SIGNAL_HINTS" in codes


def test_debug_probe_report_and_cli(tmp_path: Path, capsys):
    project = _project(tmp_path)
    _failure(project, "run-cli")

    report = write_debug_probe_suggestions(project, run_id="run-cli")
    assert Path(report["path"]).is_file()

    rc = main(
        [
            "--project",
            str(project.root),
            "debug-probes",
            "--run",
            "run-cli",
        ]
    )
    assert rc == 0
    output = capsys.readouterr().out
    assert "DEBUG PROBES:" in output
    assert "waveform-probe" in output
    assert (project.root / ".zddv" / "debug" / "probe-suggestions.json").is_file()
