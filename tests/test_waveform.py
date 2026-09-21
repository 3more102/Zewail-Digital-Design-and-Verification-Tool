from __future__ import annotations

from pathlib import Path

import pytest

from zddv.config import initialize_project
from zddv.storage import record_run
from zddv.waveform import index_vcd, resolve_waveform, write_waveform_index


VCD = """$date
  2026-09-21
$end
$version ZDDV test $end
$timescale 1 ns $end
$scope module tb $end
$var wire 1 ! clk $end
$var wire 4 " count [3:0] $end
$scope module dut $end
$var wire 1 # rst_n $end
$upscope $end
$upscope $end
$enddefinitions $end
#0
0!
b0000 "
0#
#5
1!
#10
0!
1#
b0001 "
"""


def test_index_vcd_builds_signal_activity_and_time_index(tmp_path: Path):
    waveform = tmp_path / "waveform.vcd"
    waveform.write_text(VCD, encoding="utf-8")

    index = index_vcd(waveform, checkpoint_interval=2)

    assert index["format"] == "vcd"
    assert index["metadata"]["timescale"] == "1 ns"
    assert index["summary"] == {
        "scopes": 2,
        "signals": 3,
        "identifier_codes": 3,
        "active_signals": 3,
        "inactive_signals": 0,
        "value_changes": 7,
        "unknown_identifier_codes": 0,
    }
    assert index["time_index"]["first_time"] == 0
    assert index["time_index"]["last_time"] == 10
    assert index["time_index"]["duration"] == 10
    assert index["time_index"]["checkpoints"] == [
        {"time": 0, "line": 14},
        {"time": 10, "line": 20},
    ]

    signals = {item["full_name"]: item for item in index["signals"]}
    assert signals["tb.clk"]["activity"]["changes"] == 3
    assert signals["tb.count"]["range"] == "[3:0]"
    assert signals["tb.count"]["activity"]["last_value"] == "b0001"
    assert signals["tb.dut.rst_n"]["activity"]["first_value"] == "0"


def test_alias_identifier_activity_is_preserved_for_each_signal(tmp_path: Path):
    waveform = tmp_path / "alias.vcd"
    waveform.write_text(
        """$timescale 1ns $end
$scope module top $end
$var wire 1 ! a $end
$var wire 1 ! a_alias $end
$upscope $end
$enddefinitions $end
#0
0!
#1
1!
""",
        encoding="utf-8",
    )

    index = index_vcd(waveform)

    assert index["summary"]["signals"] == 2
    assert index["summary"]["identifier_codes"] == 1
    assert [item["activity"]["changes"] for item in index["signals"]] == [2, 2]


def test_write_waveform_index_for_explicit_path(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    waveform = project.root / "trace.vcd"
    waveform.write_text(VCD, encoding="utf-8")

    result = write_waveform_index(project, "trace.vcd")

    output = Path(result["path"])
    assert output.name == "trace.vcd.index.json"
    assert output.exists()
    assert result["project"] == "demo"
    assert result["run_id"] is None
    assert result["summary"]["signals"] == 3


def test_resolve_waveform_uses_latest_recorded_run(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    run_dir = project.root / ".zddv" / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    waveform = run_dir / "waveform.vcd"
    waveform.write_text(VCD, encoding="utf-8")

    record_run(
        project,
        {
            "run_id": "run-1",
            "created_at": "2026-09-21T20:00:00+00:00",
            "project": project.name,
            "simulator": "verilator",
            "simulator_version": "Verilator test",
            "top": "tb_top",
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

    resolved, run_id = resolve_waveform(project)

    assert resolved == waveform.resolve()
    assert run_id == "run-1"

    result = write_waveform_index(project)
    assert Path(result["path"]) == run_dir / "waveform.index.json"
    assert result["run_id"] == "run-1"


def test_non_vcd_format_is_rejected(tmp_path: Path):
    waveform = tmp_path / "waveform.fst"
    waveform.write_bytes(b"not-an-fst")

    with pytest.raises(RuntimeError, match="currently supports VCD"):
        index_vcd(waveform)
