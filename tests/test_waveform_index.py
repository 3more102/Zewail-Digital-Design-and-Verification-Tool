from pathlib import Path

from zddv.config import initialize_project
from zddv.waveform_index import parse_vcd_header, write_waveform_index


def _vcd(path: Path) -> Path:
    path.write_text(
        """$date today $end
$timescale 1ns $end
$scope module top $end
$var wire 1 ! clk $end
$scope module dut $end
$var wire 4 \" count [3:0] $end
$upscope $end
$var reg 1 # done $end
$upscope $end
$enddefinitions $end
#0
0!
b0000 \"
0#
""",
        encoding="utf-8",
    )
    return path


def test_parse_vcd_header_builds_scope_and_signal_index(tmp_path: Path):
    path = _vcd(tmp_path / "waveform.vcd")
    index = parse_vcd_header(path)

    assert index["timescale"] == "1ns"
    assert index["stats"]["scopes"] == 2
    assert index["stats"]["signals"] == 3
    assert index["stats"]["scalar_signals"] == 2
    assert index["stats"]["vector_signals"] == 1
    assert index["stats"]["by_type"] == {"reg": 1, "wire": 2}

    signals = {item["path"]: item for item in index["signals"]}
    assert signals["top.clk"]["width"] == 1
    assert signals["top.dut.count"]["width"] == 4
    assert signals["top.done"]["type"] == "reg"


def test_write_waveform_index(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    index = parse_vcd_header(_vcd(tmp_path / "waveform.vcd"))
    index.update({"project": project.name, "run_id": "run-42"})

    output = write_waveform_index(project, index)
    assert output.exists()
    assert output.name == "run-42.json"
    assert output.parent.name == "waveforms"
