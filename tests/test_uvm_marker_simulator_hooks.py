from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from zddv.config import ProjectConfig
from zddv.simulator import QuestaBackend, VcsBackend, XceliumBackend


def _project(tmp_path: Path, simulator: str) -> ProjectConfig:
    root = tmp_path / simulator
    (root / "rtl").mkdir(parents=True)
    (root / "tb").mkdir()
    (root / "rtl" / "dut.sv").write_text("module dut; endmodule\n", encoding="utf-8")
    (root / "tb" / "tb_top.sv").write_text(
        "module tb_top; dut u_dut(); initial #1 $finish; endmodule\n",
        encoding="utf-8",
    )
    return ProjectConfig(
        root=root,
        name="marker-hook",
        top="tb_top",
        simulator=simulator,
        rtl=["rtl/*.sv"],
        tb=["tb/*.sv"],
        waveform=False,
        coverage=False,
    )


@pytest.mark.parametrize(
    ("simulator", "backend_type", "module_name"),
    [
        ("questa", QuestaBackend, "zddv.simulator.questa"),
        ("vcs", VcsBackend, "zddv.simulator.vcs"),
        ("xcelium", XceliumBackend, "zddv.simulator.xcelium"),
    ],
)
def test_commercial_run_auto_ingests_marker_only_output_without_false_uvm_report(
    tmp_path: Path,
    monkeypatch,
    simulator,
    backend_type,
    module_name,
):
    project = _project(tmp_path, simulator)
    backend = backend_type()

    if simulator == "questa":
        (project.root / ".zddv" / "build" / "work").mkdir(parents=True)
        monkeypatch.setattr(backend, "_tool", lambda name: name)
    elif simulator == "vcs":
        executable = (project.root / ".zddv" / "build" / "simv").resolve()
        executable.parent.mkdir(parents=True)
        executable.write_text("simv fixture\n", encoding="utf-8")
    else:
        (project.root / ".zddv" / "build" / "xcelium.d").mkdir(parents=True)
        monkeypatch.setattr(backend, "_tool", lambda: "xrun")

    monkeypatch.setattr(backend, "version", lambda: f"{simulator} test")

    marker_calls: list[dict[str, object]] = []
    report_calls: list[dict[str, object]] = []

    def fake_marker(project_arg, path, **kwargs):
        marker_calls.append({"project": project_arg, "path": path, **kwargs})
        return {"status": "PASS"}

    def fake_report(project_arg, path, **kwargs):
        report_calls.append({"project": project_arg, "path": path, **kwargs})
        return {"status": "PASS"}

    monkeypatch.setattr(f"{module_name}.analyze_uvm_marker_log", fake_marker)
    monkeypatch.setattr(f"{module_name}.analyze_uvm_log", fake_report)
    monkeypatch.setattr(
        f"{module_name}.subprocess.run",
        lambda command, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=(
                'ZDDV_UVM_SEQUENCE {"sequence_id":"seq-1","sequence":"smoke_seq",'
                '"state":"UVM_BODY"}\n'
                'ZDDV_UVM_ITEM {"item_id":"item-1","event":"REQUEST"}\n'
            ),
        ),
    )

    result = backend.run(project)

    assert result.status == "PASS"
    assert report_calls == []
    assert len(marker_calls) == 1
    call = marker_calls[0]
    assert call["project"] is project
    assert call["path"] is None
    assert call["run_id"] == result.run_id
    assert call["source"] == f"{simulator}-marker-run"
