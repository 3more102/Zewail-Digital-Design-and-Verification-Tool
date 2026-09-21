import json
from pathlib import Path

from zddv.cli import main
from zddv.config import initialize_project
from zddv.protocols.axi4 import analyze_axi4_file, analyze_axi4_trace


def test_reconstructs_axi4_write_burst_with_id_and_addresses():
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
            ],
        }
    )

    assert result["status"] == "PASS"
    assert result["summary"]["completed_transactions"] == 1
    assert result["summary"]["writes"] == 1
    assert result["summary"]["accepted_write_beats"] == 4
    tx = result["transactions"][0]
    assert tx["id"] == 3
    assert tx["expected_beats"] == 4
    assert tx["completed_beats"] == 4
    assert tx["burst"] == "INCR"
    assert tx["beat_addresses"] == [0x100, 0x104, 0x108, 0x10C]
    assert [beat["data"] for beat in tx["write_beats"]] == [0x11, 0x22, 0x33, 0x44]


def test_allows_read_data_interleaving_across_ids_and_preserves_same_id_order():
    result = analyze_axi4_trace(
        {
            "samples": [
                {
                    "cycle": 0,
                    "ARVALID": 1,
                    "ARREADY": 1,
                    "ARID": 1,
                    "ARADDR": "0x200",
                    "ARLEN": 1,
                    "ARSIZE": 2,
                    "ARBURST": 1,
                },
                {
                    "cycle": 1,
                    "ARVALID": 1,
                    "ARREADY": 1,
                    "ARID": 2,
                    "ARADDR": "0x300",
                    "ARLEN": 0,
                    "ARSIZE": 2,
                    "ARBURST": 1,
                },
                {
                    "cycle": 2,
                    "RVALID": 1,
                    "RREADY": 1,
                    "RID": 1,
                    "RDATA": "0xAAAA",
                    "RRESP": "OKAY",
                    "RLAST": 0,
                },
                {
                    "cycle": 3,
                    "RVALID": 1,
                    "RREADY": 1,
                    "RID": 2,
                    "RDATA": "0xBBBB",
                    "RRESP": "OKAY",
                    "RLAST": 1,
                },
                {
                    "cycle": 4,
                    "RVALID": 1,
                    "RREADY": 1,
                    "RID": 1,
                    "RDATA": "0xCCCC",
                    "RRESP": "SLVERR",
                    "RLAST": 1,
                },
            ]
        }
    )

    assert result["status"] == "PASS"
    assert result["summary"]["reads"] == 2
    assert result["summary"]["accepted_read_beats"] == 3
    assert result["summary"]["error_responses"] == 1
    reads = {tx["id"]: tx for tx in result["transactions"]}
    assert [beat["data"] for beat in reads[1]["read_beats"]] == [0xAAAA, 0xCCCC]
    assert reads[1]["beat_addresses"] == [0x200, 0x204]
    assert reads[2]["read_beats"][0]["data"] == 0xBBBB


def test_reports_axi4_burst_geometry_violations():
    result = analyze_axi4_trace(
        {
            "samples": [
                {
                    "cycle": 0,
                    "ARVALID": 1,
                    "ARREADY": 1,
                    "ARID": 1,
                    "ARADDR": "0xFFC",
                    "ARLEN": 1,
                    "ARSIZE": 2,
                    "ARBURST": "INCR",
                },
                {
                    "cycle": 1,
                    "AWVALID": 1,
                    "AWREADY": 1,
                    "AWID": 2,
                    "AWADDR": "0x102",
                    "AWLEN": 2,
                    "AWSIZE": 2,
                    "AWBURST": "WRAP",
                },
            ]
        }
    )

    codes = {item["code"] for item in result["violations"]}
    assert result["status"] == "FAIL"
    assert "burst_crosses_4kb" in codes
    assert "invalid_wrap_length" in codes
    assert "wrap_address_unaligned" in codes


