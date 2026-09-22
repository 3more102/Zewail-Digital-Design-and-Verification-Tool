from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from zddv.cli import main
from zddv.config import initialize_project, save_project
from zddv.formal import FormalCheckRequest, FormalCheckResult, SymbiYosysBackend
from zddv.formal.sby import render_sby_bmc_config
from zddv.storage import list_formal_result_snapshots


def _project(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    (project.root / "rtl" / "dut.sv").write_text(
        """module dut(input logic clk, input logic req, output logic ack);
always_ff @(posedge clk) ack <= req;
always_ff @(posedge clk) if ($past(req)) assert(ack);
endmodule
""",
        encoding="utf-8",
    )
    (project.root / "tb" / "formal_top.sv").write_text(
        """module formal_top;
logic clk, req, ack;
dut u_dut(.clk(clk), .req(req), .ack(ack));
endmodule
""",
        encoding="utf-8",
    )
    project.top = "formal_top"
    project.rtl = ["rtl/*.sv"]
    project.tb = ["tb/*.sv"]
    save_project(project)
    return project


def test_render_sby_bmc_config_uses_finite_depth_and_project_sources(
    tmp_path: Path,
):
    project = _project(tmp_path)
    request = FormalCheckRequest(mode="bmc", depth=12)

    text = render_sby_bmc_config(project, request)

    assert "[options]" in text
    assert "mode bmc" in text
    assert "depth 12" in text
    assert "[engines]\nsmtbmc" in text
    assert "read_verilog -formal -sv rtl/dut.sv" in text
    assert "read_verilog -formal -sv tb/formal_top.sv" in text
    assert "prep -top formal_top" in text
    assert "[files]" in text
    assert (project.root / "rtl" / "dut.sv").resolve().as_posix() in text


@pytest.mark.parametrize(
    ("request", "message"),
    [
        (FormalCheckRequest(mode="bmc"), "explicit depth"),
        (FormalCheckRequest(mode="prove", depth=10), "bmc mode only"),
        (
            FormalCheckRequest(
                mode="bmc",
                depth=10,
                properties=("p_req_ack",),
            ),
            "property filters",
        ),
    ],
)
def test_render_sby_rejects_unsupported_requests(
    tmp_path: Path,
    request: FormalCheckRequest,
    message: str,
):
    with pytest.raises(ValueError, match=message):
        render_sby_bmc_config(_project(tmp_path), request)


def test_sby_backend_version_uses_version_flag(tmp_path: Path, monkeypatch):
    _project(tmp_path)
    monkeypatch.setattr(
        "zddv.formal.sby.shutil.which",
        lambda name: "/opt/sby/bin/sby" if name == "sby" else None,
    )
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = list(command)
        return SimpleNamespace(returncode=0, stdout="sby 0.58\n")

    monkeypatch.setattr("zddv.formal.sby.subprocess.run", fake_run)

    assert SymbiYosysBackend().version() == "sby 0.58"
    assert captured["command"] == ["/opt/sby/bin/sby", "--version"]


@pytest.mark.parametrize(
    ("returncode", "terminal", "expected"),
    [
        (0, "SBY [job] DONE (PASS, rc=0)\n", "PASS"),
        (2, "SBY [job] DONE (FAIL, rc=2)\n", "FAIL"),
        (16, "SBY [job] DONE (ERROR, rc=16)\n", "ERROR"),
        (0, "SBY [job] completed without terminal marker\n", "ERROR"),
    ],
)
def test_sby_backend_normalizes_terminal_status_and_retains_evidence(
    tmp_path: Path,
    monkeypatch,
    returncode: int,
    terminal: str,
    expected: str,
):
    project = _project(tmp_path)
    monkeypatch.setattr(
        "zddv.formal.sby.shutil.which",
        lambda name: "/opt/sby/bin/sby" if name == "sby" else None,
    )
    captured: dict[str, object] = {}

    def fake_process(command, *, cwd, timeout_s):
        captured["command"] = list(command)
        captured["cwd"] = cwd
        captured["timeout_s"] = timeout_s
        config = Path(command[-1])
        assert config.exists()
        assert "depth 20" in config.read_text(encoding="utf-8")
        return SimpleNamespace(
            returncode=returncode,
            output=terminal,
            timed_out=False,
        )

    monkeypatch.setattr("zddv.formal.sby._run_process", fake_process)

    result = SymbiYosysBackend().check(
        project,
        FormalCheckRequest(mode="bmc", depth=20, timeout_s=3.0),
    )

    assert result.status == expected
    assert result.engine == "smtbmc"
    assert result.returncode == returncode
    assert result.request.depth == 20
    assert result.log_path.read_text(encoding="utf-8") == terminal
    assert len(result.artifacts) == 1
    assert result.artifacts[0].suffix == ".sby"
    assert captured["cwd"] == project.root
    assert captured["timeout_s"] == pytest.approx(3.0)
    command = captured["command"]
    assert command[:4] == [
        "/opt/sby/bin/sby",
        "-f",
        "-d",
        str(result.run_dir),
    ]


def test_sby_backend_timeout_is_unknown(tmp_path: Path, monkeypatch):
    project = _project(tmp_path)
    monkeypatch.setattr(
        "zddv.formal.sby.shutil.which",
        lambda name: "/usr/bin/sby",
    )

    def fake_process(command, *, cwd, timeout_s):
        return SimpleNamespace(
            returncode=-1,
            output="SBY [job] engine_0: running\n",
            timed_out=True,
        )

    monkeypatch.setattr("zddv.formal.sby._run_process", fake_process)

    result = SymbiYosysBackend().check(
        project,
        FormalCheckRequest(mode="bmc", depth=8, timeout_s=0.5),
    )

    assert result.status == "UNKNOWN"
    assert result.returncode == -1
    assert "timed out" in result.log_path.read_text(encoding="utf-8")


def test_formal_bmc_cli_persists_bounded_snapshot(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    project = _project(tmp_path)

    class FakeBackend:
        def version(self) -> str:
            return "sby test"

        def check(self, loaded, request):
            assert loaded.root == project.root
            assert request.mode == "bmc"
            assert request.depth == 16
            assert request.timeout_s == pytest.approx(2.0)
            run_dir = (
                project.root / ".zddv" / "formal" / "sby" / "test"
            )
            run_dir.mkdir(parents=True, exist_ok=True)
            config_path = (
                project.root / ".zddv" / "formal" / "sby" / "test.sby"
            )
            config_path.write_text(
                "[options]\nmode bmc\ndepth 16\n",
                encoding="utf-8",
            )
            log_path = run_dir / "formal.log"
            log_path.write_text(
                "SBY [test] DONE (PASS, rc=0)\n",
                encoding="utf-8",
            )
            return FormalCheckResult(
                backend="sby",
                engine="smtbmc",
                request=request,
                command=("sby",),
                returncode=0,
                status="PASS",
                run_dir=run_dir,
                log_path=log_path,
                artifacts=(config_path,),
                runtime_ms=1.25,
            )

    monkeypatch.setattr("zddv.cli.SymbiYosysBackend", FakeBackend)

    rc = main(
        [
            "--project",
            str(project.root),
            "formal-bmc",
            "--depth",
            "16",
            "--timeout",
            "2",
        ]
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "Formal backend: sby test" in output
    assert "FORMAL BMC PASS: depth=16 engine=smtbmc scope=BOUNDED" in output
    assert "Snapshot:" in output

    rows = list_formal_result_snapshots(
        project,
        status="PASS",
        mode="bmc",
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["backend"] == "sby"
    assert row["proof_scope"] == "BOUNDED"
    assert row["request_depth"] == 16
    assert row["property_count"] == 0
    assert row["proved_count"] == 0
