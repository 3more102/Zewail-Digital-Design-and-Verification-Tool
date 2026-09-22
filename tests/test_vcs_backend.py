from __future__ import annotations

from json import loads
from pathlib import Path
import subprocess
from types import SimpleNamespace

from zddv.cli import main
from zddv.config import ProjectConfig
from zddv.simulator import VcsBackend, get_backend


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
        simulator="vcs",
        rtl=["rtl/*.sv"],
        tb=["tb/*.sv"],
        waveform=waveform,
        coverage=coverage,
    )


def test_backend_factory_selects_vcs():
    assert isinstance(get_backend("vcs"), VcsBackend)
    assert isinstance(get_backend("VCS"), VcsBackend)


def test_vcs_build_uses_native_two_step_compile_contract(tmp_path, monkeypatch):
    project = _project(tmp_path)
    backend = VcsBackend()
    monkeypatch.setattr(backend, "version", lambda: "VCS test")
    monkeypatch.setattr(backend, "_tool", lambda: "vcs")

    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = list(command)
        executable = Path(command[command.index("-o") + 1])
        executable.write_text("simv fixture\n", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="compile complete\n")

    monkeypatch.setattr("zddv.simulator.vcs.subprocess.run", fake_run)

    result = backend.build(project)

    command = captured["command"]
    assert result.passed is True
    assert result.executable == (project.root / ".zddv" / "build" / "simv").resolve()
    assert command[:5] == ["vcs", "-full64", "-sverilog", "-ntb_opts", "uvm-1.2"]
    assert command[command.index("-top") + 1] == "tb_top"
    assert command[command.index("-o") + 1] == str(result.executable)
    assert str((project.root / "rtl" / "dut.sv").resolve()) in command
    assert str((project.root / "tb" / "tb_top.sv").resolve()) in command

    manifest = loads(
        (project.root / ".zddv" / "build" / "build.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["simulator"] == "vcs"
    assert manifest["uvm_library"] == "uvm-1.2"
    assert manifest["waveform_capture"] == "vcd"
    assert manifest["coverage_capture"] == "disabled"



def test_vcs_build_instruments_native_coverage(tmp_path, monkeypatch):
    project = _project(tmp_path, coverage=True)
    backend = VcsBackend()
    monkeypatch.setattr(backend, "version", lambda: "VCS test")
    monkeypatch.setattr(backend, "_tool", lambda: "vcs")
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = list(command)
        executable = Path(command[command.index("-o") + 1])
        executable.write_text("simv fixture\n", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="compile complete\n")

    monkeypatch.setattr("zddv.simulator.vcs.subprocess.run", fake_run)

    result = backend.build(project)

    assert result.passed is True
    command = captured["command"]
    assert command[command.index("-cm") + 1] == "line+cond+fsm+tgl+branch"
    manifest = loads(
        (project.root / ".zddv" / "build" / "build.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["coverage_requested"] is True
    assert manifest["coverage_capture"] == "instrumented"
    assert manifest["coverage_metrics"] == "line+cond+fsm+tgl+branch"

def test_vcs_run_preserves_seed_plusargs_waveform_and_uvm(tmp_path, monkeypatch):
    project = _project(tmp_path)
    backend = VcsBackend()
    executable = (project.root / ".zddv" / "build" / "simv").resolve()
    executable.parent.mkdir(parents=True)
    executable.write_text("simv fixture\n", encoding="utf-8")

    monkeypatch.setattr(backend, "version", lambda: "VCS test")

    uvm_log = """UVM_INFO @ 0: reporter [RNTST] Running test smoke...
--- UVM Report Summary ---
UVM_INFO : 1
UVM_WARNING : 0
UVM_ERROR : 0
UVM_FATAL : 0
"""
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = list(command)
        cwd = Path(kwargs["cwd"])
        (cwd / "waveform.vcd").write_text("$date\n$end\n", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout=uvm_log)

    monkeypatch.setattr("zddv.simulator.vcs.subprocess.run", fake_run)

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
    assert "+vcs+dumpvars+waveform.vcd" in command
    assert "+ZDDV_TEST=smoke" in command
    assert "+UVM_TESTNAME=smoke" in command
    assert "+ZDDV_SEED=42" in command
    assert "+ntb_random_seed=42" in command
    assert "+MODE=stress" in command

    run_record = loads((result.run_dir / "run.json").read_text(encoding="utf-8"))
    assert run_record["simulator"] == "vcs"
    assert run_record["seed"] == 42
    assert run_record["plusargs"] == ["+MODE=stress"]
    assert run_record["coverage_capture"] == "disabled"

    uvm = loads(
        (project.root / ".zddv" / "uvm" / "latest.json").read_text(
            encoding="utf-8"
        )
    )
    assert uvm["source"] == "vcs-run"
    assert uvm["status"] == "PASS"
    assert uvm["test_name"] == "smoke"
    assert uvm["run_id"] == result.run_id
    assert uvm["simulator"] == "vcs"


def test_vcs_run_auto_ingests_explicit_uvm_markers_without_false_report_snapshot(
    tmp_path,
    monkeypatch,
):
    project = _project(tmp_path, waveform=False)
    backend = VcsBackend()
    executable = (project.root / ".zddv" / "build" / "simv").resolve()
    executable.parent.mkdir(parents=True)
    executable.write_text("simv fixture\n", encoding="utf-8")

    monkeypatch.setattr(backend, "version", lambda: "VCS test")

    marker_output = (
        'ZDDV_UVM_ITEM {"item_id":"item-1","event":"REQUEST",'
        '"sequence_id":"seq-1","sequence":"smoke_seq",'
        '"sequencer":"uvm_test_top.env.sqr"}\n'
        'ZDDV_UVM_SEQUENCE {"sequence_id":"seq-1","sequence":"smoke_seq",'
        '"sequencer":"uvm_test_top.env.sqr","state":"UVM_BODY"}\n'
    )

    monkeypatch.setattr(
        "zddv.simulator.vcs.subprocess.run",
        lambda command, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=marker_output,
        ),
    )

    result = backend.run(project)

    assert result.status == "PASS"
    item = loads(
        (project.root / ".zddv" / "uvm" / "items" / "latest.json").read_text(
            encoding="utf-8"
        )
    )
    sequence = loads(
        (project.root / ".zddv" / "uvm" / "sequences" / "latest.json").read_text(
            encoding="utf-8"
        )
    )
    assert item["source"] == "vcs-uvm-item-log"
    assert item["run_id"] == result.run_id
    assert item["input_mode"] == "explicit-log-marker"
    assert sequence["source"] == "vcs-uvm-sequence-log"
    assert sequence["run_id"] == result.run_id
    assert sequence["input_mode"] == "explicit-log-marker"
    assert not (project.root / ".zddv" / "uvm" / "latest.json").exists()


def test_vcs_explicit_uvm_test_plusarg_is_not_duplicated(tmp_path, monkeypatch):
    project = _project(tmp_path, waveform=False)
    backend = VcsBackend()
    executable = (project.root / ".zddv" / "build" / "simv").resolve()
    executable.parent.mkdir(parents=True)
    executable.write_text("simv fixture\n", encoding="utf-8")

    monkeypatch.setattr(backend, "version", lambda: "VCS test")

    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = list(command)
        return SimpleNamespace(returncode=0, stdout="simulation complete\n")

    monkeypatch.setattr("zddv.simulator.vcs.subprocess.run", fake_run)

    result = backend.run(
        project,
        test_name="zddv_label",
        plusargs=["+UVM_TESTNAME=explicit_uvm_test", "+MODE=stress"],
    )

    command = captured["command"]
    assert [
        arg for arg in command
        if str(arg).startswith("+UVM_TESTNAME=")
    ] == ["+UVM_TESTNAME=explicit_uvm_test"]
    assert "+ZDDV_TEST=zddv_label" in command

    run_record = loads((result.run_dir / "run.json").read_text(encoding="utf-8"))
    assert run_record["plusargs"] == [
        "+UVM_TESTNAME=explicit_uvm_test",
        "+MODE=stress",
    ]


def test_vcs_timeout_is_recorded(tmp_path, monkeypatch):
    project = _project(tmp_path, waveform=False)
    backend = VcsBackend()
    executable = (project.root / ".zddv" / "build" / "simv").resolve()
    executable.parent.mkdir(parents=True)
    executable.write_text("simv fixture\n", encoding="utf-8")

    monkeypatch.setattr(backend, "version", lambda: "VCS test")

    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(
            command,
            kwargs.get("timeout") or 1,
            output="partial",
        )

    monkeypatch.setattr("zddv.simulator.vcs.subprocess.run", fake_run)

    result = backend.run(project, timeout_s=0.01)

    assert result.status == "TIMEOUT"
    assert result.returncode == 124
    assert "ZDDV_TIMEOUT" in result.log_path.read_text(encoding="utf-8")
    run_record = loads((result.run_dir / "run.json").read_text(encoding="utf-8"))
    assert run_record["status"] == "TIMEOUT"
    assert run_record["returncode"] == 124



def test_vcs_run_captures_requested_coverage_database(tmp_path, monkeypatch):
    project = _project(tmp_path, waveform=False, coverage=True)
    backend = VcsBackend()
    executable = (project.root / ".zddv" / "build" / "simv").resolve()
    executable.parent.mkdir(parents=True)
    executable.write_text("simv fixture\n", encoding="utf-8")

    monkeypatch.setattr(backend, "version", lambda: "VCS test")

    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = list(command)
        coverage_dir = Path(command[command.index("-cm_dir") + 1])
        coverage_dir.mkdir(parents=True)
        return SimpleNamespace(
            returncode=0,
            stdout="simulation complete\n",
        )

    monkeypatch.setattr("zddv.simulator.vcs.subprocess.run", fake_run)

    result = backend.run(project)

    command = captured["command"]
    assert command[command.index("-cm") + 1] == "line+cond+fsm+tgl+branch"
    coverage_dir = Path(command[command.index("-cm_dir") + 1])
    assert result.coverage_path == coverage_dir
    assert coverage_dir.name == "coverage.vdb"

    run_record = loads((result.run_dir / "run.json").read_text(encoding="utf-8"))
    assert run_record["coverage_requested"] is True
    assert run_record["coverage"] == str(coverage_dir)
    assert run_record["coverage_capture"] == "vdb"
    assert run_record["coverage_metrics"] == "line+cond+fsm+tgl+branch"


def test_vcs_run_does_not_claim_missing_coverage_database(tmp_path, monkeypatch):
    project = _project(tmp_path, waveform=False, coverage=True)
    backend = VcsBackend()
    executable = (project.root / ".zddv" / "build" / "simv").resolve()
    executable.parent.mkdir(parents=True)
    executable.write_text("simv fixture\n", encoding="utf-8")

    monkeypatch.setattr(backend, "version", lambda: "VCS test")
    monkeypatch.setattr(
        "zddv.simulator.vcs.subprocess.run",
        lambda command, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="simulation complete\n",
        ),
    )

    result = backend.run(project)

    assert result.coverage_path is None
    run_record = loads((result.run_dir / "run.json").read_text(encoding="utf-8"))
    assert run_record["coverage"] is None
    assert run_record["coverage_capture"] == "missing"

def test_doctor_can_check_vcs_backend(monkeypatch, capsys):
    class FakeBackend:
        def version(self):
            return "VCS test"

    monkeypatch.setattr("zddv.cli.get_backend", lambda name: FakeBackend())

    rc = main(["doctor", "--simulator", "vcs"])

    assert rc == 0
    output = capsys.readouterr().out
    assert "ZDDV 0.6.0" in output
    assert "[PASS] VCS test" in output


def test_vcs_version_prefers_documented_lowercase_id_option(monkeypatch):
    backend = VcsBackend()
    monkeypatch.setattr(backend, "_tool", lambda: "vcs")
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(list(command))
        return SimpleNamespace(
            returncode=0,
            stdout="Compiler version = VCS test\n",
            stderr="",
        )

    monkeypatch.setattr("zddv.simulator.vcs.subprocess.run", fake_run)

    version = backend.version()

    assert version == "Compiler version = VCS test"
    assert commands == [["vcs", "-id"]]


def test_vcs_version_falls_back_to_legacy_uppercase_id_option(monkeypatch):
    backend = VcsBackend()
    monkeypatch.setattr(backend, "_tool", lambda: "vcs")
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(list(command))
        if command[-1] == "-id":
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="unknown option -id\n",
            )
        return SimpleNamespace(
            returncode=0,
            stdout="Compiler version = VCS legacy test\n",
            stderr="",
        )

    monkeypatch.setattr("zddv.simulator.vcs.subprocess.run", fake_run)

    version = backend.version()

    assert version == "Compiler version = VCS legacy test"
    assert commands == [["vcs", "-id"], ["vcs", "-ID"]]
