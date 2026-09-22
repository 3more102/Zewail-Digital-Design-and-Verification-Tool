from __future__ import annotations

import json
from pathlib import Path

import pytest

from zddv.cli import main
from zddv.config import initialize_project, save_project
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


def _trace() -> dict[str, object]:
    return {
        "property": "tb_top.dut.p_count",
        "property_kind": "assert",
        "trace_kind": "counterexample",
        "source": "sby:smtbmc",
        "time_unit": "1 ns",
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
                "values": {
                    "tb_top.clk": "0",
                    "tb_top.dut.count": "0000",
                },
            },
            {
                "step": 1,
                "time": 5,
                "values": {
                    "tb_top.clk": "1",
                    "tb_top.dut.count": "0001",
                },
            },
        ],
    }


def test_formal_trace_crossprobe_reuses_rtl_source_and_connectivity(tmp_path: Path):
    project = _project(tmp_path)

    result = build_formal_trace_crossprobe(project, _trace())

    assert result["analysis"] == "formal_trace_crossprobe"
    assert result["trace_kind"] == "counterexample"
    assert result["summary"] == {
        "signals": 2,
        "matched": 2,
        "partial": 0,
        "errors": 0,
    }

    count = next(
        item for item in result["signals"]
        if item["signal"]["name"] == "tb_top.dut.count"
    )
    assert count["status"] == "MATCHED"
    assert count["hierarchy"]["design_path"] == "tb_top.dut"
    assert count["hierarchy"]["type"] == "counter"
    assert count["source"]["file"] == "rtl/counter.sv"
    assert count["source"]["declaration"]["line"] == 3
    assert "output logic [3:0] count" in count["source"]["declaration"]["text"]
    assert count["connectivity"]["analysis_level"] == "source_structural"
    assert {item["kind"] for item in count["connectivity"]["drivers"]} == {
        "procedural_assignment"
    }
    assert {item["kind"] for item in count["connectivity"]["loads"]} == {
        "boundary_port",
        "procedural_assignment",
    }


def test_formal_trace_crossprobe_signal_selection_rejects_ambiguous_short_name(
    tmp_path: Path,
):
    project = _project(tmp_path)
    trace = _trace()
    trace["signals"] = [
        {"name": "a.count", "width": 1, "metadata": {}},
        {"name": "b.count", "width": 1, "metadata": {}},
    ]
    trace["steps"] = [
        {
            "step": 0,
            "time": 0,
            "values": {"a.count": "0", "b.count": "1"},
        }
    ]

    with pytest.raises(RuntimeError, match="ambiguous"):
        build_formal_trace_crossprobe(project, trace, signals=["count"])


def test_formal_trace_crossprobe_enforces_explicit_signal_bound(tmp_path: Path):
    project = _project(tmp_path)

    with pytest.raises(RuntimeError, match="max_signals=1"):
        build_formal_trace_crossprobe(project, _trace(), max_signals=1)


def test_write_formal_trace_crossprobe_report_preserves_input_provenance(
    tmp_path: Path,
):
    project = _project(tmp_path)
    path = project.root / "formal-trace.json"
    path.write_text(json.dumps(_trace()), encoding="utf-8")

    result = write_formal_trace_crossprobe_report(
        project,
        path,
        signals=["tb_top.dut.count"],
    )

    assert result["summary"]["signals"] == 1
    assert result["summary"]["matched"] == 1
    assert result["input_path"] == str(path.resolve())
    assert len(result["input_sha256"]) == 64
    assert result["design_index_path"].endswith("design-index.json")
    assert result["connectivity_index_path"].endswith("connectivity.json")
    assert Path(result["report_path"]).is_file()


def test_formal_trace_crossprobe_cli_writes_report(tmp_path: Path, capsys):
    project = _project(tmp_path)
    path = project.root / "formal-trace.json"
    path.write_text(json.dumps(_trace()), encoding="utf-8")

    rc = main(
        [
            "--project",
            str(project.root),
            "formal-trace-crossprobe",
            "formal-trace.json",
            "--signal",
            "tb_top.dut.count",
        ]
    )

    output = capsys.readouterr().out
    assert rc == 0
    assert "FORMAL TRACE CROSSPROBE: counterexample" in output
    assert "matched=1" in output
    assert "tb_top.dut.count -> rtl/counter.sv:3" in output
    assert (
        project.root / ".zddv" / "formal" / "crossprobe" / "latest.json"
    ).is_file()
