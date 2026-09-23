from __future__ import annotations

from pathlib import Path

import pytest

from zddv.config import initialize_project
from zddv.desktop_waveform import (
    build_desktop_waveform_snapshot,
    probe_desktop_waveform_signal,
)
from zddv.storage import record_run


def _run_record(
    run_id: str,
    *,
    seed: int,
    waveform: str | None,
) -> dict:
    return {
        "run_id": run_id,
        "created_at": f"2026-09-23T07:{seed:02d}:00+00:00",
        "project": "demo",
        "simulator": "verilator",
        "simulator_version": "Verilator 5.x",
        "top": "tb_top",
        "test": "smoke",
        "seed": seed,
        "status": "PASS",
        "returncode": 0,
        "duration_ms": 12.5,
        "run_dir": f".zddv/runs/{run_id}",
        "log": f".zddv/runs/{run_id}/simulation.log",
        "waveform": waveform,
        "coverage": None,
        "timeout_s": 30.0,
        "command": ["sim"],
        "plusargs": [],
    }


def test_desktop_waveform_navigation_and_probe_are_in_memory(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    vcd = project.root / "trace.vcd"
    vcd.write_text(
        """$date today $end
$version zddv-test $end
$timescale 1ns $end
$scope module tb $end
$var wire 1 ! clk $end
$var wire 8 " data [7:0] $end
$upscope $end
$enddefinitions $end
#0
0!
b00000000 "
#5
1!
b00000001 "
#10
0!
b00000010 "
""",
        encoding="utf-8",
    )
    record_run(
        project,
        _run_record("run-wave", seed=3, waveform=str(vcd)),
    )

    navigation = build_desktop_waveform_snapshot(project)

    assert navigation["run_id"] == "run-wave"
    assert navigation["format"] == "vcd"
    assert navigation["parse_status"] == "indexed"
    assert navigation["timescale"] == "1ns"
    assert navigation["summary"]["signals"] == 2
    assert [signal["path"] for signal in navigation["signals"]] == [
        "tb.clk",
        "tb.data",
    ]

    probe = probe_desktop_waveform_signal(
        project,
        "tb.data",
        run_id="run-wave",
        start_time=0,
        end_time=10,
        max_changes=10,
    )

    assert probe["run_id"] == "run-wave"
    assert probe["summary"]["signals"] == 1
    assert probe["signals"][0]["changes"] == [
        {"time": 0, "value": "00000000"},
        {"time": 5, "value": "00000001"},
        {"time": 10, "value": "00000010"},
    ]
    assert not (project.root / ".zddv" / "waveforms").exists()


def test_desktop_waveform_navigation_reports_missing_evidence(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    record_run(
        project,
        _run_record("run-no-wave", seed=4, waveform=None),
    )

    with pytest.raises(RuntimeError, match="No run with an existing waveform artifact"):
        build_desktop_waveform_snapshot(project)
