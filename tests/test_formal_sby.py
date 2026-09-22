from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from zddv.cli import main
from zddv.config import ProjectConfig, save_project
from zddv.formal import (
    FormalCheckRequest,
    FormalCheckResult,
    FormalPropertyResult,
    SymbiYosysBackend,
)
from zddv.formal.sby import (
    parse_sby_status_jsonl,
    render_sby_bmc_config,
    render_sby_cover_config,
)
from zddv.storage import list_formal_result_snapshots


def _project(tmp_path: Path) -> ProjectConfig:
    root = tmp_path / "demo"
    (root / "rtl").mkdir(parents=True)
    (root / "tb").mkdir()
    (root / "rtl" / "dut.sv").write_text(
        """module dut(input logic clk, input logic req, output logic ack);
always_ff @(posedge clk) ack <= req;
always_ff @(posedge clk) if ($past(req)) assert(ack);
endmodule
""",
        encoding="utf-8",
    )
    (root / "tb" / "formal_top.sv").write_text(
        """module formal_top;
logic clk, req, ack;
dut u_dut(.clk(clk), .req(req), .ack(ack));
endmodule
""",
        encoding="utf-8",
    )
    project = ProjectConfig(
        root=root,
        name="demo",
        top="formal_top",
        simulator="verilator",
        rtl=["rtl/*.sv"],
        tb=["tb/*.sv"],
        waveform=False,
        coverage=False,
    )
    save_project(project)
    return project


def test_render_sby_bmc_config_uses_explicit_bound_and_project_sources(tmp_path: Path):
    project = _project(tmp_path)
    request = FormalCheckRequest(mode="bmc", depth=12)

    text = render_sby_bmc_config(project, request)

    assert "[options]" in text
    assert "mode bmc" in text
    assert "depth 12" in text
    assert "timeout " not in text
    assert "[engines]\nsmtbmc" in text
    assert "read_verilog -formal -sv rtl/dut.sv" in text
    assert "read_verilog -formal -sv tb/formal_top.sv" in text
    assert "prep -top formal_top" in text
    assert "[files]" in text
    assert (project.root / "rtl" / "dut.sv").resolve().as_posix() in text


def test_render_sby_cover_config_uses_explicit_bound_and_project_sources(tmp_path: Path):
    project = _project(tmp_path)
    request = FormalCheckRequest(mode="cover", depth=24)

    text = render_sby_cover_config(project, request)

    assert "[options]" in text
    assert "mode cover" in text
    assert "depth 24" in text
    assert "[engines]\nsmtbmc" in text
    assert "read_verilog -formal -sv rtl/dut.sv" in text
    assert "prep -top formal_top" in text


@pytest.mark.parametrize(
    ("formal_request", "message"),
    [
        (FormalCheckRequest(mode="bmc"), "explicit depth"),
        (FormalCheckRequest(mode="prove", depth=10), "bmc mode only"),
        (
            FormalCheckRequest(mode="bmc", depth=10, properties=("p_req_ack",)),
            "property filters",
        ),
    ],
)
def test_render_sby_bmc_config_rejects_unsupported_or_unbounded_requests(
    tmp_path: Path,
    formal_request: FormalCheckRequest,
    message: str,
):
    with pytest.raises(ValueError, match=message):
        render_sby_bmc_config(_project(tmp_path), formal_request)


def test_sby_backend_version_uses_documented_version_flag(tmp_path: Path, monkeypatch):
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


def test_parse_sby_status_jsonl_normalizes_assertions_and_retains_trace():
    rows = parse_sby_status_jsonl(
        "\n".join(
            [
                '{"task_name":"demo","mode":"bmc","engine":"smtbmc","name":"formal_top.a_req_ack","location":"rtl/dut.sv:3","kind":"ASSERT","status":"PASS","depth":20}',
                '{"task_name":"demo","mode":"bmc","engine":"smtbmc","name":"formal_top.a_never_bad","location":"rtl/dut.sv:4","kind":"ASSERT","status":"FAIL","trace":"/tmp/demo/engine_0/trace.vcd","depth":7}',
                '{"task_name":"demo","mode":"bmc","name":"formal_top.c_seen","kind":"COVER","status":"PASS","depth":3}',
            ]
        )
        + "\n"
    )

    assert [row.name for row in rows] == [
        "formal_top.a_req_ack",
        "formal_top.a_never_bad",
    ]
    assert rows[0].kind == "assert"
    assert rows[0].status == "PASS"
    assert rows[0].depth == 20
    assert rows[1].status == "FAIL"
    assert rows[1].depth == 7
    assert rows[1].trace_path == Path("/tmp/demo/engine_0/trace.vcd")


