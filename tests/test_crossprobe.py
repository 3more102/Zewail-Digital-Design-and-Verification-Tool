from pathlib import Path

import pytest

from zddv.cli import main
from zddv.config import initialize_project, save_project
from zddv.crossprobe import build_crossprobe, write_crossprobe_report
from zddv.design_index import build_design_index
from zddv.waveform import build_waveform_index


VCD = """$timescale 1ns $end
$scope module TOP $end
$scope module tb_top $end
$var wire 1 ! clk $end
$scope module dut $end
$var wire 4 # count [3:0] $end
$upscope $end
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


def test_crossprobe_maps_vcd_scope_to_rtl_declaration(tmp_path: Path):
    project = _project(tmp_path)
    waveform_path = project.root / "trace.vcd"
    waveform_path.write_text(VCD, encoding="utf-8")

    result = build_crossprobe(
        project,
        "tb_top.dut.count",
        build_waveform_index(waveform_path, project_name=project.name),
        design_index=build_design_index(project),
    )

    assert result["status"] == "MATCHED"
    assert result["signal_match"] == "path-suffix"
    assert result["waveform"]["signal"]["path"] == "TOP.tb_top.dut.count"
    assert result["hierarchy"]["design_path"] == "tb_top.dut"
    assert result["hierarchy"]["type"] == "counter"
    assert result["source"]["file"] == "rtl/counter.sv"
    assert result["source"]["declaration"]["line"] == 3
    assert "output logic [3:0] count" in result["source"]["declaration"]["text"]
    assert result["connectivity"]["analysis_level"] == "source_structural"
    assert result["connectivity"]["unit"] == "counter"
    assert result["connectivity"]["signal"] == "count"
    assert {item["kind"] for item in result["connectivity"]["drivers"]} == {"procedural_assignment"}
    assert {item["kind"] for item in result["connectivity"]["loads"]} == {"boundary_port"}


def test_crossprobe_rejects_ambiguous_short_signal_name(tmp_path: Path):
    project = _project(tmp_path)
    waveform_path = project.root / "ambiguous.vcd"
    waveform_path.write_text(
        VCD.replace(
            "$upscope $end\n$upscope $end\n$upscope $end",
            "$upscope $end\n$scope module dut2 $end\n"
            "$var wire 4 $ count [3:0] $end\n"
            "$upscope $end\n$upscope $end\n$upscope $end",
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="ambiguous"):
        build_crossprobe(
            project,
            "count",
            build_waveform_index(waveform_path, project_name=project.name),
            design_index=build_design_index(project),
        )


def test_crossprobe_cli_writes_report(tmp_path: Path, capsys):
    project = _project(tmp_path)
    waveform_path = project.root / "trace.vcd"
    waveform_path.write_text(VCD, encoding="utf-8")

    rc = main([
        "--project",
        str(project.root),
        "crossprobe",
        "tb_top.dut.count",
        "--input",
        "trace.vcd",
    ])

    assert rc == 0
    output = capsys.readouterr().out
    assert "CROSSPROBE MATCHED" in output
    assert "tb_top.dut" in output
    assert "rtl/counter.sv:3" in output
    assert "Connectivity: drivers=1 loads=1" in output
    assert (project.root / ".zddv" / "design" / "connectivity.json").is_file()
    assert (project.root / ".zddv" / "debug" / "crossprobe.json").is_file()


def test_write_crossprobe_report_can_use_direct_input(tmp_path: Path):
    project = _project(tmp_path)
    waveform_path = project.root / "trace.vcd"
    waveform_path.write_text(VCD, encoding="utf-8")

    result = write_crossprobe_report(
        project,
        "TOP.tb_top.clk",
        input_path="trace.vcd",
    )

    assert result["status"] == "MATCHED"
    assert result["hierarchy"]["design_path"] == "tb_top"
    assert result["source"]["file"] == "tb/tb_top.sv"
    assert result["connectivity"]["unit"] == "tb_top"
    assert result["connectivity_index_path"].endswith("connectivity.json")
    assert Path(result["report_path"]).is_file()
