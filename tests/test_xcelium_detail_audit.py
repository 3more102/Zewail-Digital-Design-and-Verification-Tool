from __future__ import annotations

import hashlib
import json
from pathlib import Path

from zddv.cli import main
from zddv.config import initialize_project
from zddv.xcelium_detail_audit import (
    audit_xcelium_imc_detail,
    write_xcelium_imc_detail_audit,
)


IMC_DETAIL = """IMC(64): fixture
Coverage Report: Block Coverage
Instance name: tb.dut
File name: rtl/dut.sv
Number of covered blocks: 1 of 2
Count Block Line
0 1 10 code 10 foo;

Coverage Report: Expression Coverage
Instance name: tb.dut
File name: rtl/dut.sv
index | grade | line | expression
1.1 | 50% (1/2/2) | 12 | a && b

Coverage Report: Toggle Coverage
Instance name: tb.dut
Hit(Full)  Hit(Rise)  Hit(Fall)   Signal
1 1 1 ready

Coverage Report: FSM Coverage
Instance name: tb.dut
State | Hits
IDLE | 4
BUSY | 0

Coverage Report: Functional Coverage
Instance name: tb
Coverpoint | Covered | Total
opcode_cp | 7 | 8
"""


def test_audit_classifies_verified_and_unverified_sections():
    result = audit_xcelium_imc_detail(IMC_DETAIL)

    assert result["summary"]["sections"] == 5
    assert result["summary"]["verified_sections"] == 3
    assert result["summary"]["unverified_sections"] == 2
    assert result["summary"]["metrics"] == [
        "block",
        "expression",
        "fsm",
        "functional",
        "toggle",
    ]

    by_metric = {section["metric"]: section for section in result["sections"]}
    assert by_metric["block"]["normalization_status"] == "verified-parser-available"
    assert by_metric["expression"]["normalization_status"] == "verified-parser-available"
    assert by_metric["toggle"]["normalization_status"] == "verified-parser-available"
    assert by_metric["fsm"]["normalization_status"] == "schema-unverified"
    assert by_metric["functional"]["normalization_status"] == "schema-unverified"
    assert all(len(section["section_sha256"]) == 64 for section in result["sections"])
    assert by_metric["block"]["start_line"] < by_metric["fsm"]["start_line"]
    assert any("State | Hits" == cue for cue in by_metric["fsm"]["schema_cues"])
    assert result["limitations"]


def test_audit_section_hash_is_deterministic_and_content_sensitive():
    first = audit_xcelium_imc_detail(IMC_DETAIL)
    second = audit_xcelium_imc_detail(IMC_DETAIL)
    changed = audit_xcelium_imc_detail(IMC_DETAIL.replace("BUSY | 0", "BUSY | 1"))

    assert first["sections"][3]["section_sha256"] == second["sections"][3]["section_sha256"]
    assert first["sections"][3]["section_sha256"] != changed["sections"][3]["section_sha256"]


def test_no_section_headers_remain_evidence_only():
    result = audit_xcelium_imc_detail("IMC(64): unknown layout\nrow 1\n")
    assert result["summary"]["sections"] == 0
    assert result["summary"]["verified_sections"] == 0
    assert result["summary"]["unverified_sections"] == 0
    assert result["preamble_line_count"] == 2
    assert "evidence-only" in result["limitations"][0]


def test_writer_records_exact_source_digest(tmp_path: Path):
    source = tmp_path / "detail.txt"
    output = tmp_path / "detail-audit.json"
    raw = IMC_DETAIL.encode("utf-8")
    source.write_bytes(raw)

    result = write_xcelium_imc_detail_audit(source, output)

    assert result["source_sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["summary"]["sections"] == 5
    assert output.is_file()
    persisted = json.loads(output.read_text(encoding="utf-8"))
    assert persisted["source_path"] == str(source.resolve())
    assert persisted["source_sha256"] == result["source_sha256"]


def test_cli_audits_default_xcelium_detail_path(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    source = project.root / ".zddv" / "coverage" / "xcelium" / "detail.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(IMC_DETAIL, encoding="utf-8")

    rc = main(
        [
            "--project",
            str(project.root),
            "xcelium-detail-audit",
            "--show",
            "5",
        ]
    )

    assert rc == 0
    terminal = capsys.readouterr().out
    assert "XCELIUM DETAIL AUDIT: 5 section(s); verified=3 unverified=2" in terminal
    assert "schema-unverified" in terminal
    assert "no item-level schema is inferred" in terminal

    report = (
        project.root
        / ".zddv"
        / "coverage"
        / "xcelium"
        / "detail-audit.json"
    )
    assert report.is_file()
