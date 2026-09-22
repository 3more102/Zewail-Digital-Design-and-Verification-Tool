from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from zddv.config import ProjectConfig
from zddv.formal import FormalCheckRequest
from zddv.formal.symbiyosys import SymbiYosysBackend, parse_sby_status


def _project(tmp_path: Path) -> ProjectConfig:
    root = tmp_path / "demo"
    (root / "rtl").mkdir(parents=True)
    (root / "rtl" / "dut.sv").write_text("module dut; endmodule\n", encoding="utf-8")
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


def test_parse_sby_status_is_conservative():
    assert (
        parse_sby_status(
            "SBY [x] engine_0: Status returned by engine: PASS",
            returncode=0,
        )
        == "PASS"
    )
    assert (
        parse_sby_status(
            "SBY [x] engine_0: Status returned by engine for basecase: FAIL",
            returncode=1,
        )
        == "FAIL"
    )
    assert parse_sby_status("SBY [x] DONE (TIMEOUT, rc=1)", returncode=1) == "UNKNOWN"
    assert parse_sby_status("no status", returncode=0) == "UNKNOWN"
    assert parse_sby_status("no status", returncode=2) == "ERROR"


def test_render_config_materializes_documented_default_depth(tmp_path: Path):
    backend = SymbiYosysBackend()
    project = _project(tmp_path)

    text, request = backend.render_config(project, FormalCheckRequest(mode="bmc"))

    assert request.depth == 20
    assert "[options]\nmode bmc\ndepth 20" in text
    assert "[engines]\nsmtbmc" in text
    assert "read -formal src_0000.sv" in text
    assert "prep -top dut" in text
    assert "src_0000.sv " in text


def test_render_config_preserves_requested_depth_and_timeout(tmp_path: Path):
    backend = SymbiYosysBackend()
    project = _project(tmp_path)

    text, request = backend.render_config(
        project,
        FormalCheckRequest(mode="prove", depth=12, timeout_s=0.25),
    )

    assert request.depth == 12
    assert request.timeout_s == pytest.approx(0.25)
    assert "mode prove" in text
    assert "depth 12" in text
    assert "timeout 1" in text


def test_render_config_rejects_property_filter(tmp_path: Path):
    backend = SymbiYosysBackend()
    project = _project(tmp_path)

    with pytest.raises(RuntimeError, match="property filtering"):
        backend.render_config(
            project,
            FormalCheckRequest(mode="prove", properties=("p_a",)),
        )


def test_render_config_rejects_non_compilation_units(tmp_path: Path):
    project = _project(tmp_path)
    (project.root / "rtl" / "defs.svh").write_text("// header\n", encoding="utf-8")
    project.rtl = ["rtl/*"]

    with pytest.raises(RuntimeError, match="only .v/.sv"):
        SymbiYosysBackend().render_config(project, FormalCheckRequest(mode="bmc"))


def test_check_maps_fail_and_collects_trace(tmp_path: Path, monkeypatch):
    backend = SymbiYosysBackend()
    project = _project(tmp_path)
    monkeypatch.setattr(backend, "_tool", lambda: "/tools/sby")

    def fake_run(command, **kwargs):
        work_dir = Path(command[command.index("-d") + 1])
        (work_dir / "engine_0").mkdir(parents=True)
        (work_dir / "engine_0" / "trace.vcd").write_text(
            "$enddefinitions $end\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            command,
            1,
            "SBY [x] engine_0: Status returned by engine: FAIL\n",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = backend.check(project, FormalCheckRequest(mode="bmc", depth=8))

    assert result.status == "FAIL"
    assert result.returncode == 1
    assert result.request.depth == 8
    assert result.engine == "smtbmc"
    assert any(path.name == "trace.vcd" for path in result.artifacts)
    assert result.log_path.is_file()


def test_check_timeout_is_unknown(tmp_path: Path, monkeypatch):
    backend = SymbiYosysBackend()
    project = _project(tmp_path)
    monkeypatch.setattr(backend, "_tool", lambda: "/tools/sby")

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=args[0],
            timeout=0.1,
            output="partial",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = backend.check(
        project,
        FormalCheckRequest(mode="prove", depth=5, timeout_s=0.1),
    )

    assert result.status == "UNKNOWN"
    assert result.returncode == 124
    assert "ZDDV_FORMAL_TIMEOUT" in result.log_path.read_text(encoding="utf-8")


def test_version_uses_sby_version(monkeypatch):
    backend = SymbiYosysBackend()
    monkeypatch.setattr(backend, "_tool", lambda: "/tools/sby")

    def fake_run(command, **kwargs):
        assert command == ["/tools/sby", "--version"]
        return subprocess.CompletedProcess(command, 0, "SBY 1.7.1\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert backend.version() == "SBY 1.7.1"
