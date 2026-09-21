import json
from pathlib import Path

from zddv.cli import main
from zddv.config import initialize_project
from zddv.protocols.axi4 import analyze_axi4_file, analyze_axi4_trace


def test_reconstructs_axi4_write_and_read_bursts():
    result = analyze_axi4_trace(
        {
            "source": "unit-test",
            "samples": [
                {
                    "cycle": 0,
                    "AWVALID": 1,
                    "AWREADY": 1,
                    "AWID": 3,
                    "AWADDR": "0x100",
                    "AWLEN": 3,
                    "AWSIZE": 2,
                    "AWBURST": "INCR",
                },
                {
                    "cycle": 1,
                    "WVALID": 1,
                    "WREADY": 1,
                    "WDATA": "0x11",
                    "WSTRB": "0xF",
                    "WLAST": 0,
                },
                {
                    "cycle": 2,
                    "WVALID": 1,
                    "WREADY": 1,
                    "WDATA": "0x22",
                    "WSTRB": "0xF",
                    "WLAST": 0,
                },
                {
                    "cycle": 3,
                    "WVALID": 1,
                    "WREADY": 1,
                    "WDATA": "0x33",
                    "WSTRB": "0xF",
                    "WLAST": 0,
                },
                {
                    "cycle": 4,
                    "WVALID": 1,
                    "WREADY": 1,
                    "WDATA": "0x44",
                    "WSTRB": "0xF",
                    "WLAST": 1,
                },
                {
                    "cycle": 5,
                    "BVALID": 1,
                    "BREADY": 1,
                    "BID": 3,
                    "BRESP": "OKAY",
                },
                {
                    "cycle": 6,
                    "ARVALID": 1,
                    "ARREADY": 1,
                    "ARID": 7,
                    "ARADDR": "0x200",
                    "ARLEN": 1,
                    "ARSIZE": 2,
                    "ARBURST": "INCR",
                },
                {
                    "cycle": 7,
                    "RVALID": 1,
                    "RREADY": 1,
                    "RID": 7,
                    "RDATA": "0xAA",
                    "RRESP": "OKAY",
                    "RLAST": 0,
                },
                {
                    "cycle": 8,
                    "RVALID": 1,
                    "RREADY": 1,
                    "RID": 7,
                    "RDATA": "0xBB",
                    "RRESP": "OKAY",
                    "RLAST": 1,
                },
            ],
        }
    )

    assert result["status"] == "PASS"
    assert result["summary"]["issued_write_bursts"] == 1
    assert result["summary"]["issued_read_bursts"] == 1
    assert result["summary"]["completed_transactions"] == 2
    assert result["summary"]["writes"] == 1
    assert result["summary"]["reads"] == 1

    write = next(
        tx for tx in result["transactions"]
        if tx["direction"] == "WRITE"
    )
    read = next(
        tx for tx in result["transactions"]
        if tx["direction"] == "READ"
    )

    assert write["id"] == 3
    assert write["beats_expected"] == 4
    assert write["burst"] == "INCR"
    assert [beat["address"] for beat in write["data_beats"]] == [
        0x100,
        0x104,
        0x108,
        0x10C,
    ]

    assert read["id"] == 7
    assert read["beats_expected"] == 2
    assert [beat["address"] for beat in read["data_beats"]] == [
        0x200,
        0x204,
    ]
    assert [beat["data"] for beat in read["data_beats"]] == [
        0xAA,
        0xBB,
    ]


