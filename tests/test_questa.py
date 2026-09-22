import json
from pathlib import Path
import subprocess

from zddv.config import initialize_project
from zddv.simulator.questa import QuestaBackend
from zddv.storage import get_run_record
from zddv.uvm import analyze_uvm_log


def _project(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    (project.root / "rtl" / "dut.sv").write_text(
        "module dut(input logic clk); endmodule\n",
        encoding="utf-8",
    )
    (project.root / "tb" / "tb_top.sv").write_text(
        "module tb_top; logic clk; dut u_dut(.clk(clk)); endmodule\n",
        encoding="utf-8",
    )
    project.rtl = ["rtl/*.sv"]
    project.tb = ["tb/*.sv"]
    project.waveform = True
    project.coverage = False
    return project


def _install_fake_questa(monkeypatch):
    import zddv.simulator.questa as questa_module

    def fake_which(name: str):
        if name in {"vlib", "vlog", "vsim"}:
            return f"/tools/{name}"
        return None

    def fake_run(command, **kwargs):
        command = [str(item) for item in command]
        cwd = kwargs.get("cwd")

        if command[:2] == ["/tools/vsim", "-version"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="QuestaSim-64 2025.1 Simulator\n",
                stderr="",
            )

        if command[0] == "/tools/vlib":
            if cwd is not None:
                (Path(cwd) / "work").mkdir(parents=True, exist_ok=True)
            return subprocess.CompletedProcess(command, 0, stdout="Library created\n")

        if command[0] == "/tools/vlog":
            return subprocess.CompletedProcess(command, 0, stdout="Compile complete\n")

        if command[0] == "/tools/vsim":
            if "-wlf" in command:
                path = Path(command[command.index("-wlf") + 1])
                path.write_text("fake-wlf\n", encoding="utf-8")
            uvm_test = "default_test"
            for arg in command:
                if arg.startswith("+UVM_TESTNAME="):
                    uvm_test = arg.split("=", 1)[1]
            stdout = (
                f"UVM_INFO @ 0: reporter [RNTST] Running test {uvm_test}...\n"
                "--- UVM Report Summary ---\n"
                "** Report counts by severity\n"
                "UVM_INFO : 1\n"
                "UVM_WARNING : 0\n"
                "UVM_ERROR : 0\n"
                "UVM_FATAL : 0\n"
            )
            return subprocess.CompletedProcess(command, 0, stdout=stdout)

        raise AssertionError(f"Unexpected command: {command}")

    monkeypatch.setattr(questa_module.shutil, "which", fake_which)
    monkeypatch.setattr(questa_module.subprocess, "run", fake_run)


def test_questa_version_and_build_use_classic_batch_flow(tmp_path: Path, monkeypatch):
    _install_fake_questa(monkeypatch)
    project = _project(tmp_path)
    backend = QuestaBackend()

    assert backend.version() == "QuestaSim-64 2025.1 Simulator"

    result = backend.build(project)

    assert result.passed is True
    assert result.returncode == 0
    assert result.log_path.is_file()
    assert result.executable is not None
    assert result.executable.is_file()

    manifest = json.loads(result.executable.read_text(encoding="utf-8"))
    assert manifest["simulator"] == "questa"
    assert manifest["top"] == "tb_top"
    assert manifest["returncode"] == 0
    assert manifest["commands"][0] == ["/tools/vlib", "work"]
    assert manifest["commands"][1][0:4] == [
        "/tools/vlog",
        "-sv",
        "-work",
        "work",
    ]
    assert any(path.endswith("dut.sv") for path in manifest["commands"][1])
    assert any(path.endswith("tb_top.sv") for path in manifest["commands"][1])


def test_questa_run_records_result_and_selects_uvm_test(tmp_path: Path, monkeypatch):
    _install_fake_questa(monkeypatch)
    project = _project(tmp_path)
    backend = QuestaBackend()
    backend.build(project)

    result = backend.run(
        project,
        test_name="smoke_case",
        seed=17,
        plusargs=["+MODE=stress"],
        timeout_s=5.0,
    )

    assert result.status == "PASS"
    assert result.returncode == 0
    assert result.waveform_path is not None
    assert result.waveform_path.name == "waveform.wlf"
    assert result.waveform_path.is_file()
    assert "+ZDDV_TEST=smoke_case" in result.command
    assert "+UVM_TESTNAME=smoke_case" in result.command
    assert "+MODE=stress" in result.command
    assert result.command[result.command.index("-sv_seed") + 1] == "17"
    assert result.command[-2:] == ["-do", "run -all; quit -f"]

    stored = get_run_record(project, result.run_id)
    assert stored is not None
    assert stored["simulator"] == "questa"
    assert stored["test_name"] == "smoke_case"
    assert stored["seed"] == 17
    assert stored["status"] == "PASS"

    uvm = analyze_uvm_log(project, None, run_id=result.run_id)
    assert uvm["run_id"] == result.run_id
    assert uvm["source"] == "questa"
    assert uvm["test_name"] == "smoke_case"
    assert uvm["status"] == "PASS"


def test_questa_respects_explicit_uvm_test_plusarg(tmp_path: Path, monkeypatch):
    _install_fake_questa(monkeypatch)
    project = _project(tmp_path)
    backend = QuestaBackend()
    backend.build(project)

    result = backend.run(
        project,
        test_name="zddv_label",
        plusargs=["+UVM_TESTNAME=explicit_uvm_test"],
    )

    uvm_args = [arg for arg in result.command if arg.startswith("+UVM_TESTNAME=")]
    assert uvm_args == ["+UVM_TESTNAME=explicit_uvm_test"]

    report = analyze_uvm_log(project, None, run_id=result.run_id)
    assert report["test_name"] == "explicit_uvm_test"


def test_questa_reports_missing_tool(monkeypatch):
    import zddv.simulator.questa as questa_module

    monkeypatch.setattr(questa_module.shutil, "which", lambda name: None)

    try:
        QuestaBackend().version()
    except RuntimeError as exc:
        assert "vsim" in str(exc)
        assert "PATH" in str(exc)
    else:
        raise AssertionError("Expected RuntimeError when vsim is unavailable")


def test_questa_cli_backend_and_doctor(monkeypatch, capsys):
    _install_fake_questa(monkeypatch)

    from zddv.cli import _backend, main

    assert isinstance(_backend("questa"), QuestaBackend)
    rc = main(["doctor", "--simulator", "questa"])
    assert rc == 0
    output = capsys.readouterr().out
    assert "[PASS] QuestaSim-64 2025.1 Simulator" in output
