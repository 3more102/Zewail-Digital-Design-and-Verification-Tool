from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from zddv.cli import cmd_coverage
from zddv.config import ProjectConfig
from zddv.coverage import (
    inspect_questa_coverage_xml,
    merge_questa_coverage,
    parse_questa_coverage_summary,
    parse_questa_functional_coverage_report,
)
from zddv.storage import (
    list_coverage_snapshots,
    list_functional_coverage_bins,
    list_functional_coverage_snapshots,
)


QUESTA_SUMMARY = """QuestaSim-64 vcover 2024.2 Coverage Utility 2024.05 May 20 2024
Coverage Report Totals BY INSTANCES: Number of Instances 23

    Enabled Coverage              Bins      Hits    Misses    Weight  Coverage
    ----------------              ----      ----    ------    ------  --------
    Branches                      3044      2982        62         1    97.96%
    Expressions                   1665      1143       522         1    68.64%
    Statements                    4920      4920         0         1   100.00%
    Toggles                      72906     37574     35332         1    51.53%
Total coverage (filtered view): 79.53%
"""

QUESTA_FUNCTIONAL = """COVERGROUP COVERAGE:
--------------------
Covergroup                              Metric       Goal    Bins    Status
TYPE /top/dut/APB_cg                    66.67%        100       -    Uncovered
    covered/total bins:                     2          3
    missing/total bins:                     1          3
    % Hit:                              66.67%        100

    Coverpoint APB_cg::type_cp          50.00%        100       -    Uncovered
        covered/total bins:                 1          2
        missing/total bins:                 1          2
        % Hit:                          50.00%        100
        bin write                         0          1         ZERO
        bin read                        154          1         Covered

    Cross APB_cg::write_x_data         100.00%        100       -    Covered
        bin legal_pair                    2          2         Covered
        illegal bin bad_pair              1          1         Covered
"""


QUESTA_DETAILS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<q:coverage xmlns:q="urn:zddv:synthetic">
  <q:file path="rtl/dut.sv">
    <q:statement line="12" hits="0"/>
    <q:statement line="13" hits="4"/>
  </q:file>
  <q:toggle signal="ready" hits="1"/>
