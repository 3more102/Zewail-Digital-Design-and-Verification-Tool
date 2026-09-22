from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from zddv.cli import main
from zddv.config import initialize_project
from zddv.formal.vcd_trace import (
    ingest_formal_vcd_trace,
    parse_formal_vcd_trace,
)


def _write_vcd(path: Path) -> None:
    path.write_text(
        """$date today $end
$version zddv-test $end
$timescale 1 ns $end
$scope module top $end
$var wire 1 ! clk $end
$var wire 1 \" req $end
$var wire 2 # state [1:0] $end
$upscope $end
$enddefinitions $end
#0
$dumpvars
0!
1\"
b00 #
$end
#5
1!
b01 #
#10
0!
0\"
b1x #
""",
        encoding="utf-8",
    )


def test_parse_formal_vcd_trace_preserves_hierarchy_and_logic_values(tmp_path: Path):
    path = tmp_path / "trace.vcd"
    _write_vcd(path)

    result = parse_formal_vcd_trace(
        path,
        property_name="top.p_req_ack",
        property_kind="assert",
        source="sby",
    )

    assert result["analysis"] == "formal_counterexample"
    assert result["property"] == "top.p_req_ack"
    assert result["property_kind"] == "assert"
    assert result["trace_kind"] == "counterexample"
    assert result["source"] == "sby"
    assert result["time_unit"] == "1 ns"
    assert result["summary"] == {
        "signals": 3,
        "steps": 3,
        "complete_signal_steps": 3,
        "partial_signal_steps": 0,
        "first_time": 0,
        "last_time": 10,
        "first_cycle": None,
        "last_cycle": None,
    }
    assert [signal["name"] for signal in result["signals"]] == [
        "top.clk",
        "top.req",
        "top.state",
    ]
    assert result["signals"][2]["width"] == 2
    assert result["signals"][2]["metadata"]["range"] == "[1:0]"
    assert result["steps"][0]["values"] == {
        "top.clk": "0",
        "top.req": "1",
        "top.state": "00",
    }
    assert result["steps"][1]["values"]["top.state"] == "01"
    assert result["steps"][2]["values"]["top.state"] == "1x"


def test_parse_formal_vcd_trace_supports_unique_short_signal_selection(tmp_path: Path):
    path = tmp_path / "trace.vcd"
    _write_vcd(path)

    result = parse_formal_vcd_trace(
        path,
        property_name="top.c_state",
        property_kind="cover",
        signals=["state"],
    )

    assert result["trace_kind"] == "witness"
    assert [signal["name"] for signal in result["signals"]] == ["top.state"]
    assert [step["values"] for step in result["steps"]] == [
        {"top.state": "00"},
        {"top.state": "01"},
        {"top.state": "1x"},
    ]


def test_parse_formal_vcd_trace_rejects_ambiguous_short_name(tmp_path: Path):
    path = tmp_path / "ambiguous.vcd"
    path.write_text(
        """$timescale 1ns $end
$scope module a $end
$var wire 1 ! valid $end
$upscope $end
$scope module b $end
$var wire 1 \" valid $end
$upscope $end
$enddefinitions $end
#0
0!
1\"
""",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="ambiguous"):
        parse_formal_vcd_trace(
            path,
            property_name="p",
            property_kind="assert",
            signals=["valid"],
        )


def test_ingest_formal_vcd_trace_writes_provenance(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    path = project.root / "trace.vcd"
    _write_vcd(path)

    result = ingest_formal_vcd_trace(
        project,
        path,
        property_name="top.p_req_ack",
        property_kind="assert",
        source="sby",
    )

    output = Path(result["normalized_path"])
    assert output.is_file()
    assert result["input_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["project"] == "demo"
    assert saved["metadata"]["format"] == "vcd"
    assert saved["metadata"]["selected_signals"] == 3


def test_formal_vcd_trace_cli_writes_normalized_witness(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    path = project.root / "trace.vcd"
    _write_vcd(path)

    rc = main(
        [
            "--project",
            str(project.root),
            "formal-vcd-trace",
            str(path),
            "--property",
            "top.c_state",
            "--kind",
            "cover",
            "--source",
            "sby-cover",
            "--signal",
            "state",
        ]
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "FORMAL VCD TRACE: witness" in output
    assert "property=top.c_state" in output
    assert "Signals/Steps: 1/3" in output

    normalized = project.root / ".zddv/formal/counterexamples/latest.json"
    saved = json.loads(normalized.read_text(encoding="utf-8"))
    assert saved["trace_kind"] == "witness"
    assert saved["source"] == "sby-cover"
    assert [signal["name"] for signal in saved["signals"]] == ["top.state"]


def test_formal_vcd_trace_requires_explicit_step_limit(tmp_path: Path):
    path = tmp_path / "trace.vcd"
    _write_vcd(path)

    with pytest.raises(RuntimeError, match="max_steps=2"):
        parse_formal_vcd_trace(
            path,
            property_name="top.p_req_ack",
            property_kind="assert",
            max_steps=2,
        )
