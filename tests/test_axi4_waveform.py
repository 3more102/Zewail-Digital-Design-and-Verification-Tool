from pathlib import Path

import pytest

from zddv.cli import main
from zddv.config import initialize_project
from zddv.protocols.axi4_waveform import (
    analyze_axi4_waveform,
    extract_axi4_trace_from_vcd,
)


FIXTURE = Path(__file__).resolve().parents[1] / "examples" / "axi4_waveform.vcd"


def test_extracts_burst_aware_axi4_trace_from_vcd():
    trace = extract_axi4_trace_from_vcd(FIXTURE)

    assert trace["source"] == "vcd-waveform"
    assert trace["waveform"]["scope"] == "tb.axi"
    assert trace["waveform"]["clock"] == "ACLK"
    assert trace["waveform"]["timescale"] == "1ns"
    assert trace["data_width_bits"] == 16
    assert trace["waveform"]["data_width_bits"] == 16
    assert trace["waveform"]["rdata_width_bits"] == 16
    assert trace["waveform"]["wstrb_width"] == 2
    assert trace["id_widths"] == {
        "ID_W_WIDTH": 2,
        "ID_R_WIDTH": 2,
    }
    assert trace["waveform"]["id_widths"] == trace["id_widths"]
    assert trace["user_signal_widths"] == {
        "AWUSER": 4,
        "WUSER": 4,
        "BUSER": 2,
        "ARUSER": 4,
        "RUSER": 6,
    }
    assert trace["waveform"]["user_signal_widths"] == trace["user_signal_widths"]
    assert trace["samples"][0]["time"] == 5
    assert trace["samples"][0]["AWID"] == 1
    assert trace["samples"][0]["AWLEN"] == 1
    assert trace["samples"][0]["AWUSER"] == 0xA
    assert trace["samples"][1]["WDATA"] == 0x1111
    assert trace["samples"][1]["WUSER"] == 0x1
    assert trace["samples"][2]["WLAST"] == 1
    assert trace["samples"][3]["BUSER"] == 0x3
    assert trace["samples"][4]["ARID"] == 2
    assert trace["samples"][4]["ARUSER"] == 0xB
    assert trace["samples"][5]["RUSER"] == 0x4
    assert trace["samples"][6]["RLAST"] == 1
    assert trace["samples"][6]["RUSER"] == 0x5