def test_parse_sby_status_jsonl_normalizes_cover_reachability_and_witness():
    rows = parse_sby_status_jsonl(
        "\n".join(
            [
                '{"task_name":"demo","mode":"cover","engine":"smtbmc","name":"formal_top.c_seen","location":"tb/formal_top.sv:8","kind":"COVER","status":"PASS","trace":"/tmp/demo/engine_0/trace0.vcd","depth":3}',
                '{"task_name":"demo","mode":"cover","engine":"smtbmc","name":"formal_top.c_missing","location":"tb/formal_top.sv:9","kind":"COVER","status":"FAIL","depth":24}',
                '{"task_name":"demo","mode":"cover","name":"formal_top.a_safe","kind":"ASSERT","status":"PASS","depth":24}',
            ]
        )
        + "\n",
        mode="cover",
    )

    assert [row.name for row in rows] == [
        "formal_top.c_seen",
        "formal_top.c_missing",
    ]
    assert rows[0].kind == "cover"
    assert rows[0].status == "COVERED"
    assert rows[0].depth == 3
    assert rows[0].trace_path == Path("/tmp/demo/engine_0/trace0.vcd")
    assert rows[1].status == "UNCOVERED"
    assert rows[1].depth == 24


def test_parse_sby_status_jsonl_rejects_conflicting_assertion_rows():
    with pytest.raises(ValueError, match="conflicting SBY property-status rows"):
        parse_sby_status_jsonl(
            "\n".join(
                [
                    '{"name":"formal_top.a_req_ack","kind":"ASSERT","status":"PASS","depth":20}',
                    '{"name":"formal_top.a_req_ack","kind":"ASSERT","status":"FAIL","depth":7}',
                ]
            )
        )


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
        if "--statusfmt" in command:
            return SimpleNamespace(returncode=0, output="", timed_out=False)

        captured["command"] = list(command)
        captured["cwd"] = cwd
        captured["timeout_s"] = timeout_s
        config = Path(command[-1])
        assert config.exists()
        config_text = config.read_text(encoding="utf-8")
        assert "depth 20" in config_text
        assert "timeout 3" in config_text
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
    if expected in {"PASS", "FAIL"}:
        assert len(result.artifacts) == 2
        assert result.artifacts[1].name == "property-status.jsonl"
    else:
        assert len(result.artifacts) == 1
    assert result.artifacts[0].suffix == ".sby"
    assert captured["cwd"] == project.root
    assert captured["timeout_s"] == pytest.approx(3.0)
    command = captured["command"]
    assert command[:4] == ["/opt/sby/bin/sby", "-f", "-d", str(result.run_dir)]


