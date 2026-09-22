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


def test_xcelium_build_uses_native_elaborate_flow(tmp_path, monkeypatch):
    project = _project(tmp_path)
    backend = XceliumBackend()
    monkeypatch.setattr(backend, "version", lambda: "TOOL: xrun test")
    monkeypatch.setattr(backend, "_tool", lambda: "xrun")

    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = list(command)
        captured["cwd"] = Path(kwargs["cwd"])
        library_dir = Path(command[command.index("-xmlibdirname") + 1])
        library_dir.mkdir(parents=True)
        return SimpleNamespace(returncode=0, stdout="elaboration complete\n")

    monkeypatch.setattr("zddv.simulator.xcelium.subprocess.run", fake_run)

    result = backend.build(project)

    command = captured["command"]
    library_dir = (
        project.root / ".zddv" / "build" / "xcelium.d"
    ).resolve()
    assert result.passed is True
    assert result.executable is None
    assert result.artifact == library_dir
    assert command[0] == "xrun"
    assert "-sv" in command
    assert "-uvm" in command
    assert "-elaborate" in command
    assert command[command.index("-top") + 1] == "tb_top"
    assert command[command.index("-xmlibdirname") + 1] == str(library_dir)
    assert command[command.index("-access") + 1] == "+rwc"
    assert str((project.root / "rtl" / "dut.sv").resolve()) in command
    assert str((project.root / "tb" / "tb_top.sv").resolve()) in command

    manifest = loads(
        (project.root / ".zddv" / "build" / "build.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["simulator"] == "xcelium"
    assert manifest["waveform_capture"] == "vcd-tcl"
    assert manifest["coverage_capture"] == "disabled"
    assert manifest["build_artifact"] == str(library_dir)


def test_xcelium_build_omits_debug_access_without_waveform(tmp_path, monkeypatch):
    project = _project(tmp_path, waveform=False)
    backend = XceliumBackend()
    monkeypatch.setattr(backend, "version", lambda: "TOOL: xrun test")
    monkeypatch.setattr(backend, "_tool", lambda: "xrun")

    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(list(command))
        library_dir = Path(command[command.index("-xmlibdirname") + 1])
        library_dir.mkdir(parents=True)
        return SimpleNamespace(returncode=0, stdout="ok\n")

    monkeypatch.setattr("zddv.simulator.xcelium.subprocess.run", fake_run)

    result = backend.build(project)

    assert result.passed is True
    assert "-access" not in commands[0]


def test_xcelium_run_preserves_seed_plusargs_waveform_and_uvm(
    tmp_path,
    monkeypatch,
):
    project = _project(tmp_path)
    backend = XceliumBackend()
    library_dir = (
        project.root / ".zddv" / "build" / "xcelium.d"
    ).resolve()
    library_dir.mkdir(parents=True)

    monkeypatch.setattr(backend, "version", lambda: "TOOL: xrun test")
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
    assert command[0] == "xrun"
    assert "-R" in command
    assert command[command.index("-xmlibdirname") + 1] == str(library_dir)
    assert command[command.index("-svseed") + 1] == "42"
    assert "+ZDDV_TEST=smoke" in command
    assert "+UVM_TESTNAME=smoke" in command
    assert "+ZDDV_SEED=42" in command
    assert "+MODE=stress" in command

    input_path = Path(command[command.index("-input") + 1])
    input_text = input_path.read_text(encoding="utf-8")
    assert "database -open zddv_vcd -vcd -into waveform.vcd" in input_text
    assert "[scope -tops] -depth all -all" in input_text
    assert "\nrun\n" in input_text
    assert input_text.endswith("exit\n")

    run_record = loads(
        (result.run_dir / "run.json").read_text(encoding="utf-8")
    )
    assert run_record["simulator"] == "xcelium"
    assert run_record["seed"] == 42
    assert run_record["coverage_requested"] is False
    assert run_record["coverage"] is None
    assert run_record["build_artifact"] == str(library_dir)

    uvm = loads(
        (project.root / ".zddv" / "uvm" / "latest.json").read_text(
            encoding="utf-8"
        )
    )
    assert uvm["source"] == "xcelium-run"
    assert uvm["status"] == "PASS"
    assert uvm["test_name"] == "smoke"
    assert uvm["run_id"] == result.run_id
    assert uvm["simulator"] == "xcelium"


def test_xcelium_run_without_waveform_has_no_input_script(tmp_path, monkeypatch):
    project = _project(tmp_path, waveform=False)
    backend = XceliumBackend()
    (project.root / ".zddv" / "build" / "xcelium.d").mkdir(parents=True)

    monkeypatch.setattr(backend, "version", lambda: "TOOL: xrun test")
    monkeypatch.setattr(backend, "_tool", lambda: "xrun")

    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = list(command)
        return SimpleNamespace(returncode=0, stdout="simulation complete\n")

    monkeypatch.setattr("zddv.simulator.xcelium.subprocess.run", fake_run)

    result = backend.run(project)

    assert result.status == "PASS"
    assert result.waveform_path is None
    assert "-input" not in captured["command"]
    assert not (result.run_dir / "zddv_xcelium.tcl").exists()


def test_xcelium_explicit_uvm_test_plusarg_is_not_duplicated(
    tmp_path,
    monkeypatch,
):
    project = _project(tmp_path, waveform=False)
    backend = XceliumBackend()
    (project.root / ".zddv" / "build" / "xcelium.d").mkdir(parents=True)

    monkeypatch.setattr(backend, "version", lambda: "TOOL: xrun test")
    monkeypatch.setattr(backend, "_tool", lambda: "xrun")

    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = list(command)
        return SimpleNamespace(returncode=0, stdout="simulation complete\n")

    monkeypatch.setattr("zddv.simulator.xcelium.subprocess.run", fake_run)

    result = backend.run(
        project,
        test_name="zddv_label",
        plusargs=["+UVM_TESTNAME=explicit_uvm_test", "+MODE=stress"],
    )

    command = captured["command"]
    uvm_test_args = [
        arg for arg in command
        if str(arg).startswith("+UVM_TESTNAME=")
    ]
    assert uvm_test_args == ["+UVM_TESTNAME=explicit_uvm_test"]
    assert "+ZDDV_TEST=zddv_label" in command

    run_record = loads(
        (result.run_dir / "run.json").read_text(encoding="utf-8")
    )
    assert run_record["plusargs"] == [
        "+UVM_TESTNAME=explicit_uvm_test",
        "+MODE=stress",
    ]


def test_xcelium_timeout_is_recorded(tmp_path, monkeypatch):
    project = _project(tmp_path, waveform=False)
    backend = XceliumBackend()
    (project.root / ".zddv" / "build" / "xcelium.d").mkdir(parents=True)

    monkeypatch.setattr(backend, "version", lambda: "TOOL: xrun test")
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
    run_record = loads(
        (result.run_dir / "run.json").read_text(encoding="utf-8")
    )
    assert run_record["status"] == "TIMEOUT"
    assert run_record["returncode"] == 124


def test_xcelium_build_instruments_native_coverage(tmp_path, monkeypatch):
    project = _project(tmp_path, waveform=False, coverage=True)
    backend = XceliumBackend()
    monkeypatch.setattr(backend, "version", lambda: "TOOL: xrun test")
    monkeypatch.setattr(backend, "_tool", lambda: "xrun")

    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = list(command)
        library_dir = Path(command[command.index("-xmlibdirname") + 1])
        library_dir.mkdir(parents=True)
        return SimpleNamespace(returncode=0, stdout="elaboration complete\n")

    monkeypatch.setattr("zddv.simulator.xcelium.subprocess.run", fake_run)

    result = backend.build(project)

    command = captured["command"]
    assert result.passed is True
    assert command[command.index("-coverage") + 1] == "all"

    manifest = loads(
        (project.root / ".zddv" / "build" / "build.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["coverage_requested"] is True
    assert manifest["coverage_capture"] == "instrumented"
    assert manifest["coverage_metrics"] == "all"


def test_xcelium_run_captures_native_coverage_database(tmp_path, monkeypatch):
    project = _project(tmp_path, waveform=False, coverage=True)
    backend = XceliumBackend()
    library_dir = (
        project.root / ".zddv" / "build" / "xcelium.d"
    ).resolve()
    library_dir.mkdir(parents=True)

    monkeypatch.setattr(backend, "version", lambda: "TOOL: xrun test")
    monkeypatch.setattr(backend, "_tool", lambda: "xrun")

    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = list(command)
        coverage_workdir = Path(command[command.index("-covworkdir") + 1])
        coverage_test = command[command.index("-covtest") + 1]
        coverage_dir = coverage_workdir / "scope" / coverage_test
        coverage_dir.mkdir(parents=True)
        (coverage_dir / "icc_test.ucm").write_text("model", encoding="utf-8")
        (coverage_dir / "icc_test.ucd").write_text("data", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="simulation complete\n")

    monkeypatch.setattr("zddv.simulator.xcelium.subprocess.run", fake_run)

    result = backend.run(project, test_name="coverage_smoke", seed=11)

    command = captured["command"]
    assert "-covoverwrite" in command
    assert command[command.index("-covtest") + 1] == "zddv"
    assert result.coverage_path is not None
    assert result.coverage_path.name == "zddv"
    assert any(result.coverage_path.glob("*.ucm"))
    assert any(result.coverage_path.glob("*.ucd"))

    run_record = loads(
        (result.run_dir / "run.json").read_text(encoding="utf-8")
    )
    assert run_record["coverage_requested"] is True
    assert run_record["coverage"] == str(result.coverage_path)
    assert run_record["coverage_capture"] == "ucm-ucd"
    assert run_record["coverage_metrics"] == "all"


def test_xcelium_version_uses_xrun_version(monkeypatch):
    backend = XceliumBackend()
    monkeypatch.setattr(backend, "_tool", lambda: "xrun")
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(list(command))
        return SimpleNamespace(
            returncode=0,
            stdout="TOOL: xrun 24.09-s010\n",
            stderr="",
        )

    monkeypatch.setattr("zddv.simulator.xcelium.subprocess.run", fake_run)

    version = backend.version()

    assert version == "TOOL: xrun 24.09-s010"
    assert commands == [["xrun", "-version"]]


def test_doctor_can_check_xcelium_backend(monkeypatch, capsys):
    class FakeBackend:
        def version(self):
            return "TOOL: xrun test"

    monkeypatch.setattr("zddv.cli.get_backend", lambda name: FakeBackend())

    rc = main(["doctor", "--simulator", "xcelium"])

    assert rc == 0
    output = capsys.readouterr().out
    assert "ZDDV 0.6.0" in output
    assert "[PASS] TOOL: xrun test" in output
