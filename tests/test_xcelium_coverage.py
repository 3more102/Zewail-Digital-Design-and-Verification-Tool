from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from zddv.config import ProjectConfig
from zddv.storage import list_coverage_score_snapshots
from zddv.coverage import merge_coverage, merge_xcelium_coverage, parse_xcelium_imc_summary


def _project(tmp_path: Path) -> ProjectConfig:
    root = tmp_path / "demo"
    root.mkdir()
    return ProjectConfig(
        root=root,
        name="demo",
        top="tb_top",
        simulator="xcelium",
        coverage=True,
        waveform=False,
    )


def _coverage_run(project: ProjectConfig, run_id: str) -> Path:
    path = (
        project.root
        / project.run_dir
        / run_id
        / "coverage"
        / run_id
    ).resolve()
    path.mkdir(parents=True)
    (path / f"{run_id}.ucd").write_text("fixture\n", encoding="utf-8")
    return path


def test_merge_xcelium_coverage_uses_imc_and_retains_evidence(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    first = _coverage_run(project, "run-a")
    second = _coverage_run(project, "run-b")

    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/cadence/bin/imc" if name == "imc" else None,
    )

    captured: dict[str, object] = {}

    def fake_run(command, cwd):
        captured["command"] = list(command)
        captured["cwd"] = Path(cwd)
        script_path = Path(command[command.index("-exec") + 1])
        captured["script"] = script_path.read_text(encoding="utf-8")

        out_dir = project.root / ".zddv" / "coverage"
        merged = out_dir / "xcelium-imc-merged"
        merged.mkdir(parents=True)
        (merged / "merged.ucd").write_text("merged\n", encoding="utf-8")
        report_dir = out_dir / "xcelium-imc-report"
        (report_dir / "summary.txt").write_text(
            "IMC summary fixture\n",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="IMC merge complete\n")

    monkeypatch.setattr("zddv.coverage._run", fake_run)

    result = merge_coverage(project)

    assert result["inputs"] == [str(first), str(second)]
    assert result["metrics"] is None
    assert result["metrics_status"] == "not-normalized"
    assert Path(result["merged"]).name == "xcelium-imc-merged"
    assert Path(result["summary"]).name == "summary.txt"

    command = captured["command"]
    assert command[:3] == ["/opt/cadence/bin/imc", "-batch", "-exec"]
    script = str(captured["script"])
    assert "merge -out" in script
    assert "-overwrite" in script
    assert first.as_posix() in script
    assert second.as_posix() in script
    assert "load -run" in script
    assert 'report -summary -inst "*..."' in script
    assert script.rstrip().endswith("exit")

    manifest = json.loads(
        Path(result["metrics_path"]).read_text(encoding="utf-8")
    )
    assert manifest["status"] == "merged-report-captured"
    assert manifest["metrics_status"] == "not-normalized"
    assert manifest["input_count"] == 2
    assert len(manifest["merged_ucd_files"]) == 1
    assert manifest["snapshot_id"] is None


def test_merge_xcelium_coverage_requires_native_run_database(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/cadence/bin/imc" if name == "imc" else None,
    )

    with pytest.raises(RuntimeError, match=r"No Xcelium \.ucd run databases"):
        merge_xcelium_coverage(project)


def test_merge_xcelium_coverage_rejects_missing_merged_ucd(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    _coverage_run(project, "run-a")
    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/cadence/bin/imc" if name == "imc" else None,
    )
    monkeypatch.setattr(
        "zddv.coverage._run",
        lambda command, cwd: SimpleNamespace(
            returncode=0,
            stdout="IMC returned success without a merged database\n",
        ),
    )

    with pytest.raises(RuntimeError, match="IMC coverage merge/report failed"):
        merge_xcelium_coverage(project)


def test_merge_xcelium_coverage_requires_imc(tmp_path: Path, monkeypatch):
    project = _project(tmp_path)
    _coverage_run(project, "run-a")
    monkeypatch.setattr("zddv.coverage.shutil.which", lambda name: None)

    with pytest.raises(RuntimeError, match="Cadence IMC was not found"):
        merge_xcelium_coverage(project)

def test_parse_xcelium_imc_summary_normalizes_explicit_overall_scores():
    report = """
name     Overall* Average   Overall* Covered   Block   Expr
tb_top   87.50%             91.25%             88.00%  90.00%
"""

    metrics = parse_xcelium_imc_summary(report)

    assert metrics["tool_total_coverage"] == pytest.approx(91.25)
    assert metrics["by_metric"] == {
        "overall_average": pytest.approx(87.50),
        "overall_covered": pytest.approx(91.25),
    }
    assert metrics["scope"] == "tb_top"
    assert metrics["metric_semantics"] == "imc-summary-overall"


def test_parse_xcelium_imc_summary_rejects_unlabelled_or_out_of_range_scores():
    with pytest.raises(ValueError, match="Overall Average/Covered"):
        parse_xcelium_imc_summary("tb_top 87.50% 91.25%\n")

    with pytest.raises(ValueError, match="outside 0..100"):
        parse_xcelium_imc_summary(
            "name Overall Average Overall Covered\n"
            "tb_top 87.50% 101.00%\n"
        )


def test_merge_xcelium_coverage_persists_explicit_overall_score(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    _coverage_run(project, "run-a")
    _coverage_run(project, "run-b")

    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/cadence/bin/imc" if name == "imc" else None,
    )

    def fake_run(command, cwd):
        out_dir = project.root / ".zddv" / "coverage"
        merged = out_dir / "xcelium-imc-merged"
        merged.mkdir(parents=True)
        (merged / "merged.ucd").write_text("merged\n", encoding="utf-8")
        report_dir = out_dir / "xcelium-imc-report"
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / "summary.txt").write_text(
            "name Overall* Average Overall* Covered Block\n"
            "tb_top 84.50% 89.75% 88.00%\n",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="IMC merge complete\n")

    monkeypatch.setattr("zddv.coverage._run", fake_run)

    result = merge_xcelium_coverage(project)

    assert result["metrics_status"] == "normalized"
    assert result["metrics"]["tool_total_coverage"] == pytest.approx(89.75)
    assert result["metrics"]["by_metric"]["overall_average"] == pytest.approx(84.50)
    assert result["snapshot_id"]

    rows = list_coverage_score_snapshots(project, limit=1)
    assert len(rows) == 1
    assert rows[0]["simulator"] == "xcelium"
    assert rows[0]["score"] == pytest.approx(89.75)
    assert rows[0]["by_metric"]["overall_covered"] == pytest.approx(89.75)

