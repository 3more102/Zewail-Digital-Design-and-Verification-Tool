from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from zddv.cli import cmd_coverage
from zddv.config import ProjectConfig
from zddv.coverage import (
    merge_coverage,
    merge_vcs_coverage,
    parse_vcs_urg_dashboard,
)
from zddv.storage import list_coverage_snapshots


URG_DASHBOARD = """Unified Coverage Report

Total Coverage Summary
SCORE | LINE | COND | TOGGLE | FSM | BRANCH
94.50 | 96.00 | 91.25 | 97.50 | 88.00 | 99.75

Hierarchical coverage data for top-level instances
"""


def _project(tmp_path: Path) -> ProjectConfig:
    root = tmp_path / "demo"
    root.mkdir()
    return ProjectConfig(
        root=root,
        name="demo",
        top="tb_top",
        simulator="vcs",
        rtl=[],
        tb=[],
        waveform=False,
        coverage=True,
    )


def test_parse_vcs_urg_dashboard_preserves_score_only_metrics():
    metrics = parse_vcs_urg_dashboard(URG_DASHBOARD)

    assert metrics["counts_available"] is False
    assert metrics["total_points"] == 0
    assert metrics["hit_points"] == 0
    assert metrics["hit_rate"] == 94.50
    assert metrics["tool_total_coverage"] == 94.50
    assert metrics["score_source"] == "urg-dashboard"
    assert metrics["by_type"] == {
        "branch": {"hit_rate": 99.75},
        "condition": {"hit_rate": 91.25},
        "fsm": {"hit_rate": 88.00},
        "line": {"hit_rate": 96.00},
        "toggle": {"hit_rate": 97.50},
    }


def test_parse_vcs_urg_dashboard_rejects_missing_summary():
    metrics = parse_vcs_urg_dashboard("no coverage table here\n")

    assert metrics["tool_total_coverage"] is None
    assert metrics["by_type"] == {}
    assert metrics["counts_available"] is False


def test_merge_vcs_coverage_runs_urg_and_persists_score_snapshot(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    run_root = (project.root / project.run_dir).resolve()
    for name in ("run-a", "run-b"):
        (run_root / name / "coverage.vdb").mkdir(parents=True)

    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/synopsys/bin/urg" if name == "urg" else None,
    )

    commands: list[list[str]] = []

    def fake_run(command, cwd):
        commands.append(list(command))
        merged = Path(command[command.index("-dbname") + 1])
        report = Path(command[command.index("-report") + 1])
        merged.mkdir(parents=True)
        report.mkdir(parents=True)
        (report / "dashboard.txt").write_text(
            URG_DASHBOARD,
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="URG complete\n")

    monkeypatch.setattr("zddv.coverage._run", fake_run)

    result = merge_vcs_coverage(project)

    command = commands[0]
    assert command[0] == "/opt/synopsys/bin/urg"
    assert command[1] == "-dir"
    assert command[command.index("-format") + 1] == "text"
    assert Path(result["merged"]).is_dir()
    assert Path(result["summary"]).name == "dashboard.txt"
    assert result["metrics"]["counts_available"] is False
    assert result["metrics"]["hit_rate"] == 94.50
    assert result["metrics"]["by_type"]["branch"]["hit_rate"] == 99.75

    payload = json.loads(
        Path(result["metrics_path"]).read_text(encoding="utf-8")
    )
    assert payload["simulator"] == "vcs"
    assert payload["input_count"] == 2
    assert payload["tool_total_coverage"] == 94.50
    assert payload["counts_available"] is False
    assert payload["report_dir"] == str(
        project.root / ".zddv" / "coverage" / "urg-report"
    )

    snapshots = list_coverage_snapshots(project, limit=5)
    assert len(snapshots) == 1
    assert snapshots[0]["simulator"] == "vcs"
    assert snapshots[0]["total_points"] == 0
    assert snapshots[0]["hit_points"] == 0
    assert snapshots[0]["hit_rate"] == pytest.approx(94.50)
    assert snapshots[0]["by_type"]["line"]["hit_rate"] == 96.00


def test_merge_coverage_dispatches_vcs(monkeypatch, tmp_path: Path):
    project = _project(tmp_path)
    sentinel = {"snapshot_id": "vcs-cov"}
    monkeypatch.setattr(
        "zddv.coverage.merge_vcs_coverage",
        lambda loaded: sentinel,
    )

    assert merge_coverage(project) is sentinel


def test_coverage_cli_prints_vcs_score_without_fake_point_counts(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    project = _project(tmp_path)
    monkeypatch.setattr("zddv.cli.load_project", lambda path: project)
    monkeypatch.setattr(
        "zddv.cli.merge_coverage",
        lambda loaded: {
            "inputs": ["run-a/coverage.vdb", "run-b/coverage.vdb"],
            "merged": "/tmp/coverage.vdb",
            "summary": "/tmp/urg-report/dashboard.txt",
            "metrics_path": "/tmp/metrics.json",
            "metrics": {
                "total_points": 0,
                "hit_points": 0,
                "unhit_points": 0,
                "hit_rate": 94.50,
                "by_type": {
                    "line": {"hit_rate": 96.00},
                    "branch": {"hit_rate": 99.75},
                },
                "tool_total_coverage": 94.50,
                "counts_available": False,
                "score_source": "urg-dashboard",
            },
            "snapshot_id": "cov-vcs-test",
            "report": "",
            "report_dir": "/tmp/urg-report",
        },
    )

    rc = cmd_coverage(SimpleNamespace(project=str(project.root)))

    assert rc == 0
    output = capsys.readouterr().out
    assert "Coverage score: 94.50%" in output
    assert "raw point counts unavailable" in output
    assert "Coverage points:" not in output
