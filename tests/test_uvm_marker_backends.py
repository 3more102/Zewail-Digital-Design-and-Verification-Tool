from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from zddv.config import ProjectConfig
from zddv.simulator.questa import QuestaBackend
from zddv.simulator.vcs import VcsBackend
from zddv.simulator.xcelium import XceliumBackend


_MARKER_LOG = """ZDDV_UVM_SEQUENCE {"sequence_id":"seq-auto","sequence":"auto_seq","sequencer":"uvm_test_top.env.sqr","state":"UVM_BODY","time":"1 ns"}
ZDDV_UVM_SEQUENCE {"sequence_id":"seq-auto","sequence":"auto_seq","sequencer":"uvm_test_top.env.sqr","state":"UVM_ENDED","time":"2 ns"}
ZDDV_UVM_SEQUENCE {"sequence_id":"seq-auto","sequence":"auto_seq","sequencer":"uvm_test_top.env.sqr","state":"UVM_POST_START","time":"3 ns"}
ZDDV_UVM_SEQUENCE {"sequence_id":"seq-auto","sequence":"auto_seq","sequencer":"uvm_test_top.env.sqr","state":"UVM_FINISHED","time":"4 ns"}
ZDDV_UVM_ITEM {"item_id":"item-auto","event":"GRANT","sequence_id":"seq-auto","sequence":"auto_seq","sequencer":"uvm_test_top.env.sqr","item":"req","transaction_id":1,"time":"5 ns"}
ZDDV_UVM_ITEM {"item_id":"item-auto","event":"REQUEST","sequence_id":"seq-auto","sequence":"auto_seq","sequencer":"uvm_test_top.env.sqr","item":"req","transaction_id":1,"time":"5 ns"}
ZDDV_UVM_ITEM {"item_id":"item-auto","event":"ITEM_DONE","sequence_id":"seq-auto","sequence":"auto_seq","sequencer":"uvm_test_top.env.sqr","item":"req","transaction_id":1,"time":"6 ns"}
"""


def _project(tmp_path: Path, simulator: str) -> ProjectConfig:
    root = tmp_path / simulator
    root.mkdir()
    return ProjectConfig(
        root=root,
        name=f"marker-{simulator}",
        top="tb_top",
        simulator=simulator,
        waveform=False,
        coverage=False,
    )


def _assert_marker_only_run(project: ProjectConfig, result, simulator: str) -> None:
    marker_path = project.root / ".zddv" / "uvm" / "markers" / "latest.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert result.status == "PASS"
    assert marker["status"] == "PASS"
    assert marker["source"] == f"{simulator}-marker-run"
    assert marker["run_id"] == result.run_id
    assert marker["run_status"] == "PASS"
    assert marker["summary"]["sequence_marker_lines"] == 4
    assert marker["summary"]["item_marker_lines"] == 3
    assert not (project.root / ".zddv" / "uvm" / "latest.json").exists()
    assert (project.root / ".zddv" / "uvm" / "sequences" / "latest.json").exists()
    assert (project.root / ".zddv" / "uvm" / "items" / "latest.json").exists()


def test_questa_run_auto_ingests_exact_marker_contracts(tmp_path, monkeypatch):
    project = _project(tmp_path, "questa")
    backend = QuestaBackend()
    (project.root / ".zddv" / "build" / "work").mkdir(parents=True)

    monkeypatch.setattr(backend, "version", lambda: "Questa test")
    monkeypatch.setattr(backend, "_tool", lambda name: name)
    monkeypatch.setattr(
        "zddv.simulator.questa.subprocess.run",
        lambda command, **kwargs: SimpleNamespace(returncode=0, stdout=_MARKER_LOG),
    )

    result = backend.run(project)
    _assert_marker_only_run(project, result, "questa")


def test_vcs_run_auto_ingests_exact_marker_contracts(tmp_path, monkeypatch):
    project = _project(tmp_path, "vcs")
    backend = VcsBackend()
    executable = (project.root / ".zddv" / "build" / "simv").resolve()
    executable.parent.mkdir(parents=True)
    executable.write_text("simv fixture\n", encoding="utf-8")

    monkeypatch.setattr(backend, "version", lambda: "VCS test")
    monkeypatch.setattr(
        "zddv.simulator.vcs.subprocess.run",
        lambda command, **kwargs: SimpleNamespace(returncode=0, stdout=_MARKER_LOG),
    )

    result = backend.run(project)
    _assert_marker_only_run(project, result, "vcs")


def test_xcelium_run_auto_ingests_exact_marker_contracts(tmp_path, monkeypatch):
    project = _project(tmp_path, "xcelium")
    backend = XceliumBackend()
    (project.root / ".zddv" / "build" / "xcelium.d").mkdir(parents=True)

    monkeypatch.setattr(backend, "version", lambda: "TOOL: xrun test")
    monkeypatch.setattr(backend, "_tool", lambda: "xrun")
    monkeypatch.setattr(
        "zddv.simulator.xcelium.subprocess.run",
        lambda command, **kwargs: SimpleNamespace(returncode=0, stdout=_MARKER_LOG),
    )

    result = backend.run(project)
    _assert_marker_only_run(project, result, "xcelium")
