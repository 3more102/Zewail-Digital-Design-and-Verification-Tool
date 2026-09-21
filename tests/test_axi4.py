import json
from pathlib import Path

from zddv.cli import main
from zddv.config import initialize_project
from zddv.protocols.axi4 import analyze_axi4_file, analyze_axi4_trace


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


def test_exclusive_read_write_sequence_tracks_success():
    result = analyze_axi4_trace(
        {"samples": [
            {"cycle": 0, "ARVALID": 1, "ARREADY": 1, "ARID": 7, "ARADDR": 0x100, "ARLEN": 0, "ARSIZE": 2, "ARBURST": "INCR", "ARLOCK": 1, "ARCACHE": 0, "ARPROT": 2},
            {"cycle": 1, "RVALID": 1, "RREADY": 1, "RID": 7, "RDATA": 0x11, "RRESP": "EXOKAY", "RLAST": 1},
            {"cycle": 2, "AWVALID": 1, "AWREADY": 1, "AWID": 7, "AWADDR": 0x100, "AWLEN": 0, "AWSIZE": 2, "AWBURST": "INCR", "AWLOCK": 1, "AWCACHE": 0, "AWPROT": 2},
            {"cycle": 3, "WVALID": 1, "WREADY": 1, "WDATA": 0x22, "WSTRB": 0xF, "WLAST": 1},
            {"cycle": 4, "BVALID": 1, "BREADY": 1, "BID": 7, "BRESP": "EXOKAY"},
        ]}
    )

    assert result["status"] == "PASS"
    assert result["summary"]["exclusive_reads"] == 1
    assert result["summary"]["exclusive_writes"] == 1
    assert result["summary"]["exclusive_write_successes"] == 1
    write = next(tx for tx in result["transactions"] if tx["direction"] == "WRITE")
    assert write["exclusive_sequence"]["observed"] is True
    assert write["exclusive_sequence"]["attributes_match"] is True
    assert write["exclusive_write_outcome"] == "SUCCESS"


def test_failed_exclusive_write_with_okay_is_legal():
    result = analyze_axi4_trace(
        {"samples": [
            {"cycle": 0, "ARVALID": 1, "ARREADY": 1, "ARID": 3, "ARADDR": 0x80, "ARLEN": 0, "ARSIZE": 2, "ARBURST": "INCR", "ARLOCK": 1},
            {"cycle": 1, "RVALID": 1, "RREADY": 1, "RID": 3, "RDATA": 0, "RRESP": "EXOKAY", "RLAST": 1},
            {"cycle": 2, "AWVALID": 1, "AWREADY": 1, "AWID": 3, "AWADDR": 0x80, "AWLEN": 0, "AWSIZE": 2, "AWBURST": "INCR", "AWLOCK": 1},
            {"cycle": 3, "WVALID": 1, "WREADY": 1, "WDATA": 1, "WSTRB": 0xF, "WLAST": 1},
            {"cycle": 4, "BVALID": 1, "BREADY": 1, "BID": 3, "BRESP": "OKAY"},
        ]}
    )

    assert result["status"] == "PASS"
    assert result["summary"]["exclusive_write_failures"] == 1
    write = next(tx for tx in result["transactions"] if tx["direction"] == "WRITE")
    assert write["exclusive_write_outcome"] == "FAILED"


def test_reports_exclusive_geometry_restrictions():
    result = analyze_axi4_trace(
        {"samples": [
            {"cycle": 0, "ARVALID": 1, "ARREADY": 1, "ARID": 1, "ARADDR": 0x104, "ARLEN": 1, "ARSIZE": 2, "ARBURST": "INCR", "ARLOCK": 1},
            {"cycle": 1, "ARVALID": 1, "ARREADY": 1, "ARID": 2, "ARADDR": 0x200, "ARLEN": 2, "ARSIZE": 2, "ARBURST": "INCR", "ARLOCK": 1},
            {"cycle": 2, "ARVALID": 1, "ARREADY": 1, "ARID": 3, "ARADDR": 0x400, "ARLEN": 16, "ARSIZE": 3, "ARBURST": "INCR", "ARLOCK": 1},
        ]}
    )

    codes = {item["code"] for item in result["violations"]}
    assert "exclusive_address_not_total_size_aligned" in codes
    assert "exclusive_transfer_size_not_power_of_two" in codes
    assert "exclusive_burst_too_long" in codes
    assert "exclusive_transfer_too_large" in codes


