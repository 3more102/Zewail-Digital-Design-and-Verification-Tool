import json
from pathlib import Path

from zddv.cli import main
from zddv.config import initialize_project
from zddv.storage import record_run
from zddv.waveform import (
    build_waveform_index,
    parse_vcd_header,
    probe_vcd,
    select_waveform_run,
    write_waveform_index,
)


VCD = """$date
    September 21, 2026
$end
$version ZDDV test generator $end
$timescale 1ns $end
$scope module tb_top $end
$var wire 1 ! clk $end
$var reg 1 " rst_n $end
$scope module dut $end
$var wire 4 # count [3:0] $end
$upscope $end
$upscope $end
$enddefinitions $end
#0
0!
0"
b0000 #
#5
1!
"""


def _record(run_id: str, waveform: Path | None, created_at: str) -> dict:
    return {
        "run_id": run_id,
        "created_at": created_at,
        "project": "demo",
        "simulator": "verilator",
        "simulator_version": "Verilator test",
        "top": "tb_top",
        "test": "smoke",
        "seed": 1,
        "status": "PASS",
        "returncode": 0,
        "duration_ms": 10.0,
        "run_dir": str(waveform.parent if waveform else Path("/tmp") / run_id),
        "log": str((waveform.parent if waveform else Path("/tmp") / run_id) / "simulation.log"),
        "waveform": str(waveform) if waveform else None,
        "coverage": None,
        "timeout_s": 10.0,
        "command": ["zddv_sim"],
        "plusargs": [],
    }


def test_parse_vcd_header_indexes_scopes_and_signals(tmp_path: Path):
    path = tmp_path / "waveform.vcd"
    path.write_text(VCD, encoding="utf-8")

    result = parse_vcd_header(path)

    assert result["date"] == "September 21, 2026"
    assert result["version"] == "ZDDV test generator"
    assert result["timescale"] == "1ns"
    assert [scope["path"] for scope in result["scopes"]] == ["tb_top", "tb_top.dut"]
    assert [signal["path"] for signal in result["signals"]] == [
        "tb_top.clk",
        "tb_top.rst_n",
        "tb_top.dut.count",
    ]
    assert result["signals"][2]["width"] == 4
    assert result["signals"][2]["range"] == "[3:0]"
    assert result["summary"] == {
        "scopes": 2,
        "signals": 3,
        "unique_value_ids": 3,
        "declared_bits": 6,
    }


def test_vcd_parser_stops_before_value_change_body(tmp_path: Path):
    path = tmp_path / "waveform.vcd"
    path.write_text(VCD + "$scope module fake_after_header $end\n", encoding="utf-8")

    result = parse_vcd_header(path)

    assert "fake_after_header" not in {scope["name"] for scope in result["scopes"]}


def test_fst_is_indexed_as_metadata_only(tmp_path: Path):
    path = tmp_path / "waveform.fst"
    path.write_bytes(b"FST-placeholder")

    result = build_waveform_index(path, run_id="run-fst", project_name="demo")

    assert result["format"] == "fst"
    assert result["parse_status"] == "metadata-only"
    assert result["run_id"] == "run-fst"
    assert result["signals"] == []
    assert "FST" in result["note"]


def test_select_latest_waveform_run_and_write_index(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    old_dir = project.root / ".zddv" / "runs" / "old"
    new_dir = project.root / ".zddv" / "runs" / "new"
    old_dir.mkdir(parents=True)
    new_dir.mkdir(parents=True)
    old_wave = old_dir / "waveform.vcd"
    new_wave = new_dir / "waveform.vcd"
    old_wave.write_text(VCD, encoding="utf-8")
    new_wave.write_text(VCD.replace("1ns", "10ps"), encoding="utf-8")

    record_run(project, _record("run-old", old_wave, "2026-09-21T19:00:00+00:00"))
    record_run(project, _record("run-new", new_wave, "2026-09-21T20:00:00+00:00"))

    selected = select_waveform_run(project)
    assert selected["run_id"] == "run-new"

    result = write_waveform_index(project)

    assert result["run_id"] == "run-new"
    assert result["timescale"] == "10ps"
    assert Path(result["path"]).is_file()
    assert Path(result["latest_path"]).is_file()

    latest = json.loads(Path(result["latest_path"]).read_text(encoding="utf-8"))
    assert latest["run_id"] == "run-new"
    assert latest["artifact"]["project_path"].endswith("new/waveform.vcd")


def test_waveform_index_can_target_exact_run(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    run_dir = project.root / ".zddv" / "runs" / "chosen"
    run_dir.mkdir(parents=True)
    waveform = run_dir / "waveform.vcd"
    waveform.write_text(VCD, encoding="utf-8")
    record_run(project, _record("chosen", waveform, "2026-09-21T20:00:00+00:00"))

    result = write_waveform_index(project, run_id="chosen")

    assert result["run_id"] == "chosen"
    assert Path(result["path"]).name == "chosen.json"


def test_waveform_index_cli_with_direct_input(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    waveform = project.root / "manual.vcd"
    waveform.write_text(VCD, encoding="utf-8")

    rc = main([
        "--project",
        str(project.root),
        "waveform-index",
        "--input",
        "manual.vcd",
    ])

    assert rc == 0
    output = capsys.readouterr().out
    assert "WAVEFORM INDEX: VCD" in output
    assert "3 signal(s)" in output
    assert "Timescale: 1ns" in output
    assert (project.root / ".zddv" / "waveforms" / "manual.json").is_file()



PROBE_VCD = """$timescale 1ns $end
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
#5
1!
b0001 #
#10
0!
b0010 #
#15
1!
b0011 #
#20
0!
b0100 #
"""


def test_probe_vcd_returns_value_and_transition_window(tmp_path: Path):
    path = tmp_path / "probe.vcd"
    path.write_text(PROBE_VCD, encoding="utf-8")

    result = probe_vcd(
        path,
        ["count"],
        at_time=10,
        before=5,
        after=5,
        max_transitions=10,
        project_name="demo",
    )

    assert result["timescale"] == "1ns"
    assert result["time"] == {
        "tick": 10,
        "before": 5,
        "after": 5,
        "window_start": 5,
        "window_end": 15,
    }
    assert result["summary"] == {
        "signals": 1,
        "transitions_in_window": 3,
        "truncated_signals": 0,
    }

    signal = result["signals"][0]
    assert signal["query"] == "count"
    assert signal["path"] == "tb_top.dut.count"
    assert signal["value_at"] == "0010"
    assert signal["last_transition"] == {"time": 10, "value": "0010"}
    assert signal["next_transition"] == {"time": 15, "value": "0011"}
    assert signal["transitions"] == [
        {"time": 5, "value": "0001"},
        {"time": 10, "value": "0010"},
        {"time": 15, "value": "0011"},
    ]


def test_waveform_probe_cli_with_direct_input(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    waveform = project.root / "probe.vcd"
    waveform.write_text(PROBE_VCD, encoding="utf-8")

    rc = main([
        "--project",
        str(project.root),
        "waveform-probe",
        "--input",
        "probe.vcd",
        "--signal",
        "tb_top.dut.count",
        "--time",
        "10",
        "--before",
        "5",
        "--after",
        "5",
    ])

    assert rc == 0
    output = capsys.readouterr().out
    assert "WAVEFORM PROBE: 1 signal(s), 3 transition(s) in window" in output
    assert "Timescale: 1ns" in output
    assert "tb_top.dut.count value=0010 last=10 next=15 transitions=3" in output
    assert (project.root / ".zddv" / "debug" / "waveform-probe.json").is_file()
