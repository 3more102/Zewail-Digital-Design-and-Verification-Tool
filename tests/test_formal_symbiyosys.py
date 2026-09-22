from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from zddv.config import ProjectConfig
from zddv.formal import FormalCheckRequest
from zddv.formal.symbiyosys import SymbiYosysBackend


def _project(tmp_path: Path) -> ProjectConfig:
    root = tmp_path / "demo"
    rtl = root / "rtl"
    rtl.mkdir(parents=True)
    (rtl / "dut.sv").write_text(
        "module dut(input logic clk); always_ff @(posedge clk) assert(1'b1); endmodule\n",
        encoding="utf-8",
    )
    return ProjectConfig(
        root=root,
        name="demo",
        top="dut",
        simulator="verilator",
        rtl=["rtl/*.sv"],
        tb=[],
        waveform=False,
        coverage=False,
    )


def test_symbiyosys_version_uses_sby(monkeypatch, tmp_path: Path):
    backend = SymbiYosysBackend()
    monkeypatch.setattr("zddv.formal.symbiyosys.shutil.which", lambda name: "/tools/sby")

    def fake_run(command, **kwargs):
        assert command == ["/tools/sby", "--version"]
        assert kwargs["capture_output"] is True
        return SimpleNamespace(returncode=0, stdout="SBY v0.68\n", stderr="")

    monkeypatch.setattr("zddv.formal.symbiyosys.subprocess.run", fake_run)

    assert backend.version() == "SBY v0.68"


def test_symbiyosys_bmc_generates_explicit_sby_config(monkeypatch, tmp_path: Path):
    project = _project(tmp_path)
    backend = SymbiYosysBackend()
    monkeypatch.setattr("zddv.formal.symbiyosys.shutil.which", lambda name: "/tools/sby")

    observed = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs
        run_dir = Path(command[command.index("-d") + 1])
        run_dir.mkdir(parents=True)
        (run_dir / "status").write_text("PASS\n", encoding="utf-8")
        return SimpleNamespace(
            returncode=0,
            stdout="SBY [job] DONE (PASS, rc=0)\n",
        )

    monkeypatch.setattr("zddv.formal.symbiyosys.subprocess.run", fake_run)

    request = FormalCheckRequest(mode="bmc", depth=25, timeout_s=2.2)
    result = backend.check(project, request)

    assert result.status == "PASS"
    assert result.backend == "symbiyosys"
    assert result.engine == "smtbmc"
    assert result.properties == ()
    assert result.log_path.is_file()
    assert result.run_dir.is_dir()
    assert observed["kwargs"]["cwd"] == project.root

    config_path = next(path for path in result.artifacts if path.suffix == ".sby")
    config = config_path.read_text(encoding="utf-8")
    assert "[options]" in config
    assert "mode bmc" in config
    assert "depth 25" in config
    assert "timeout 3" in config
    assert "expect pass,fail,unknown,error,timeout" in config
    assert "[engines]\nsmtbmc" in config
    assert f'read -formal "{(project.root / "rtl" / "dut.sv").resolve().as_posix()}"' in config
    assert "prep -top dut" in config


def test_symbiyosys_preserves_fail_trace_as_evidence(monkeypatch, tmp_path: Path):
    project = _project(tmp_path)
    backend = SymbiYosysBackend()
    monkeypatch.setattr("zddv.formal.symbiyosys.shutil.which", lambda name: "/tools/sby")

    def fake_run(command, **kwargs):
        run_dir = Path(command[command.index("-d") + 1])
        engine_dir = run_dir / "engine_0"
        engine_dir.mkdir(parents=True)
        (engine_dir / "trace.vcd").write_text("$enddefinitions $end\n", encoding="utf-8")
        return SimpleNamespace(
            returncode=0,
            stdout="SBY [job] summary: engine_0 returned FAIL\nSBY [job] DONE (FAIL, rc=0)\n",
        )

    monkeypatch.setattr("zddv.formal.symbiyosys.subprocess.run", fake_run)

    result = backend.check(project, FormalCheckRequest(mode="prove"))

    assert result.status == "FAIL"
    assert any(path.name == "trace.vcd" for path in result.artifacts)
    assert result.properties == ()


def test_symbiyosys_timeout_is_not_overclaimed_as_pass(monkeypatch, tmp_path: Path):
    project = _project(tmp_path)
    backend = SymbiYosysBackend()
    monkeypatch.setattr("zddv.formal.symbiyosys.shutil.which", lambda name: "/tools/sby")

    def fake_run(command, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout="SBY [job] DONE (TIMEOUT, rc=0)\n",
        )

    monkeypatch.setattr("zddv.formal.symbiyosys.subprocess.run", fake_run)

    result = backend.check(project, FormalCheckRequest(mode="bmc", depth=10))

    assert result.status == "UNKNOWN"


def test_symbiyosys_rejects_unimplemented_property_filtering(tmp_path: Path):
    project = _project(tmp_path)
    backend = SymbiYosysBackend()

    with pytest.raises(NotImplementedError, match="property filtering"):
        backend.check(
            project,
            FormalCheckRequest(mode="bmc", properties=["p_req_ack"]),
        )


def test_symbiyosys_requires_formal_sources(tmp_path: Path):
    root = tmp_path / "empty"
    root.mkdir()
    project = ProjectConfig(root=root, name="empty", top="dut", rtl=[], tb=[])
    backend = SymbiYosysBackend()

    with pytest.raises(RuntimeError, match="No \\.v/\\.sv sources"):
        backend.check(project, FormalCheckRequest(mode="bmc"))
