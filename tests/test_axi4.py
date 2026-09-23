import json
from pathlib import Path

import pytest

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



def test_accepts_matching_axi4_exclusive_sequence():
    result = analyze_axi4_trace(
        {"samples": [
            {
                "cycle": 0,
                "ARVALID": 1,
                "ARREADY": 1,
                "ARID": 7,
                "ARADDR": 0x100,
                "ARLEN": 1,
                "ARSIZE": 2,
                "ARBURST": "INCR",
                "ARLOCK": 1,
                "ARCACHE": 0,
                "ARPROT": 2,
                "ARREGION": 1,
            },
            {
                "cycle": 1,
                "RVALID": 1,
                "RREADY": 1,
                "RID": 7,
                "RDATA": 0xAA,
                "RRESP": "EXOKAY",
                "RLAST": 0,
            },
            {
                "cycle": 2,
                "RVALID": 1,
                "RREADY": 1,
                "RID": 7,
                "RDATA": 0xBB,
                "RRESP": "EXOKAY",
                "RLAST": 1,
            },
            {
                "cycle": 3,
                "AWVALID": 1,
                "AWREADY": 1,
                "AWID": 7,
                "AWADDR": 0x100,
                "AWLEN": 1,
                "AWSIZE": 2,
                "AWBURST": "INCR",
                "AWLOCK": 1,
                "AWCACHE": 0,
                "AWPROT": 2,
                "AWREGION": 1,
            },
            {
                "cycle": 4,
                "WVALID": 1,
                "WREADY": 1,
                "WDATA": 0x11,
                "WSTRB": 0xF,
                "WLAST": 0,
            },
            {
                "cycle": 5,
                "WVALID": 1,
                "WREADY": 1,
                "WDATA": 0x22,
                "WSTRB": 0xF,
                "WLAST": 1,
            },
            {
                "cycle": 6,
                "BVALID": 1,
                "BREADY": 1,
                "BID": 7,
                "BRESP": "EXOKAY",
            },
        ]}
    )

    assert result["status"] == "PASS"
    assert result["summary"]["exclusive_reads"] == 1
    assert result["summary"]["exclusive_writes"] == 1
    assert result["summary"]["matched_exclusive_writes"] == 1

    read = next(
        tx for tx in result["transactions"]
        if tx["direction"] == "READ"
    )
    write = next(
        tx for tx in result["transactions"]
        if tx["direction"] == "WRITE"
    )
    assert read["exclusive"] is True
    assert read["exclusive_total_bytes"] == 8
    assert read["exclusive_response_class"] == "EXOKAY"
    assert write["exclusive"] is True
    assert write["exclusive_pair_status"] == "matched"


def test_reports_axi4_exclusive_size_and_alignment_restrictions():
    result = analyze_axi4_trace(
        {"samples": [
            {
                "cycle": 0,
                "ARVALID": 1,
                "ARREADY": 1,
                "ARID": 1,
                "ARADDR": 0x104,
                "ARLEN": 2,
                "ARSIZE": 2,
                "ARBURST": "INCR",
                "ARLOCK": 1,
            },
            {
                "cycle": 1,
                "ARVALID": 1,
                "ARREADY": 1,
                "ARID": 2,
                "ARADDR": 0x200,
                "ARLEN": 16,
                "ARSIZE": 3,
                "ARBURST": "INCR",
                "ARLOCK": 1,
            },
        ]}
    )

    codes = {item["code"] for item in result["violations"]}
    assert result["status"] == "FAIL"
    assert "exclusive_size_not_power_of_two" in codes
    assert "exclusive_address_unaligned" in codes
    assert "exclusive_burst_too_long" in codes
    assert "exclusive_size_exceeds_128_bytes" in codes


def test_reports_exclusive_write_started_before_matching_read_completion():
    result = analyze_axi4_trace(
        {"samples": [
            {
                "cycle": 0,
                "ARVALID": 1,
                "ARREADY": 1,
                "ARID": 3,
                "ARADDR": 0x100,
                "ARLEN": 1,
                "ARSIZE": 2,
                "ARBURST": "INCR",
                "ARLOCK": 1,
            },
            {
                "cycle": 1,
                "RVALID": 1,
                "RREADY": 1,
                "RID": 3,
                "RDATA": 1,
                "RRESP": "EXOKAY",
                "RLAST": 0,
                "AWVALID": 1,
                "AWREADY": 1,
                "AWID": 3,
                "AWADDR": 0x100,
                "AWLEN": 1,
                "AWSIZE": 2,
                "AWBURST": "INCR",
                "AWLOCK": 1,
            },
            {
                "cycle": 2,
                "RVALID": 1,
                "RREADY": 1,
                "RID": 3,
                "RDATA": 2,
                "RRESP": "EXOKAY",
                "RLAST": 1,
                "WVALID": 1,
                "WREADY": 1,
                "WDATA": 1,
                "WSTRB": 0xF,
                "WLAST": 0,
            },
            {
                "cycle": 3,
                "WVALID": 1,
                "WREADY": 1,
                "WDATA": 2,
                "WSTRB": 0xF,
                "WLAST": 1,
            },
            {
                "cycle": 4,
                "BVALID": 1,
                "BREADY": 1,
                "BID": 3,
                "BRESP": "OKAY",
            },
        ]}
    )

    codes = {item["code"] for item in result["violations"]}
    assert "exclusive_write_before_read_complete" in codes


