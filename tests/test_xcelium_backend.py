from __future__ import annotations

from json import loads
from pathlib import Path
import subprocess
from types import SimpleNamespace

from zddv.cli import main
from zddv.config import ProjectConfig
from zddv.simulator import XceliumBackend, get_backend


def _project(
    tmp_path: Path,
    *,
    waveform: bool = True,
    coverage: bool = False,
) -> ProjectConfig:
    root = tmp_path / "demo"
    (root / "rtl").mkdir(parents=True)
    (root / "tb").mkdir()
    (root / "rtl" / "dut.sv").write_text(
        "module dut; endmodule\n",
        encoding="utf-8",
    )
    (root / "tb" / "tb_top.sv").write_text(
        "module tb_top; dut u_dut(); initial #1 $finish; endmodule\n",
        encoding="utf-8",
    )
    return ProjectConfig(
        root=root,
        name="demo",
        top="tb_top",
        simulator="xcelium",
        rtl=["rtl/*.sv"],
        tb=["tb/*.sv"],
        waveform=waveform,
        coverage=coverage,
    )


def test_backend_factory_selects_xcelium():
    assert isinstance(get_backend("xcelium"), XceliumBackend)
    assert isinstance(get_backend("XRUN"), XceliumBackend)


def test_xcelium_version_uses_documented_xrun_version(monkeypatch):
    backend = XceliumBackend()
    monkeypatch.setattr(backend, "_tool", lambda: "xrun")
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(list(command))
        return SimpleNamespace(
            returncode=0,
            stdout="TOOL: xrun 25.03-s001\n",
            stderr="",
        )

    monkeypatch.setattr("zddv.simulator.xcelium.subprocess.run", fake_run)

    assert backend.version() == "TOOL: xrun 25.03-s001"
    assert commands == [["xrun", "-version"]]


