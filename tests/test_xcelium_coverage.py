from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from zddv.config import ProjectConfig
from zddv.coverage import merge_coverage, merge_xcelium_coverage


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
        simulator="xcelium",
        rtl=["rtl/*.sv"],
        tb=["tb/*.sv"],
        waveform=False,
        coverage=True,
    )


def _coverage_inputs(project: ProjectConfig) -> list[str]:
    run_root = (project.root / project.run_dir).resolve()
    inputs: list[str] = []
    for name in ("run-a", "run-b"):
        coverage = run_root / name / "coverage" / name
        coverage.mkdir(parents=True)
        ucd = coverage / f"icc_{name}.ucd"
        ucd.write_text("ucd fixture\n", encoding="utf-8")
        inputs.append(str(ucd))
    return inputs


def test_merge_xcelium_coverage_uses_imc_and_retains_evidence(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    inputs = _coverage_inputs(project)
    out_dir = (project.root / ".zddv" / "coverage").resolve()

    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/cadence/bin/imc" if name == "imc" else None,
    )
    captured: dict[str, object] = {}

    def fake_run(command, cwd):
        captured["command"] = list(command)
        captured["cwd"] = Path(cwd)
        merged = out_dir / "xcelium-merged"
        merged.mkdir(parents=True)
        (merged / "merged.ucd").write_text("merged\n", encoding="utf-8")
        (out_dir / "xcelium-summary.txt").write_text(
            "IMC report fixture\n",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="IMC merge complete\n")

    monkeypatch.setattr("zddv.coverage._run", fake_run)

    result = merge_xcelium_coverage(project)

    command = captured["command"]
    assert command[:2] == ["/opt/cadence/bin/imc", "-batch"]
    assert command[2] == "-execcmd"
    assert "merge -runfile" in command[3]
    assert "report_metrics -out" in command[3]
    assert "-detail -metrics all" in command[3]
    assert captured["cwd"] == project.root

    runfile = Path(result["runfile"])
    assert runfile.read_text(encoding="utf-8").splitlines() == inputs
    assert Path(result["merged"]).is_dir()
    assert result["report_status"] == "captured"
    assert Path(result["report_path"]).is_file()
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
    assert manifest["report_status"] == "captured"
    assert len(manifest["merged_ucd"]) == 1


def test_merge_xcelium_coverage_requires_native_ucd(tmp_path: Path, monkeypatch):
    project = _project(tmp_path)
    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/cadence/bin/imc" if name == "imc" else None,
    )

    with pytest.raises(RuntimeError, match="No Xcelium .ucd coverage files"):
        merge_xcelium_coverage(project)


def test_merge_xcelium_coverage_requires_imc(tmp_path: Path, monkeypatch):
    project = _project(tmp_path)
    _coverage_inputs(project)
    monkeypatch.setattr("zddv.coverage.shutil.which", lambda name: None)

    with pytest.raises(RuntimeError, match="Cadence IMC was not found"):
        merge_xcelium_coverage(project)


def test_merge_xcelium_coverage_requires_merged_ucd(tmp_path: Path, monkeypatch):
    project = _project(tmp_path)
    _coverage_inputs(project)
    monkeypatch.setattr(
        "zddv.coverage.shutil.which",
        lambda name: "/opt/cadence/bin/imc" if name == "imc" else None,
    )
    monkeypatch.setattr(
        "zddv.coverage._run",
        lambda command, cwd: SimpleNamespace(
            returncode=0,
            stdout="IMC returned success without a merged UCD\n",
        ),
    )

    with pytest.raises(RuntimeError, match="Xcelium IMC coverage merge/report failed"):
        merge_xcelium_coverage(project)


def test_merge_coverage_dispatches_xcelium(tmp_path: Path, monkeypatch):
    project = _project(tmp_path)
    sentinel = {"simulator": "xcelium"}
    monkeypatch.setattr(
        "zddv.coverage.merge_xcelium_coverage",
        lambda loaded: sentinel,
    )

    assert merge_coverage(project) is sentinel
