from __future__ import annotations

from json import loads
from pathlib import Path
import subprocess
from types import SimpleNamespace

from zddv.config import ProjectConfig
from zddv.simulator import QuestaBackend, get_backend


def _project(tmp_path: Path, *, waveform: bool = True) -> ProjectConfig:
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
        simulator="questa",
        rtl=["rtl/*.sv"],
        tb=["tb/*.sv"],
        waveform=waveform,
        coverage=False,
    )


def test_backend_factory_selects_questa():
    assert isinstance(get_backend("questa"), QuestaBackend)
    assert isinstance(get_backend("QuestaSim"), QuestaBackend)


def test_questa_build_uses_native_library_and_compile_flow(tmp_path, monkeypatch):
    project = _project(tmp_path)
    backend = QuestaBackend()
    monkeypatch.setattr(backend, "version", lambda: "Questa test")
    monkeypatch.setattr(backend, "_tool", lambda name: name)

    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(list(command))
        if command[0] == "vlib":
            (Path(kwargs["cwd"]) / "work").mkdir(parents=True)
        return SimpleNamespace(returncode=0, stdout="ok\n")

    monkeypatch.setattr("zddv.simulator.questa.subprocess.run", fake_run)

    result = backend.build(project)

    assert result.passed is True
    assert result.executable is None
    assert result.artifact == (project.root / ".zddv" / "build" / "work").resolve()
    assert commands[0] == ["vlib", "work"]
    assert commands[1][:4] == ["vlog", "-sv", "-work", "work"]
    assert str((project.root / "rtl" / "dut.sv").resolve()) in commands[1]
    assert str((project.root / "tb" / "tb_top.sv").resolve()) in commands[1]

    manifest = loads(
        (project.root / ".zddv" / "build" / "build.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["simulator"] == "questa"
    assert manifest["build_artifact"].endswith("work")
    assert manifest["coverage_capture"] == "not_implemented"


def test_questa_run_preserves_seed_plusargs_waveform_and_uvm(tmp_path, monkeypatch):
    project = _project(tmp_path)
    backend = QuestaBackend()
    work = (project.root / ".zddv" / "build" / "work").resolve()
    work.mkdir(parents=True)

    monkeypatch.setattr(backend, "version", lambda: "Questa test")
    monkeypatch.setattr(backend, "_tool", lambda name: name)

    uvm_log = """# UVM_INFO @ 0: reporter [RNTST] Running test smoke...
# UVM Report Summary
# UVM_INFO : 1
# UVM_WARNING : 0
# UVM_ERROR : 0
# UVM_FATAL : 0
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

    monkeypatch.setattr("zddv.simulator.questa.subprocess.run", fake_run)

    result = backend.run(
        project,
        test_name="smoke",
        seed=42,
        plusargs=["+MODE=stress"],
        timeout_s=5,
    )

    command = captured["command"]
    assert result.status == "PASS"
    assert result.waveform_path is not None
    assert result.waveform_path.exists()
    assert "-lib" in command
    assert str(work) in command
    assert "-sv_seed" in command
    assert "42" in command
    assert "-voptargs=+acc" in command
    assert "+ZDDV_TEST=smoke" in command
    assert "+UVM_TESTNAME=smoke" in command
    assert "+ZDDV_SEED=42" in command
    assert "+MODE=stress" in command

    do_text = (result.run_dir / "zddv_questa.do").read_text(encoding="utf-8")
    assert "vcd file waveform.vcd" in do_text
    assert "run -all" in do_text

    run_record = loads((result.run_dir / "run.json").read_text(encoding="utf-8"))
    assert run_record["simulator"] == "questa"
    assert run_record["seed"] == 42
    assert run_record["coverage"] is None
    assert run_record["coverage_capture"] == "not_implemented"

    uvm = loads(
        (project.root / ".zddv" / "uvm" / "latest.json").read_text(
            encoding="utf-8"
        )
    )
    assert uvm["source"] == "questa-run"
    assert uvm["status"] == "PASS"
    assert uvm["test_name"] == "smoke"
    assert uvm["run_id"] == result.run_id
    assert uvm["run_status"] == "PASS"
    assert uvm["run_returncode"] == 0
    assert uvm["simulator"] == "questa"


def test_questa_run_without_waveform_does_not_request_acc(tmp_path, monkeypatch):
    project = _project(tmp_path, waveform=False)
    backend = QuestaBackend()
    (project.root / ".zddv" / "build" / "work").mkdir(parents=True)

    monkeypatch.setattr(backend, "version", lambda: "Questa test")
    monkeypatch.setattr(backend, "_tool", lambda name: name)

    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = list(command)
        return SimpleNamespace(returncode=0, stdout="simulation complete\n")

    monkeypatch.setattr("zddv.simulator.questa.subprocess.run", fake_run)

    result = backend.run(project)

    assert result.status == "PASS"
    assert result.waveform_path is None
    assert "-voptargs=+acc" not in captured["command"]
    assert not (result.run_dir / "waveform.vcd").exists()
    assert not (project.root / ".zddv" / "uvm" / "latest.json").exists()


def test_questa_timeout_is_recorded_without_false_uvm_snapshot(tmp_path, monkeypatch):
    project = _project(tmp_path, waveform=False)
    backend = QuestaBackend()
    (project.root / ".zddv" / "build" / "work").mkdir(parents=True)

    monkeypatch.setattr(backend, "version", lambda: "Questa test")
    monkeypatch.setattr(backend, "_tool", lambda name: name)

    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs.get("timeout") or 1, output="partial")

    monkeypatch.setattr("zddv.simulator.questa.subprocess.run", fake_run)

    result = backend.run(project, timeout_s=0.01)

    assert result.status == "TIMEOUT"
    assert result.returncode == 124
    assert "ZDDV_TIMEOUT" in result.log_path.read_text(encoding="utf-8")
    assert not (project.root / ".zddv" / "uvm" / "latest.json").exists()

    run_record = loads((result.run_dir / "run.json").read_text(encoding="utf-8"))
    assert run_record["status"] == "TIMEOUT"
    assert run_record["returncode"] == 124
