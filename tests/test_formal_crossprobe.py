from __future__ import annotations

from pathlib import Path

import pytest

from zddv.cli import main
from zddv.config import initialize_project, save_project
from zddv.formal.crossprobe import write_formal_trace_crossprobe
from zddv.formal.vcd_trace import ingest_formal_vcd_trace


VCD = """$timescale 1ns $end
$scope module formal_top $end
$scope module dut $end
$var wire 4 # count [3:0] $end
$upscope $end
$upscope $end
$enddefinitions $end
#0
b0000 #
#5
b0001 #
"""


def _project(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    rtl = project.root / "rtl"
    formal = project.root / "formal"
    rtl.mkdir(exist_ok=True)
    formal.mkdir(exist_ok=True)

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
    (formal / "formal_top.sv").write_text(
        """module formal_top;
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
    project.tb = ["formal/*.sv"]
    project.top = "formal_top"
    save_project(project)
    return project


def _normalized_trace(project):
    vcd = project.root / "trace.vcd"
    vcd.write_text(VCD, encoding="utf-8")
    return ingest_formal_vcd_trace(
        project,
        vcd,
        property_name="formal_top.p_count",
        property_kind="assert",
        source="sby:smtbmc",
        output=".zddv/formal/counterexamples/test.json",
    )


def test_formal_trace_crossprobe_maps_signal_to_rtl_and_connectivity(tmp_path: Path):
    project = _project(tmp_path)
    normalized = _normalized_trace(project)

    result = write_formal_trace_crossprobe(
        project,
        normalized["normalized_path"],
        signals=["count"],
    )

    assert result["analysis"] == "formal_trace_crossprobe"
    assert result["property"] == "formal_top.p_count"
    assert result["trace_kind"] == "counterexample"
    assert result["summary"] == {
        "signals": 1,
        "matched": 1,
        "partial": 0,
    }

    item = result["results"][0]
    assert item["status"] == "MATCHED"
    assert item["waveform"]["signal"]["path"] == "formal_top.dut.count"
    assert item["hierarchy"]["design_path"] == "formal_top.dut"
    assert item["source"]["file"] == "rtl/counter.sv"
    assert item["source"]["declaration"]["line"] == 3
    assert item["connectivity"]["unit"] == "counter"
    assert item["connectivity"]["signal"] == "count"
    assert Path(result["report_path"]).is_file()


def test_formal_trace_crossprobe_requires_explicit_bound_for_large_trace(tmp_path: Path):
    project = _project(tmp_path)
    normalized = _normalized_trace(project)

    with pytest.raises(RuntimeError, match="max_signals=1"):
        write_formal_trace_crossprobe(
            project,
            normalized["normalized_path"],
            max_signals=1,
        )


def test_formal_crossprobe_cli_writes_source_summary(tmp_path: Path, capsys):
    project = _project(tmp_path)
    normalized = _normalized_trace(project)

    rc = main(
        [
            "--project",
            str(project.root),
            "formal-crossprobe",
            normalized["normalized_path"],
            "--signal",
            "count",
        ]
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "FORMAL CROSSPROBE:" in output
    assert "matched=1 partial=0" in output
    assert "formal_top.dut.count" in output
    assert "rtl/counter.sv:3" in output
    assert (project.root / ".zddv" / "formal" / "crossprobe.json").is_file()