def test_reports_same_cycle_exclusive_write_before_read_completion():
    result = analyze_axi4_trace(
        {"samples": [
            {
                "cycle": 0,
                "ARVALID": 1,
                "ARREADY": 1,
                "ARID": 13,
                "ARADDR": 0x100,
                "ARLEN": 0,
                "ARSIZE": 2,
                "ARBURST": "INCR",
                "ARLOCK": 1,
                "AWVALID": 1,
                "AWREADY": 1,
                "AWID": 13,
                "AWADDR": 0x100,
                "AWLEN": 0,
                "AWSIZE": 2,
                "AWBURST": "INCR",
                "AWLOCK": 1,
            },
            {
                "cycle": 1,
                "RVALID": 1,
                "RREADY": 1,
                "RID": 13,
                "RDATA": 0,
                "RRESP": "EXOKAY",
                "RLAST": 1,
                "WVALID": 1,
                "WREADY": 1,
                "WDATA": 1,
                "WSTRB": 0xF,
                "WLAST": 1,
            },
            {
                "cycle": 2,
                "BVALID": 1,
                "BREADY": 1,
                "BID": 13,
                "BRESP": "OKAY",
            },
        ]}
    )

    codes = {item["code"] for item in result["violations"]}
    assert "exclusive_write_before_read_complete" in codes


def test_reports_mixed_okay_and_exokay_on_exclusive_read():
    result = analyze_axi4_trace(
        {"samples": [
            {
                "cycle": 0,
                "ARVALID": 1,
                "ARREADY": 1,
                "ARID": 4,
                "ARADDR": 0x100,
                "ARLEN": 1,
                "ARSIZE": 2,
                "ARBURST": "INCR",
                "ARLOCK": 1,
            },
            {
                "cycle": 1,
                "RVALID": 1,
                "RREADY": 1,
                "RID": 4,
                "RDATA": 1,
                "RRESP": "EXOKAY",
                "RLAST": 0,
            },
            {
                "cycle": 2,
                "RVALID": 1,
                "RREADY": 1,
                "RID": 4,
                "RDATA": 2,
                "RRESP": "OKAY",
                "RLAST": 1,
            },
        ]}
    )

    codes = {item["code"] for item in result["violations"]}
    assert result["status"] == "FAIL"
    assert "exclusive_read_mixed_okay_exokay" in codes


def test_rejects_exokay_for_normal_or_unmatched_exclusive_write():
    normal = analyze_axi4_trace(
        {"samples": [
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
                "RDATA": 0,
                "RRESP": "EXOKAY",
                "RLAST": 1,
            },
        ]}
    )
    assert "exokay_without_exclusive_request" in {
        item["code"] for item in normal["violations"]
    }

    unmatched = analyze_axi4_trace(
        {"samples": [
            {
                "cycle": 0,
                "AWVALID": 1,
                "AWREADY": 1,
                "AWID": 9,
                "AWADDR": 0x80,
                "AWLEN": 0,
                "AWSIZE": 2,
                "AWBURST": "INCR",
                "AWLOCK": 1,
            },
            {
                "cycle": 1,
                "WVALID": 1,
                "WREADY": 1,
                "WDATA": 0x55,
                "WSTRB": 0xF,
                "WLAST": 1,
            },
            {
                "cycle": 2,
                "BVALID": 1,
                "BREADY": 1,
                "BID": 9,
                "BRESP": "EXOKAY",
            },
        ]}
    )
    assert "exokay_without_matching_exclusive_read" in {
        item["code"] for item in unmatched["violations"]
    }


def test_unmatched_exclusive_write_with_okay_is_not_protocol_failure():
    result = analyze_axi4_trace(
        {"samples": [
            {
                "cycle": 0,
                "AWVALID": 1,
                "AWREADY": 1,
                "AWID": 12,
                "AWADDR": 0x80,
                "AWLEN": 0,
                "AWSIZE": 2,
                "AWBURST": "INCR",
                "AWLOCK": 1,
            },
            {
                "cycle": 1,
                "WVALID": 1,
                "WREADY": 1,
                "WDATA": 0x55,
                "WSTRB": 0xF,
                "WLAST": 1,
            },
            {
                "cycle": 2,
                "BVALID": 1,
                "BREADY": 1,
                "BID": 12,
                "BRESP": "OKAY",
            },
        ]}
    )

    assert result["status"] == "PASS"
    write = result["transactions"][0]
    assert write["exclusive"] is True
    assert write["exclusive_pair_status"] == "no_prior_exclusive_read"


