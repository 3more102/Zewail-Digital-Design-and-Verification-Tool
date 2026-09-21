from pathlib import Path

import pytest

from zddv.config import ProjectConfig, initialize_project
from zddv.storage import record_run
from zddv.waveform import parse_vcd, write_waveform_index


VCD = """$date
  2026-09-21
$end
$version ZDDV test $end
$timescale 1 ns $end
$scope module tb_top $end
$var wire 1 ! clk $end
$var wire 1 " rst_n $end
$scope module dut $end
$var wire 4 # count [3:0] $end
$var wire 1 ! clk_alias $end
$upscope $end
$upscope $end
$enddefinitions $end
#0
0!
0"
b0000 #
#5
1!
#10
0!
1"
b0001 #
"""


def _record(run_id: str, waveform: Path) -> dict:
    return {
        "run_id": run_id,
        "created_at": "2026-09-21T21:00:00+00:00",
        "project": "demo",
        "simulator": "verilator",
        "simulator_version": "Verilator test",
        "top": "tb_top",
        "test": "smoke",
        "seed": 1,
        "status": "PASS",
        "returncode": 0,
        "duration_ms": 12.5,
        "run_dir": str(waveform.parent),
        "log": str(waveform.parent / "simulation.log"),
        "waveform": str(waveform),
        "coverage": None,
        "timeout_s": 10.0,
        "command": ["zddv_sim"],
        "plusargs": [],
    }


def test_parse_vcd_indexes_scopes_signals_and_activity(tmp_path: Path):
    waveform = tmp_path / "waveform.vcd"
    waveform.write_text(VCD, encoding="utf-8")

    result = parse_vcd(waveform)

    assert result["format"] == "vcd"
    assert result["timescale"] == "1ns"
    assert result["start_time"] == 0
    assert result["end_time"] == 10
    assert result["timestamp_count"] == 3
    assert result["value_change_count"] == 7
    assert result["summary"] == {
        "scopes": 2,
        "signals": 4,
        "identifier_codes": 3,
        "aliased_identifiers": 1,
        "active_signals": 4,
        "inactive_signals": 0,
    }

    by_path = {signal["path"]: signal for signal in result["signals"]}
    assert by_path["tb_top.clk"]["activity_count"] == 3
    assert by_path["tb_top.dut.clk_alias"]["activity_count"] == 3
    assert by_path["tb_top.dut.count"]["width"] == 4
    assert by_path["tb_top.dut.count"]["reference"] == "count [3:0]"


def test_write_waveform_index_selects_latest_run(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    run_dir = project.root / ".zddv" / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    waveform = run_dir / "waveform.vcd"
    waveform.write_text(VCD, encoding="utf-8")
    (run_dir / "simulation.log").write_text("PASS\n", encoding="utf-8")

    record_run(project, _record("run-1", waveform))

    result = write_waveform_index(project)

    assert result["run_id"] == "run-1"
    index_path = Path(result["index_path"])
    assert index_path.exists()
    assert index_path.name == "run-1.json"
    assert result["summary"]["signals"] == 4


def test_manual_waveform_path_and_invalid_format(tmp_path: Path):
    project = ProjectConfig(root=tmp_path, name="demo")
    waveform = tmp_path / "manual.vcd"
    waveform.write_text(VCD, encoding="utf-8")

    result = write_waveform_index(project, waveform_path="manual.vcd")
    assert result["run_id"] is None
    assert Path(result["index_path"]).exists()

    fst = tmp_path / "waveform.fst"
    fst.write_bytes(b"not-fst")
    with pytest.raises(ValueError, match="supports VCD"):
        parse_vcd(fst)


def test_invalid_vcd_requires_enddefinitions(tmp_path: Path):
    waveform = tmp_path / "bad.vcd"
    waveform.write_text("$scope module top $end\n", encoding="utf-8")

    with pytest.raises(ValueError, match="enddefinitions"):
        parse_vcd(waveform)