def test_reports_mixed_okay_exokay_on_exclusive_read():
    result = analyze_axi4_trace(
        {"samples": [
            {"cycle": 0, "ARVALID": 1, "ARREADY": 1, "ARID": 4, "ARADDR": 0x100, "ARLEN": 1, "ARSIZE": 2, "ARBURST": "INCR", "ARLOCK": 1},
            {"cycle": 1, "RVALID": 1, "RREADY": 1, "RID": 4, "RDATA": 1, "RRESP": "EXOKAY", "RLAST": 0},
            {"cycle": 2, "RVALID": 1, "RREADY": 1, "RID": 4, "RDATA": 2, "RRESP": "OKAY", "RLAST": 1},
        ]}
    )

    codes = {item["code"] for item in result["violations"]}
    assert "mixed_exclusive_read_responses" in codes


def test_reports_exclusive_write_started_before_read_completion():
    result = analyze_axi4_trace(
        {"samples": [
            {"cycle": 0, "ARVALID": 1, "ARREADY": 1, "ARID": 5, "ARADDR": 0x100, "ARLEN": 0, "ARSIZE": 2, "ARBURST": "INCR", "ARLOCK": 1},
            {"cycle": 1, "AWVALID": 1, "AWREADY": 1, "AWID": 5, "AWADDR": 0x100, "AWLEN": 0, "AWSIZE": 2, "AWBURST": "INCR", "AWLOCK": 1},
            {"cycle": 2, "RVALID": 1, "RREADY": 1, "RID": 5, "RDATA": 0, "RRESP": "EXOKAY", "RLAST": 1},
            {"cycle": 3, "WVALID": 1, "WREADY": 1, "WDATA": 1, "WSTRB": 0xF, "WLAST": 1},
            {"cycle": 4, "BVALID": 1, "BREADY": 1, "BID": 5, "BRESP": "OKAY"},
        ]}
    )

    codes = {item["code"] for item in result["violations"]}
    assert "exclusive_write_before_read_complete" in codes


def test_reports_exokay_for_mismatched_observed_exclusive_sequence():
    result = analyze_axi4_trace(
        {"samples": [
            {"cycle": 0, "ARVALID": 1, "ARREADY": 1, "ARID": 6, "ARADDR": 0x100, "ARLEN": 0, "ARSIZE": 2, "ARBURST": "INCR", "ARLOCK": 1},
            {"cycle": 1, "RVALID": 1, "RREADY": 1, "RID": 6, "RDATA": 0, "RRESP": "EXOKAY", "RLAST": 1},
            {"cycle": 2, "AWVALID": 1, "AWREADY": 1, "AWID": 6, "AWADDR": 0x104, "AWLEN": 0, "AWSIZE": 2, "AWBURST": "INCR", "AWLOCK": 1},
            {"cycle": 3, "WVALID": 1, "WREADY": 1, "WDATA": 1, "WSTRB": 0xF, "WLAST": 1},
            {"cycle": 4, "BVALID": 1, "BREADY": 1, "BID": 6, "BRESP": "EXOKAY"},
        ]}
    )

    codes = {item["code"] for item in result["violations"]}
    assert "exokay_with_mismatched_exclusive_sequence" in codes


def test_reports_exokay_after_matching_exclusive_read_returned_okay():
    result = analyze_axi4_trace(
        {"samples": [
            {"cycle": 0, "ARVALID": 1, "ARREADY": 1, "ARID": 8, "ARADDR": 0x100, "ARLEN": 0, "ARSIZE": 2, "ARBURST": "INCR", "ARLOCK": 1},
            {"cycle": 1, "RVALID": 1, "RREADY": 1, "RID": 8, "RDATA": 0, "RRESP": "OKAY", "RLAST": 1},
            {"cycle": 2, "AWVALID": 1, "AWREADY": 1, "AWID": 8, "AWADDR": 0x100, "AWLEN": 0, "AWSIZE": 2, "AWBURST": "INCR", "AWLOCK": 1},
            {"cycle": 3, "WVALID": 1, "WREADY": 1, "WDATA": 1, "WSTRB": 0xF, "WLAST": 1},
            {"cycle": 4, "BVALID": 1, "BREADY": 1, "BID": 8, "BRESP": "EXOKAY"},
        ]}
    )

    codes = {item["code"] for item in result["violations"]}
    assert "exokay_after_unsupported_exclusive_read" in codes


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