def test_validates_axi4_address_sideband_widths():
    result = analyze_axi4_trace(
        {"samples": [
            {
                "cycle": 0,
                "AWVALID": 1,
                "AWREADY": 1,
                "AWID": 1,
                "AWADDR": 0x100,
                "AWLEN": 0,
                "AWSIZE": 2,
                "AWBURST": "INCR",
                "AWCACHE": 16,
                "AWPROT": 8,
                "AWQOS": 16,
                "AWREGION": 16,
            },
            {
                "cycle": 1,
                "WVALID": 1,
                "WREADY": 1,
                "WDATA": 0xAA,
                "WSTRB": 0xF,
                "WLAST": 1,
            },
            {
                "cycle": 2,
                "BVALID": 1,
                "BREADY": 1,
                "BID": 1,
                "BRESP": "OKAY",
            },
        ]}
    )

    sideband_violations = [
        item for item in result["violations"]
        if item["code"] == "invalid_address_sideband"
    ]
    assert result["status"] == "FAIL"
    assert {item["signal"] for item in sideband_violations} == {
        "AWCACHE", "AWPROT", "AWQOS", "AWREGION"
    }


def test_reports_axregion_change_inside_same_4kb_address_space():
    result = analyze_axi4_trace(
        {"samples": [
            {
                "cycle": 0,
                "ARVALID": 1,
                "ARREADY": 1,
                "ARID": 1,
                "ARADDR": 0x100,
                "ARLEN": 0,
                "ARSIZE": 2,
                "ARBURST": "INCR",
                "ARREGION": 2,
            },
            {
                "cycle": 1,
                "RVALID": 1,
                "RREADY": 1,
                "RID": 1,
                "RDATA": 0x11,
                "RRESP": "OKAY",
                "RLAST": 1,
            },
            {
                "cycle": 2,
                "ARVALID": 1,
                "ARREADY": 1,
                "ARID": 2,
                "ARADDR": 0x180,
                "ARLEN": 0,
                "ARSIZE": 2,
                "ARBURST": "INCR",
                "ARREGION": 3,
            },
            {
                "cycle": 3,
                "RVALID": 1,
                "RREADY": 1,
                "RID": 2,
                "RDATA": 0x22,
                "RRESP": "OKAY",
                "RLAST": 1,
            },
        ]}
    )

    violation = next(
        item for item in result["violations"]
        if item["code"] == "region_changed_within_4kb"
    )
    assert result["status"] == "FAIL"
    assert violation["signal"] == "ARREGION"
    assert violation["expected"] == 2
    assert violation["actual"] == 3


def test_accepts_valid_address_sidebands_and_preserves_qos():
    result = analyze_axi4_trace(
        {"samples": [
            {
                "cycle": 0,
                "ARVALID": 1,
                "ARREADY": 1,
                "ARID": 4,
                "ARADDR": 0x240,
                "ARLEN": 0,
                "ARSIZE": 2,
                "ARBURST": "INCR",
                "ARCACHE": 0xF,
                "ARPROT": 0x7,
                "ARQOS": 0xA,
                "ARREGION": 0x5,
            },
            {
                "cycle": 1,
                "RVALID": 1,
                "RREADY": 1,
                "RID": 4,
                "RDATA": 0x33,
                "RRESP": "OKAY",
                "RLAST": 1,
            },
        ]}
    )

    assert result["status"] == "PASS"
    tx = result["transactions"][0]
    assert tx["cache"] == 0xF
    assert tx["cache_attributes"] == {
        "encoding": 0xF,
        "direction": "read",
        "bufferable": True,
        "modifiable": True,
        "read_allocate": True,
        "write_allocate": True,
        "allocate": True,
        "other_allocate": True,
        "allocate_bit": 2,
        "other_allocate_bit": 3,
        "cache_lookup_required": True,
        "memory_class": "write_back",
        "reserved": False,
    }
    assert tx["prot"] == 0x7
    assert tx["prot_attributes"] == {
        "encoding": 0x7,
        "privileged": True,
        "non_secure": True,
        "instruction": True,
        "privilege_class": "privileged",
        "security_class": "non-secure",
        "access_class": "instruction",
        "access_is_hint": True,
    }
    assert tx["qos"] == 0xA
    assert tx["qos_attributes"] == {
        "encoding": 0xA,
        "is_default_no_qos": False,
        "recommended_priority": 0xA,
        "higher_value_recommended_higher_priority": True,
        "exact_use_protocol_defined": False,
        "axi_ordering_rules_take_precedence": True,
    }
    assert tx["region"] == 0x5
    assert tx["arcache"] == 0xF
    assert tx["arprot"] == 0x7
    assert tx["arqos"] == 0xA
    assert tx["arregion"] == 0x5