def test_xcelium_build_elaborates_reusable_snapshot(tmp_path, monkeypatch):
    project = _project(tmp_path)
    backend = XceliumBackend()
    monkeypatch.setattr(backend, "version", lambda: "Xcelium test")
    monkeypatch.setattr(backend, "_tool", lambda: "xrun")
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = list(command)
        captured["cwd"] = Path(kwargs["cwd"])
        libdir = Path(command[command.index("-xmlibdirname") + 1])
        libdir.mkdir(parents=True)
        return SimpleNamespace(returncode=0, stdout="elaboration complete\n")

    monkeypatch.setattr("zddv.simulator.xcelium.subprocess.run", fake_run)

    result = backend.build(project)

    command = captured["command"]
    assert result.passed is True
    assert result.executable is None
    assert result.artifact == (
        project.root / ".zddv" / "build" / "xcelium.d"
    ).resolve()
    assert command[:5] == ["xrun", "-elaborate", "-64bit", "-sv", "-uvm"]
    assert "-top" in command
    assert command[command.index("-top") + 1] == "tb_top"
    assert "-xmlibdirname" in command
    assert "-access" in command
    assert command[command.index("-access") + 1] == "+r"

    manifest = loads(
        (project.root / ".zddv" / "build" / "build.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["simulator"] == "xcelium"
    assert manifest["waveform_capture"] == "vcd"
    assert manifest["coverage_capture"] == "disabled"


def test_xcelium_run_reuses_snapshot_and_preserves_runtime_controls(
    tmp_path,
    monkeypatch,
):
    project = _project(tmp_path)
    backend = XceliumBackend()
    library_dir = (
        project.root / ".zddv" / "build" / "xcelium.d"
    ).resolve()
    library_dir.mkdir(parents=True)

    monkeypatch.setattr(backend, "version", lambda: "Xcelium test")
    monkeypatch.setattr(backend, "_tool", lambda: "xrun")
    uvm_log = """UVM_INFO @ 0: reporter [RNTST] Running test smoke...
UVM Report Summary
UVM_INFO : 1
UVM_WARNING : 0
UVM_ERROR : 0
UVM_FATAL : 0
"""
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = list(command)
        captured["cwd"] = Path(kwargs["cwd"])
        (Path(kwargs["cwd"]) / "waveform.vcd").write_text(
            "$date\n$end\n",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout=uvm_log)

    monkeypatch.setattr("zddv.simulator.xcelium.subprocess.run", fake_run)

    result = backend.run(
        project,
        test_name="smoke",
        seed=42,
        plusargs=["+MODE=stress"],
        timeout_s=5,
    )

    command = captured["command"]
    assert result.status == "PASS"
    assert result.waveform_path == result.run_dir / "waveform.vcd"
    assert command[:2] == ["xrun", "-R"]
    assert command[command.index("-xmlibdirname") + 1] == str(library_dir)
    assert command[command.index("-svseed") + 1] == "42"
    assert "+ZDDV_TEST=smoke" in command
    assert "+UVM_TESTNAME=smoke" in command
    assert "+ZDDV_SEED=42" in command
    assert "+MODE=stress" in command
    assert "-input" in command

    tcl = (result.run_dir / "zddv_xcelium.tcl").read_text(encoding="utf-8")
    assert "database -open zddv_vcd -vcd -into waveform.vcd" in tcl
    assert "probe -create -database zddv_vcd [scope -tops] -depth all -all" in tcl
    assert tcl.endswith("run\nexit\n")

    run_record = loads((result.run_dir / "run.json").read_text(encoding="utf-8"))
    assert run_record["simulator"] == "xcelium"
    assert run_record["seed"] == 42
    assert run_record["coverage_capture"] == "disabled"

    uvm = loads(
        (project.root / ".zddv" / "uvm" / "latest.json").read_text(
            encoding="utf-8"
        )
    )
    assert uvm["source"] == "xcelium-run"
    assert uvm["run_id"] == result.run_id
    assert uvm["simulator"] == "xcelium"


def test_xcelium_explicit_uvm_test_plusarg_is_not_duplicated(tmp_path, monkeypatch):
    project = _project(tmp_path, waveform=False)
    backend = XceliumBackend()
    (project.root / ".zddv" / "build" / "xcelium.d").mkdir(parents=True)
    monkeypatch.setattr(backend, "version", lambda: "Xcelium test")
    monkeypatch.setattr(backend, "_tool", lambda: "xrun")
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = list(command)
        return SimpleNamespace(returncode=0, stdout="simulation complete\n")

    monkeypatch.setattr("zddv.simulator.xcelium.subprocess.run", fake_run)

    backend.run(
        project,
        test_name="zddv_label",
        plusargs=["+UVM_TESTNAME=explicit_uvm_test", "+MODE=stress"],
    )

    command = captured["command"]
    assert [
        arg for arg in command if str(arg).startswith("+UVM_TESTNAME=")
    ] == ["+UVM_TESTNAME=explicit_uvm_test"]
    assert "+ZDDV_TEST=zddv_label" in command


def test_xcelium_requested_coverage_is_explicitly_not_implemented(
    tmp_path,
    monkeypatch,
):
    project = _project(tmp_path, waveform=False, coverage=True)
    backend = XceliumBackend()
    library_dir = project.root / ".zddv" / "build" / "xcelium.d"
    library_dir.mkdir(parents=True)
    monkeypatch.setattr(backend, "version", lambda: "Xcelium test")
    monkeypatch.setattr(backend, "_tool", lambda: "xrun")
    monkeypatch.setattr(
        "zddv.simulator.xcelium.subprocess.run",
        lambda command, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="simulation complete\n",
        ),
    )

    result = backend.run(project)

    assert result.coverage_path is None
    record = loads((result.run_dir / "run.json").read_text(encoding="utf-8"))
    assert record["coverage_requested"] is True
    assert record["coverage"] is None
    assert record["coverage_capture"] == "not-implemented"


def test_xcelium_timeout_is_recorded(tmp_path, monkeypatch):
    project = _project(tmp_path, waveform=False)
    backend = XceliumBackend()
    (project.root / ".zddv" / "build" / "xcelium.d").mkdir(parents=True)
    monkeypatch.setattr(backend, "version", lambda: "Xcelium test")
    monkeypatch.setattr(backend, "_tool", lambda: "xrun")

    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(
            command,
            kwargs.get("timeout") or 1,
            output="partial",
        )

    monkeypatch.setattr("zddv.simulator.xcelium.subprocess.run", fake_run)

    result = backend.run(project, timeout_s=0.01)

    assert result.status == "TIMEOUT"
    assert result.returncode == 124
    assert "ZDDV_TIMEOUT" in result.log_path.read_text(encoding="utf-8")


def test_doctor_can_check_xcelium_backend(monkeypatch, capsys):
    class FakeBackend:
        def version(self):
            return "Xcelium test"

    monkeypatch.setattr("zddv.cli.get_backend", lambda name: FakeBackend())

    rc = main(["doctor", "--simulator", "xcelium"])

    assert rc == 0
    output = capsys.readouterr().out
    assert "ZDDV 0.6.0" in output
    assert "[PASS] Xcelium test" in output