def test_allows_out_of_order_completion_across_ids():
    result = analyze_axi4_trace(
        {
            "samples": [
                {
                    "cycle": 0,
                    "ARVALID": 1,
                    "ARREADY": 1,
                    "ARID": 1,
                    "ARADDR": "0x100",
                    "ARLEN": 0,
                    "ARSIZE": 2,
                    "ARBURST": "INCR",
                },
                {
                    "cycle": 1,
                    "ARVALID": 1,
                    "ARREADY": 1,
                    "ARID": 2,
                    "ARADDR": "0x200",
                    "ARLEN": 0,
                    "ARSIZE": 2,
                    "ARBURST": "INCR",
                },
                {
                    "cycle": 2,
                    "RVALID": 1,
                    "RREADY": 1,
                    "RID": 2,
                    "RDATA": "0x22",
                    "RRESP": "OKAY",
                    "RLAST": 1,
                },
                {
                    "cycle": 3,
                    "RVALID": 1,
                    "RREADY": 1,
                    "RID": 1,
                    "RDATA": "0x11",
                    "RRESP": "OKAY",
                    "RLAST": 1,
                },
                {
                    "cycle": 4,
                    "AWVALID": 1,
                    "AWREADY": 1,
                    "AWID": 4,
                    "AWADDR": "0x300",
                    "AWLEN": 0,
                    "AWSIZE": 2,
                    "AWBURST": "INCR",
                },
                {
                    "cycle": 5,
                    "WVALID": 1,
                    "WREADY": 1,
                    "WDATA": "0x44",
                    "WSTRB": "0xF",
                    "WLAST": 1,
                },
                {
                    "cycle": 6,
                    "AWVALID": 1,
                    "AWREADY": 1,
                    "AWID": 5,
                    "AWADDR": "0x400",
                    "AWLEN": 0,
                    "AWSIZE": 2,
                    "AWBURST": "INCR",
                },
                {
                    "cycle": 7,
                    "WVALID": 1,
                    "WREADY": 1,
                    "WDATA": "0x55",
                    "WSTRB": "0xF",
                    "WLAST": 1,
                },
                {
                    "cycle": 8,
                    "BVALID": 1,
                    "BREADY": 1,
                    "BID": 5,
                    "BRESP": "OKAY",
                },
                {
                    "cycle": 9,
                    "BVALID": 1,
                    "BREADY": 1,
                    "BID": 4,
                    "BRESP": "OKAY",
                },
            ]
        }
    )

    assert result["status"] == "PASS"
    assert result["summary"]["completed_transactions"] == 4

    read_ids = [
        tx["id"]
        for tx in result["transactions"]
        if tx["direction"] == "READ"
    ]
    write_ids = [
        tx["id"]
        for tx in result["transactions"]
        if tx["direction"] == "WRITE"
    ]
    assert read_ids == [2, 1]
    assert write_ids == [5, 4]


def test_reports_wlast_early():
    result = analyze_axi4_trace(
        {
            "samples": [
                {
                    "AWVALID": 1,
                    "AWREADY": 1,
                    "AWADDR": "0x100",
                    "AWLEN": 3,
                    "AWSIZE": 2,
                    "AWBURST": "INCR",
                },
                {
                    "WVALID": 1,
                    "WREADY": 1,
                    "WDATA": 1,
                    "WSTRB": "0xF",
                    "WLAST": 0,
                },
                {
                    "WVALID": 1,
                    "WREADY": 1,
                    "WDATA": 2,
                    "WSTRB": "0xF",
                    "WLAST": 1,
                },
                {
                    "BVALID": 1,
                    "BREADY": 1,
                    "BRESP": "OKAY",
                },
            ]
        }
    )

    codes = {item["code"] for item in result["violations"]}
    assert result["status"] == "FAIL"
    assert "wlast_early" in codes


def test_reports_missing_and_late_rlast():
    result = analyze_axi4_trace(
        {
            "samples": [
                {
                    "ARVALID": 1,
                    "ARREADY": 1,
                    "ARADDR": "0x100",
                    "ARLEN": 1,
                    "ARSIZE": 2,
                    "ARBURST": "INCR",
                },
                {
                    "RVALID": 1,
                    "RREADY": 1,
                    "RDATA": 1,
                    "RRESP": "OKAY",
                    "RLAST": 0,
                },
                {
                    "RVALID": 1,
                    "RREADY": 1,
                    "RDATA": 2,
                    "RRESP": "OKAY",
                    "RLAST": 0,
                },
                {
                    "RVALID": 1,
                    "RREADY": 1,
                    "RDATA": 3,
                    "RRESP": "OKAY",
                    "RLAST": 1,
                },
            ]
        }
    )

    codes = {item["code"] for item in result["violations"]}
    assert result["status"] == "FAIL"
    assert "missing_rlast_on_final_beat" in codes
    assert "rlast_late" in codes