def test_preserves_valid_write_address_sidebands():
    result = analyze_axi4_trace(
        {"samples": [
            {
                "cycle": 0,
                "AWVALID": 1,
                "AWREADY": 1,
                "AWID": 6,
                "AWADDR": 0x300,
                "AWLEN": 0,
                "AWSIZE": 2,
                "AWBURST": "INCR",
                "AWCACHE": 0x3,
                "AWPROT": 0x2,
                "AWQOS": 0xC,
                "AWREGION": 0x7,
            },
            {
                "cycle": 1,
                "WVALID": 1,
                "WREADY": 1,
                "WDATA": 0x55,
                "WSTRB": 0xF,
                "WLAST": 1,
            },
            {
                "cycle": 2,
                "BVALID": 1,
                "BREADY": 1,
                "BID": 6,
                "BRESP": "OKAY",
            },
        ]}
    )

    assert result["status"] == "PASS"
    tx = result["transactions"][0]
    assert tx["cache"] == 0x3
    assert tx["prot"] == 0x2
    assert tx["prot_attributes"] == {
        "encoding": 0x2,
        "privileged": False,
        "non_secure": True,
        "instruction": False,
        "privilege_class": "unprivileged",
        "security_class": "non-secure",
        "access_class": "data",
        "access_is_hint": True,
    }
    assert tx["qos"] == 0xC
    assert tx["qos_attributes"] == {
        "encoding": 0xC,
        "is_default_no_qos": False,
        "recommended_priority": 0xC,
        "higher_value_recommended_higher_priority": True,
        "exact_use_protocol_defined": False,
        "axi_ordering_rules_take_precedence": True,
    }
    assert tx["region"] == 0x7
    assert tx["awcache"] == 0x3
    assert tx["awprot"] == 0x2
    assert tx["awqos"] == 0xC
    assert tx["awregion"] == 0x7



def test_rejects_reserved_axi4_cache_encodings():
    result = analyze_axi4_trace(
        {"samples": [
            {
                "cycle": 0,
                "AWVALID": 1,
                "AWREADY": 1,
                "AWID": 1,
                "AWADDR": 0x100,
                "AWLEN": 0,
                "AWSIZE": 2,
                "AWBURST": "INCR",
                "AWCACHE": 0x4,
            },
            {
                "cycle": 1,
                "WVALID": 1,
                "WREADY": 1,
                "WDATA": 0xAA,
                "WSTRB": 0xF,
                "WLAST": 1,
            },
            {
                "cycle": 2,
                "BVALID": 1,
                "BREADY": 1,
                "BID": 1,
                "BRESP": "OKAY",
            },
            {
                "cycle": 3,
                "ARVALID": 1,
                "ARREADY": 1,
                "ARID": 2,
                "ARADDR": 0x200,
                "ARLEN": 0,
                "ARSIZE": 2,
                "ARBURST": "INCR",
                "ARCACHE": 0x8,
            },
            {
                "cycle": 4,
                "RVALID": 1,
                "RREADY": 1,
                "RID": 2,
                "RDATA": 0x55,
                "RRESP": "OKAY",
                "RLAST": 1,
            },
        ]}
    )

    cache_violations = [
        item for item in result["violations"]
        if item["code"] == "reserved_cache_encoding"
    ]
    assert result["status"] == "FAIL"
    assert {item["signal"] for item in cache_violations} == {
        "AWCACHE", "ARCACHE"
    }
    assert {item["actual"] for item in cache_violations} == {0x4, 0x8}


def test_accepts_axi4_legacy_compatible_cache_encodings():
    result = analyze_axi4_trace(
        {"samples": [
            {
                "cycle": 0,
                "ARVALID": 1,
                "ARREADY": 1,
                "ARID": 3,
                "ARADDR": 0x400,
                "ARLEN": 0,
                "ARSIZE": 2,
                "ARBURST": "INCR",
                "ARCACHE": 0x6,
            },
            {
                "cycle": 1,
                "RVALID": 1,
                "RREADY": 1,
                "RID": 3,
                "RDATA": 0x12,
                "RRESP": "OKAY",
                "RLAST": 1,
            },
        ]}
    )

    assert result["status"] == "PASS"
    attrs = result["transactions"][0]["cache_attributes"]
    assert attrs["encoding"] == 0x6
    assert attrs["modifiable"] is True
    assert attrs["read_allocate"] is True
    assert attrs["reserved"] is False

