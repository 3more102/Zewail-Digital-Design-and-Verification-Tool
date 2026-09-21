import json
from pathlib import Path

from zddv.cli import main
from zddv.config import initialize_project
from zddv.protocols.axi4 import analyze_axi4_file, analyze_axi4_trace


def test_reconstructs_axi4_bursts_with_ids_and_interleaved_reads():
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
                    "AWLEN": 1,
                    "AWSIZE": 2,
                    "AWBURST": "INCR",
                    "ARVALID": 1,
                    "ARREADY": 1,
                    "ARID": 5,
                    "ARADDR": "0x200",
                    "ARLEN": 1,
                    "ARSIZE": 2,
                    "ARBURST": "INCR",
                },
                {
                    "cycle": 1,
                    "WVALID": 1,
                    "WREADY": 1,
                    "WDATA": "0xAAAA",
                    "WSTRB": "0xF",
                    "WLAST": 0,
                    "ARVALID": 1,
                    "ARREADY": 1,
                    "ARID": 6,
                    "ARADDR": "0x300",
                    "ARLEN": 0,
                    "ARSIZE": 2,
                    "ARBURST": "INCR",
                },
                {
                    "cycle": 2,
                    "WVALID": 1,
                    "WREADY": 1,
                    "WDATA": "0xBBBB",
                    "WSTRB": "0xF",
                    "WLAST": 1,
                    "RVALID": 1,
                    "RREADY": 1,
                    "RID": 6,
                    "RDATA": "0x66",
                    "RRESP": "OKAY",
                    "RLAST": 1,
                },
                {
                    "cycle": 3,
                    "BVALID": 1,
                    "BREADY": 1,
                    "BID": 3,
                    "BRESP": "OKAY",
                    "RVALID": 1,
                    "RREADY": 1,
                    "RID": 5,
                    "RDATA": "0x51",
                    "RRESP": "OKAY",
                    "RLAST": 0,
                },
                {
                    "cycle": 4,
                    "RVALID": 1,
                    "RREADY": 1,
                    "RID": 5,
                    "RDATA": "0x52",
                    "RRESP": "SLVERR",
                    "RLAST": 1,
                },
            ],
        }
    )

    assert result["status"] == "PASS"
    assert result["summary"]["issued_write_bursts"] == 1
    assert result["summary"]["issued_read_bursts"] == 2
    assert result["summary"]["completed_transactions"] == 3
    assert result["summary"]["write_beats"] == 2
    assert result["summary"]["read_beats"] == 3
    assert result["summary"]["error_response_beats"] == 1

    write = next(tx for tx in result["transactions"] if tx["direction"] == "WRITE")
    reads = [tx for tx in result["transactions"] if tx["direction"] == "READ"]
    assert write["id"] == 3
    assert write["address"] == 0x100
    assert write["beat_count"] == 2
    assert [beat["data"] for beat in write["beats"]] == [0xAAAA, 0xBBBB]

    by_id = {tx["id"]: tx for tx in reads}
    assert by_id[6]["beat_count"] == 1
    assert by_id[5]["beat_count"] == 2
    assert [beat["data"] for beat in by_id[5]["beats"]] == [0x51, 0x52]


def test_reports_last_and_burst_legality_violations():
    result = analyze_axi4_trace(
        {
            "samples": [
                {
                    "cycle": 0,
                    "AWVALID": 1,
                    "AWREADY": 1,
                    "AWADDR": "0xFF0",
                    "AWLEN": 3,
                    "AWSIZE": 3,
                    "AWBURST": "INCR",
                },
                {
                    "cycle": 1,
                    "WVALID": 1,
                    "WREADY": 1,
                    "WDATA": 1,
                    "WSTRB": "0xFF",
                    "WLAST": 1,
                },
                {
                    "cycle": 2,
                    "WVALID": 1,
                    "WREADY": 1,
                    "WDATA": 2,
                    "WSTRB": "0xFF",
                    "WLAST": 0,
                },
                {
                    "cycle": 3,
                    "WVALID": 1,
                    "WREADY": 1,
                    "WDATA": 3,
                    "WSTRB": "0xFF",
                    "WLAST": 0,
                },
                {
                    "cycle": 4,
                    "WVALID": 1,
                    "WREADY": 1,
                    "WDATA": 4,
                    "WSTRB": "0xFF",
                    "WLAST": 0,
                },
            ]
        }
    )

    codes = {item["code"] for item in result["violations"]}
    assert result["status"] == "FAIL"
    assert "burst_crosses_4kb_boundary" in codes
    assert "wlast_early" in codes
    assert "wlast_missing" in codes
    assert "missing_write_response" in codes


def test_wrap_length_and_response_id_checks():
    result = analyze_axi4_trace(
        {
            "samples": [
                {
                    "cycle": 0,
                    "ARVALID": 1,
                    "ARREADY": 1,
                    "ARID": 2,
                    "ARADDR": "0x80",
                    "ARLEN": 2,
                    "ARSIZE": 2,
                    "ARBURST": "WRAP",
                },
                {
                    "cycle": 1,
                    "RVALID": 1,
                    "RREADY": 1,
                    "RID": 7,
                    "RDATA": 0,
                    "RRESP": "OKAY",
                    "RLAST": 1,
                },
            ]
        }
    )

    codes = {item["code"] for item in result["violations"]}
    assert result["status"] == "FAIL"
    assert "invalid_wrap_length" in codes
    assert "read_data_unknown_id" in codes
    assert "incomplete_read_burst" in codes


def test_valid_and_payload_stability_is_checked():
    result = analyze_axi4_trace(
        {
            "samples": [
                {
                    "cycle": 0,
                    "AWVALID": 1,
                    "AWREADY": 0,
                    "AWID": 1,
                    "AWADDR": "0x100",
                    "AWLEN": 0,
                    "AWSIZE": 2,
                    "AWBURST": "INCR",
                },
                {
                    "cycle": 1,
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

    codes = {item["code"] for item in result["violations"]}
    assert "payload_changed_while_stalled" in codes


def test_analyze_axi4_file_and_cli_write_report(tmp_path: Path, capsys):
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
                        "ARID": 4,
                        "ARADDR": "0x40",
                        "ARLEN": 0,
                        "ARSIZE": 2,
                        "ARBURST": "INCR",
                    },
                    {
                        "cycle": 1,
                        "RVALID": 1,
                        "RREADY": 1,
                        "RID": 4,
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
