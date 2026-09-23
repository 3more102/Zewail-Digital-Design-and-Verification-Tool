import json
from pathlib import Path

from zddv.cli import main
from zddv.config import initialize_project
from zddv.protocols.ucie import analyze_ucie_file, analyze_ucie_trace


def test_ucie_public_flit_trace_clean_health():
    result = analyze_ucie_trace(
        {
            "source": "unit-test",
            "profile": "public-flit-68-256",
            "negotiated": {
                "width": "x16",
                "lane_numbering": "normal",
                "frequency_gt_s": 32,
                "protocol": "CXL",
            },
            "flits": [
                {
                    "cycle": 10,
                    "time": 100,
                    "direction": "TX",
                    "size_bytes": 68,
                    "header_bytes": 2,
                    "ack_nak": "ACK",
                    "crc_ok": True,
                },
                {
                    "cycle": 11,
                    "time": 110,
                    "direction": "RX",
                    "size_bytes": 256,
                    "header_bytes": 2,
                    "ack_nak": "ACK",
                    "crc_ok": 1,
                },
            ],
        }
    )

    assert result["status"] == "PASS"
    assert result["health"] == "CLEAN"
    assert result["summary"]["flits"] == 2
    assert result["summary"]["tx_flits"] == 1
    assert result["summary"]["rx_flits"] == 1
    assert result["summary"]["ack_flits"] == 2
    assert result["summary"]["crc_error_flits"] == 0
    assert result["summary"]["flit_sizes"] == {"68": 1, "256": 1}
    assert result["negotiated"]["protocol"] == "CXL"


def test_ucie_nak_and_crc_error_are_health_events_not_trace_violations():
    result = analyze_ucie_trace(
        {
            "flits": [
                {
                    "cycle": 0,
                    "direction": "RX",
                    "size_bytes": 68,
                    "ack_nak": "NAK",
                    "crc_ok": False,
                }
            ]
        }
    )

    assert result["status"] == "PASS"
    assert result["health"] == "DEGRADED"
    assert result["summary"]["nak_flits"] == 1
    assert result["summary"]["crc_error_flits"] == 1
    assert result["summary"]["violations"] == 0


def test_ucie_reports_normalized_trace_contract_violations():
    result = analyze_ucie_trace(
        {
            "flits": [
                {
                    "cycle": 4,
                    "time": 40,
                    "direction": "TX",
                    "size_bytes": 68,
                    "header_bytes": 2,
                    "ack_nak": "ACK",
                    "crc_ok": True,
                },
                {
                    "cycle": 3,
                    "time": 30,
                    "direction": "SIDEWAYS",
                    "size_bytes": 128,
                    "header_bytes": 4,
                    "ack_nak": "MAYBE",
                    "crc_ok": None,
                },
            ]
        }
    )

    assert result["status"] == "FAIL"
    codes = {item["code"] for item in result["violations"]}
    assert "non_monotonic_cycle" in codes
    assert "non_monotonic_time" in codes
    assert "invalid_direction" in codes
    assert "unsupported_public_flit_size" in codes
    assert "public_header_size_mismatch" in codes
    assert "invalid_ack_nak" in codes
    assert "missing_or_invalid_crc_status" in codes


def test_ucie_file_and_cli_write_report(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    trace = project.root / "ucie.json"
    trace.write_text(
        json.dumps(
            {
                "source": "file-test",
                "flits": [
                    {
                        "cycle": 0,
                        "direction": "TX",
                        "size_bytes": 68,
                        "ack_nak": "ACK",
                        "crc_ok": True,
                    },
                    {
                        "cycle": 1,
                        "direction": "RX",
                        "size_bytes": 68,
                        "ack_nak": "NAK",
                        "crc_ok": False,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    result = analyze_ucie_file(project, trace)
    report = Path(result["report_path"])
    assert report.is_file()
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["protocol"] == "UCIe"
    assert payload["status"] == "PASS"
    assert payload["health"] == "DEGRADED"

    rc = main(["--project", str(project.root), "ucie-analyze", str(trace)])

    assert rc == 0
    output = capsys.readouterr().out
    assert "UCIe PASS" in output
    assert "health=DEGRADED" in output
    assert "2 flit(s)" in output



def test_ucie_3_0_accepts_64_gt_s_public_generation_rate():
    result = analyze_ucie_trace(
        {
            "negotiated": {
                "spec_version": "3.0",
                "data_rate_gt_s": 64,
                "protocol": "CXL",
            },
            "flits": [
                {
                    "cycle": 0,
                    "direction": "TX",
                    "size_bytes": 256,
                    "ack_nak": "ACK",
                    "crc_ok": True,
                }
            ],
        }
    )

    assert result["status"] == "PASS"
    assert result["negotiated"]["spec_version"] == "3.0"
    assert result["negotiated"]["data_rate_gt_s"] == 64.0
    assert result["negotiated"]["public_generation_max_data_rate_gt_s"] == 64.0


def test_ucie_2_0_rejects_rate_above_public_32_gt_s_generation_ceiling():
    result = analyze_ucie_trace(
        {
            "negotiated": {
                "spec_version": "2.0",
                "data_rate_gt_s": 48,
            },
            "flits": [
                {
                    "cycle": 0,
                    "direction": "RX",
                    "size_bytes": 68,
                    "ack_nak": "ACK",
                    "crc_ok": True,
                }
            ],
        }
    )

    assert result["status"] == "FAIL"
    violation = next(
        item
        for item in result["violations"]
        if item["code"] == "data_rate_exceeds_public_generation"
    )
    assert violation["scope"] == "negotiated"
    assert violation["expected"] == "<= 32 GT/s for UCIe 2.0"
    assert violation["actual"] == 48.0


def test_ucie_legacy_frequency_field_is_preserved_and_aliased_to_data_rate():
    result = analyze_ucie_trace(
        {
            "negotiated": {
                "spec_version": 3,
                "frequency_gt_s": "32",
            },
            "flits": [],
        }
    )

    assert result["status"] == "PASS"
    assert result["negotiated"]["spec_version"] == "3.0"
    assert result["negotiated"]["frequency_gt_s"] == "32"
    assert result["negotiated"]["data_rate_gt_s"] == 32.0
    assert result["negotiated"]["public_generation_max_data_rate_gt_s"] == 64.0


def test_ucie_rejects_unmodeled_public_spec_version():
    result = analyze_ucie_trace(
        {
            "negotiated": {
                "spec_version": "4.0",
                "data_rate_gt_s": 64,
            },
            "flits": [],
        }
    )

    assert result["status"] == "FAIL"
    violation = next(
        item
        for item in result["violations"]
        if item["code"] == "unsupported_public_spec_version"
    )
    assert violation["scope"] == "negotiated"
    assert violation["expected"] == "1.0/1.1/2.0/3.0"