def test_decodes_axi4_cache_allocate_bits_by_channel_direction():
    result = analyze_axi4_trace(
        {"samples": [
            {
                "cycle": 0,
                "AWVALID": 1,
                "AWREADY": 1,
                "AWID": 1,
                "AWADDR": 0x100,
                "AWLEN": 0,
                "AWSIZE": 2,
                "AWBURST": "INCR",
                "AWCACHE": 0x6,
                "ARVALID": 1,
                "ARREADY": 1,
                "ARID": 2,
                "ARADDR": 0x200,
                "ARLEN": 0,
                "ARSIZE": 2,
                "ARBURST": "INCR",
                "ARCACHE": 0x6,
            },
            {
                "cycle": 1,
                "WVALID": 1,
                "WREADY": 1,
                "WDATA": 0x11,
                "WSTRB": 0xF,
                "WLAST": 1,
                "RVALID": 1,
                "RREADY": 1,
                "RID": 2,
                "RDATA": 0x22,
                "RRESP": "OKAY",
                "RLAST": 1,
            },
            {
                "cycle": 2,
                "BVALID": 1,
                "BREADY": 1,
                "BID": 1,
                "BRESP": "OKAY",
            },
        ]}
    )

    assert result["status"] == "PASS"
    write_tx = next(
        tx for tx in result["transactions"] if tx["direction"] == "WRITE"
    )
    read_tx = next(
        tx for tx in result["transactions"] if tx["direction"] == "READ"
    )

    write_cache = write_tx["cache_attributes"]
    assert write_cache["encoding"] == 0x6
    assert write_cache["direction"] == "write"
    assert write_cache["allocate"] is False
    assert write_cache["other_allocate"] is True
    assert write_cache["allocate_bit"] == 3
    assert write_cache["other_allocate_bit"] == 2
    assert write_cache["memory_class"] == "write_through"
    assert write_cache["read_allocate"] is True
    assert write_cache["write_allocate"] is False

    read_cache = read_tx["cache_attributes"]
    assert read_cache["encoding"] == 0x6
    assert read_cache["direction"] == "read"
    assert read_cache["allocate"] is True
    assert read_cache["other_allocate"] is False
    assert read_cache["allocate_bit"] == 2
    assert read_cache["other_allocate_bit"] == 3
    assert read_cache["memory_class"] == "write_through"
    assert read_cache["read_allocate"] is True
    assert read_cache["write_allocate"] is False


def test_decodes_axi4_cache_memory_classes_without_inventing_topology():
    cases = {
        0x0: "device_non_bufferable",
        0x1: "device_bufferable",
        0x2: "normal_non_cacheable_non_bufferable",
        0x3: "normal_non_cacheable_bufferable",
        0xA: "write_through",
        0xB: "write_back",
        0xE: "write_through",
        0xF: "write_back",
    }
    for encoding, memory_class in cases.items():
        result = analyze_axi4_trace(
            {"samples": [
                {
                    "cycle": 0,
                    "ARVALID": 1,
                    "ARREADY": 1,
                    "ARID": encoding,
                    "ARADDR": 0x400 + encoding * 0x10,
                    "ARLEN": 0,
                    "ARSIZE": 2,
                    "ARBURST": "INCR",
                    "ARCACHE": encoding,
                },
                {
                    "cycle": 1,
                    "RVALID": 1,
                    "RREADY": 1,
                    "RID": encoding,
                    "RDATA": 0x55,
                    "RRESP": "OKAY",
                    "RLAST": 1,
                },
            ]}
        )
        assert result["status"] == "PASS"
        attrs = result["transactions"][0]["cache_attributes"]
        assert attrs["memory_class"] == memory_class
        assert attrs["reserved"] is False


def test_preserves_optional_axi4_user_sidebands_without_interpreting_them():
    result = analyze_axi4_trace(
        {"samples": [
            {
                "cycle": 0,
                "AWVALID": 1,
                "AWREADY": 1,
                "AWID": 1,
                "AWADDR": 0x100,
                "AWLEN": 0,
                "AWSIZE": 2,
                "AWBURST": "INCR",
                "AWUSER": 0xA,
                "ARVALID": 1,
                "ARREADY": 1,
                "ARID": 2,
                "ARADDR": 0x200,
                "ARLEN": 0,
                "ARSIZE": 2,
                "ARBURST": "INCR",
                "ARUSER": "read-tag",
            },
            {
                "cycle": 1,
                "WVALID": 1,
                "WREADY": 1,
                "WDATA": 0x55,
                "WSTRB": 0xF,
                "WLAST": 1,
                "WUSER": 0x3,
                "RVALID": 1,
                "RREADY": 1,
                "RID": 2,
                "RDATA": 0x66,
                "RRESP": "OKAY",
                "RLAST": 1,
                "RUSER": 0x12,
            },
            {
                "cycle": 2,
                "BVALID": 1,
                "BREADY": 1,
                "BID": 1,
                "BRESP": "OKAY",
                "BUSER": "write-response-tag",
            },
        ]}
    )

    assert result["status"] == "PASS"
    write = next(tx for tx in result["transactions"] if tx["direction"] == "WRITE")
    read = next(tx for tx in result["transactions"] if tx["direction"] == "READ")
    assert write["awuser"] == 0xA
    assert write["wuser"] == [0x3]
    assert write["buser"] == "write-response-tag"
    assert read["aruser"] == "read-tag"
    assert read["ruser"] == [0x12]


