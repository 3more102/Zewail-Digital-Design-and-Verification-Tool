from __future__ import annotations

import json
from pathlib import Path

import pytest

from zddv.cli import main
from zddv.config import initialize_project, save_project
from zddv.connectivity import build_connectivity_index
from zddv.design_index import build_design_index
from zddv.formal.trace_crossprobe import build_formal_trace_crossprobe


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


def _trace_payload() -> dict:
    return {
        "property": "tb_top.p_count_safe",
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
                    "range": None,
                    "var_type": "wire",
                    "id_code": "!",
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
                    "id_code": "#",
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
                "metadata": {},
            },
            {
                "step": 1,
                "time": 5,
                "values": {
                    "tb_top.clk": "1",
                    "tb_top.dut.count": "0001",
                },
                "metadata": {},
            },
        ],
        "input_sha256": "native-trace-sha256",
    }


def test_formal_trace_crossprobe_maps_signal_to_rtl_and_connectivity(tmp_path: Path):
    project = _project(tmp_path)
    payload = _trace_payload()

    result = build_formal_trace_crossprobe(
        project,
        payload,
        ["tb_top.dut.count"],
        design_index=build_design_index(project),
        connectivity_index=build_connectivity_index(project),
    )

    assert result["analysis"] == "formal_trace_crossprobe"
    assert result["trace"]["property"] == "tb_top.p_count_safe"
    assert result["summary"] == {"queries": 1, "matched": 1, "partial": 0}

    item = result["signals"][0]
    assert item["status"] == "MATCHED"
    assert item["signal_match"] == "exact"
    assert item["trace_signal"]["path"] == "tb_top.dut.count"
    assert item["hierarchy"]["design_path"] == "tb_top.dut"
    assert item["source"]["file"] == "rtl/counter.sv"
    assert item["source"]["declaration"]["line"] == 3
    assert "output logic [3:0] count" in item["source"]["declaration"]["text"]
    assert item["connectivity"]["unit"] == "counter"
    assert item["connectivity"]["signal"] == "count"
    assert {entry["kind"] for entry in item["connectivity"]["drivers"]} == {
        "procedural_assignment"
    }


def test_formal_trace_crossprobe_rejects_ambiguous_short_name(tmp_path: Path):
    project = _project(tmp_path)
    payload = _trace_payload()
    payload["signals"].append(
        {
            "name": "tb_top.other.count",
            "width": 4,
            "metadata": {
                "scope": "tb_top.other",
                "reference": "count",
            },
        }
    )
    payload["steps"][0]["values"]["tb_top.other.count"] = "0000"
    payload["steps"][1]["values"]["tb_top.other.count"] = "0001"

    with pytest.raises(RuntimeError, match="ambiguous"):
        build_formal_trace_crossprobe(
            project,
            payload,
            ["count"],
            design_index=build_design_index(project),
            connectivity_index=build_connectivity_index(project),
        )


def test_formal_trace_crossprobe_cli_writes_report(tmp_path: Path, capsys):
    project = _project(tmp_path)
    trace_path = project.root / "formal-trace.json"
    trace_path.write_text(json.dumps(_trace_payload()), encoding="utf-8")

    rc = main(
        [
            "--project",
            str(project.root),
            "formal-trace-crossprobe",
            "formal-trace.json",
            "--signal",
            "count",
            "--signal",
            "clk",
        ]
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "FORMAL TRACE CROSSPROBE" in output
    assert "matched=2 partial=0" in output
    assert "rtl/counter.sv:3" in output
    assert "tb/tb_top.sv:2" in output

    report_path = project.root / ".zddv" / "debug" / "formal-trace-crossprobe.json"
    assert report_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["trace"]["native_input_sha256"] == "native-trace-sha256"
    assert len(report["trace"]["normalized_trace_sha256"]) == 64
    assert report["summary"]["matched"] == 2
    assert report["design_index_path"].endswith("index.json")
    assert report["connectivity_index_path"].endswith("connectivity.json")
