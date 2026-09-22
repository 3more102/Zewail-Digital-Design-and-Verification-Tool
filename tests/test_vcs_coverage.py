from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from zddv.cli import cmd_coverage
from zddv.config import ProjectConfig
from zddv.coverage import merge_vcs_coverage


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
        (Path(cwd) / command[command.index("-report") + 1]).mkdir()
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
    assert result["metrics"] is None
    assert result["snapshot_id"] is None
    assert result["metrics_status"] == "pending-normalization"

    manifest = json.loads(
        Path(result["metrics_path"]).read_text(encoding="utf-8")
    )
    assert manifest["status"] == "merged-report-captured"
    assert manifest["metrics_status"] == "pending-normalization"
    assert manifest["input_count"] == 2
    assert manifest["inputs"] == inputs
    assert manifest["command"] == command


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