def test_checks_user_sideband_stability_while_channel_is_stalled():
    result = analyze_axi4_trace(
        {"samples": [
            {
                "cycle": 0,
                "AWVALID": 1,
                "AWREADY": 0,
                "AWID": 3,
                "AWADDR": 0x300,
                "AWLEN": 0,
                "AWSIZE": 2,
                "AWBURST": "INCR",
                "AWUSER": 1,
            },
            {
                "cycle": 1,
                "AWVALID": 1,
                "AWREADY": 0,
                "AWID": 3,
                "AWADDR": 0x300,
                "AWLEN": 0,
                "AWSIZE": 2,
                "AWBURST": "INCR",
                "AWUSER": 2,
            },
            {
                "cycle": 2,
                "AWVALID": 1,
                "AWREADY": 1,
                "AWID": 3,
                "AWADDR": 0x300,
                "AWLEN": 0,
                "AWSIZE": 2,
                "AWBURST": "INCR",
                "AWUSER": 1,
            },
            {
                "cycle": 3,
                "WVALID": 1,
                "WREADY": 1,
                "WDATA": 0xAA,
                "WSTRB": 0xF,
                "WLAST": 1,
            },
            {
                "cycle": 4,
                "BVALID": 1,
                "BREADY": 1,
                "BID": 3,
                "BRESP": "OKAY",
            },
        ]}
    )

    violation = next(
        item for item in result["violations"]
        if item["code"] == "payload_changed_while_stalled"
        and item["signal"] == "AWUSER"
    )
    assert result["status"] == "FAIL"
    assert violation["channel"] == "AW"
    assert violation["expected"] == 1
    assert violation["actual"] == 2



def test_validates_narrow_write_strobes_against_byte_lanes():
    result = analyze_axi4_trace(
        {
            "data_width_bits": 32,
            "samples": [
                {"cycle": 0, "AWVALID": 1, "AWREADY": 1, "AWID": 1, "AWADDR": 0x100, "AWLEN": 4, "AWSIZE": 0, "AWBURST": "INCR"},
                {"cycle": 1, "WVALID": 1, "WREADY": 1, "WDATA": 0x11, "WSTRB": 0x1, "WLAST": 0},
                {"cycle": 2, "WVALID": 1, "WREADY": 1, "WDATA": 0x22, "WSTRB": 0x2, "WLAST": 0},
                {"cycle": 3, "WVALID": 1, "WREADY": 1, "WDATA": 0x33, "WSTRB": 0x4, "WLAST": 0},
                {"cycle": 4, "WVALID": 1, "WREADY": 1, "WDATA": 0x44, "WSTRB": 0x8, "WLAST": 0},
                {"cycle": 5, "WVALID": 1, "WREADY": 1, "WDATA": 0x55, "WSTRB": 0x1, "WLAST": 1},
                {"cycle": 6, "BVALID": 1, "BREADY": 1, "BID": 1, "BRESP": "OKAY"},
            ],
        }
    )
    assert result["status"] == "PASS"
    assert result["transactions"][0]["allowed_write_strobes"] == [0x1, 0x2, 0x4, 0x8, 0x1]


def test_accepts_unaligned_first_write_with_matching_strobes():
    result = analyze_axi4_trace(
        {
            "data_width_bits": 32,
            "samples": [
                {"cycle": 0, "AWVALID": 1, "AWREADY": 1, "AWADDR": 0x102, "AWLEN": 1, "AWSIZE": 2, "AWBURST": "INCR"},
                {"cycle": 1, "WVALID": 1, "WREADY": 1, "WDATA": 0xAAAA, "WSTRB": 0xC, "WLAST": 0},
                {"cycle": 2, "WVALID": 1, "WREADY": 1, "WDATA": 0xBBBB, "WSTRB": 0xF, "WLAST": 1},
                {"cycle": 3, "BVALID": 1, "BREADY": 1, "BRESP": "OKAY"},
            ],
        }
    )
    assert result["status"] == "PASS"
    assert result["transactions"][0]["allowed_write_strobes"] == [0xC, 0xF]


def test_unaligned_fixed_burst_keeps_same_partial_lane_window():
    result = analyze_axi4_trace(
        {
            "data_width_bits": 32,
            "samples": [
                {"cycle": 0, "AWVALID": 1, "AWREADY": 1, "AWADDR": 0x103, "AWLEN": 1, "AWSIZE": 2, "AWBURST": "FIXED"},
                {"cycle": 1, "WVALID": 1, "WREADY": 1, "WDATA": 0x11, "WSTRB": 0x8, "WLAST": 0},
                {"cycle": 2, "WVALID": 1, "WREADY": 1, "WDATA": 0x22, "WSTRB": 0x8, "WLAST": 1},
                {"cycle": 3, "BVALID": 1, "BREADY": 1, "BRESP": "OKAY"},
            ],
        }
    )
    assert result["status"] == "PASS"
    assert result["transactions"][0]["allowed_write_strobes"] == [0x8, 0x8]


