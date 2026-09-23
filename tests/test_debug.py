from pathlib import Path
import subprocess

from zddv.assertions import ingest_assertion_log
from zddv.cli import main
from zddv.config import initialize_project
from zddv.debug import correlate_assertions, write_assertion_waveform_report
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


def _run_record(run_id: str, root: Path, waveform: Path | None) -> dict:
    run_dir = root / ".zddv" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return {
        "run_id": run_id,
        "created_at": "2026-09-21T21:00:00+00:00",
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
        "waveform": str(waveform) if waveform else None,
        "coverage": None,
        "timeout_s": 10.0,
        "command": ["zddv_sim"],
        "plusargs": [],
    }


def test_correlates_assertion_to_run_waveform_and_signal_hint(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    run_id = "run-fail"
    run_dir = project.root / ".zddv" / "runs" / run_id
    run_dir.mkdir(parents=True)
    waveform = run_dir / "waveform.vcd"
    waveform.write_text(VCD, encoding="utf-8")
    log = run_dir / "simulation.log"
    log.write_text(
        "ZDDV_ASSERT counter_sequence FAIL expected=7 final_count=8\n",
        encoding="utf-8",
    )

    record_run(project, _run_record(run_id, project.root, waveform))
    ingest_assertion_log(
        project,
        run_id=run_id,
        log_path=log,
        created_at="2026-09-21T21:00:00+00:00",
    )

    report = correlate_assertions(project, run_id=run_id, status="FAIL")

    assert report["summary"] == {
        "events": 1,
        "with_run_record": 1,
        "with_waveform": 1,
        "with_indexed_waveform": 1,
        "with_signal_hints": 1,
    }
    event = report["events"][0]
    assert event["run"]["test_name"] == "smoke"
    assert event["waveform"]["format"] == "vcd"
    assert event["waveform"]["parse_status"] == "indexed"
    assert event["waveform"]["signal_hints"] == [
        {
            "path": "tb_top.dut.count",
            "name": "count",
            "width": 4,
            "range": "[3:0]",
            "match": "exact-name",
        }
    ]


def test_correlation_keeps_event_when_waveform_is_missing(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    run_id = "run-no-wave"
    record = _run_record(run_id, project.root, None)
    record_run(project, record)
    log = Path(record["log"])
    log.write_text(
        "ZDDV_ASSERT no_wave FAIL state=bad\n",
        encoding="utf-8",
    )
    ingest_assertion_log(
        project,
        run_id=run_id,
        log_path=log,
        created_at="2026-09-21T21:00:00+00:00",
    )

    report = correlate_assertions(project, run_id=run_id)

    assert report["summary"]["events"] == 1
    assert report["summary"]["with_waveform"] == 0
    assert report["events"][0]["waveform"] is None


def test_write_assertion_waveform_report(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    run_id = "run-pass"
    run_dir = project.root / ".zddv" / "runs" / run_id
    run_dir.mkdir(parents=True)
    waveform = run_dir / "waveform.vcd"
    waveform.write_text(VCD, encoding="utf-8")
    log = run_dir / "simulation.log"
    log.write_text(
        "ZDDV_ASSERT counter_sequence PASS final_count=8\n",
        encoding="utf-8",
    )

    record = _run_record(run_id, project.root, waveform)
    record["status"] = "PASS"
    record["returncode"] = 0
    record_run(project, record)
    ingest_assertion_log(
        project,
        run_id=run_id,
        log_path=log,
        created_at="2026-09-21T21:00:00+00:00",
    )

    result = write_assertion_waveform_report(
        project,
        run_id=run_id,
        output=".zddv/debug/custom.json",
    )

    assert Path(result["path"]).is_file()
    assert result["summary"]["with_indexed_waveform"] == 1


def test_assertion_waveform_cli(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    run_id = "run-cli"
    run_dir = project.root / ".zddv" / "runs" / run_id
    run_dir.mkdir(parents=True)
    waveform = run_dir / "waveform.vcd"
    waveform.write_text(VCD, encoding="utf-8")
    log = run_dir / "simulation.log"
    log.write_text(
        "ZDDV_ASSERT counter_sequence FAIL final_count=8\n",
        encoding="utf-8",
    )
    record_run(project, _run_record(run_id, project.root, waveform))
    ingest_assertion_log(
        project,
        run_id=run_id,
        log_path=log,
        created_at="2026-09-21T21:00:00+00:00",
    )

    rc = main([
        "--project",
        str(project.root),
        "assertion-waveform",
        "--run",
        run_id,
        "--status",
        "FAIL",
    ])

    assert rc == 0
    output = capsys.readouterr().out
    assert "ASSERTION/WAVEFORM: 1 event(s)" in output
    assert "tb_top.dut.count" in output
    assert (project.root / ".zddv" / "debug" / "assertion-waveform.json").is_file()


def test_assertion_waveform_cli_supports_explicit_fst2vcd(
    tmp_path: Path,
    capsys,
    monkeypatch,
):
    project = initialize_project(tmp_path / "demo")
    run_id = "run-fst"
    run_dir = project.root / ".zddv" / "runs" / run_id
    run_dir.mkdir(parents=True)
    waveform = run_dir / "waveform.fst"
    waveform.write_bytes(b"FST-placeholder")
    log = run_dir / "simulation.log"
    log.write_text(
        "ZDDV_ASSERT counter_sequence FAIL final_count=8\n",
        encoding="utf-8",
    )
    record_run(project, _run_record(run_id, project.root, waveform))
    ingest_assertion_log(
        project,
        run_id=run_id,
        log_path=log,
        created_at="2026-09-21T21:00:00+00:00",
    )

    monkeypatch.setattr(
        "zddv.fst_adapter.shutil.which",
        lambda requested: "/usr/bin/fst2vcd" if requested == "fst2vcd" else None,
    )

    def fake_run(command, **kwargs):
        Path(command[command.index("-o") + 1]).write_text(VCD, encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("zddv.fst_adapter.subprocess.run", fake_run)

    rc = main([
        "--project",
        str(project.root),
        "assertion-waveform",
        "--run",
        run_id,
        "--status",
        "FAIL",
        "--fst2vcd",
    ])

    assert rc == 0
    output = capsys.readouterr().out
    assert "1 fully indexed" in output
    assert "tb_top.dut.count" in output
    report = write_assertion_waveform_report(
        project,
        run_id=run_id,
        status="FAIL",
        fst_converter="fst2vcd",
    )
    event = report["events"][0]
    assert event["waveform"]["format"] == "fst"
    assert event["waveform"]["parse_status"] == "indexed-via-fst2vcd"
    assert event["waveform"]["adapter"]["adapter"] == "fst2vcd"
    assert event["waveform"]["signal_hints"][0]["path"] == "tb_top.dut.count"


def test_assertion_waveform_fst_stays_metadata_only_without_explicit_adapter(
    tmp_path: Path,
):
    project = initialize_project(tmp_path / "demo")
    run_id = "run-fst-no-adapter"
    run_dir = project.root / ".zddv" / "runs" / run_id
    run_dir.mkdir(parents=True)
    waveform = run_dir / "waveform.fst"
    waveform.write_bytes(b"FST-placeholder")
    log = run_dir / "simulation.log"
    log.write_text(
        "ZDDV_ASSERT counter_sequence FAIL final_count=8\n",
        encoding="utf-8",
    )
    record_run(project, _run_record(run_id, project.root, waveform))
    ingest_assertion_log(
        project,
        run_id=run_id,
        log_path=log,
        created_at="2026-09-21T21:00:00+00:00",
    )

    report = correlate_assertions(project, run_id=run_id, status="FAIL")

    assert report["summary"]["with_waveform"] == 1
    assert report["summary"]["with_indexed_waveform"] == 0
    assert report["summary"]["with_signal_hints"] == 0
    event = report["events"][0]
    assert event["waveform"]["format"] == "fst"
    assert event["waveform"]["parse_status"] == "metadata-only"
    assert "adapter" not in event["waveform"]


def test_assertion_waveform_explicit_fst_converts_once_per_run(
    tmp_path: Path,
    monkeypatch,
):
    project = initialize_project(tmp_path / "demo")
    run_id = "run-fst-cache"
    run_dir = project.root / ".zddv" / "runs" / run_id
    run_dir.mkdir(parents=True)
    waveform = run_dir / "waveform.fst"
    waveform.write_bytes(b"FST-placeholder")
    log = run_dir / "simulation.log"
    log.write_text(
        "ZDDV_ASSERT first_check FAIL count=8\n"
        "ZDDV_ASSERT second_check FAIL count=9\n",
        encoding="utf-8",
    )
    record_run(project, _run_record(run_id, project.root, waveform))
    ingest_assertion_log(
        project,
        run_id=run_id,
        log_path=log,
        created_at="2026-09-21T21:00:00+00:00",
    )

    monkeypatch.setattr(
        "zddv.fst_adapter.shutil.which",
        lambda requested: "/usr/bin/fst2vcd" if requested == "fst2vcd" else None,
    )
    calls = []

    def fake_run(command, **kwargs):
        calls.append(list(command))
        Path(command[command.index("-o") + 1]).write_text(VCD, encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("zddv.fst_adapter.subprocess.run", fake_run)

    report = correlate_assertions(
        project,
        run_id=run_id,
        status="FAIL",
        fst_converter="fst2vcd",
    )

    assert len(calls) == 1
    assert report["summary"]["with_indexed_waveform"] == 2
    assert report["summary"]["with_signal_hints"] == 2
    for event in report["events"]:
        assert event["waveform"]["parse_status"] == "indexed-via-fst2vcd"
        assert event["waveform"]["adapter"]["adapter"] == "fst2vcd"
