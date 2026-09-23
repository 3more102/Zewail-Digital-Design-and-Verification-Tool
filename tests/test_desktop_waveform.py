from __future__ import annotations

from pathlib import Path

import pytest

from zddv.config import initialize_project, save_project
from zddv.desktop_waveform import (
    build_desktop_crossprobe_evidence_rows,
    build_desktop_waveform_snapshot,
    crossprobe_desktop_waveform_signal,
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


def test_desktop_waveform_crossprobe_is_read_only(tmp_path: Path):
    project = initialize_project(tmp_path / "crossprobe")
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

    vcd = project.root / "crossprobe.vcd"
    vcd.write_text(
        """$timescale 1ns $end
$scope module TOP $end
$scope module tb_top $end
$scope module dut $end
$var wire 4 ! count [3:0] $end
$upscope $end
$upscope $end
$upscope $end
$enddefinitions $end
#0
b0000 !
#5
b0001 !
""",
        encoding="utf-8",
    )
    record_run(
        project,
        _run_record("run-crossprobe", seed=5, waveform=str(vcd)),
    )

    result = crossprobe_desktop_waveform_signal(
        project,
        "TOP.tb_top.dut.count",
        run_id="run-crossprobe",
    )

    assert result["status"] == "MATCHED"
    assert result["hierarchy_resolution"] == "source_structural"
    assert result["hierarchy"]["design_path"] == "tb_top.dut"
    assert result["source"]["file"] == "rtl/counter.sv"
    assert result["source"]["declaration"]["line"] == 3
    assert result["connectivity"]["analysis_level"] == "source_structural"
    assert len(result["connectivity"]["drivers"]) == 1
    assert result["elaborated_evidence"]["status"] == "NOT_PRESENT"
    assert not (project.root / ".zddv" / "design" / "connectivity.json").exists()
    assert not (project.root / ".zddv" / "debug" / "crossprobe.json").exists()


def test_formats_elaborated_port_and_pin_evidence_for_desktop():
    rows = build_desktop_crossprobe_evidence_rows(
        {
            "hierarchy_resolution": "simulator_elaborated",
            "hierarchy": {
                "design_path": "tb_top.dut",
                "type": "counter",
            },
            "source": {
                "unit": "counter",
                "file": "rtl/counter.sv",
                "declaration": {"line": 3},
            },
            "connectivity": {"drivers": [{}], "loads": [{}, {}]},
            "elaborated_port": {
                "status": "MATCHED",
                "instance_path": "tb_top.dut",
                "module": "counter",
                "signal": "count",
                "port": {"direction": "output"},
            },
            "elaborated_connectivity": {
                "analysis_level": "simulator_elaborated_direct_pin_varref",
                "evidence_contract": "verilator_cell_pin_direct_varref_only",
                "parent_signal_bindings": [],
                "instance_port_bindings": [
                    {
                        "instance_path": "tb_top.dut",
                        "pin": "count",
                        "parent_instance_path": "tb_top",
                        "parent_signal": "count",
                        "port_direction": "output",
                        "relationship": "child_output_to_parent_signal",
                    }
                ],
            },
            "elaborated_evidence": {"status": "PRESENT"},
        }
    )

    assert ("Hierarchy", "simulator_elaborated · tb_top.dut · counter") in rows
    assert ("RTL source", "counter · rtl/counter.sv:3") in rows
    assert ("Drivers", "1 source-structural item(s)") in rows
    assert ("Loads", "2 source-structural item(s)") in rows
    assert (
        "Elaborated port",
        "MATCHED · tb_top.dut.count · output · module=counter",
    ) in rows
    assert (
        "Elaborated pins",
        "0 parent-signal / 1 instance-port binding(s) · "
        "contract=verilator_cell_pin_direct_varref_only",
    ) in rows
    assert (
        "Elaborated pin",
        "tb_top.dut.count -> tb_top.count · direction=output · "
        "child_output_to_parent_signal",
    ) in rows
    assert ("Elaboration", "PRESENT") in rows


def test_formats_missing_elaborated_pin_evidence_without_inference():
    rows = build_desktop_crossprobe_evidence_rows(
        {
            "elaborated_port": {
                "status": "UNAVAILABLE",
                "reason": "port_evidence_metadata_missing",
            },
            "elaborated_connectivity": None,
            "elaborated_evidence": {"status": "NOT_PRESENT"},
        }
    )

    assert (
        "Elaborated port",
        "UNAVAILABLE · port_evidence_metadata_missing",
    ) in rows
    assert (
        "Elaborated pins",
        "No normalized direct pin binding matched this signal.",
    ) in rows
    assert ("Elaboration", "NOT_PRESENT") in rows
