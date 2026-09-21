import json
from pathlib import Path

from zddv.cli import main
from zddv.config import initialize_project
from zddv.protocols.axi4 import (
    analyze_axi4_file,
    analyze_axi4_trace,
    analyze_axi4_waveform,
    extract_axi4_trace_from_vcd,
)


def test_reconstructs_axi4_incr_write_and_read_bursts():
    result = analyze_axi4_trace(
        {"samples": [
            {"cycle": 0, "AWVALID": 1, "AWREADY": 1, "AWID": 1, "AWADDR": "0x100", "AWLEN": 1, "AWSIZE": 2, "AWBURST": "INCR"},
            {"cycle": 1, "WVALID": 1, "WREADY": 1, "WDATA": "0xAAAA", "WSTRB": "0xF", "WLAST": 0,
             "ARVALID": 1, "ARREADY": 1, "ARID": 3, "ARADDR": "0x200", "ARLEN": 1, "ARSIZE": 2, "ARBURST": 1},
            {"cycle": 2, "WVALID": 1, "WREADY": 1, "WDATA": "0xBBBB", "WSTRB": "0xF", "WLAST": 1,
             "RVALID": 1, "RREADY": 1, "RID": 3, "RDATA": "0x11", "RRESP": "OKAY", "RLAST": 0},
            {"cycle": 3, "BVALID": 1, "BREADY": 1, "BID": 1, "BRESP": "OKAY",
             "RVALID": 1, "RREADY": 1, "RID": 3, "RDATA": "0x22", "RRESP": 0, "RLAST": 1},
        ]}
    )
    assert result["status"] == "PASS"
    assert result["summary"]["completed_transactions"] == 2
    write = next(tx for tx in result["transactions"] if tx["direction"] == "WRITE")
    read = next(tx for tx in result["transactions"] if tx["direction"] == "READ")
    assert write["write_data"] == [0xAAAA, 0xBBBB]
    assert write["beat_addresses"] == [0x100, 0x104]
    assert read["read_data"] == [0x11, 0x22]
    assert read["beat_addresses"] == [0x200, 0x204]


def test_accepts_write_data_before_write_address():
    result = analyze_axi4_trace(
        {"samples": [
            {"cycle": 0, "WVALID": 1, "WREADY": 1, "WDATA": 1, "WSTRB": 0xF, "WLAST": 0},
            {"cycle": 1, "WVALID": 1, "WREADY": 1, "WDATA": 2, "WSTRB": 0xF, "WLAST": 1},
            {"cycle": 2, "AWVALID": 1, "AWREADY": 1, "AWID": 5, "AWADDR": 0x80, "AWLEN": 1, "AWSIZE": 2, "AWBURST": "INCR"},
            {"cycle": 3, "BVALID": 1, "BREADY": 1, "BID": 5, "BRESP": "OKAY"},
        ]}
    )
    assert result["status"] == "PASS"
    assert result["transactions"][0]["write_data"] == [1, 2]


def test_read_data_can_interleave_across_ids():
    result = analyze_axi4_trace(
        {"samples": [
            {"cycle": 0, "ARVALID": 1, "ARREADY": 1, "ARID": 1, "ARADDR": 0x100, "ARLEN": 1, "ARSIZE": 2, "ARBURST": "INCR"},
            {"cycle": 1, "ARVALID": 1, "ARREADY": 1, "ARID": 2, "ARADDR": 0x200, "ARLEN": 0, "ARSIZE": 2, "ARBURST": "INCR"},
            {"cycle": 2, "RVALID": 1, "RREADY": 1, "RID": 1, "RDATA": 0xA1, "RRESP": "OKAY", "RLAST": 0},
            {"cycle": 3, "RVALID": 1, "RREADY": 1, "RID": 2, "RDATA": 0xB0, "RRESP": "SLVERR", "RLAST": 1},
            {"cycle": 4, "RVALID": 1, "RREADY": 1, "RID": 1, "RDATA": 0xA2, "RRESP": "OKAY", "RLAST": 1},
        ]}
    )
    assert result["status"] == "PASS"
    reads = [tx for tx in result["transactions"] if tx["direction"] == "READ"]
    assert [tx["id"] for tx in reads] == [2, 1]
    assert result["summary"]["read_error_beats"] == 1


