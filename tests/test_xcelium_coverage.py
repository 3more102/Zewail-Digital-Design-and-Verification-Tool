from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from zddv.cli import cmd_coverage_history
from zddv.config import ProjectConfig
from zddv.coverage import (
    merge_coverage,
    merge_xcelium_coverage,
    parse_xcelium_imc_summary,
)
from zddv.storage import list_coverage_score_snapshots


IMC_SUMMARY = """IMC(64): test build
Starting batch mode
Legend: Metric* means cumulative
name Overall* Average Overall* Covered Code* Average Code* Covered Fsm* Average Fsm* Covered Functional* Average Functional* Covered
--------------------------------------------------------------------------------------------------------------------------------
tb_top 86.25% 82.50% (33/40) 80.00% 75.00% (18/24) n/a n/a 92.50% 90.00% (9/10)
"""


def _project(tmp_path: Path, simulator: str = "xcelium") -> ProjectConfig:
    root = tmp_path / "demo"
    root.mkdir()
    return ProjectConfig(
        root=root,
        name="demo",
        top="tb_top",
        simulator=simulator,
        waveform=False,
        coverage=True,
    )


def _coverage_run(project: ProjectConfig, name: str) -> Path:
    path = (
        project.root
        / project.run_dir
        / name
        / "coverage"
        / name
    ).resolve()
    path.mkdir(parents=True)
    (path / f"icc_{name}.ucd").write_text("data", encoding="utf-8")
    return path


def test_parse_xcelium_imc_summary_uses_explicit_overall_columns():
    metrics = parse_xcelium_imc_summary(IMC_SUMMARY)

    assert metrics["scope"] == "tb_top"
    assert metrics["tool_total_coverage"] == pytest.approx(82.50)
    assert metrics["by_metric"]["overall_average"] == pytest.approx(86.25)
    assert metrics["by_metric"]["overall_covered"] == pytest.approx(82.50)
    assert metrics["metric_semantics"] == "imc-summary-overall"


def test_parse_xcelium_imc_summary_rejects_unknown_layout():
    with pytest.raises(ValueError, match="Overall Average/Covered"):
        parse_xcelium_imc_summary("name Some Other Columns\ntb 90.0%\n")


def test_merge_xcelium_coverage_uses_union_imc_and_persists_history(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    inputs = [
        _coverage_run(project, "run-a"),
        _coverage_run(project, "run-b"),
    ]
    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/cadence/bin/imc" if name == "imc" else None,
    )

    commands: list[list[str]] = []

    def fake_run(command, cwd):
        commands.append(list(command))
        cwd = Path(cwd)
        if "-load" not in command:
            assert command[:2] == ["/opt/cadence/bin/imc", "-execcmd"]
            assert "merge -overwrite -runfile" in command[2]
            assert "-out merged -metrics all" in command[2]
            assert "-initial_model union_all" in command[2]
            assert "-message 1" in command[2]
            merged = cwd / "cov_work" / "scope" / "merged"
            merged.mkdir(parents=True)
            (merged / "icc_merged.ucm").write_text("model", encoding="utf-8")
            (merged / "icc_merged.ucd").write_text("data", encoding="utf-8")
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "Merging IUS Coverage ...\n"
                    "Total conflicts during target model creation: 0\n"
                    "Total items not merged : 0\n"
                ),
            )

        assert command[command.index("-load") + 1].endswith(
            "cov_work/scope/merged"
        )
        assert command[command.index("-execcmd") + 1] == (
            "report -summary -cumulative on -inst -local off; exit"
        )
        return SimpleNamespace(returncode=0, stdout=IMC_SUMMARY)

    monkeypatch.setattr("zddv.coverage._run", fake_run)

    result = merge_xcelium_coverage(project)

    assert result["inputs"] == [str(path) for path in inputs]
    assert Path(result["merged"]).is_dir()
    assert Path(result["summary"]).read_text(encoding="utf-8") == IMC_SUMMARY
    assert result["metrics_status"] == "normalized"
    assert result["metrics"]["tool_total_coverage"] == pytest.approx(82.50)
    assert result["snapshot_id"] is not None
    assert len(commands) == 2
    assert "Total items not merged : 0" in Path(result["merge_log"]).read_text(
        encoding="utf-8"
    )

    runfile = Path(result["runfile"]).read_text(encoding="utf-8").splitlines()
    assert runfile == [
        str((inputs[0] / "icc_run-a.ucd").resolve()),
        str((inputs[1] / "icc_run-b.ucd").resolve()),
    ]

    snapshots = list_coverage_score_snapshots(project, limit=5)
    assert len(snapshots) == 1
    assert snapshots[0]["snapshot_id"] == result["snapshot_id"]
    assert snapshots[0]["score"] == pytest.approx(82.50)
    assert snapshots[0]["by_metric"]["overall_average"] == pytest.approx(86.25)

    manifest = json.loads(
        Path(result["metrics_path"]).read_text(encoding="utf-8")
    )
    assert manifest["simulator"] == "xcelium"
    assert manifest["merge_model"] == "union_all"
    assert manifest["merge_log"] == result["merge_log"]
    assert manifest["metrics"]["scope"] == "tb_top"


def test_merge_xcelium_coverage_requires_native_run_databases(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/cadence/bin/imc" if name == "imc" else None,
    )

    with pytest.raises(RuntimeError, match="No Xcelium coverage run databases"):
        merge_xcelium_coverage(project)


@pytest.mark.parametrize("simulator", ["xcelium", "xrun"])
def test_merge_coverage_dispatches_xcelium_aliases(
    tmp_path: Path,
    monkeypatch,
    simulator: str,
):
    project = _project(tmp_path, simulator=simulator)
    expected = {"inputs": [], "merged": "merged"}

    monkeypatch.setattr(
        "zddv.coverage.merge_xcelium_coverage",
        lambda loaded: expected if loaded is project else None,
    )

    assert merge_coverage(project) is expected


def test_xcelium_coverage_history_uses_score_snapshots(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    project = _project(tmp_path)
    _coverage_run(project, "run-a")
    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/cadence/bin/imc" if name == "imc" else None,
    )

    def fake_run(command, cwd):
        cwd = Path(cwd)
        if "-load" not in command:
            merged = cwd / "cov_work" / "scope" / "merged"
            merged.mkdir(parents=True)
            (merged / "icc_merged.ucm").write_text("model", encoding="utf-8")
            (merged / "icc_merged.ucd").write_text("data", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="merge complete\n")
        return SimpleNamespace(returncode=0, stdout=IMC_SUMMARY)

    monkeypatch.setattr("zddv.coverage._run", fake_run)
    merge_xcelium_coverage(project)
    monkeypatch.setattr("zddv.cli.load_project", lambda path: project)

    rc = cmd_coverage_history(
        SimpleNamespace(project=str(project.root), limit=5)
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "82.50%" in output
    assert "overall_average=86.25%" in output
