from __future__ import annotations

import json
from pathlib import Path

import pytest

from zddv.cli import main
from zddv.config import initialize_project, save_project
from zddv.formal.counterexample import normalize_formal_counterexample
from zddv.formal.trace_crossprobe import (
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
    input logic req,
    output logic [3:0] count
);
    always_ff @(posedge clk)
        if (req)
            count <= count + 1'b1;
endmodule
""",
        encoding="utf-8",
    )
    (tb / "formal_top.sv").write_text(
        """module formal_top;
    logic clk;
    logic req;
    logic [3:0] count;
    counter dut(
        .clk(clk),
        .req(req),
        .count(count)
    );
endmodule
""",
        encoding="utf-8",
    )
    project.rtl = ["rtl/*.sv"]
    project.tb = ["tb/*.sv"]
    project.top = "formal_top"
    save_project(project)
    return project


def _trace() -> dict:
    return normalize_formal_counterexample(
        {
            "property": "formal_top.p_count",
            "property_kind": "assert",
            "trace_kind": "counterexample",
            "source": "sby:smtbmc",
            "time_unit": "1 ns",
            "signals": [
                {
                    "name": "formal_top.dut.clk",
                    "width": 1,
                    "metadata": {
                        "scope": "formal_top.dut",
                        "reference": "clk",
                        "var_type": "wire",
                    },
                },
                {
                    "name": "formal_top.dut.count",
                    "width": 4,
                    "metadata": {
                        "scope": "formal_top.dut",
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
                        "formal_top.dut.clk": "0",
                        "formal_top.dut.count": "0000",
                    },
                },
                {
                    "step": 1,
                    "time": 5,
                    "values": {
                        "formal_top.dut.clk": "1",
                        "formal_top.dut.count": "0001",
                    },
                },
            ],
        }
    )


def _write_trace(project, path: str = "formal-trace.json") -> Path:
    destination = project.root / path
    destination.write_text(
        json.dumps(_trace(), indent=2) + "\n",
        encoding="utf-8",
    )
    return destination


def test_formal_trace_crossprobe_maps_signal_to_rtl_and_connectivity(tmp_path: Path):
    project = _project(tmp_path)

    report = build_formal_trace_crossprobe(
        project,
        _trace(),
        selectors=["formal_top.dut.count"],
    )

    assert report["property"] == "formal_top.p_count"
    assert report["trace_kind"] == "counterexample"
    assert report["summary"] == {
        "selected_signals": 1,
        "matched": 1,
        "partial": 0,
        "unresolved": 0,
    }

    row = report["signals"][0]
    assert row["status"] == "MATCHED"
    crossprobe = row["crossprobe"]
    assert crossprobe["hierarchy"]["design_path"] == "formal_top.dut"
    assert crossprobe["hierarchy"]["type"] == "counter"
    assert crossprobe["source"]["file"] == "rtl/counter.sv"
    assert crossprobe["source"]["declaration"]["line"] == 4
    assert "output logic [3:0] count" in crossprobe["source"]["declaration"]["text"]
    assert crossprobe["connectivity"]["unit"] == "counter"
    assert crossprobe["connectivity"]["signal"] == "count"
    assert len(crossprobe["connectivity"]["drivers"]) >= 1
    assert len(crossprobe["connectivity"]["loads"]) >= 1


def test_formal_trace_crossprobe_supports_unique_short_signal_selector(tmp_path: Path):
    project = _project(tmp_path)

    report = build_formal_trace_crossprobe(
        project,
        _trace(),
        selectors=["count"],
    )

    assert report["summary"]["selected_signals"] == 1
    assert report["signals"][0]["signal"] == "formal_top.dut.count"
    assert report["signals"][0]["status"] == "MATCHED"


def test_formal_trace_crossprobe_requires_explicit_limit_for_large_trace(tmp_path: Path):
    project = _project(tmp_path)

    with pytest.raises(RuntimeError, match="exceeding max_signals=1"):
        build_formal_trace_crossprobe(
            project,
            _trace(),
            max_signals=1,
        )


def test_write_formal_trace_crossprobe_report_preserves_trace_context(tmp_path: Path):
    project = _project(tmp_path)
    trace_path = _write_trace(project)

    report = write_formal_trace_crossprobe_report(
        project,
        trace_path,
        selectors=["count"],
    )

    assert report["input_path"] == str(trace_path.resolve())
    assert Path(report["design_index_path"]).is_file()
    assert Path(report["connectivity_index_path"]).is_file()
    assert Path(report["report_path"]).is_file()

    saved = json.loads(Path(report["report_path"]).read_text(encoding="utf-8"))
    assert saved["property"] == "formal_top.p_count"
    assert saved["source"] == "sby:smtbmc"
    assert saved["summary"]["matched"] == 1


def test_formal_trace_crossprobe_cli_prints_source_mapping(tmp_path: Path, capsys):
    project = _project(tmp_path)
    _write_trace(project)

    rc = main(
        [
            "--project",
            str(project.root),
            "formal-trace-crossprobe",
            "formal-trace.json",
            "--signal",
            "count",
        ]
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "FORMAL TRACE CROSSPROBE: property=formal_top.p_count" in output
    assert "Matched/Partial/Unresolved: 1/0/0" in output
    assert "[MATCHED] formal_top.dut.count -> rtl/counter.sv:4" in output
    assert (project.root / ".zddv" / "formal" / "crossprobe" / "latest.json").is_file()
