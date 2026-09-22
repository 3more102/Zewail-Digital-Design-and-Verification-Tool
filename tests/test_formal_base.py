from __future__ import annotations

from pathlib import Path

import pytest

from zddv.config import ProjectConfig
from zddv.formal import (
    FormalBackend,
    FormalCheckRequest,
    FormalCheckResult,
    FormalPropertyResult,
)


def _project(tmp_path: Path) -> ProjectConfig:
    root = tmp_path / "demo"
    root.mkdir()
    return ProjectConfig(
        root=root,
        name="demo",
        top="dut",
        simulator="verilator",
        rtl=[],
        tb=[],
        waveform=False,
        coverage=False,
    )


def test_formal_check_request_normalizes_public_contract():
    request = FormalCheckRequest(
        mode=" BMC ",
        depth=20,
        properties=["p_req_ack", "p_no_overflow"],
        timeout_s=2,
    )

    assert request.mode == "bmc"
    assert request.depth == 20
    assert request.properties == ("p_req_ack", "p_no_overflow")
    assert request.timeout_s == pytest.approx(2.0)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"mode": "simulation"}, "unsupported formal mode"),
        ({"mode": "bmc", "depth": 0}, "depth must be >= 1"),
        ({"mode": "prove", "properties": [""]}, "must not be empty"),
        ({"mode": "cover", "timeout_s": 0}, "timeout must be > 0"),
    ],
)
def test_formal_check_request_rejects_invalid_inputs(kwargs, message):
    with pytest.raises(ValueError, match=message):
        FormalCheckRequest(**kwargs)


def test_formal_property_result_uses_kind_specific_statuses(tmp_path: Path):
    assertion = FormalPropertyResult(
        name="p_req_ack",
        kind="ASSERT",
        status="fail",
        depth=7,
        trace_path=tmp_path / "p_req_ack.vcd",
    )
    cover = FormalPropertyResult(
        name="c_recovery",
        kind="cover",
        status="covered",
        depth=12,
    )

    assert assertion.kind == "assert"
    assert assertion.status == "FAIL"
    assert assertion.trace_path == tmp_path / "p_req_ack.vcd"
    assert cover.status == "COVERED"

    with pytest.raises(ValueError, match="unsupported assert property status"):
        FormalPropertyResult(name="p_bad", kind="assert", status="covered")


def test_formal_check_result_normalizes_artifacts_and_summary(tmp_path: Path):
    request = FormalCheckRequest(mode="prove")
    properties = (
        FormalPropertyResult(name="p_a", kind="assert", status="PASS"),
        FormalPropertyResult(name="p_b", kind="assert", status="FAIL", depth=9),
        FormalPropertyResult(name="c_a", kind="cover", status="COVERED", depth=4),
    )
    result = FormalCheckResult(
        backend="dummy",
        engine="engine-a",
        request=request,
        command=["formal", "--prove"],
        returncode=1,
        status="fail",
        run_dir=tmp_path / "run",
        log_path=tmp_path / "run" / "formal.log",
        properties=properties,
        artifacts=[tmp_path / "run" / "engine-a.log"],
        runtime_ms=12.5,
    )

    assert result.status == "FAIL"
    assert result.passed is False
    assert result.command == ("formal", "--prove")
    assert result.artifacts == (tmp_path / "run" / "engine-a.log",)
    assert result.property_summary() == {"COVERED": 1, "FAIL": 1, "PASS": 1}


class _DummyFormalBackend(FormalBackend):
    name = "dummy"

    def version(self) -> str:
        return "dummy 1.0"

    def check(
        self,
        project: ProjectConfig,
        request: FormalCheckRequest,
    ) -> FormalCheckResult:
        run_dir = project.root / ".zddv" / "formal" / "dummy"
        return FormalCheckResult(
            backend=self.name,
            request=request,
            command=("dummy-formal", request.mode),
            returncode=0,
            status="PASS",
            run_dir=run_dir,
            log_path=run_dir / "formal.log",
        )


def test_formal_backend_contract_accepts_concrete_adapter(tmp_path: Path):
    backend = _DummyFormalBackend()
    result = backend.check(_project(tmp_path), FormalCheckRequest(mode="prove"))

    assert backend.version() == "dummy 1.0"
    assert result.backend == "dummy"
    assert result.passed is True
