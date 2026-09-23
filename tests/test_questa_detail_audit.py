from __future__ import annotations

import hashlib
import json
from pathlib import Path

from zddv.cli import main
from zddv.config import initialize_project
from zddv.questa_detail_audit import (
    audit_questa_coverage_evidence,
    write_questa_coverage_evidence_audit,
)


CODE_DETAILS = """Coverage Report by file with details

Statement Coverage for file top.sv --
10 1 1 assign y = a & b;

Condition Coverage for file top.sv --
Line 12 Item 1 (a && b)
Rows: hits FEC Targets Non-Masking Condition(s)
Row 1: 1 a_0 b
Row 2: 0 a_1 ~b
"""

MULTIBIT_EXPRESSION = """Coverage Report by file with details

Expression Coverage for file top.sv --

Focused Expression View
Line 28 Item 1 ((a & b) | (c & d))
Rows: FEC Target                  Hits
                                  i = <0> <1>
Row 1: a[i]_0                     1 ***0*** b[i]
Row 2: a[i]_1                ***0*** 1 b[i]
"""

TOGGLE_XML = """<?xml version="1.0"?>
<coverage_report>
  <code_coverage_report lines="1" byInstance="1">
    <instanceData path="/tb/dut" du="dut">
      <toggleSummary active="2" hits="1" percent="50.0" />
      <tog name="ready" c0="4" c1="0" />
      <toge name="req" c1H_0L="2" c0L_1H="1" c0L_Z="0"
            cZ_0L="3" c1H_Z="0" cZ_1H="0" />
      <togenum name="state">
        <togenumval name="IDLE" c="3" />
        <togenumval name="RUN" c="0" />
      </togenum>
    </instanceData>
  </code_coverage_report>
</coverage_report>
"""


def _write_fixture(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "code-details.txt").write_text(CODE_DETAILS, encoding="utf-8")
    (directory / "multibit-expression.txt").write_text(
        MULTIBIT_EXPRESSION,
        encoding="utf-8",
    )
    (directory / "toggle-details.xml").write_text(TOGGLE_XML, encoding="utf-8")
    (directory / "toggle-details.txt").write_text(
        "Toggle Coverage\nstate enum layout retained as evidence\n",
        encoding="utf-8",
    )


def test_audit_reports_existing_parsers_and_pending_toggle_schema(tmp_path: Path):
    source = tmp_path / "coverage"
    _write_fixture(source)

    result = audit_questa_coverage_evidence(source)

    assert result["summary"]["files_present"] == 4
    assert result["summary"]["parser_supported_files"] == 3
    assert result["summary"]["evidence_only_files"] == 1
    assert result["summary"]["normalized_points"] >= 4
    assert result["summary"]["layout_fingerprinted_files"] == 4
    assert result["summary"]["pending_schema_targets"] == [
        "multibit-condition",
        "enumerated-or-unknown-toggle",
    ]
    assert result["summary"]["pending_toggle_tags"] == {
        "togenum": 1,
        "togenumval": 2,
    }

    by_name = {item["name"]: item for item in result["files"]}
    assert by_name["code-details.txt"]["normalization_status"] == (
        "verified-parser-available"
    )
    assert by_name["multibit-expression.txt"]["normalization_status"] == (
        "verified-parser-available"
    )
    assert by_name["toggle-details.xml"]["normalization_status"] == (
        "verified-parser-available"
    )
    assert by_name["toggle-details.txt"]["normalization_status"] == "evidence-only"

    xml_layout = by_name["toggle-details.xml"]["layout"]
    assert xml_layout["kind"] == "xml-structure"
    assert len(xml_layout["fingerprint_sha256"]) == 64
    assert "attribute_names" in xml_layout["sample_elements"][-1]
    assert "attribute_values" not in json.dumps(xml_layout["sample_elements"])
    assert "pending toggle semantics are inferred" in xml_layout["semantics"]

    text_layout = by_name["multibit-expression.txt"]["layout"]
    assert text_layout["kind"] == "text-lexical-cues"
    assert len(text_layout["fingerprint_sha256"]) == 64
    assert text_layout["structured_rows"] > 0
    assert "raw values are excluded" in text_layout["semantics"]


