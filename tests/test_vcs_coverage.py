from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from zddv.cli import cmd_coverage, cmd_coverage_history
from zddv.config import ProjectConfig
from zddv.coverage import merge_vcs_coverage, parse_vcs_urg_dashboard
from zddv.storage import list_coverage_score_snapshots


def _project(tmp_path: Path) -> ProjectConfig:
    root = tmp_path / "demo"
    (root / "rtl").mkdir(parents=True)
    (root / "tb").mkdir()
    (root / "rtl" / "dut.sv").write_text(
        "module dut; endmodule\n",
        encoding="utf-8",
    )
    (root / "tb" / "tb_top.sv").write_text(
        "module tb_top; dut u_dut(); endmodule\n",
        encoding="utf-8",
    )
    return ProjectConfig(
        root=root,
        name="demo",
        top="tb_top",
        simulator="vcs",
        rtl=["rtl/*.sv"],
        tb=["tb/*.sv"],
        waveform=False,
        coverage=True,
    )


def test_merge_vcs_coverage_uses_urg_and_retains_report_evidence(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    run_root = (project.root / project.run_dir).resolve()
    inputs = []
    for name in ("run-a", "run-b"):
        coverage = run_root / name / "coverage.vdb"
        coverage.mkdir(parents=True)
        (coverage / "fixture.txt").write_text(name, encoding="utf-8")
        inputs.append(str(coverage))

    out_dir = (project.root / ".zddv" / "coverage").resolve()
    stale_merged = out_dir / "coverage.vdb"
    stale_report = out_dir / "urg-report"
    stale_merged.mkdir(parents=True)
    stale_report.mkdir()
    (stale_merged / "stale").write_text("old", encoding="utf-8")
    (stale_report / "stale").write_text("old", encoding="utf-8")

    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/synopsys/bin/urg" if name == "urg" else None,
    )
    captured: dict[str, object] = {}

    def fake_run(command, cwd):
        captured["command"] = list(command)
        captured["cwd"] = Path(cwd)
        assert not stale_merged.exists()
        assert not stale_report.exists()
        (Path(cwd) / command[command.index("-dbname") + 1]).mkdir()
        report_dir = Path(cwd) / command[command.index("-report") + 1]
        report_dir.mkdir()
        (report_dir / "dashboard.txt").write_text(
            """Unified Coverage Report

Total Coverage Summary
SCORE  LINE  COND  TOGGLE  FSM  BRANCH  ASSERT  GROUP
97.74  99.03  97.75  98.53  100.00  99.01  98.63  91.19

Total Groups Coverage Summary
COVERED  EXPECTED  SCORE  COVERED  EXPECTED  INST SCORE  WEIGHT
491  528  92.99  490  527  92.98  1
""",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="URG merge complete\n")

    monkeypatch.setattr("zddv.coverage._run", fake_run)

    result = merge_vcs_coverage(project)

    command = captured["command"]
    assert command == [
        "/opt/synopsys/bin/urg",
        "-dir",
        *inputs,
        "-dbname",
        "coverage.vdb",
        "-report",
        "urg-report",
        "-format",
        "both",
    ]
    assert captured["cwd"] == out_dir
    assert Path(result["merged"]).is_dir()
    assert Path(result["report_dir"]).is_dir()
    assert result["metrics_status"] == "normalized"
    assert result["metrics"]["tool_total_coverage"] == pytest.approx(97.74)
    assert result["metrics"]["by_metric"]["line"] == pytest.approx(99.03)
    assert result["metrics"]["by_metric"]["group"] == pytest.approx(91.19)
    assert result["metrics"]["by_metric_counts"]["group"] == {
        "covered": 491,
        "total": 528,
        "hit_rate": pytest.approx(92.99),
    }
    assert result["metrics"]["by_metric_counts"]["group_instance"] == {
        "covered": 490,
        "total": 527,
        "hit_rate": pytest.approx(92.98),
    }
    assert result["metrics"]["count_status"] == "normalized"
    assert result["snapshot_id"] is not None
    assert Path(result["summary"]).name == "dashboard.txt"

    snapshots = list_coverage_score_snapshots(project, limit=5)
    assert len(snapshots) == 1
    assert snapshots[0]["snapshot_id"] == result["snapshot_id"]
    assert snapshots[0]["score"] == pytest.approx(97.74)
    assert snapshots[0]["by_metric"]["branch"] == pytest.approx(99.01)
    assert snapshots[0]["by_metric_counts"]["group"]["covered"] == 491
    assert snapshots[0]["by_metric_counts"]["group"]["total"] == 528
    assert snapshots[0]["by_metric_counts"]["group_instance"]["covered"] == 490

    manifest = json.loads(
        Path(result["metrics_path"]).read_text(encoding="utf-8")
    )
    assert manifest["status"] == "merged-report-captured"
    assert manifest["metrics_status"] == "normalized"
    assert manifest["input_count"] == 2
    assert manifest["inputs"] == inputs
    assert manifest["command"] == command
    assert manifest["metrics"]["tool_total_coverage"] == pytest.approx(97.74)
    assert manifest["metrics"]["by_metric_counts"]["group"]["covered"] == 491
    assert manifest["metrics"]["by_metric_counts"]["group_instance"]["total"] == 527


