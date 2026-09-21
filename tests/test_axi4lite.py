import json
from pathlib import Path

from zddv.cli import main
from zddv.config import initialize_project
from zddv.protocols.axi4lite import (
    analyze_axi4lite_file,
    analyze_axi4lite_trace,
    analyze_axi4lite_waveform,
    extract_axi4lite_trace_from_vcd,
)


def test_reconstructs_axi4lite_transactions_with_independent_write_channels():
    result = analyze_axi4lite_trace(
        {
            "source": "unit-test",
            "samples": [
                {
                    "cycle": 0,
                    "AWVALID": 1,
                    "AWREADY": 1,
                    "AWADDR": "0x10",
                    "WREADY": 1,
                    "BREADY": 1,
                    "RREADY": 1,
                },
                {
                    "cycle": 1,
                    "WVALID": 1,
                    "WREADY": 1,
                    "WDATA": "0xCAFE",
                    "WSTRB": "0xF",
                    "BREADY": 1,
                    "ARVALID": 1,
                    "ARREADY": 1,
                    "ARADDR": "0x20",
                    "RREADY": 1,
                },
                {
                    "cycle": 2,
                    "BVALID": 1,
                    "BREADY": 0,
                    "BRESP": "OKAY",
                    "RVALID": 1,
                    "RREADY": 1,
                    "RDATA": "0x55",
                    "RRESP": 0,
                },
                {
                    "cycle": 3,
                    "BVALID": 1,
                    "BREADY": 1,
                    "BRESP": "OKAY",
                },
            ],
        }
    )

    assert result["status"] == "PASS"
    assert result["summary"]["issued_write_requests"] == 1
    assert result["summary"]["issued_read_requests"] == 1
    assert result["summary"]["completed_transactions"] == 2
    assert result["summary"]["writes"] == 1
    assert result["summary"]["reads"] == 1
    assert result["summary"]["channel_stall_cycles"]["B"] == 1

    read = next(tx for tx in result["transactions"] if tx["direction"] == "READ")
    write = next(tx for tx in result["transactions"] if tx["direction"] == "WRITE")
    assert read["address"] == 0x20
    assert read["read_data"] == 0x55
    assert write["address"] == 0x10
    assert write["write_data"] == 0xCAFE
    assert write["wstrb"] == 0xF


def test_reports_valid_and_payload_stability_violations():
    result = analyze_axi4lite_trace(
        {
            "samples": [
                {
                    "cycle": 10,
                    "AWVALID": 1,
                    "AWREADY": 0,
                    "AWADDR": "0x100",
                },
                {
                    "cycle": 11,
                    "AWVALID": 1,
                    "AWREADY": 1,
                    "AWADDR": "0x104",
                },
                {
                    "cycle": 12,
                    "ARVALID": 1,
                    "ARREADY": 0,
                    "ARADDR": "0x200",
                },
                {
                    "cycle": 13,
                    "ARVALID": 0,
                    "ARREADY": 0,
                },
            ]
        }
    )

    codes = {item["code"] for item in result["violations"]}
    assert result["status"] == "FAIL"
    assert "payload_changed_while_stalled" in codes
    assert "valid_dropped_before_handshake" in codes


def test_rejects_exokay_and_response_before_request():
    result = analyze_axi4lite_trace(
        {
            "samples": [
                {
                    "cycle": 0,
                    "BVALID": 1,
                    "BREADY": 1,
                    "BRESP": "EXOKAY",
                },
                {
                    "cycle": 1,
                    "RVALID": 1,
                    "RREADY": 1,
                    "RDATA": 0,
                    "RRESP": 1,
                },
            ]
        }
    )

    codes = [item["code"] for item in result["violations"]]
    assert result["status"] == "FAIL"
    assert codes.count("exclusive_response_not_supported") == 2
    assert "write_response_before_request" in codes
    assert "read_response_before_request" in codes


def test_multiple_outstanding_requests_complete_in_order():
    result = analyze_axi4lite_trace(
        {
            "samples": [
                {
                    "cycle": 0,
                    "ARVALID": 1,
                    "ARREADY": 1,
                    "ARADDR": "0x10",
                },
                {
                    "cycle": 1,
                    "ARVALID": 1,
                    "ARREADY": 1,
                    "ARADDR": "0x20",
                },
                {
                    "cycle": 2,
                    "RVALID": 1,
                    "RREADY": 1,
                    "RDATA": "0xAA",
                    "RRESP": "OKAY",
                },
                {
                    "cycle": 3,
                    "RVALID": 1,
                    "RREADY": 1,
                    "RDATA": "0xBB",
                    "RRESP": "SLVERR",
                },
            ]
        }
    )

    assert result["status"] == "PASS"
    reads = [tx for tx in result["transactions"] if tx["direction"] == "READ"]
    assert [tx["address"] for tx in reads] == [0x10, 0x20]
    assert [tx["read_data"] for tx in reads] == [0xAA, 0xBB]
    assert result["summary"]["error_responses"] == 1