def test_checks_wrap_rules_and_4kb_boundary():
    wrap = analyze_axi4_trace(
        {
            "samples": [
                {
                    "AWVALID": 1,
                    "AWREADY": 1,
                    "AWADDR": "0x102",
                    "AWLEN": 2,
                    "AWSIZE": 2,
                    "AWBURST": "WRAP",
                },
                {
                    "WVALID": 1,
                    "WREADY": 1,
                    "WDATA": 1,
                    "WSTRB": "0xF",
                    "WLAST": 0,
                },
                {
                    "WVALID": 1,
                    "WREADY": 1,
                    "WDATA": 2,
                    "WSTRB": "0xF",
                    "WLAST": 0,
                },
                {
                    "WVALID": 1,
                    "WREADY": 1,
                    "WDATA": 3,
                    "WSTRB": "0xF",
                    "WLAST": 1,
                },
                {
                    "BVALID": 1,
                    "BREADY": 1,
                    "BRESP": "OKAY",
                },
            ]
        }
    )
    wrap_codes = {item["code"] for item in wrap["violations"]}
    assert "invalid_wrap_length" in wrap_codes
    assert "wrap_address_unaligned" in wrap_codes

    boundary = analyze_axi4_trace(
        {
            "samples": [
                {
                    "ARVALID": 1,
                    "ARREADY": 1,
                    "ARADDR": "0xFFC",
                    "ARLEN": 1,
                    "ARSIZE": 2,
                    "ARBURST": "INCR",
                },
                {
                    "RVALID": 1,
                    "RREADY": 1,
                    "RDATA": 1,
                    "RRESP": "OKAY",
                    "RLAST": 0,
                },
                {
                    "RVALID": 1,
                    "RREADY": 1,
                    "RDATA": 2,
                    "RRESP": "OKAY",
                    "RLAST": 1,
                },
            ]
        }
    )
    boundary_codes = {
        item["code"] for item in boundary["violations"]
    }
    assert "burst_crosses_4kb" in boundary_codes


def test_reports_stalled_payload_change():
    result = analyze_axi4_trace(
        {
            "samples": [
                {
                    "cycle": 10,
                    "AWVALID": 1,
                    "AWREADY": 0,
                    "AWADDR": "0x100",
                    "AWLEN": 0,
                    "AWSIZE": 2,
                    "AWBURST": "INCR",
                },
                {
                    "cycle": 11,
                    "AWVALID": 1,
                    "AWREADY": 1,
                    "AWADDR": "0x104",
                    "AWLEN": 0,
                    "AWSIZE": 2,
                    "AWBURST": "INCR",
                },
            ]
        }
    )

    codes = {item["code"] for item in result["violations"]}
    assert "payload_changed_while_stalled" in codes


def test_analyze_axi4_file_and_cli_write_report(
    tmp_path: Path,
    capsys,
):
    project = initialize_project(tmp_path / "demo")
    trace = project.root / "axi4.json"
    trace.write_text(
        json.dumps(
            {
                "source": "file-test",
                "samples": [
                    {
                        "ARVALID": 1,
                        "ARREADY": 1,
                        "ARID": 9,
                        "ARADDR": "0x40",
                        "ARLEN": 0,
                        "ARSIZE": 2,
                        "ARBURST": "INCR",
                    },
                    {
                        "RVALID": 1,
                        "RREADY": 1,
                        "RID": 9,
                        "RDATA": "0x1234",
                        "RRESP": "OKAY",
                        "RLAST": 1,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    result = analyze_axi4_file(project, trace)
    report = Path(result["report_path"])
    assert report.is_file()
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["protocol"] == "AXI4"
    assert payload["summary"]["completed_transactions"] == 1

    rc = main(
        [
            "--project",
            str(project.root),
            "axi4-analyze",
            str(trace),
        ]
    )
    assert rc == 0
    output = capsys.readouterr().out
    assert "AXI4 PASS" in output
    assert "1 completed transaction(s)" in output
