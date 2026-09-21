import json
from pathlib import Path

from zddv.config import initialize_project
from zddv.protocols.apb import analyze_apb_file, analyze_apb_trace


def test_reconstructs_apb_transactions_and_wait_states():
    result = analyze_apb_trace(
        {
            "source": "unit-test",
            "samples": [
                {"cycle": 0, "PSEL": 0, "PENABLE": 0, "PREADY": 1, "PWRITE": 0},
                {
                    "cycle": 1,
                    "PSEL": 1,
                    "PENABLE": 0,
                    "PREADY": 1,
                    "PWRITE": 1,
                    "PADDR": "0x10",
                    "PWDATA": "0xCAFE",
                    "PSTRB": "0x3",
                },
                {
                    "cycle": 2,
                    "PSEL": 1,
                    "PENABLE": 1,
                    "PREADY": 0,
                    "PWRITE": 1,
                    "PADDR": "0x10",
                    "PWDATA": "0xCAFE",
                    "PSTRB": "0x3",
                },
                {
                    "cycle": 3,
                    "PSEL": 1,
                    "PENABLE": 1,
                    "PREADY": 1,
                    "PWRITE": 1,
                    "PADDR": "0x10",
                    "PWDATA": "0xCAFE",
                    "PSTRB": "0x3",
                },
                {
                    "cycle": 4,
                    "PSEL": 1,
                    "PENABLE": 0,
                    "PREADY": 1,
                    "PWRITE": 0,
                    "PADDR": "0x20",
                    "PSTRB": 0,
                },
                {
                    "cycle": 5,
                    "PSEL": 1,
                    "PENABLE": 1,
                    "PREADY": 1,
                    "PWRITE": 0,
                    "PADDR": "0x20",
                    "PSTRB": 0,
                    "PRDATA": "0x55",
                },
            ],
        }
    )

    assert result["status"] == "PASS"
    assert result["summary"] == {
        "samples": 6,
        "attempted_transfers": 2,
        "completed_transactions": 2,
        "reads": 1,
        "writes": 1,
        "error_responses": 0,
        "wait_cycles": 1,
        "violations": 0,
    }
    assert result["transactions"][0]["address"] == 0x10
    assert result["transactions"][0]["wait_cycles"] == 1
    assert result["transactions"][1]["read_data"] == 0x55


def test_reports_protocol_violations():
    result = analyze_apb_trace(
        {
            "samples": [
                {
                    "cycle": 10,
                    "PSEL": 1,
                    "PENABLE": 0,
                    "PREADY": 1,
                    "PWRITE": 0,
                    "PADDR": "0x100",
                    "PSTRB": 1,
                },
                {
                    "cycle": 11,
                    "PSEL": 1,
                    "PENABLE": 1,
                    "PREADY": 0,
                    "PWRITE": 0,
                    "PADDR": "0x104",
                    "PSTRB": 1,
                },
                {
                    "cycle": 12,
                    "PSEL": 0,
                    "PENABLE": 1,
                    "PREADY": 0,
                    "PWRITE": 0,
                    "PADDR": "0x104",
                },
            ]
        }
    )

    codes = {item["code"] for item in result["violations"]}
    assert result["status"] == "FAIL"
    assert "read_with_active_pstrb" in codes
    assert "request_changed_during_transfer" in codes
    assert "penable_without_psel" in codes
    assert "access_terminated_before_ready" in codes


def test_analyze_apb_file_writes_json_report(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    trace = project.root / "apb.json"
    trace.write_text(
        json.dumps(
            {
                "source": "file-test",
                "samples": [
                    {
                        "cycle": 0,
                        "PSEL": 1,
                        "PENABLE": 0,
                        "PWRITE": 1,
                        "PADDR": 0,
                        "PWDATA": 7,
                    },
                    {
                        "cycle": 1,
                        "PSEL": 1,
                        "PENABLE": 1,
                        "PREADY": 1,
                        "PWRITE": 1,
                        "PADDR": 0,
                        "PWDATA": 7,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    result = analyze_apb_file(project, trace)

    report = Path(result["report_path"])
    assert report.exists()
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["protocol"] == "APB"
    assert payload["summary"]["completed_transactions"] == 1
    assert payload["status"] == "PASS"
