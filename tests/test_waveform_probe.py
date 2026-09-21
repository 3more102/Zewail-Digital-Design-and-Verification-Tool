from pathlib import Path

import pytest

from zddv.cli import main
from zddv.config import initialize_project
from zddv.waveform_probe import probe_vcd_signals, write_waveform_probe


VCD = """$timescale 1ns $end
$scope module tb_top $end
$var wire 1 ! clk $end
$scope module dut $end
$var wire 4 # count [3:0] $end
$var real 1 % temperature $end
$upscope $end
$upscope $end
$enddefinitions $end
#0
0!
b0000 #
r25.0 %
#5
1!
b0001 #
#10
0!
b0010 #
r25.5 %
#15
1!
b0011 #
"""


def test_probe_selected_vcd_signals(tmp_path: Path):
    waveform = tmp_path / "waveform.vcd"
    waveform.write_text(VCD, encoding="utf-8")

    result = probe_vcd_signals(
        waveform,
        ["clk", "tb_top.dut.count"],
        start_time=5,
        end_time=10,
    )

    assert result["timescale"] == "1ns"
    assert result["summary"] == {
        "signals": 2,
        "total_changes": 4,
        "truncated_signals": 0,
    }
    clk, count = result["signals"]
    assert clk["path"] == "tb_top.clk"
    assert clk["changes"] == [
        {"time": 5, "value": "1"},
        {"time": 10, "value": "0"},
    ]
    assert count["changes"] == [
        {"time": 5, "value": "0001"},
        {"time": 10, "value": "0010"},
    ]


def test_probe_real_value_and_change_limit(tmp_path: Path):
    waveform = tmp_path / "waveform.vcd"
    waveform.write_text(VCD, encoding="utf-8")

    result = probe_vcd_signals(waveform, ["temperature"], max_changes=1)

    temperature = result["signals"][0]
    assert temperature["changes"] == [{"time": 0, "value": "25.0"}]
    assert temperature["truncated"] is True
    assert result["summary"]["truncated_signals"] == 1


def test_probe_rejects_ambiguous_leaf_name(tmp_path: Path):
    waveform = tmp_path / "waveform.vcd"
    waveform.write_text(
        """$timescale 1ns $end
$scope module top $end
$scope module a $end
$var wire 1 ! valid $end
$upscope $end
$scope module b $end
$var wire 1 " valid $end
$upscope $end
$upscope $end
$enddefinitions $end
#0
0!
0"
""",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="ambiguous"):
        probe_vcd_signals(waveform, ["valid"])


def test_write_waveform_probe_and_cli_direct_input(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    waveform = project.root / "manual.vcd"
    waveform.write_text(VCD, encoding="utf-8")

    result = write_waveform_probe(
        project,
        ["count"],
        input_path="manual.vcd",
        end_time=10,
    )

    assert Path(result["path"]).is_file()
    assert Path(result["latest_path"]).is_file()
    assert result["signals"][0]["path"] == "tb_top.dut.count"

    rc = main(
        [
            "--project",
            str(project.root),
            "waveform-probe",
            "count",
            "--input",
            "manual.vcd",
            "--start",
            "5",
            "--end",
            "10",
            "--max-changes",
            "10",
        ]
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "WAVEFORM PROBE: 1 signal(s), 2 change(s)" in output
    assert "tb_top.dut.count" in output
