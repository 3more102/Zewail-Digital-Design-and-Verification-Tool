from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from zddv.cli import cmd_coverage, cmd_coverage_holes
from zddv.config import ProjectConfig
from zddv.coverage import (
    merge_questa_coverage,
    parse_questa_coverage_summary,
    parse_questa_functional_coverage_report,
    parse_questa_zero_coverage_report,
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

QUESTA_CODE_ZEROS = """Coverage Report by file with details

=================================================================================
=== File: rtl/top.sv
=================================================================================
Statement Coverage:
    Enabled Coverage            Active      Hits    Misses % Covered
    ----------------            ------      ----    ------ ---------
    Stmts                            4         2         2     50.00

================================Statement Details================================

Statement Coverage for file rtl/top.sv --

    12              1                          0
    40              2                     ***0*** missing_statement

=================================================================================
=== File: rtl/control.sv
=================================================================================
Branch Coverage:
    Enabled Coverage            Active      Hits    Misses % Covered
    ----------------            ------      ----    ------ ---------
    Branches                         2         1         1     50.00

=================================Branch Details==================================

Branch Coverage for file rtl/control.sv --

    21                                          3 Count coming in to IF
    21              1                     ***0*** if (ready)

Toggle Coverage for file rtl/control.sv --

    7               1                     ***0*** state[0]
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


def test_parse_questa_zero_report_normalizes_source_items():
    points = parse_questa_zero_coverage_report(QUESTA_CODE_ZEROS)

    assert len(points) == 4
    assert points[0] == {
        "name": "rtl/top.sv:12:statement:1",
        "count": 0,
        "hit": False,
        "type": "statement",
        "source_file": "rtl/top.sv",
        "line": 12,
        "detail": "1                          0",
    }
    assert points[1]["name"] == "rtl/top.sv:40:statement:1"
    assert points[2]["type"] == "branch"
    assert points[2]["source_file"] == "rtl/control.sv"
    assert points[2]["line"] == 21
    assert points[3]["type"] == "toggle"
    assert all("Count coming in to IF" not in point["detail"] for point in points)


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
            output = Path(command[command.index("-output") + 1])
            output.write_text("<coverage/>\n", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="xml report written\n")
        if command[1:4] == ["report", "-zeros", "-details"]:
            output = Path(command[command.index("-output") + 1])
            output.write_text(QUESTA_CODE_ZEROS, encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="zero report written\n")
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
        "-codeAll",
        "-output",
        str(Path(result["merged"]).with_name("details.xml")),
        result["merged"],
    ]
    assert commands[4] == [
        "/opt/questa/bin/vcover",
        "report",
        "-zeros",
        "-details",
        "-codeAll",
        "-output",
        str(Path(result["merged"]).with_name("zeros.txt")),
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
    assert payload["functional_bins"] == 3
    assert payload["functional_snapshot_id"] == result["functional_snapshot_id"]
    evidence = payload["detailed_code_coverage_evidence"]
    assert evidence["xml"]["status"] == "captured"
    assert evidence["zero_detail"]["status"] == "captured"
    assert Path(evidence["xml"]["path"]).read_text(encoding="utf-8") == "<coverage/>\n"
    assert Path(evidence["zero_detail"]["path"]).read_text(
        encoding="utf-8"
    ) == QUESTA_CODE_ZEROS
    normalized = payload["normalized_code_coverage"]
    assert normalized["status"] == "ok"
    assert normalized["hole_items"] == 4
    code_items = json.loads(Path(normalized["path"]).read_text(encoding="utf-8"))
    assert len(code_items["points"]) == 4
    assert code_items["points"][2]["type"] == "branch"
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
            "detailed_code_coverage_evidence": {
                "xml": {"status": "captured", "path": "/tmp/details.xml"},
                "zero_detail": {"status": "captured", "path": "/tmp/zeros.txt"},
            },
            "normalized_code_coverage": {
                "status": "ok",
                "hole_items": 4,
                "path": "/tmp/code-items.json",
            },
        },
    )

    rc = cmd_coverage(SimpleNamespace(project=str(project.root)))

    assert rc == 0
    output = capsys.readouterr().out
    assert "Functional coverage bins: 3" in output
    assert "Functional snapshot: fcov-test" in output
    assert "Functional report: /tmp/functional.txt" in output
    assert "Detailed code coverage XML: captured /tmp/details.xml" in output
    assert "Zero-hit source detail: captured /tmp/zeros.txt" in output
    assert (
        "Normalized code coverage holes: ok 4 item(s) /tmp/code-items.json"
        in output
    )


def test_questa_detailed_evidence_failure_is_nonfatal_and_does_not_reuse_stale_files(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    run_dir = (project.root / project.run_dir / "run-a").resolve()
    run_dir.mkdir(parents=True)
    (run_dir / "coverage.ucdb").write_text("fixture\n", encoding="utf-8")

    out_dir = (project.root / ".zddv" / "coverage").resolve()
    out_dir.mkdir(parents=True)
    (out_dir / "details.xml").write_text("stale\n", encoding="utf-8")
    (out_dir / "zeros.txt").write_text("stale\n", encoding="utf-8")

    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/questa/bin/vcover" if name == "vcover" else None,
    )

    def fake_run(command, cwd):
        if command[1] == "merge":
            output = Path(command[command.index("-out") + 1])
            output.write_text("merged\n", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="merge complete\n")
        if command[1:3] == ["report", "-summary"]:
            return SimpleNamespace(returncode=0, stdout=QUESTA_SUMMARY)
        if command[1:4] == ["report", "-cvg", "-details"]:
            return SimpleNamespace(returncode=0, stdout="")
        if "-xml" in command or "-zeros" in command:
            return SimpleNamespace(returncode=2, stdout="unsupported fixture\n")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("zddv.coverage._run", fake_run)

    result = merge_questa_coverage(project)
    evidence = result["detailed_code_coverage_evidence"]

    assert evidence["xml"]["status"] == "failed"
    assert evidence["xml"]["returncode"] == 2
    assert evidence["xml"]["diagnostic"] == "unsupported fixture"
    assert evidence["zero_detail"]["status"] == "failed"
    assert evidence["zero_detail"]["diagnostic"] == "unsupported fixture"
    assert not Path(evidence["xml"]["path"]).exists()
    assert not Path(evidence["zero_detail"]["path"]).exists()
    normalized = result["normalized_code_coverage"]
    assert normalized["status"] == "failed"
    payload = json.loads(Path(normalized["path"]).read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["points"] == []


def test_coverage_holes_cli_reads_questa_normalized_items(tmp_path: Path, monkeypatch, capsys):
    project = _project(tmp_path)
    coverage_dir = project.root / ".zddv" / "coverage"
    coverage_dir.mkdir(parents=True)
    code_items = coverage_dir / "code-items.json"
    code_items.write_text(
        json.dumps(
            {
                "source": "questa-vcover-zero-detail",
                "status": "ok",
                "evidence_path": str(coverage_dir / "zeros.txt"),
                "points": parse_questa_zero_coverage_report(QUESTA_CODE_ZEROS),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("zddv.cli.load_project", lambda path: project)

    output = coverage_dir / "holes.json"
    rc = cmd_coverage_holes(
        SimpleNamespace(
            project=str(project.root),
            output=str(output),
            point_type="branch",
            limit=20,
            show=20,
        )
    )

    assert rc == 0
    text = capsys.readouterr().out
    assert "Coverage holes (branch): 1 unhit point(s)" in text
    assert "rtl/control.sv:21" in text
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["by_type"] == {"branch": 1}
    assert report["holes"][0]["source_file"] == "rtl/control.sv"
    assert report["holes"][0]["line"] == 21