def test_reports_wlast_and_rlast_length_errors():
    result = analyze_axi4_trace(
        {
            "samples": [
                {
                    "cycle": 0,
                    "AWVALID": 1,
                    "AWREADY": 1,
                    "AWID": 4,
                    "AWADDR": "0x80",
                    "AWLEN": 3,
                    "AWSIZE": 2,
                    "AWBURST": "INCR",
                },
                {
                    "cycle": 1,
                    "WVALID": 1,
                    "WREADY": 1,
                    "WDATA": 1,
                    "WSTRB": 15,
                    "WLAST": 0,
                },
                {
                    "cycle": 2,
                    "WVALID": 1,
                    "WREADY": 1,
                    "WDATA": 2,
                    "WSTRB": 15,
                    "WLAST": 1,
                },
                {
                    "cycle": 3,
                    "BVALID": 1,
                    "BREADY": 1,
                    "BID": 4,
                    "BRESP": "OKAY",
                },
                {
                    "cycle": 4,
                    "ARVALID": 1,
                    "ARREADY": 1,
                    "ARID": 7,
                    "ARADDR": "0x400",
                    "ARLEN": 1,
                    "ARSIZE": 2,
                    "ARBURST": "INCR",
                },
                {
                    "cycle": 5,
                    "RVALID": 1,
                    "RREADY": 1,
                    "RID": 7,
                    "RDATA": 10,
                    "RRESP": "OKAY",
                    "RLAST": 0,
                },
                {
                    "cycle": 6,
                    "RVALID": 1,
                    "RREADY": 1,
                    "RID": 7,
                    "RDATA": 11,
                    "RRESP": "OKAY",
                    "RLAST": 0,
                },
            ]
        }
    )

    codes = {item["code"] for item in result["violations"]}
    assert result["status"] == "FAIL"
    assert "wlast_early" in codes
    assert "rlast_missing" in codes


def test_reports_channel_payload_change_while_stalled():
    result = analyze_axi4_trace(
        {
            "samples": [
                {
                    "cycle": 10,
                    "AWVALID": 1,
                    "AWREADY": 0,
                    "AWID": 1,
                    "AWADDR": "0x100",
                    "AWLEN": 0,
                    "AWSIZE": 2,
                    "AWBURST": "INCR",
                },
                {
                    "cycle": 11,
                    "AWVALID": 1,
                    "AWREADY": 1,
                    "AWID": 1,
                    "AWADDR": "0x104",
                    "AWLEN": 0,
                    "AWSIZE": 2,
                    "AWBURST": "INCR",
                },
            ]
        }
    )

    assert result["status"] == "FAIL"
    changed = [
        item for item in result["violations"]
        if item["code"] == "payload_changed_while_stalled"
    ]
    assert changed
    assert changed[0]["signal"] == "AWADDR"


def test_wrap_burst_addresses_are_reconstructed():
    result = analyze_axi4_trace(
        {
            "samples": [
                {
                    "cycle": 0,
                    "ARVALID": 1,
                    "ARREADY": 1,
                    "ARID": 5,
                    "ARADDR": "0x10C",
                    "ARLEN": 3,
                    "ARSIZE": 2,
                    "ARBURST": "WRAP",
                },
                {
                    "cycle": 1,
                    "RVALID": 1,
                    "RREADY": 1,
                    "RID": 5,
                    "RDATA": 1,
                    "RRESP": "OKAY",
                    "RLAST": 0,
                },
                {
                    "cycle": 2,
                    "RVALID": 1,
                    "RREADY": 1,
                    "RID": 5,
                    "RDATA": 2,
                    "RRESP": "OKAY",
                    "RLAST": 0,
                },
                {
                    "cycle": 3,
                    "RVALID": 1,
                    "RREADY": 1,
                    "RID": 5,
                    "RDATA": 3,
                    "RRESP": "OKAY",
                    "RLAST": 0,
                },
                {
                    "cycle": 4,
                    "RVALID": 1,
                    "RREADY": 1,
                    "RID": 5,
                    "RDATA": 4,
                    "RRESP": "OKAY",
                    "RLAST": 1,
                },
            ]
        }
    )

    assert result["status"] == "PASS"
    tx = result["transactions"][0]
    assert tx["beat_addresses"] == [0x10C, 0x100, 0x104, 0x108]


def test_axi4_file_and_cli_write_report(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    trace = project.root / "axi4.json"
    trace.write_text(
        json.dumps(
            {
                "source": "file-test",
                "samples": [
                    {
                        "cycle": 0,
                        "ARVALID": 1,
                        "ARREADY": 1,
                        "ARID": 9,
                        "ARADDR": "0x40",
                        "ARLEN": 0,
                        "ARSIZE": 2,
                        "ARBURST": "INCR",
                    },
                    {
                        "cycle": 1,
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
