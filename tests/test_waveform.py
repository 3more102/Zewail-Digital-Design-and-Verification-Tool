from pathlib import Path

import pytest

from zddv.config import initialize_project
from zddv.storage import record_run
from zddv.waveform import index_run_waveform, parse_vcd_index


VCD = """$date
    today
$end
$version ZDDV test $end
$timescale 1 ns $end
$scope module tb $end
$var wire 1 ! clk $end
$scope module dut $end
$var wire 4 \" count [3:0] $end
$upscope $end
$upscope $end
$enddefinitions $end
$dumpvars
0!
b0000 "
$end
#5
1!
#10
0!
b0001 "
"""


def test_parse_vcd_signal_and_activity_index(tmp_path: Path):
    path = tmp_path / "waveform.vcd"
    path.write_text(VCD, encoding="utf-8")

    result = parse_vcd_index(path)

    assert result["schema_version"] == 1
    assert result["format"] == "vcd"
    assert result["timescale"] == "1 ns"
    assert result["start_time"] == 5
    assert result["end_time"] == 10
    assert result["duration_ticks"] == 5
    assert result["summary"] == {
        "signals": 2,
        "scopes": 2,
        "value_changes": 5,
    }

    clk = next(item for item in result["signals"] if item["full_name"] == "tb.clk")
    assert clk["width"] == 1
    assert clk["changes"] == 3
    assert clk["first_activity"] == 0
    assert clk["last_activity"] == 10

    count = next(
        item for item in result["signals"] if item["full_name"] == "tb.dut.count"
    )
    assert count["width"] == 4
    assert count["range"] == "[3:0]"
    assert count["changes"] == 2
    assert count["first_activity"] == 0
    assert count["last_activity"] == 10


def _record(project, *, run_id: str, waveform: Path, created_at: str):
    run_dir = project.root / ".zddv" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    record_run(
        project,
        {
            "run_id": run_id,
            "created_at": created_at,
            "project": project.name,
            "simulator": "verilator",
            "simulator_version": "Verilator test",
            "top": "tb",
            "test": "smoke",
            "seed": 1,
            "status": "PASS",
            "returncode": 0,
            "duration_ms": 1.0,
            "run_dir": str(run_dir),
            "log": str(run_dir / "simulation.log"),
            "waveform": str(waveform),
            "coverage": None,
            "timeout_s": None,
            "command": ["sim"],
            "plusargs": [],
        },
    )
    return run_dir


def test_index_latest_run_waveform_and_write_artifact(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    old_wave = tmp_path / "old.vcd"
    old_wave.write_text(VCD, encoding="utf-8")
    new_wave = tmp_path / "new.vcd"
    new_wave.write_text(VCD, encoding="utf-8")

    _record(
        project,
        run_id="run-old",
        waveform=old_wave,
        created_at="2026-09-21T20:00:00+00:00",
    )
    new_run_dir = _record(
        project,
        run_id="run-new",
        waveform=new_wave,
        created_at="2026-09-21T20:01:00+00:00",
    )

    result = index_run_waveform(project)

    assert result["run_id"] == "run-new"
    assert Path(result["index_path"]) == new_run_dir / "waveform.index.json"
    assert Path(result["index_path"]).exists()


def test_index_specific_run(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    wave = tmp_path / "wave.vcd"
    wave.write_text(VCD, encoding="utf-8")
    _record(
        project,
        run_id="chosen",
        waveform=wave,
        created_at="2026-09-21T20:00:00+00:00",
    )

    result = index_run_waveform(project, run_id="chosen")

    assert result["run_id"] == "chosen"


def test_fst_is_rejected_until_adapter_is_implemented(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    fst = tmp_path / "waveform.fst"
    fst.write_bytes(b"fst")
    _record(
        project,
        run_id="fst-run",
        waveform=fst,
        created_at="2026-09-21T20:00:00+00:00",
    )

    with pytest.raises(RuntimeError, match="VCD files only"):
        index_run_waveform(project, run_id="fst-run")