def test_analyzes_axi4_waveform_with_timestamped_transactions(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    result = analyze_axi4_waveform(project, input_path=FIXTURE)

    assert result["status"] == "PASS"
    assert result["summary"]["completed_transactions"] == 2
    assert result["summary"]["completed_writes"] == 1
    assert result["summary"]["completed_reads"] == 1
    assert result["waveform"]["scope"] == "tb.axi"

    write = next(tx for tx in result["transactions"] if tx["direction"] == "WRITE")
    read = next(tx for tx in result["transactions"] if tx["direction"] == "READ")

    assert write["id"] == 1
    assert write["write_data"] == [0x1111, 0x2222]
    assert write["beat_addresses"] == [0x100, 0x102]
    assert write["aw_time"] == 5
    assert write["w_times"] == [15, 25]
    assert write["response_time"] == 35
    assert write["awuser"] == 0xA
    assert write["wuser"] == [0x1, 0x2]
    assert write["buser"] == 0x3

    assert read["id"] == 2
    assert read["read_data"] == [0x3333, 0x4444]
    assert read["beat_addresses"] == [0x200, 0x202]
    assert read["ar_time"] == 45
    assert read["read_times"] == [55, 65]
    assert read["response_time"] == 65
    assert read["aruser"] == 0xB
    assert read["ruser"] == [0x4, 0x5]
    assert result["id_widths"] == {
        "ID_R_WIDTH": 2,
        "ID_W_WIDTH": 2,
    }
    assert result["user_signal_widths"] == {
        "ARUSER": 4,
        "AWUSER": 4,
        "BUSER": 2,
        "RUSER": 6,
        "WUSER": 4,
    }

    assert Path(result["trace_path"]).is_file()
    assert Path(result["report_path"]).is_file()


def test_axi4_waveform_cli(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")

    rc = main([
        "--project",
        str(project.root),
        "axi4-waveform",
        "--input",
        str(FIXTURE),
    ])

    assert rc == 0
    output = capsys.readouterr().out
    assert "AXI4 WAVEFORM PASS" in output
    assert "2 completed transaction(s)" in output
    assert "Scope: tb.axi" in output
    assert "Normalized trace:" in output


def test_rejects_mismatched_axi4_vcd_read_write_data_widths(tmp_path: Path):
    source = FIXTURE.read_text(encoding="utf-8")
    source = source.replace(
        "$var wire 16 B RDATA [15:0] $end",
        "$var wire 32 B RDATA [31:0] $end",
    )
    bad = tmp_path / "mismatched_data_width.vcd"
    bad.write_text(source, encoding="utf-8")

    try:
        extract_axi4_trace_from_vcd(bad)
    except RuntimeError as exc:
        assert "WDATA and RDATA widths must match" in str(exc)
    else:
        raise AssertionError("Expected mismatched WDATA/RDATA widths to fail")


def test_rejects_nonstandard_axi4_vcd_data_width(tmp_path: Path):
    source = FIXTURE.read_text(encoding="utf-8")
    source = source.replace(
        "$var wire 16 k WDATA [15:0] $end",
        "$var wire 24 k WDATA [23:0] $end",
    )
    source = source.replace(
        "$var wire 2 l WSTRB [1:0] $end",
        "$var wire 3 l WSTRB [2:0] $end",
    )
    source = source.replace(
        "$var wire 16 B RDATA [15:0] $end",
        "$var wire 24 B RDATA [23:0] $end",
    )
    bad = tmp_path / "nonstandard_data_width.vcd"
    bad.write_text(source, encoding="utf-8")

    try:
        extract_axi4_trace_from_vcd(bad)
    except RuntimeError as exc:
        assert "8, 16, 32, 64, 128, 256, 512, or 1024" in str(exc)
    else:
        raise AssertionError("Expected nonstandard AXI4 VCD data width to fail")


def test_axi4_waveform_user_widths_are_checked_against_axi_properties(tmp_path: Path):
    source = FIXTURE.read_text(encoding="utf-8")
    source = source.replace(
        "$var wire 6 I RUSER [5:0] $end",
        "$var wire 5 I RUSER [4:0] $end",
    )
    bad = tmp_path / "bad_user_widths.vcd"
    bad.write_text(source, encoding="utf-8")
    project = initialize_project(tmp_path / "demo-user-widths")

    try:
        analyze_axi4_waveform(project, input_path=bad)
    except ValueError as exc:
        assert "USER_DATA_WIDTH + USER_RESP_WIDTH" in str(exc)
    else:
        raise AssertionError("Expected inconsistent AXI4 USER VCD widths to fail")



def test_axi4_waveform_rejects_mismatched_write_id_widths(tmp_path: Path):
    source = FIXTURE.read_text(encoding="utf-8")
    source = source.replace(
        "$var wire 2 p BID [1:0] $end",
        "$var wire 3 p BID [2:0] $end",
    )
    bad = tmp_path / "mismatched_write_id_width.vcd"
    bad.write_text(source, encoding="utf-8")

    with pytest.raises(RuntimeError, match="ID_W_WIDTH"):
        extract_axi4_trace_from_vcd(bad)


def test_axi4_waveform_keeps_partial_id_pair_unknown(tmp_path: Path):
    source = FIXTURE.read_text(encoding="utf-8")
    source = source.replace("$var wire 2 p BID [1:0] $end\n", "")
    partial = tmp_path / "partial_write_id_pair.vcd"
    partial.write_text(source, encoding="utf-8")

    trace = extract_axi4_trace_from_vcd(partial)

    assert "ID_W_WIDTH" not in trace["id_widths"]
    assert trace["id_widths"]["ID_R_WIDTH"] == 2


def test_axi4_waveform_keeps_undumped_id_pair_unknown(tmp_path: Path):
    source = FIXTURE.read_text(encoding="utf-8")
    source = source.replace("$var wire 2 d AWID [1:0] $end\n", "")
    source = source.replace("$var wire 2 p BID [1:0] $end\n", "")
    partial = tmp_path / "undumped_write_id_pair.vcd"
    partial.write_text(source, encoding="utf-8")

    trace = extract_axi4_trace_from_vcd(partial)

    assert "ID_W_WIDTH" not in trace["id_widths"]
    assert trace["id_widths"]["ID_R_WIDTH"] == 2