def test_write_responses_can_complete_out_of_order_across_ids():
    result = analyze_axi4_trace(
        {"samples": [
            {"cycle": 0, "AWVALID": 1, "AWREADY": 1, "AWID": 1, "AWADDR": 0x100, "AWLEN": 0, "AWSIZE": 2, "AWBURST": "INCR"},
            {"cycle": 1, "AWVALID": 1, "AWREADY": 1, "AWID": 2, "AWADDR": 0x200, "AWLEN": 0, "AWSIZE": 2, "AWBURST": "INCR"},
            {"cycle": 2, "WVALID": 1, "WREADY": 1, "WDATA": 0x11, "WSTRB": 0xF, "WLAST": 1},
            {"cycle": 3, "WVALID": 1, "WREADY": 1, "WDATA": 0x22, "WSTRB": 0xF, "WLAST": 1},
            {"cycle": 4, "BVALID": 1, "BREADY": 1, "BID": 2, "BRESP": "OKAY"},
            {"cycle": 5, "BVALID": 1, "BREADY": 1, "BID": 1, "BRESP": "OKAY"},
        ]}
    )
    assert result["status"] == "PASS"
    writes = [tx for tx in result["transactions"] if tx["direction"] == "WRITE"]
    assert [tx["id"] for tx in writes] == [2, 1]
    assert writes[0]["write_data"] == [0x22]
    assert writes[1]["write_data"] == [0x11]


def test_reports_wlast_and_rlast_length_mismatches():
    result = analyze_axi4_trace(
        {"samples": [
            {"cycle": 0, "AWVALID": 1, "AWREADY": 1, "AWADDR": 0, "AWLEN": 3, "AWSIZE": 2, "AWBURST": "INCR"},
            {"cycle": 1, "WVALID": 1, "WREADY": 1, "WDATA": 1, "WSTRB": 0xF, "WLAST": 0},
            {"cycle": 2, "WVALID": 1, "WREADY": 1, "WDATA": 2, "WSTRB": 0xF, "WLAST": 1},
            {"cycle": 3, "ARVALID": 1, "ARREADY": 1, "ARADDR": 0x100, "ARLEN": 1, "ARSIZE": 2, "ARBURST": "INCR"},
            {"cycle": 4, "RVALID": 1, "RREADY": 1, "RDATA": 7, "RRESP": "OKAY", "RLAST": 1},
        ]}
    )
    codes = {item["code"] for item in result["violations"]}
    assert result["status"] == "FAIL"
    assert "wlast_early" in codes
    assert "rlast_early" in codes


def test_reports_4kb_crossing_and_invalid_wrap_length():
    result = analyze_axi4_trace(
        {"samples": [
            {"cycle": 0, "ARVALID": 1, "ARREADY": 1, "ARID": 1, "ARADDR": "0xFFC", "ARLEN": 1, "ARSIZE": 2, "ARBURST": "INCR"},
            {"cycle": 1, "AWVALID": 1, "AWREADY": 1, "AWID": 2, "AWADDR": 0x200, "AWLEN": 2, "AWSIZE": 2, "AWBURST": "WRAP"},
        ]}
    )
    codes = {item["code"] for item in result["violations"]}
    assert "burst_crosses_4kb_boundary" in codes
    assert "invalid_wrap_length" in codes


def test_axi4_error_responses_are_not_protocol_violations():
    result = analyze_axi4_trace(
        {"samples": [
            {"cycle": 0, "ARVALID": 1, "ARREADY": 1, "ARADDR": 0, "ARLEN": 0, "ARSIZE": 2, "ARBURST": "INCR"},
            {"cycle": 1, "RVALID": 1, "RREADY": 1, "RDATA": 0, "RRESP": "DECERR", "RLAST": 1},
        ]}
    )
    assert result["status"] == "PASS"
    assert result["summary"]["read_error_beats"] == 1


