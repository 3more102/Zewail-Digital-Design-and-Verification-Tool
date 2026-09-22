from __future__ import annotations

import json
from pathlib import Path

import pytest

from zddv.cli import main
from zddv.config import initialize_project, save_project
from zddv.design_index import build_design_index
from zddv.formal.crossprobe import (
    build_formal_trace_crossprobe,
    write_formal_trace_crossprobe_report,
)


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


def _trace() -> dict:
    return {
        "schema": "zddv.formal.counterexample.v1",
        "analysis": "formal_counterexample",
        "property": "tb_top.p_count_safe",
        "property_kind": "assert",
        "trace_kind": "counterexample",
        "source": "sby:smtbmc",
        "time_unit": "1 ns",
        "summary": {
            "signals": 2,
            "steps": 2,
            "complete_signal_steps": 2,
            "partial_signal_steps": 0,
            "first_time": 0,
            "last_time": 5,
            "first_cycle": None,
            "last_cycle": None,
        },
        "signals": [
            {
                "name": "tb_top.clk",
                "width": 1,
                "metadata": {
                    "scope": "tb_top",
                    "reference": "clk",
                    "var_type": "wire",
                },
            },
            {
                "name": "tb_top.dut.count",
                "width": 4,
                "metadata": {
                    "scope": "tb_top.dut",
                    "reference": "count",
                    "range": "[3:0]",
                    "var_type": "wire",
                },
            },
        ],
        "steps": [
            {
                "step": 0,
                "time": 0,
                "cycle": None,
                "values": {"tb_top.clk": "0", "tb_top.dut.count": "0000"},
                "metadata": {},
            },
            {
                "step": 1,
                "time": 5,
                "cycle": None,
                "values": {"tb_top.clk": "1", "tb_top.dut.count": "0001"},
                "metadata": {},
            },
        ],
        "metadata": {"format": "vcd", "waveform_path": "formal/trace.vcd"},
        "input_path": "/tmp/formal/trace.vcd",
        "input_sha256": "deadbeef",
        "normalized_path": "/tmp/formal/trace.json",
    }


def test_formal_trace_crossprobe_maps_signal_to_rtl_and_connectivity(tmp_path: Path):
    project = _project(tmp_path)

    result = build_formal_trace_crossprobe(
        project,
        "tb_top.dut.count",
        _trace(),
        design_index=build_design_index(project),
    )

    assert result["analysis"] == "formal_trace_crossprobe"
    assert result["status"] == "MATCHED"
    assert result["signal_match"] == "exact"
    assert result["waveform"]["format"] == "normalized-formal-vcd"
    assert result["waveform"]["signal"]["path"] == "tb_top.dut.count"
    assert result["hierarchy"]["design_path"] == "tb_top.dut"
    assert result["source"]["file"] == "rtl/counter.sv"
    assert result["source"]["declaration"]["line"] == 3
    assert "output logic [3:0] count" in result["source"]["declaration"]["text"]
    assert result["connectivity"]["unit"] == "counter"
    assert result["connectivity"]["signal"] == "count"
    assert result["formal_trace"]["property"] == "tb_top.p_count_safe"
    assert result["formal_trace"]["trace_kind"] == "counterexample"
    assert result["formal_trace"]["input_sha256"] == "deadbeef"


def test_formal_trace_crossprobe_rejects_ambiguous_short_signal_name(tmp_path: Path):
    project = _project(tmp_path)
    trace = _trace()
    trace["signals"].append(
        {
            "name": "tb_top.count",
            "width": 4,
            "metadata": {"scope": "tb_top", "reference": "count"},
        }
    )

    with pytest.raises(RuntimeError, match="ambiguous"):
        build_formal_trace_crossprobe(
            project,
            "count",
            trace,
            design_index=build_design_index(project),
        )


def test_write_formal_trace_crossprobe_report_and_cli(tmp_path: Path, capsys):
    project = _project(tmp_path)
    trace_path = project.root / "formal-trace.json"
    trace_path.write_text(json.dumps(_trace()), encoding="utf-8")

    report = write_formal_trace_crossprobe_report(
        project,
        trace_path,
        "count",
    )
    assert report["status"] == "MATCHED"
    assert report["formal_trace_path"] == str(trace_path.resolve())
    assert Path(report["report_path"]).is_file()
    assert report["design_index_path"].endswith("index.json")
    assert report["connectivity_index_path"].endswith("connectivity.json")

    rc = main(
        [
            "--project",
            str(project.root),
            "formal-trace-crossprobe",
            "formal-trace.json",
            "count",
        ]
    )
    assert rc == 0
    output = capsys.readouterr().out
    assert "FORMAL TRACE CROSSPROBE MATCHED" in output
    assert "property=tb_top.p_count_safe" in output
    assert "rtl/counter.sv:3" in output
    assert "Connectivity: drivers=1 loads=2" in output


def test_formal_trace_crossprobe_requires_normalized_trace_contract(tmp_path: Path):
    project = _project(tmp_path)

    with pytest.raises(ValueError, match="normalized formal"):
        build_formal_trace_crossprobe(
            project,
            "count",
            {"analysis": "other", "signals": []},
        )