def test_unaligned_fixed_burst_at_4kb_edge_stays_in_aligned_container():
    result = analyze_axi4_trace(
        {
            "data_width_bits": 32,
            "samples": [
                {"cycle": 0, "AWVALID": 1, "AWREADY": 1, "AWADDR": 0xFFF, "AWLEN": 1, "AWSIZE": 2, "AWBURST": "FIXED"},
                {"cycle": 1, "WVALID": 1, "WREADY": 1, "WDATA": 0x11, "WSTRB": 0x8, "WLAST": 0},
                {"cycle": 2, "WVALID": 1, "WREADY": 1, "WDATA": 0x22, "WSTRB": 0x8, "WLAST": 1},
                {"cycle": 3, "BVALID": 1, "BREADY": 1, "BRESP": "OKAY"},
            ],
        }
    )

    assert result["status"] == "PASS"
    assert result["transactions"][0]["allowed_write_strobes"] == [0x8, 0x8]
    assert "burst_crosses_4kb_boundary" not in {
        item["code"] for item in result["violations"]
    }


def test_reports_write_strobe_outside_transfer_lanes():
    result = analyze_axi4_trace(
        {
            "data_width_bits": 32,
            "samples": [
                {"cycle": 0, "AWVALID": 1, "AWREADY": 1, "AWADDR": 0x102, "AWLEN": 0, "AWSIZE": 2, "AWBURST": "INCR"},
                {"cycle": 1, "WVALID": 1, "WREADY": 1, "WDATA": 0xAAAA, "WSTRB": 0x3, "WLAST": 1},
                {"cycle": 2, "BVALID": 1, "BREADY": 1, "BRESP": "OKAY"},
            ],
        }
    )
    codes = {item["code"] for item in result["violations"]}
    assert result["status"] == "FAIL"
    assert "write_strobe_outside_transfer_lanes" in codes


def test_reports_write_strobe_width_and_transfer_size_errors():
    strobe = analyze_axi4_trace(
        {
            "data_width_bits": 32,
            "samples": [
                {"cycle": 0, "AWVALID": 1, "AWREADY": 1, "AWADDR": 0x100, "AWLEN": 0, "AWSIZE": 2, "AWBURST": "INCR"},
                {"cycle": 1, "WVALID": 1, "WREADY": 1, "WDATA": 0, "WSTRB": 0x10, "WLAST": 1},
                {"cycle": 2, "BVALID": 1, "BREADY": 1, "BRESP": "OKAY"},
            ],
        }
    )
    assert "invalid_write_strobe" in {item["code"] for item in strobe["violations"]}

    size = analyze_axi4_trace(
        {
            "data_width_bits": 32,
            "samples": [
                {"cycle": 0, "ARVALID": 1, "ARREADY": 1, "ARADDR": 0x200, "ARLEN": 0, "ARSIZE": 3, "ARBURST": "INCR"},
            ],
        }
    )
    assert "transfer_size_exceeds_data_bus_width" in {item["code"] for item in size["violations"]}


def test_rejects_nonstandard_axi4_data_width_metadata():
    try:
        analyze_axi4_trace({"data_width_bits": 24, "samples": []})
    except ValueError as exc:
        assert "8, 16, 32, 64, 128, 256, 512, or 1024" in str(exc)
    else:
        raise AssertionError("Expected nonstandard data_width_bits to fail")


def test_accepts_all_zero_write_strobe_as_partial_write():
    result = analyze_axi4_trace(
        {
            "data_width_bits": 32,
            "samples": [
                {
                    "cycle": 0,
                    "AWVALID": 1,
                    "AWREADY": 1,
                    "AWADDR": 0x100,
                    "AWLEN": 0,
                    "AWSIZE": 2,
                    "AWBURST": "INCR",
                },
                {
                    "cycle": 1,
                    "WVALID": 1,
                    "WREADY": 1,
                    "WDATA": 0,
                    "WSTRB": 0,
                    "WLAST": 1,
                },
                {
                    "cycle": 2,
                    "BVALID": 1,
                    "BREADY": 1,
                    "BRESP": "OKAY",
                },
            ],
        }
    )

    assert result["status"] == "PASS"
    assert result["transactions"][0]["write_strobes"] == [0]
    assert result["transactions"][0]["allowed_write_strobes"] == [0xF]