def test_analyze_axi4lite_file_and_cli_write_report(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    trace = project.root / "axi4lite.json"
    trace.write_text(
        json.dumps(
            {
                "source": "file-test",
                "samples": [
                    {
                        "cycle": 0,
                        "ARVALID": 1,
                        "ARREADY": 1,
                        "ARADDR": "0x40",
                    },
                    {
                        "cycle": 1,
                        "RVALID": 1,
                        "RREADY": 1,
                        "RDATA": "0x1234",
                        "RRESP": "OKAY",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    result = analyze_axi4lite_file(project, trace)
    report = Path(result["report_path"])
    assert report.is_file()
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["protocol"] == "AXI4-Lite"
    assert payload["summary"]["completed_transactions"] == 1

    rc = main(
        [
            "--project",
            str(project.root),
            "axi4lite-analyze",
            str(trace),
        ]
    )
    assert rc == 0
    output = capsys.readouterr().out
    assert "AXI4-Lite PASS" in output
    assert "1 completed transaction(s)" in output


AXI4LITE_VCD = """$timescale 1ns $end
$scope module tb $end
$scope module axi $end
$var wire 1 a ACLK $end
$var wire 1 b AWVALID $end
$var wire 1 c AWREADY $end
$var wire 8 d AWADDR [7:0] $end
$var wire 3 e AWPROT [2:0] $end
$var wire 1 f WVALID $end
$var wire 1 g WREADY $end
$var wire 16 h WDATA [15:0] $end
$var wire 2 i WSTRB [1:0] $end
$var wire 1 j BVALID $end
$var wire 1 k BREADY $end
$var wire 2 l BRESP [1:0] $end
$var wire 1 m ARVALID $end
$var wire 1 n ARREADY $end
$var wire 8 o ARADDR [7:0] $end
$var wire 3 p ARPROT [2:0] $end
$var wire 1 q RVALID $end
$var wire 1 r RREADY $end
$var wire 16 s RDATA [15:0] $end
$var wire 2 t RRESP [1:0] $end
$upscope $end
$upscope $end
$enddefinitions $end
#0
0a
0b
1c
b00000000 d
b000 e
0f
1g
b0000000000000000 h
b00 i
0j
1k
b00 l
0m
1n
b00000000 o
b000 p
0q
1r
b0000000000000000 s
b00 t
#5
1b
1c
b00010000 d
b000 e
1a
#10
0a
#15
0b
1f
1g
b1100101011111110 h
b11 i
1m
1n
b00100000 o
b000 p
1a
#20
0a
#25
0f
0m
1j
0k
b00 l
1q
1r
b0000000001010101 s
b00 t
1a
#30
0a
#35
1j
1k
b00 l
0q
1a
"""


def test_extracts_axi4lite_transactions_from_vcd(tmp_path: Path):
    waveform = tmp_path / "axi4lite.vcd"
    waveform.write_text(AXI4LITE_VCD, encoding="utf-8")

    trace = extract_axi4lite_trace_from_vcd(waveform)

    assert trace["waveform"]["scope"] == "tb.axi"
    assert trace["waveform"]["clock"] == "ACLK"
    assert trace["waveform"]["timescale"] == "1ns"
    assert [sample["time"] for sample in trace["samples"]] == [5, 15, 25, 35]

    result = analyze_axi4lite_trace(trace)

    assert result["status"] == "PASS"
    assert result["summary"]["completed_transactions"] == 2
    assert result["summary"]["reads"] == 1
    assert result["summary"]["writes"] == 1
    assert result["summary"]["channel_stall_cycles"]["B"] == 1

    write = next(tx for tx in result["transactions"] if tx["direction"] == "WRITE")
    read = next(tx for tx in result["transactions"] if tx["direction"] == "READ")

    assert write["address"] == 0x10
    assert write["write_data"] == 0xCAFE
    assert write["wstrb"] == 0x3
    assert write["aw_time"] == 5
    assert write["w_time"] == 15
    assert write["response_time"] == 35

    assert read["address"] == 0x20
    assert read["read_data"] == 0x55
    assert read["ar_time"] == 15
    assert read["response_time"] == 25


def test_axi4lite_waveform_cli_writes_trace_and_report(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    waveform = project.root / "axi4lite.vcd"
    waveform.write_text(AXI4LITE_VCD, encoding="utf-8")

    result = analyze_axi4lite_waveform(project, input_path="axi4lite.vcd")

    assert result["status"] == "PASS"
    assert result["summary"]["completed_transactions"] == 2
    assert Path(result["trace_path"]).is_file()
    assert Path(result["report_path"]).is_file()

    rc = main(
        [
            "--project",
            str(project.root),
            "axi4lite-waveform",
            "--input",
            "axi4lite.vcd",
        ]
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "AXI4-Lite WAVEFORM PASS" in output
    assert "2 completed transaction(s)" in output
    assert "Scope: tb.axi" in output
