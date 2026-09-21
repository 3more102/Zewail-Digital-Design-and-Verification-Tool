import json
from pathlib import Path

from zddv.cli import main
from zddv.config import initialize_project
from zddv.protocols.apb import (
    analyze_apb_file,
    analyze_apb_trace,
    analyze_apb_waveform,
    extract_apb_trace_from_vcd,
)


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
    assert "access_terminated_before_ready" in codes


def test_penable_with_unselected_peripheral_is_not_a_violation():
    result = analyze_apb_trace(
        {
            "samples": [
                {"cycle": 0, "PSEL": 0, "PENABLE": 1},
                {"cycle": 1, "PSEL": 0, "PENABLE": 0},
            ]
        }
    )

    assert result["status"] == "PASS"
    assert result["summary"]["violations"] == 0


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


APB_VCD = """$timescale 1ns $end
$scope module tb $end
$scope module apb $end
$var wire 1 a PCLK $end
$var wire 1 b PSEL $end
$var wire 1 c PENABLE $end
$var wire 1 d PREADY $end
$var wire 1 e PWRITE $end
$var wire 8 f PADDR [7:0] $end
$var wire 16 g PWDATA [15:0] $end
$var wire 16 h PRDATA [15:0] $end
$var wire 2 i PSTRB [1:0] $end
$upscope $end
$upscope $end
$enddefinitions $end
#0
0a
0b
0c
1d
0e
b00000000 f
b0000000000000000 g
b0000000000000000 h
b00 i
#5
1b
0c
1d
1e
b00010000 f
b1100101011111110 g
b11 i
1a
#10
0a
#15
1c
0d
1a
#20
0a
#25
1d
1a
#30
0a
#35
0c
0e
b00100000 f
b00 i
1a
#40
0a
#45
1c
b0000000001010101 h
1a
"""


def test_extracts_apb_trace_from_vcd_on_rising_clock_edges(tmp_path: Path):
    waveform = tmp_path / "apb.vcd"
    waveform.write_text(APB_VCD, encoding="utf-8")

    trace = extract_apb_trace_from_vcd(waveform)

    assert trace["waveform"]["scope"] == "tb.apb"
    assert trace["waveform"]["clock"] == "PCLK"
    assert trace["waveform"]["timescale"] == "1ns"
    assert [sample["time"] for sample in trace["samples"]] == [5, 15, 25, 35, 45]

    result = analyze_apb_trace(trace)

    assert result["status"] == "PASS"
    assert result["summary"]["completed_transactions"] == 2
    assert result["summary"]["wait_cycles"] == 1
    assert result["transactions"][0]["direction"] == "WRITE"
    assert result["transactions"][0]["address"] == 0x10
    assert result["transactions"][0]["write_data"] == 0xCAFE
    assert result["transactions"][0]["start_time"] == 5
    assert result["transactions"][0]["end_time"] == 25
    assert result["transactions"][1]["direction"] == "READ"
    assert result["transactions"][1]["address"] == 0x20
    assert result["transactions"][1]["read_data"] == 0x55
    assert result["transactions"][1]["start_time"] == 35
    assert result["transactions"][1]["end_time"] == 45


def test_apb_waveform_scope_must_be_disambiguated(tmp_path: Path):
    waveform = tmp_path / "two_apb.vcd"
    second = APB_VCD.replace(
        "$upscope $end\n$upscope $end\n$enddefinitions",
        "$upscope $end\n"
        "$scope module apb2 $end\n"
        "$var wire 1 j PCLK $end\n"
        "$var wire 1 k PSEL $end\n"
        "$var wire 1 l PENABLE $end\n"
        "$upscope $end\n"
        "$upscope $end\n"
        "$enddefinitions",
        1,
    )
    waveform.write_text(second, encoding="utf-8")

    try:
        extract_apb_trace_from_vcd(waveform)
    except RuntimeError as exc:
        assert "Multiple APB waveform scopes" in str(exc)
        assert "tb.apb" in str(exc)
        assert "tb.apb2" in str(exc)
    else:
        raise AssertionError("Expected ambiguous APB scope detection to fail")


def test_analyze_apb_waveform_writes_normalized_trace_and_report(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    waveform = project.root / "apb.vcd"
    waveform.write_text(APB_VCD, encoding="utf-8")

    result = analyze_apb_waveform(project, input_path="apb.vcd")

    assert result["status"] == "PASS"
    assert result["summary"]["completed_transactions"] == 2
    assert Path(result["trace_path"]).is_file()
    assert Path(result["report_path"]).is_file()

    trace = json.loads(Path(result["trace_path"]).read_text(encoding="utf-8"))
    report = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
    assert trace["waveform"]["scope"] == "tb.apb"
    assert len(trace["samples"]) == 5
    assert report["transactions"][0]["end_time"] == 25


def test_apb_waveform_cli_with_direct_vcd_input(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    waveform = project.root / "apb.vcd"
    waveform.write_text(APB_VCD, encoding="utf-8")

    rc = main(
        [
            "--project",
            str(project.root),
            "apb-waveform",
            "--input",
            "apb.vcd",
        ]
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "APB WAVEFORM PASS" in output
    assert "2 completed transaction(s)" in output
    assert "Scope: tb.apb" in output
    assert (
        project.root / ".zddv" / "protocols" / "apb" / "waveform-latest.json"
    ).is_file()
