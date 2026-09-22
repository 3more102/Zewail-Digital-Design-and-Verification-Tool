from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from zddv.config import ProjectConfig
from zddv.coverage import (
    build_coverage_hole_report,
    load_normalized_coverage_points,
    merge_questa_coverage,
    parse_questa_coverage_summary,
    parse_questa_coverage_xml,
)
from zddv.storage import list_coverage_snapshots


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

QUESTA_DETAILS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<coverageReport>
  <instance name="tb_top.dut">
    <statement name="stmt_12" source="rtl/dut.sv" line="12" hits="0" />
    <branch name="if_ready.true" source="rtl/dut.sv" line="20" hits="4" />
    <toggle name="ready[0]" hits="0" />
    <covergroup name="cg_packets">
      <coverpoint name="opcode">
        <bin name="READ" hits="3" />
        <bin name="WRITE" hits="0" />
      </coverpoint>
    </covergroup>
  </instance>
</coverageReport>
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


def test_parse_questa_xml_normalizes_item_level_points_and_holes():
    points = parse_questa_coverage_xml(QUESTA_DETAILS_XML)

    assert len(points) == 5
    assert {point["type"] for point in points} == {
        "branch",
        "covergroup",
        "statement",
        "toggle",
    }
    statement = next(point for point in points if point["type"] == "statement")
    assert statement["count"] == 0
    assert statement["hit"] is False
    assert statement["name"].endswith("stmt_12@rtl/dut.sv:12")

    holes = build_coverage_hole_report(points)
    assert holes["total_holes"] == 3
    assert holes["by_type"] == {
        "covergroup": 1,
        "statement": 1,
        "toggle": 1,
    }


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
        if command[1:3] == ["report", "-xml"]:
            out_path = Path(command[command.index("-output") + 1])
            out_path.write_text(QUESTA_DETAILS_XML, encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="XML report complete\n")
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
        "-xml",
        "-output",
        result["details_path"],
        result["merged"],
    ]
    assert len(result["inputs"]) == 2
    assert Path(result["merged"]).exists()
    assert Path(result["summary"]).read_text(encoding="utf-8") == QUESTA_SUMMARY

    payload = json.loads(Path(result["metrics_path"]).read_text(encoding="utf-8"))
    assert payload["simulator"] == "questa"
    assert payload["input_count"] == 2
    assert payload["tool_total_coverage"] == 79.53
    assert payload["by_type"]["expression"]["hit"] == 1143
    assert payload["item_points"] == 5
    assert Path(result["details_path"]).read_text(encoding="utf-8") == QUESTA_DETAILS_XML
    assert load_normalized_coverage_points(project) == result["points"]
    holes = build_coverage_hole_report(result["points"])
    assert holes["total_holes"] == 3

    snapshots = list_coverage_snapshots(project, limit=5)
    assert len(snapshots) == 1
    assert snapshots[0]["simulator"] == "questa"
    assert snapshots[0]["total_points"] == 82535
    assert snapshots[0]["hit_points"] == 46619