</q:coverage>
"""


def _project(tmp_path: Path) -> ProjectConfig:
    root = tmp_path / "demo"
    root.mkdir()
    return ProjectConfig(
        root=root,
        name="demo",
        top="tb_top",
        simulator="questa",
        rtl=[],
        tb=[],
        waveform=False,
        coverage=True,
    )


def test_parse_questa_summary_preserves_tool_score_separately():
    metrics = parse_questa_coverage_summary(QUESTA_SUMMARY)

    assert metrics["total_points"] == 82535
    assert metrics["hit_points"] == 46619
    assert metrics["unhit_points"] == 35916
    assert metrics["hit_rate"] == pytest.approx(100.0 * 46619 / 82535)
    assert metrics["tool_total_coverage"] == 79.53
    assert metrics["by_type"]["branch"] == {
        "total": 3044,
        "hit": 2982,
        "hit_rate": 97.96,
    }
    assert metrics["by_type"]["statement"]["hit_rate"] == 100.0
    assert metrics["by_type"]["toggle"]["hit"] == 37574



def test_parse_questa_summary_accepts_comma_grouped_counts():
    text = QUESTA_SUMMARY.replace("3044", "3,044").replace(
        "2982", "2,982"
    ).replace("1665", "1,665").replace("1143", "1,143").replace(
        "4920", "4,920"
    ).replace("72906", "72,906").replace("37574", "37,574").replace(
        "35332", "35,332"
    )

    metrics = parse_questa_coverage_summary(text)

    assert metrics["total_points"] == 82535
    assert metrics["hit_points"] == 46619
    assert metrics["by_type"]["branch"]["total"] == 3044


def test_parse_questa_functional_coverage_keeps_only_ordinary_bins():
    payload = parse_questa_functional_coverage_report(QUESTA_FUNCTIONAL)

    assert payload["source"] == "questa-vcover"
    assert len(payload["bins"]) == 3
    assert payload["bins"][0] == {
        "scope": "/top/dut/APB_cg",
        "coverpoint": "APB_cg::type_cp",
        "bin": "write",
        "hits": 0,
        "goal": 1,
        "metadata": {
            "questa_status": "ZERO",
            "coverage_kind": "coverpoint",
        },
    }
    assert payload["bins"][2]["coverpoint"] == "APB_cg::write_x_data"
    assert payload["bins"][2]["metadata"]["coverage_kind"] == "cross"


def test_inspect_questa_xml_inventory_is_namespace_insensitive(tmp_path: Path):
    source = tmp_path / "details.xml"
    source.write_text(QUESTA_DETAILS_XML, encoding="utf-8")

    inventory = inspect_questa_coverage_xml(source)

    assert inventory["root_tag"] == "coverage"
    assert inventory["element_count"] == 5
    assert inventory["tag_counts"] == {
        "coverage": 1,
        "file": 1,
        "statement": 2,
        "toggle": 1,
    }
    assert inventory["attributes_by_tag"]["coverage"] == []
    assert inventory["attributes_by_tag"]["file"] == ["path"]
    assert inventory["attributes_by_tag"]["statement"] == ["hits", "line"]
    assert inventory["attributes_by_tag"]["toggle"] == ["hits", "signal"]
    assert len(inventory["schema_fingerprint"]) == 64


def test_questa_xml_schema_fingerprint_ignores_element_counts(tmp_path: Path):
    first = tmp_path / "first.xml"
    second = tmp_path / "second.xml"
    first.write_text(QUESTA_DETAILS_XML, encoding="utf-8")
    second.write_text(
        """<coverage><file path="other.sv"><statement line="99" hits="7"/></file><toggle signal="valid" hits="0"/></coverage>""",
        encoding="utf-8",
    )

    first_inventory = inspect_questa_coverage_xml(first)
    second_inventory = inspect_questa_coverage_xml(second)

    assert first_inventory["schema_fingerprint"] == second_inventory["schema_fingerprint"]
    assert first_inventory["tag_counts"] != second_inventory["tag_counts"]


def test_merge_questa_coverage_merges_reports_and_persists_snapshot(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    run_root = (project.root / project.run_dir).resolve()
    for name in ("run-a", "run-b"):
        run_dir = run_root / name
        run_dir.mkdir(parents=True)
        (run_dir / "coverage.ucdb").write_text(
            f"{name} fixture\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/questa/bin/vcover" if name == "vcover" else None,
    )

    commands: list[list[str]] = []

    def fake_run(command, cwd):
        commands.append(list(command))
        if command[1] == "merge":
            out_path = Path(command[command.index("-out") + 1])
            out_path.write_text("merged fixture\n", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="merge complete\n")
        if command[1:3] == ["report", "-summary"]:
            return SimpleNamespace(returncode=0, stdout=QUESTA_SUMMARY)
        if command[1:4] == ["report", "-cvg", "-details"]:
            return SimpleNamespace(returncode=0, stdout=QUESTA_FUNCTIONAL)
        if command[1:3] == ["report", "-xml"]:
            out_path = Path(command[command.index("-output") + 1])
            out_path.write_text(QUESTA_DETAILS_XML, encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("zddv.coverage._run", fake_run)

    result = merge_questa_coverage(project)

    assert commands[0][:4] == [
        "/opt/questa/bin/vcover",
        "merge",
        "-out",
        result["merged"],
    ]
    assert commands[1] == [
        "/opt/questa/bin/vcover",
        "report",
        "-summary",
        result["merged"],
    ]
    assert commands[2] == [
        "/opt/questa/bin/vcover",
        "report",
        "-cvg",
        "-details",
        result["merged"],
    ]
    assert commands[3] == [
        "/opt/questa/bin/vcover",
        "report",
        "-xml",
        "-output",
        result["details"],
        result["merged"],
    ]
    assert len(result["inputs"]) == 2
    assert Path(result["merged"]).exists()
    assert Path(result["summary"]).read_text(encoding="utf-8") == QUESTA_SUMMARY
    assert result["details_capture"] == "xml"
    assert Path(result["details"]).read_text(encoding="utf-8") == QUESTA_DETAILS_XML
    assert result["details_schema_status"] == "parsed"
    assert Path(result["details_schema"]).exists()
    assert len(result["details_schema_fingerprint"]) == 64

    payload = json.loads(Path(result["metrics_path"]).read_text(encoding="utf-8"))
    assert payload["simulator"] == "questa"
    assert payload["input_count"] == 2
    assert payload["tool_total_coverage"] == 79.53
    assert payload["by_type"]["expression"]["hit"] == 1143
    assert payload["functional_bins"] == 3
    assert payload["functional_snapshot_id"] == result["functional_snapshot_id"]
    assert payload["details_capture"] == "xml"
    assert payload["details"] == result["details"]
    assert payload["details_schema_status"] == "parsed"
    assert payload["details_schema"] == result["details_schema"]
    assert payload["details_schema_fingerprint"] == result["details_schema_fingerprint"]
    schema = json.loads(Path(result["details_schema"]).read_text(encoding="utf-8"))
    assert schema["tag_counts"]["statement"] == 2
    assert Path(result["functional_report"]).read_text(
        encoding="utf-8"
    ) == QUESTA_FUNCTIONAL

    snapshots = list_coverage_snapshots(project, limit=5)
    assert len(snapshots) == 1
    assert snapshots[0]["simulator"] == "questa"
    assert snapshots[0]["total_points"] == 82535
    assert snapshots[0]["hit_points"] == 46619


    functional_snapshots = list_functional_coverage_snapshots(project, limit=5)
    assert len(functional_snapshots) == 1
    assert (
        functional_snapshots[0]["snapshot_id"]
        == result["functional_snapshot_id"]
    )
    assert functional_snapshots[0]["source"] == "questa-vcover"
    assert functional_snapshots[0]["coverage_rate"] == pytest.approx(2 * 100.0 / 3)

    holes = list_functional_coverage_bins(
        project,
        result["functional_snapshot_id"],
        status="UNCOVERED",
    )
    assert len(holes) == 1
    assert holes[0]["coverpoint"] == "APB_cg::type_cp"
    assert holes[0]["bin_name"] == "write"


def test_coverage_cli_surfaces_questa_functional_snapshot(tmp_path: Path, monkeypatch, capsys):
    project = _project(tmp_path)
    monkeypatch.setattr("zddv.cli.load_project", lambda path: project)
    monkeypatch.setattr(
        "zddv.cli.merge_coverage",
        lambda loaded: {
            "inputs": ["run-a/coverage.ucdb"],
            "merged": "/tmp/coverage.ucdb",
            "summary": "/tmp/summary.txt",
            "metrics_path": "/tmp/metrics.json",
            "metrics": {
                "hit_points": 2,
                "total_points": 3,
                "hit_rate": 100.0 * 2 / 3,
                "tool_total_coverage": 66.67,
            },
            "snapshot_id": "cov-test",
            "report": "",
            "functional_bins": 3,
            "functional_snapshot_id": "fcov-test",
            "functional_report": "/tmp/functional.txt",
            "details": "/tmp/details.xml",
            "details_capture": "xml",
            "details_schema": "/tmp/details-schema.json",
            "details_schema_status": "parsed",
        },
    )

    rc = cmd_coverage(SimpleNamespace(project=str(project.root)))

    assert rc == 0
    output = capsys.readouterr().out
    assert "Detailed coverage XML: /tmp/details.xml" in output
    assert "Detailed XML schema: /tmp/details-schema.json" in output
    assert "Functional coverage bins: 3" in output
    assert "Functional snapshot: fcov-test" in output
    assert "Functional report: /tmp/functional.txt" in output


def test_merge_questa_coverage_keeps_summary_when_detailed_xml_is_unavailable(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    run_dir = (project.root / project.run_dir / "run-a").resolve()
    run_dir.mkdir(parents=True)
    (run_dir / "coverage.ucdb").write_text("run fixture\n", encoding="utf-8")

    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/questa/bin/vcover" if name == "vcover" else None,
    )

    def fake_run(command, cwd):
        if command[1] == "merge":
            out_path = Path(command[command.index("-out") + 1])
            out_path.write_text("merged fixture\n", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="merge complete\n")
        if command[1:3] == ["report", "-summary"]:
            return SimpleNamespace(returncode=0, stdout=QUESTA_SUMMARY)
        if command[1:4] == ["report", "-cvg", "-details"]:
            return SimpleNamespace(returncode=0, stdout=QUESTA_FUNCTIONAL)
        if command[1:3] == ["report", "-xml"]:
            return SimpleNamespace(returncode=1, stdout="XML export unavailable\n")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("zddv.coverage._run", fake_run)

    result = merge_questa_coverage(project)

    assert result["details"] is None
    assert result["details_capture"] == "unavailable"
    assert result["details_error"] == "XML export unavailable"
    assert result["details_schema"] is None
    assert result["details_schema_status"] == "unavailable"
    assert result["details_schema_fingerprint"] is None

    payload = json.loads(Path(result["metrics_path"]).read_text(encoding="utf-8"))
    assert payload["details"] is None
    assert payload["details_capture"] == "unavailable"
    assert payload["details_error"] == "XML export unavailable"

    snapshots = list_coverage_snapshots(project, limit=5)
    assert len(snapshots) == 1
    assert snapshots[0]["total_points"] == 82535


def test_merge_questa_coverage_keeps_summary_when_detailed_xml_is_invalid(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    run_dir = (project.root / project.run_dir / "run-a").resolve()
    run_dir.mkdir(parents=True)
    (run_dir / "coverage.ucdb").write_text("run fixture\n", encoding="utf-8")

    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/questa/bin/vcover" if name == "vcover" else None,
    )

    def fake_run(command, cwd):
        if command[1] == "merge":
            out_path = Path(command[command.index("-out") + 1])
            out_path.write_text("merged fixture\n", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="merge complete\n")
        if command[1:3] == ["report", "-summary"]:
            return SimpleNamespace(returncode=0, stdout=QUESTA_SUMMARY)
        if command[1:4] == ["report", "-cvg", "-details"]:
            return SimpleNamespace(returncode=0, stdout=QUESTA_FUNCTIONAL)
        if command[1:3] == ["report", "-xml"]:
            out_path = Path(command[command.index("-output") + 1])
            out_path.write_text("<coverage>", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("zddv.coverage._run", fake_run)

    result = merge_questa_coverage(project)

    assert result["details_capture"] == "xml"
    assert result["details_schema"] is None
    assert result["details_schema_status"] == "invalid_xml"
    assert result["details_schema_error"]
    assert result["metrics"]["total_points"] == 82535

    payload = json.loads(Path(result["metrics_path"]).read_text(encoding="utf-8"))
    assert payload["details_schema_status"] == "invalid_xml"
    assert payload["details_schema_error"]