def test_analyze_axi4_file_and_cli_write_report(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    trace = project.root / "axi4.json"
    trace.write_text(
        json.dumps(
            {
                "samples": [
                    {
                        "cycle": 0,
                        "ARVALID": 1,
                        "ARREADY": 1,
                        "ARADDR": 0x40,
                        "ARLEN": 0,
                        "ARSIZE": 2,
                        "ARBURST": "INCR",
                    },
                    {
                        "cycle": 1,
                        "RVALID": 1,
                        "RREADY": 1,
                        "RDATA": 0x1234,
                        "RRESP": "OKAY",
                        "RLAST": 1,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    result = analyze_axi4_file(project, trace)
    report = Path(result["report_path"])
    assert report.is_file()
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["protocol"] == "AXI4"
    assert payload["summary"]["completed_reads"] == 1

    rc = main(["--project", str(project.root), "axi4-analyze", str(trace)])
    assert rc == 0
    output = capsys.readouterr().out
    assert "AXI4 PASS" in output
    assert "1 completed transaction(s)" in output

AXI4_VCD = """$timescale 1ns $end
$scope module tb $end
$scope module axi $end
$var wire 1 aa ACLK $end
$var wire 1 ab AWVALID $end
$var wire 1 ac AWREADY $end
$var wire 2 ad AWID [1:0] $end
$var wire 12 ae AWADDR [11:0] $end
$var wire 8 af AWLEN [7:0] $end
$var wire 3 ag AWSIZE [2:0] $end
$var wire 2 ah AWBURST [1:0] $end
$var wire 1 ai WVALID $end
$var wire 1 aj WREADY $end
$var wire 16 ak WDATA [15:0] $end
$var wire 2 al WSTRB [1:0] $end
$var wire 1 am WLAST $end
$var wire 1 an BVALID $end
$var wire 1 ao BREADY $end
$var wire 2 ap BID [1:0] $end
$var wire 2 aq BRESP [1:0] $end
$var wire 1 ar ARVALID $end
$var wire 1 as ARREADY $end
$var wire 2 at ARID [1:0] $end
$var wire 12 au ARADDR [11:0] $end
$var wire 8 av ARLEN [7:0] $end
$var wire 3 aw ARSIZE [2:0] $end
$var wire 2 ax ARBURST [1:0] $end
$var wire 1 ay RVALID $end
$var wire 1 az RREADY $end
$var wire 2 ba RID [1:0] $end
$var wire 16 bb RDATA [15:0] $end
$var wire 2 bc RRESP [1:0] $end
$var wire 1 bd RLAST $end
$upscope $end
$upscope $end
$enddefinitions $end
#0
0aa
0ab
1ac
b00 ad
b000000000000 ae
b00000000 af
b000 ag
b00 ah
0ai
1aj
b0000000000000000 ak
b00 al
0am
0an
1ao
b00 ap
b00 aq
0ar
1as
b00 at
b000000000000 au
b00000000 av
b000 aw
b00 ax
0ay
1az
b00 ba
b0000000000000000 bb
b00 bc
0bd
#5
1ab
b01 ad
b000100000000 ae
b00000001 af
b010 ag
b01 ah
1aa
#10
0aa
#15
0ab
1ai
b1010101010101010 ak
b11 al
0am
1ar
b10 at
b001000000000 au
b00000001 av
b010 aw
b01 ax
1aa
#20
0aa
#25
b1011101110111011 ak
1am
0ar
1ay
b10 ba
b0000000000010001 bb
b00 bc
0bd
1aa
#30
0aa
#35
0ai
1an
b01 ap
b00 aq
b0000000000100010 bb
1bd
1aa
"""


def test_extracts_axi4_bursts_from_vcd(tmp_path: Path):
    waveform = tmp_path / "axi4.vcd"
    waveform.write_text(AXI4_VCD, encoding="utf-8")

    trace = extract_axi4_trace_from_vcd(waveform)

    assert trace["waveform"]["scope"] == "tb.axi"
    assert trace["waveform"]["clock"] == "ACLK"
    assert trace["waveform"]["timescale"] == "1ns"
    assert [sample["time"] for sample in trace["samples"]] == [5, 15, 25, 35]

    result = analyze_axi4_trace(trace)

    assert result["status"] == "PASS"
    assert result["summary"]["completed_transactions"] == 2
    assert result["summary"]["completed_reads"] == 1
    assert result["summary"]["completed_writes"] == 1
    assert result["summary"]["write_beats"] == 2
    assert result["summary"]["read_beats"] == 2

    write = next(tx for tx in result["transactions"] if tx["direction"] == "WRITE")
    read = next(tx for tx in result["transactions"] if tx["direction"] == "READ")

    assert write["id"] == 1
    assert write["address"] == 0x100
    assert write["write_data"] == [0xAAAA, 0xBBBB]
    assert write["beat_addresses"] == [0x100, 0x104]
    assert write["aw_time"] == 5
    assert write["w_times"] == [15, 25]
    assert write["response_time"] == 35

    assert read["id"] == 2
    assert read["address"] == 0x200
    assert read["read_data"] == [0x11, 0x22]
    assert read["beat_addresses"] == [0x200, 0x204]
    assert read["ar_time"] == 15
    assert read["response_time"] == 35


def test_axi4_waveform_cli_writes_trace_and_report(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    waveform = project.root / "axi4.vcd"
    waveform.write_text(AXI4_VCD, encoding="utf-8")

    result = analyze_axi4_waveform(project, input_path="axi4.vcd")

    assert result["status"] == "PASS"
    assert result["summary"]["completed_transactions"] == 2
    assert Path(result["trace_path"]).is_file()
    assert Path(result["report_path"]).is_file()

    rc = main(
        [
            "--project",
            str(project.root),
            "axi4-waveform",
            "--input",
            "axi4.vcd",
        ]
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "AXI4 WAVEFORM PASS" in output
    assert "2 completed transaction(s)" in output

