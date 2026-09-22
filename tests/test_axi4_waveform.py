from pathlib import Path

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
    assert trace["data_width_bits"] == 16
    assert trace["waveform"]["data_width_bits"] == 16
    assert trace["waveform"]["scope"] == "tb.axi"
    assert trace["waveform"]["clock"] == "ACLK"
    assert trace["waveform"]["timescale"] == "1ns"
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
    assert result["data_width_bits"] == 16
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
