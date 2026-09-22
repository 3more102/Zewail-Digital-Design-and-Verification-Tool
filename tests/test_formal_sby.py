from __future__ import annotations

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from zddv.config import ProjectConfig
from zddv.formal import FormalCheckRequest
from zddv.formal.sby import (
    SymbiYosysBackend,
    render_sby_bmc_config,
)


def _project(tmp_path: Path) -> ProjectConfig:
    root = tmp_path / "demo"
    (root / "rtl").mkdir(parents=True)
    (root / "formal").mkdir()
    (root / "rtl" / "dut.sv").write_text(
        "module dut(input logic clk); always @(posedge clk) assert(1'b1); endmodule\n",
        encoding="utf-8",
    )
    (root / "formal" / "assertions.sv").write_text(
        "module extra_assertions; endmodule\n",
        encoding="utf-8",
    )
    (root / "formal" / "defs.svh").write_text(
        "// formal header fixture\n",
        encoding="utf-8",
    )
    return ProjectConfig(
        root=root,
        name="demo",
        top="dut",
        simulator="verilator",
        rtl=["rtl/*.sv"],
        tb=["formal/*"],
        waveform=False,
        coverage=False,
    )


def test_render_sby_bmc_config_preserves_bound_sources_and_headers(tmp_path: Path):
    project = _project(tmp_path)
    text, effective, sources = render_sby_bmc_config(
        project,
        FormalCheckRequest(mode="bmc", depth=12, timeout_s=2.2),
    )

    assert "[options]\nmode bmc\ndepth 12\ntimeout 3" in text
    assert "[engines]\nsmtbmc" in text
    assert "read -formal rtl/dut.sv" in text
    assert "read -formal formal/assertions.sv" in text
    assert "read -formal formal/defs.svh" not in text
    assert "prep -top dut" in text
    assert "[files]" in text
    assert "rtl/dut.sv" in text
    assert "formal/assertions.sv" in text
    assert "formal/defs.svh" in text
    assert effective.depth == 12
    assert len(sources) == 3


def test_render_sby_bmc_config_uses_documented_default_depth(tmp_path: Path):
    project = _project(tmp_path)
    text, effective, _ = render_sby_bmc_config(
        project,
        FormalCheckRequest(mode="bmc"),
    )

    assert effective.depth == 20
    assert "depth 20" in text


@pytest.mark.parametrize("mode", ["prove", "cover"])
def test_bounded_backend_rejects_unimplemented_modes(tmp_path: Path, mode: str):
    with pytest.raises(ValueError, match="only formal mode 'bmc'"):
        render_sby_bmc_config(
            _project(tmp_path),
            FormalCheckRequest(mode=mode),
        )


def test_bounded_backend_rejects_property_filtering(tmp_path: Path):
    with pytest.raises(ValueError, match="property filters are not implemented"):
        render_sby_bmc_config(
            _project(tmp_path),
            FormalCheckRequest(mode="bmc", properties=("p_safe",)),
        )


def test_sby_check_records_pass_and_trace_evidence(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    backend = SymbiYosysBackend()
    monkeypatch.setattr(
        "zddv.formal.sby.shutil.which",
        lambda name: "/opt/oss-cad-suite/bin/sby" if name == "sby" else None,
    )

    def fake_run(command, **kwargs):
        assert command[0] == "/opt/oss-cad-suite/bin/sby"
        work_dir = Path(command[command.index("-d") + 1])
        trace = work_dir / "engine_0" / "trace.vcd"
        trace.parent.mkdir(parents=True)
        trace.write_text("$date test $end\n", encoding="utf-8")
        return SimpleNamespace(
            returncode=0,
            stdout="SBY 0:00:01 [demo] DONE (PASS, rc=0)\n",
        )

    monkeypatch.setattr("zddv.formal.sby.subprocess.run", fake_run)

    result = backend.check(
        project,
        FormalCheckRequest(mode="bmc", depth=17, timeout_s=4),
    )

    assert result.status == "PASS"
    assert result.passed is True
    assert result.request.depth == 17
    assert result.engine == "smtbmc"
    assert result.log_path.is_file()
    assert (result.run_dir / "check.sby").is_file()
    manifest_path = result.run_dir / "formal.json"
    assert manifest_path.is_file()
    assert any(path.suffix == ".vcd" for path in result.artifacts)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "PASS"
    assert manifest["native_status_evidence"] == "PASS"
    assert manifest["request"]["depth"] == 17
    assert manifest["request"]["requested_depth"] == 17
    assert len(manifest["traces"]) == 1


def test_sby_check_accepts_explicit_fail_even_with_native_nonzero_rc(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    backend = SymbiYosysBackend()
    monkeypatch.setattr(
        "zddv.formal.sby.shutil.which",
        lambda name: "/usr/bin/sby" if name == "sby" else None,
    )
    monkeypatch.setattr(
        "zddv.formal.sby.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=2,
            stdout="SBY [demo] DONE (FAIL, rc=2)\n",
        ),
    )

    result = backend.check(project, FormalCheckRequest(mode="bmc", depth=5))

    assert result.status == "FAIL"
    assert result.returncode == 2


def test_sby_check_does_not_infer_pass_from_zero_exit_code(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    backend = SymbiYosysBackend()
    monkeypatch.setattr(
        "zddv.formal.sby.shutil.which",
        lambda name: "/usr/bin/sby" if name == "sby" else None,
    )
    monkeypatch.setattr(
        "zddv.formal.sby.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="SBY completed without a terminal status marker\n",
        ),
    )

    result = backend.check(project, FormalCheckRequest(mode="bmc"))

    assert result.status == "ERROR"
    assert result.request.depth == 20


def test_sby_check_outer_timeout_is_unknown(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    backend = SymbiYosysBackend()
    monkeypatch.setattr(
        "zddv.formal.sby.shutil.which",
        lambda name: "/usr/bin/sby" if name == "sby" else None,
    )

    def timeout_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=args[0],
            timeout=kwargs["timeout"],
            output="SBY still running\n",
        )

    monkeypatch.setattr("zddv.formal.sby.subprocess.run", timeout_run)

    result = backend.check(
        project,
        FormalCheckRequest(mode="bmc", timeout_s=1.1),
    )

    assert result.status == "UNKNOWN"
    assert result.returncode == 124
    assert "ZDDV_FORMAL_TIMEOUT" in result.log_path.read_text(encoding="utf-8")


def test_sby_version_and_missing_tool(tmp_path: Path, monkeypatch):
    backend = SymbiYosysBackend()
    monkeypatch.setattr(
        "zddv.formal.sby.shutil.which",
        lambda name: "/usr/bin/sby" if name == "sby" else None,
    )
    monkeypatch.setattr(
        "zddv.formal.sby.subprocess.run",
        lambda command, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="SBY 1.5.0\n",
        ),
    )

    assert backend.version() == "SBY 1.5.0"

    monkeypatch.setattr("zddv.formal.sby.shutil.which", lambda name: None)
    with pytest.raises(RuntimeError, match="was not found in PATH"):
        backend.version()


def test_sby_rejects_unsafe_top_identifier(tmp_path: Path):
    project = _project(tmp_path)
    project.top = "dut; bad-command"

    with pytest.raises(ValueError, match="simple Verilog top identifier"):
        render_sby_bmc_config(
            project,
            FormalCheckRequest(mode="bmc"),
        )