def test_merge_vcs_coverage_requires_per_run_vdb(tmp_path: Path, monkeypatch):
    project = _project(tmp_path)
    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/synopsys/bin/urg" if name == "urg" else None,
    )

    with pytest.raises(RuntimeError, match="No coverage.vdb directories"):
        merge_vcs_coverage(project)


def test_vcs_coverage_cli_reports_pending_normalization(
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
            "summary": "/tmp/urg-report",
            "metrics_path": "/tmp/vcs-coverage.json",
            "metrics": None,
            "snapshot_id": None,
            "metrics_status": "pending-normalization",
            "report": "URG merge complete\n",
        },
    )

    rc = cmd_coverage(SimpleNamespace(project=str(project.root)))

    assert rc == 0
    output = capsys.readouterr().out
    assert "Coverage inputs: 2" in output
    assert "Merged coverage: /tmp/coverage.vdb" in output
    assert "Summary: /tmp/urg-report" in output
    assert "Coverage metrics: pending-normalization" in output
    assert "Coverage evidence: /tmp/vcs-coverage.json" in output
    assert "Snapshot: None" not in output


def test_merge_vcs_coverage_requires_urg(tmp_path: Path, monkeypatch):
    project = _project(tmp_path)
    coverage = (project.root / project.run_dir / "run-a" / "coverage.vdb").resolve()
    coverage.mkdir(parents=True)
    monkeypatch.setattr("zddv.coverage.shutil.which", lambda name: None)

    with pytest.raises(RuntimeError, match="Synopsys URG was not found"):
        merge_vcs_coverage(project)


def test_merge_vcs_coverage_requires_expected_outputs(tmp_path: Path, monkeypatch):
    project = _project(tmp_path)
    coverage = (project.root / project.run_dir / "run-a" / "coverage.vdb").resolve()
    coverage.mkdir(parents=True)

    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/synopsys/bin/urg" if name == "urg" else None,
    )
    monkeypatch.setattr(
        "zddv.coverage._run",
        lambda command, cwd: SimpleNamespace(
            returncode=0,
            stdout="URG returned success without artifacts\n",
        ),
    )

    with pytest.raises(RuntimeError, match="VCS coverage merge/report failed"):
        merge_vcs_coverage(project)



def test_parse_vcs_urg_dashboard_accepts_missing_metric_placeholder(tmp_path: Path):
    dashboard = tmp_path / "dashboard.txt"
    dashboard.write_text(
        """Unified Coverage Report

Total Coverage Summary
SCORE LINE COND TOGGLE FSM BRANCH ASSERT GROUP
89.75 99.33 93.67 100.00 -- 98.40 99.51 47.58
""",
        encoding="utf-8",
    )

    metrics = parse_vcs_urg_dashboard(dashboard)

    assert metrics["tool_total_coverage"] == pytest.approx(89.75)
    assert "fsm" not in metrics["by_metric"]
    assert metrics["by_metric"]["branch"] == pytest.approx(98.40)
    assert metrics["by_metric"]["group"] == pytest.approx(47.58)


def test_parse_vcs_urg_dashboard_preserves_blank_pipe_metric(tmp_path: Path):
    dashboard = tmp_path / "dashboard.txt"
    dashboard.write_text(
        """Unified Coverage Report

Total Coverage Summary
SCORE | LINE | COND | TOGGLE | FSM | BRANCH | ASSERT | GROUP
95.96 | 95.39 | 93.47 | 95.36 |  | 94.22 | 97.71 | 99.60
""",
        encoding="utf-8",
    )

    metrics = parse_vcs_urg_dashboard(dashboard)

    assert metrics["tool_total_coverage"] == pytest.approx(95.96)
    assert metrics["by_metric"]["toggle"] == pytest.approx(95.36)
    assert "fsm" not in metrics["by_metric"]
    assert metrics["by_metric"]["branch"] == pytest.approx(94.22)
    assert metrics["by_metric"]["group"] == pytest.approx(99.60)


def test_parse_vcs_urg_dashboard_preserves_blank_fixed_width_metric(tmp_path: Path):
    dashboard = tmp_path / "dashboard.txt"
    header = "SCORE   LINE    COND    TOGGLE   FSM     BRANCH   ASSERT   GROUP"
    names = ("SCORE", "LINE", "COND", "TOGGLE", "FSM", "BRANCH", "ASSERT", "GROUP")
    starts = [header.index(name) for name in names]
    values = ["95.96", "95.39", "93.47", "95.36", "", "94.22", "97.71", "99.60"]
    row = [" "] * 72
    for start, value in zip(starts, values, strict=True):
        row[start : start + len(value)] = value
    dashboard.write_text(
        "Unified Coverage Report\n\n"
        "Total Coverage Summary\n"
        + header
        + "\n"
        + "".join(row).rstrip()
        + "\n",
        encoding="utf-8",
    )

    metrics = parse_vcs_urg_dashboard(dashboard)

    assert metrics["tool_total_coverage"] == pytest.approx(95.96)
    assert "fsm" not in metrics["by_metric"]
    assert metrics["by_metric"]["branch"] == pytest.approx(94.22)
    assert metrics["by_metric"]["group"] == pytest.approx(99.60)