def test_xml_layout_fingerprint_ignores_values_but_detects_structure(tmp_path: Path):
    first_dir = tmp_path / "first"
    changed_value_dir = tmp_path / "changed-value"
    changed_shape_dir = tmp_path / "changed-shape"
    for directory in (first_dir, changed_value_dir, changed_shape_dir):
        directory.mkdir()

    (first_dir / "toggle-details.xml").write_text(TOGGLE_XML, encoding="utf-8")
    (changed_value_dir / "toggle-details.xml").write_text(
        TOGGLE_XML.replace('name="IDLE" c="3"', 'name="IDLE" c="4"'),
        encoding="utf-8",
    )
    (changed_shape_dir / "toggle-details.xml").write_text(
        TOGGLE_XML.replace(
            '<togenum name="state">',
            '<togenum name="state" encoding="enum">',
        ),
        encoding="utf-8",
    )

    first = audit_questa_coverage_evidence(first_dir)["files"][0]
    changed_value = audit_questa_coverage_evidence(changed_value_dir)["files"][0]
    changed_shape = audit_questa_coverage_evidence(changed_shape_dir)["files"][0]

    assert first["sha256"] != changed_value["sha256"]
    assert (
        first["layout"]["fingerprint_sha256"]
        == changed_value["layout"]["fingerprint_sha256"]
    )
    assert (
        first["layout"]["fingerprint_sha256"]
        != changed_shape["layout"]["fingerprint_sha256"]
    )


def test_text_layout_fingerprint_ignores_count_value_changes(tmp_path: Path):
    first_dir = tmp_path / "first-text"
    changed_dir = tmp_path / "changed-text"
    first_dir.mkdir()
    changed_dir.mkdir()
    (first_dir / "multibit-expression.txt").write_text(
        MULTIBIT_EXPRESSION,
        encoding="utf-8",
    )
    (changed_dir / "multibit-expression.txt").write_text(
        MULTIBIT_EXPRESSION.replace(
            "Row 1: a[i]_0                     1 ***0*** b[i]",
            "Row 1: a[i]_0                     2 ***0*** b[i]",
        ),
        encoding="utf-8",
    )

    first = audit_questa_coverage_evidence(first_dir)["files"][0]
    changed = audit_questa_coverage_evidence(changed_dir)["files"][0]

    assert first["sha256"] != changed["sha256"]
    assert (
        first["layout"]["fingerprint_sha256"]
        == changed["layout"]["fingerprint_sha256"]
    )


def test_writer_records_exact_file_hashes(tmp_path: Path):
    source = tmp_path / "coverage"
    _write_fixture(source)
    output = tmp_path / "audit.json"

    result = write_questa_coverage_evidence_audit(source, output)

    persisted = json.loads(output.read_text(encoding="utf-8"))
    code_bytes = (source / "code-details.txt").read_bytes()
    code = next(item for item in persisted["files"] if item["name"] == "code-details.txt")
    assert code["sha256"] == hashlib.sha256(code_bytes).hexdigest()
    assert result["path"] == str(output.resolve())


def test_malformed_toggle_xml_is_preserved_as_evidence(tmp_path: Path):
    source = tmp_path / "coverage"
    source.mkdir()
    (source / "toggle-details.xml").write_text("<coverage>", encoding="utf-8")

    result = audit_questa_coverage_evidence(source)

    item = result["files"][0]
    assert item["normalization_status"] == "malformed-xml"
    assert item["sha256"]
    assert item["parse_error"]


def test_cli_audits_default_questa_coverage_directory(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    source = project.root / ".zddv" / "coverage"
    _write_fixture(source)

    rc = main(
        [
            "--project",
            str(project.root),
            "questa-detail-audit",
            "--show",
            "10",
        ]
    )

    assert rc == 0
    terminal = capsys.readouterr().out
    assert "QUESTA DETAIL AUDIT:" in terminal
    assert "pending=multibit-condition,enumerated-or-unknown-toggle" in terminal
    assert "togenum=1" in terminal
    assert "layout=" in terminal
    assert "No pending schema is inferred." in terminal

    report = project.root / ".zddv" / "coverage" / "questa-detail-audit.json"
    assert report.is_file()
