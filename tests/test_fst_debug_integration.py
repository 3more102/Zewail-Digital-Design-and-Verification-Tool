import json
import subprocess
from pathlib import Path

import pytest

from zddv.assertions import ingest_assertion_log
from zddv.cli import main
from zddv.config import initialize_project, save_project
from zddv.crossprobe import write_crossprobe_report
from zddv.debug import correlate_assertions
from zddv.storage import record_run


VCD = """$timescale 1ns $end
$scope module tb_top $end
$var wire 1 ! clk $end
$scope module dut $end
$var wire 4 # count [3:0] $end
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


def _install_fake_converter(monkeypatch, calls: list[list[str]]) -> None:
    monkeypatch.setattr(
        "zddv.fst_adapter.shutil.which",
        lambda requested: "/usr/bin/fst2vcd" if requested == "fst2vcd" else None,
    )

    def fake_run(command, **kwargs):
        calls.append(list(command))
        Path(command[command.index("-o") + 1]).write_text(VCD, encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("zddv.fst_adapter.subprocess.run", fake_run)


def _run_record(run_id: str, root: Path, waveform: Path) -> dict:
    run_dir = root / ".zddv" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return {
        "run_id": run_id,
        "created_at": "2026-09-23T07:40:00+00:00",
        "project": "demo",
        "simulator": "verilator",
        "simulator_version": "Verilator test",
        "top": "tb_top",
        "test": "smoke",
        "seed": 7,
        "status": "FAIL",
        "returncode": 1,
        "duration_ms": 12.0,
        "run_dir": str(run_dir),
        "log": str(run_dir / "simulation.log"),
        "waveform": str(waveform),
        "coverage": None,
        "timeout_s": 10.0,
        "command": ["zddv_sim"],
        "plusargs": [],
    }


def test_crossprobe_fst_stays_metadata_only_without_adapter(tmp_path: Path):
    project = _project(tmp_path)
    waveform = project.root / "trace.fst"
    waveform.write_bytes(b"FST-placeholder")

    with pytest.raises(RuntimeError, match="metadata-only"):
        write_crossprobe_report(
            project,
            "tb_top.dut.count",
            input_path="trace.fst",
        )


def test_crossprobe_cli_uses_explicit_fst_adapter_and_records_provenance(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    project = _project(tmp_path)
    waveform = project.root / "trace.fst"
    waveform.write_bytes(b"FST-placeholder")
    calls: list[list[str]] = []
    _install_fake_converter(monkeypatch, calls)

    rc = main(
        [
            "--project",
            str(project.root),
            "crossprobe",
            "tb_top.dut.count",
            "--input",
            "trace.fst",
            "--fst2vcd",
        ]
    )

    assert rc == 0
    assert "CROSSPROBE MATCHED" in capsys.readouterr().out
    assert len(calls) == 1
    report = json.loads(
        (project.root / ".zddv" / "debug" / "crossprobe.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["waveform"]["format"] == "fst"
    assert report["waveform"]["parse_status"] == "indexed-via-fst2vcd"
    assert report["waveform"]["artifact"]["path"] == str(waveform.resolve())
    assert report["waveform"]["adapter"]["adapter"] == "fst2vcd"


def test_assertion_correlation_fst_stays_metadata_only_without_adapter(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    run_id = "run-fst-default"
    run_dir = project.root / ".zddv" / "runs" / run_id
    run_dir.mkdir(parents=True)
    waveform = run_dir / "waveform.fst"
    waveform.write_bytes(b"FST-placeholder")
    record = _run_record(run_id, project.root, waveform)
    log = Path(record["log"])
    log.write_text("ZDDV_ASSERT counter_sequence FAIL count=8\n", encoding="utf-8")
    record_run(project, record)
    ingest_assertion_log(
        project,
        run_id=run_id,
        log_path=log,
        created_at="2026-09-23T07:40:00+00:00",
    )

    report = correlate_assertions(project, run_id=run_id)

    assert report["summary"]["with_waveform"] == 1
    assert report["summary"]["with_indexed_waveform"] == 0
    assert report["summary"]["with_signal_hints"] == 0
    assert report["events"][0]["waveform"]["parse_status"] == "metadata-only"
    assert "adapter" not in report["events"][0]["waveform"]


def test_assertion_waveform_cli_uses_one_explicit_conversion_per_run(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    project = initialize_project(tmp_path / "demo")
    run_id = "run-fst-explicit"
    run_dir = project.root / ".zddv" / "runs" / run_id
    run_dir.mkdir(parents=True)
    waveform = run_dir / "waveform.fst"
    waveform.write_bytes(b"FST-placeholder")
    record = _run_record(run_id, project.root, waveform)
    log = Path(record["log"])
    log.write_text(
        "ZDDV_ASSERT first_check FAIL count=8\n"
        "ZDDV_ASSERT second_check FAIL count=9\n",
        encoding="utf-8",
    )
    record_run(project, record)
    ingest_assertion_log(
        project,
        run_id=run_id,
        log_path=log,
        created_at="2026-09-23T07:40:00+00:00",
    )
    calls: list[list[str]] = []
    _install_fake_converter(monkeypatch, calls)

    rc = main(
        [
            "--project",
            str(project.root),
            "assertion-waveform",
            "--run",
            run_id,
            "--fst2vcd",
        ]
    )

    assert rc == 0
    assert len(calls) == 1
    output = capsys.readouterr().out
    assert "ASSERTION/WAVEFORM: 2 event(s)" in output
    report = json.loads(
        (project.root / ".zddv" / "debug" / "assertion-waveform.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["summary"]["with_indexed_waveform"] == 2
    assert report["summary"]["with_signal_hints"] == 2
    for event in report["events"]:
        assert event["waveform"]["format"] == "fst"
        assert event["waveform"]["parse_status"] == "indexed-via-fst2vcd"
        assert event["waveform"]["adapter"]["adapter"] == "fst2vcd"
        assert event["waveform"]["artifact"] == str(waveform.resolve())
        assert event["waveform"]["signal_hints"][0]["path"] == "tb_top.dut.count"