def test_parse_vcs_urg_dashboard_parses_documented_group_counts(tmp_path: Path):
    dashboard = tmp_path / "dashboard.txt"
    dashboard.write_text(
        """Unified Coverage Report

Total Coverage Summary
SCORE LINE COND TOGGLE FSM BRANCH ASSERT GROUP
95.90 98.23 93.91 97.02 93.02 96.33 99.77 92.99

Total Groups Coverage Summary
COVERED EXPECTED SCORE COVERED EXPECTED INST SCORE WEIGHT
491 528 92.99 487 529 92.06 1
""",
        encoding="utf-8",
    )

    metrics = parse_vcs_urg_dashboard(dashboard)

    assert metrics["by_metric_counts"]["group"] == {
        "covered": 491,
        "total": 528,
        "hit_rate": pytest.approx(92.99),
    }
    assert metrics["by_metric_counts"]["group_instance"] == {
        "covered": 487,
        "total": 529,
        "hit_rate": pytest.approx(92.06),
    }
    assert metrics["count_status"] == "normalized"


def test_parse_vcs_urg_dashboard_rejects_ambiguous_summary(tmp_path: Path):
    dashboard = tmp_path / "dashboard.txt"
    dashboard.write_text(
        """Total Coverage Summary
SCORE LINE COND
not-a-score-row
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="score row could not be parsed"):
        parse_vcs_urg_dashboard(dashboard)


def test_merge_vcs_coverage_keeps_evidence_when_dashboard_is_missing(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    coverage = (project.root / project.run_dir / "run-a" / "coverage.vdb").resolve()
    coverage.mkdir(parents=True)

    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/synopsys/bin/urg" if name == "urg" else None,
    )

    def fake_run(command, cwd):
        (Path(cwd) / command[command.index("-dbname") + 1]).mkdir()
        (Path(cwd) / command[command.index("-report") + 1]).mkdir()
        return SimpleNamespace(returncode=0, stdout="URG merge complete\n")

    monkeypatch.setattr("zddv.coverage._run", fake_run)

    result = merge_vcs_coverage(project)

    assert result["metrics"] is None
    assert result["snapshot_id"] is None
    assert result["metrics_status"] == "dashboard-missing"
    assert Path(result["report_dir"]).is_dir()
    assert list_coverage_score_snapshots(project, limit=5) == []


def test_vcs_coverage_cli_surfaces_normalized_urg_scores(
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
            "metrics_path": "/tmp/vcs-coverage.json",
            "metrics": {
                "tool_total_coverage": 97.74,
                "by_metric": {
                    "line": 99.03,
                    "condition": 97.75,
                    "branch": 99.01,
                },
                "by_metric_counts": {
                    "group": {
                        "covered": 491,
                        "total": 528,
                        "hit_rate": 92.99,
                    }
                },
            },
            "snapshot_id": "cov-score-test",
            "metrics_status": "normalized",
            "report": "",
        },
    )

    rc = cmd_coverage(SimpleNamespace(project=str(project.root)))

    assert rc == 0
    output = capsys.readouterr().out
    assert "Simulator-reported total coverage: 97.74%" in output
    assert "branch=99.01%" in output
    assert "condition=97.75%" in output
    assert "line=99.03%" in output
    assert "Coverage object counts: group=491/528 (92.99%)" in output
    assert "Snapshot: cov-score-test" in output
    assert "Coverage points:" not in output


def test_vcs_coverage_history_uses_score_native_snapshots(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    project = _project(tmp_path)
    monkeypatch.setattr("zddv.cli.load_project", lambda path: project)
    monkeypatch.setattr(
        "zddv.cli.list_coverage_score_snapshots",
        lambda loaded, limit: [
            {
                "snapshot_id": "cov-score-test",
                "score": 97.74,
                "input_count": 2,
                "by_metric": {"line": 99.03, "branch": 99.01},
                "by_metric_counts": {
                    "group": {
                        "covered": 491,
                        "total": 528,
                        "hit_rate": 92.99,
                    }
                },
            }
        ],
    )

    rc = cmd_coverage_history(
        SimpleNamespace(project=str(project.root), limit=5)
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "97.74%" in output
    assert "cov-score-test" in output
    assert "branch=99.01%" in output
    assert "line=99.03%" in output
    assert "counts: group=491/528 (92.99%)" in output
    assert "HIT/TOTAL" not in output