def test_applies_explicit_master_interface_defaults_without_guessing_missing_fields():
    result = analyze_axi4_trace(
        {
            "data_width_bits": 32,
            "absent_master_signals": [
                "AWID",
                "AWREGION",
                "AWLEN",
                "AWSIZE",
                "AWBURST",
                "AWLOCK",
                "AWCACHE",
                "AWQOS",
                "WSTRB",
                "ARID",
                "ARREGION",
                "ARLEN",
                "ARSIZE",
                "ARBURST",
                "ARLOCK",
                "ARCACHE",
                "ARQOS",
            ],
            "samples": [
                {
                    "cycle": 0,
                    "AWVALID": 1,
                    "AWREADY": 1,
                    "AWADDR": 0x100,
                    "AWPROT": 0,
                    "ARVALID": 1,
                    "ARREADY": 1,
                    "ARADDR": 0x200,
                    "ARPROT": 0,
                },
                {
                    "cycle": 1,
                    "WVALID": 1,
                    "WREADY": 1,
                    "WDATA": 0xA5,
                    "WLAST": 1,
                    "RVALID": 1,
                    "RREADY": 1,
                    "RID": 0,
                    "RDATA": 0x5A,
                    "RRESP": "OKAY",
                    "RLAST": 1,
                },
                {
                    "cycle": 2,
                    "BVALID": 1,
                    "BREADY": 1,
                    "BID": 0,
                    "BRESP": "OKAY",
                },
            ],
        }
    )

    assert result["status"] == "PASS"
    assert result["master_signal_defaults"] == {
        "ARCACHE": 0,
        "ARBURST": 1,
        "ARID": 0,
        "ARLEN": 0,
        "ARLOCK": 0,
        "ARQOS": 0,
        "ARREGION": 0,
        "ARSIZE": 2,
        "AWBURST": 1,
        "AWCACHE": 0,
        "AWID": 0,
        "AWLEN": 0,
        "AWLOCK": 0,
        "AWQOS": 0,
        "AWREGION": 0,
        "AWSIZE": 2,
        "WSTRB": 0xF,
    }
    write = next(tx for tx in result["transactions"] if tx["direction"] == "WRITE")
    read = next(tx for tx in result["transactions"] if tx["direction"] == "READ")
    assert write["id"] == 0
    assert write["length"] == 0
    assert write["size"] == 2
    assert write["burst"] == "INCR"
    assert write["write_strobes"] == [0xF]
    assert write["region"] == 0
    assert write["cache"] == 0
    assert write["qos"] == 0
    assert read["id"] == 0
    assert read["length"] == 0
    assert read["size"] == 2
    assert read["burst"] == "INCR"
    assert read["region"] == 0
    assert read["cache"] == 0
    assert read["qos"] == 0


def test_master_default_signaling_requires_width_for_size_or_strobe_defaults():
    with pytest.raises(ValueError, match="data_width_bits is required"):
        analyze_axi4_trace(
            {
                "absent_master_signals": ["AWSIZE", "WSTRB"],
                "samples": [],
            }
        )


def test_master_default_signaling_rejects_signal_declared_absent_but_observed():
    with pytest.raises(ValueError, match="declared absent_master_signals"):
        analyze_axi4_trace(
            {
                "data_width_bits": 32,
                "absent_master_signals": ["AWQOS"],
                "samples": [{"AWQOS": 3}],
            }
        )


def test_master_default_signaling_rejects_required_or_unsupported_signal():
    with pytest.raises(ValueError, match="AWPROT has no supported AXI4 master-interface default"):
        analyze_axi4_trace(
            {
                "absent_master_signals": ["AWPROT"],
                "samples": [],
            }
        )


def test_missing_required_payload_still_fails_without_absence_metadata():
    result = analyze_axi4_trace(
        {
            "data_width_bits": 32,
            "samples": [
                {
                    "cycle": 0,
                    "AWVALID": 1,
                    "AWREADY": 1,
                    "AWADDR": 0x100,
                    "AWPROT": 0,
                }
            ],
        }
    )

    missing = {
        item["signal"]
        for item in result["violations"]
        if item["code"] == "missing_channel_payload"
    }
    assert result["status"] == "FAIL"
    assert {"AWLEN", "AWSIZE", "AWBURST"} <= missing



def test_decodes_axi4_qos_default_without_inventing_system_policy():
    result = analyze_axi4_trace(
        {"samples": [
            {
                "cycle": 0,
                "ARVALID": 1,
                "ARREADY": 1,
                "ARID": 1,
                "ARADDR": 0x400,
                "ARLEN": 0,
                "ARSIZE": 2,
                "ARBURST": "INCR",
                "ARQOS": 0,
            },
            {
                "cycle": 1,
                "RVALID": 1,
                "RREADY": 1,
                "RID": 1,
                "RDATA": 0xAA,
                "RRESP": "OKAY",
                "RLAST": 1,
            },
        ]}
    )

    assert result["status"] == "PASS"
    qos = result["transactions"][0]["qos_attributes"]
    assert qos == {
        "encoding": 0,
        "is_default_no_qos": True,
        "recommended_priority": 0,
        "higher_value_recommended_higher_priority": True,
        "exact_use_protocol_defined": False,
        "axi_ordering_rules_take_precedence": True,
    }
    assert "system-specific QoS scheduling policy" in " ".join(result["limitations"])