def test_sby_backend_ingests_machine_readable_assertion_statuses(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    monkeypatch.setattr(
        "zddv.formal.sby.shutil.which",
        lambda name: "/opt/sby/bin/sby" if name == "sby" else None,
    )
    captured: dict[str, object] = {}

    def fake_process(command, *, cwd, timeout_s):
        if "--statusfmt" in command:
            captured["status_command"] = list(command)
            return SimpleNamespace(
                returncode=0,
                output=(
                    '{"task_name":"demo","mode":"bmc","engine":"smtbmc",'
                    '"name":"formal_top.a_req_ack","location":"rtl/dut.sv:3",'
                    '"kind":"ASSERT","status":"PASS","depth":20}\n'
                    '{"task_name":"demo","mode":"bmc","engine":"smtbmc",'
                    '"name":"formal_top.a_bad","location":"rtl/dut.sv:4",'
                    '"kind":"ASSERT","status":"FAIL","depth":7,'
                    '"trace":"/tmp/demo/engine_0/trace.vcd"}\n'
                ),
                timed_out=False,
            )
        return SimpleNamespace(
            returncode=2,
            output="SBY [job] DONE (FAIL, rc=2)\n",
            timed_out=False,
        )

    monkeypatch.setattr("zddv.formal.sby._run_process", fake_process)

    result = SymbiYosysBackend().check(
        project,
        FormalCheckRequest(mode="bmc", depth=20, timeout_s=3.0),
    )

    assert result.status == "FAIL"
    assert [item.status for item in result.properties] == ["PASS", "FAIL"]
    assert [item.depth for item in result.properties] == [20, 7]
    assert result.properties[1].trace_path == Path("/tmp/demo/engine_0/trace.vcd")
    assert result.artifacts[1].name == "property-status.jsonl"
    assert result.artifacts[1].read_text(encoding="utf-8").count("\n") == 2
    assert captured["status_command"] == [
        "/opt/sby/bin/sby",
        "--statusfmt",
        "jsonl",
        "--latest",
        str(result.run_dir),
    ]


def test_sby_backend_ingests_machine_readable_cover_statuses(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    monkeypatch.setattr(
        "zddv.formal.sby.shutil.which",
        lambda name: "/opt/sby/bin/sby" if name == "sby" else None,
    )
    captured: dict[str, object] = {}

    def fake_process(command, *, cwd, timeout_s):
        if "--statusfmt" in command:
            captured["status_command"] = list(command)
            return SimpleNamespace(
                returncode=0,
                output=(
                    '{"task_name":"demo","mode":"cover","engine":"smtbmc",'
                    '"name":"formal_top.c_seen","kind":"COVER","status":"PASS",'
                    '"trace":"/tmp/demo/engine_0/trace0.vcd","depth":3}\n'
                    '{"task_name":"demo","mode":"cover","engine":"smtbmc",'
                    '"name":"formal_top.c_missing","kind":"COVER","status":"FAIL",'
                    '"depth":24}\n'
                ),
                timed_out=False,
            )

        config = Path(command[-1])
        config_text = config.read_text(encoding="utf-8")
        assert "mode cover" in config_text
        assert "depth 24" in config_text
        captured["run_command"] = list(command)
        return SimpleNamespace(
            returncode=2,
            output="SBY [job] DONE (FAIL, rc=2)\n",
            timed_out=False,
        )

    monkeypatch.setattr("zddv.formal.sby._run_process", fake_process)

    result = SymbiYosysBackend().check(
        project,
        FormalCheckRequest(mode="cover", depth=24, timeout_s=3.0),
    )

    assert result.status == "FAIL"
    assert result.request.mode == "cover"
    assert result.run_dir.name.startswith("cover-")
    assert [item.status for item in result.properties] == ["COVERED", "UNCOVERED"]
    assert [item.depth for item in result.properties] == [3, 24]
    assert result.properties[0].trace_path == Path("/tmp/demo/engine_0/trace0.vcd")
    assert result.artifacts[1].name == "property-status.jsonl"
    assert captured["status_command"] == [
        "/opt/sby/bin/sby",
        "--statusfmt",
        "jsonl",
        "--latest",
        str(result.run_dir),
    ]


def test_sby_backend_keeps_malformed_property_status_evidence_without_claims(
    tmp_path: Path,
    monkeypatch,
):
    project = _project(tmp_path)
    monkeypatch.setattr("zddv.formal.sby.shutil.which", lambda name: "/usr/bin/sby")

    def fake_process(command, *, cwd, timeout_s):
        if "--statusfmt" in command:
            return SimpleNamespace(
                returncode=0,
                output="{not-json}\n",
                timed_out=False,
            )
        return SimpleNamespace(
            returncode=0,
            output="SBY [job] DONE (PASS, rc=0)\n",
            timed_out=False,
        )

    monkeypatch.setattr("zddv.formal.sby._run_process", fake_process)

    result = SymbiYosysBackend().check(
        project,
        FormalCheckRequest(mode="bmc", depth=8),
    )

    assert result.status == "PASS"
    assert result.properties == ()
    assert result.artifacts[1].name == "property-status.jsonl"
    assert result.artifacts[1].read_text(encoding="utf-8") == "{not-json}\n"


def test_sby_backend_timeout_is_unknown_not_pass_or_fail(tmp_path: Path, monkeypatch):
    project = _project(tmp_path)
    monkeypatch.setattr("zddv.formal.sby.shutil.which", lambda name: "/usr/bin/sby")

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


def test_sby_backend_requires_installed_executable(tmp_path: Path, monkeypatch):
    project = _project(tmp_path)
    monkeypatch.setattr("zddv.formal.sby.shutil.which", lambda name: None)

    with pytest.raises(RuntimeError, match="not found in PATH"):
        SymbiYosysBackend().check(
            project,
            FormalCheckRequest(mode="bmc", depth=8),
        )


def test_formal_bmc_cli_surfaces_normalized_result(tmp_path: Path, monkeypatch, capsys):
    project = _project(tmp_path)

    class FakeBackend:
        def version(self) -> str:
            return "sby test"

        def check(self, loaded, request):
            assert loaded.root == project.root
            assert request.mode == "bmc"
            assert request.depth == 16
            assert request.timeout_s == pytest.approx(2.0)
            run_dir = project.root / ".zddv" / "formal" / "sby" / "test"
            return FormalCheckResult(
                backend="sby",
                engine="smtbmc",
                request=request,
                command=("sby",),
                returncode=0,
                status="PASS",
                run_dir=run_dir,
                log_path=run_dir / "formal.log",
                artifacts=(run_dir / "job.sby",),
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

    output = capsys.readouterr().out
    assert rc == 0
    assert "Formal backend: sby test" in output
    assert "FORMAL BMC PASS: depth=16 engine=smtbmc" in output
    assert "Snapshot:" in output
    assert "Report:" in output

    rows = list_formal_result_snapshots(project)
    assert len(rows) == 1
    row = rows[0]
    assert row["backend"] == "sby"
    assert row["status"] == "PASS"
    assert row["mode"] == "bmc"
    assert row["proof_scope"] == "BOUNDED"
    assert row["request_depth"] == 16
    assert row["property_count"] == 0
    assert row["bounded_safe_count"] == 0
    assert row["proved_count"] == 0
    assert row["input_path"].endswith("formal.log")
    assert not row["input_path"].endswith("job.sby")
    assert row["report_path"].endswith("zddv-formal-result.json")
    assert Path(row["report_path"]).is_file()


def test_formal_cover_cli_persists_cover_reachability(tmp_path: Path, monkeypatch, capsys):
    project = _project(tmp_path)

    class FakeBackend:
        def version(self) -> str:
            return "sby test"

        def check(self, loaded, request):
            assert loaded.root == project.root
            assert request.mode == "cover"
            assert request.depth == 24
            assert request.timeout_s == pytest.approx(2.0)
            run_dir = project.root / ".zddv" / "formal" / "sby" / "cover-test"
            return FormalCheckResult(
                backend="sby",
                engine="smtbmc",
                request=request,
                command=("sby",),
                returncode=2,
                status="FAIL",
                run_dir=run_dir,
                log_path=run_dir / "formal.log",
                properties=(
                    FormalPropertyResult(
                        name="formal_top.c_seen",
                        kind="cover",
                        status="COVERED",
                        depth=3,
                        trace_path=run_dir / "trace0.vcd",
                    ),
                    FormalPropertyResult(
                        name="formal_top.c_missing",
                        kind="cover",
                        status="UNCOVERED",
                        depth=24,
                    ),
                ),
                artifacts=(run_dir / "job.sby",),
            )

    monkeypatch.setattr("zddv.cli.SymbiYosysBackend", FakeBackend)

    rc = main(
        [
            "--project",
            str(project.root),
            "formal-cover",
            "--depth",
            "24",
            "--timeout",
            "2",
        ]
    )

    output = capsys.readouterr().out
    assert rc == 1
    assert "Formal backend: sby test" in output
    assert "FORMAL COVER FAIL: depth=24 engine=smtbmc" in output
    assert "Covered/Unreached/Unknown: 1/1/0" in output
    assert "Snapshot:" in output
    assert "Report:" in output

    rows = list_formal_result_snapshots(project)
    assert len(rows) == 1
    row = rows[0]
    assert row["backend"] == "sby"
    assert row["status"] == "FAIL"
    assert row["mode"] == "cover"
    assert row["proof_scope"] == "COVER"
    assert row["request_depth"] == 24
    assert row["property_count"] == 2
    assert row["cover_count"] == 2
    assert row["covered_goal_count"] == 1
    assert row["unreached_goal_count"] == 1
    assert row["report_path"].endswith("zddv-formal-result.json")
    assert Path(row["report_path"]).is_file()
