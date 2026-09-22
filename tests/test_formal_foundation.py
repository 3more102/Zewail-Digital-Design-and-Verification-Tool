from __future__ import annotations

import json
from pathlib import Path

import pytest

from zddv.config import initialize_project
from zddv.formal import (
    FormalBackend,
    FormalRunRequest,
    FormalRunResult,
    analyze_formal_file,
    normalize_formal_data,
)


def test_normalizes_bounded_unbounded_counterexample_and_cover_evidence():
    result = normalize_formal_data(
        {
            "engine": "example-formal",
            "engine_version": "1.0",
            "source": "unit-test",
            "properties": [
                {
                    "name": "p_unbounded",
                    "kind": "assert",
                    "status": "pass",
                    "scope": "unbounded",
                },
                {
                    "name": "p_bounded",
                    "kind": "ASSERT",
                    "status": "PASS",
                    "scope": "BOUNDED",
                    "depth": 24,
                },
                {
                    "name": "p_failure",
                    "kind": "ASSERT",
                    "status": "FAIL",
                    "scope": "BOUNDED",
                    "depth": 7,
                    "counterexample": {
                        "path": "artifacts/p_failure.vcd",
                        "format": "VCD",
                    },
                },
                {
                    "name": "c_reachable",
                    "kind": "COVER",
                    "status": "PASS",
                    "scope": "BOUNDED",
                    "depth": 12,
                    "witness": {
                        "path": "artifacts/c_reachable.vcd",
                        "format": "VCD",
                    },
                },
                {
                    "name": "c_missing",
                    "kind": "COVER",
                    "status": "FAIL",
                    "scope": "BOUNDED",
                    "depth": 40,
                },
            ],
        }
    )

    assert result["status"] == "FAIL"
    assert result["source"] == "unit-test"
    assert result["summary"] == {
        "properties": 5,
        "assertions": 3,
        "covers": 2,
        "pass": 3,
        "fail": 2,
        "unknown": 0,
        "error": 0,
        "proved_assertions": 1,
        "bounded_safe_assertions": 1,
        "counterexamples": 1,
        "covered_goals": 1,
        "unreached_goals": 1,
    }
    assert result["properties"][0]["interpretation"] == "PROVED"
    assert result["properties"][1]["interpretation"] == "BOUNDED_SAFE"
    assert result["properties"][2]["interpretation"] == "COUNTEREXAMPLE"
    assert result["properties"][3]["interpretation"] == "COVERED"
    assert result["properties"][4]["interpretation"] == "UNREACHED"


def test_unreached_cover_does_not_turn_proof_status_into_failure():
    result = normalize_formal_data(
        {
            "engine": "example-formal",
            "properties": [
                {
                    "name": "cover_goal",
                    "kind": "COVER",
                    "status": "FAIL",
                    "scope": "BOUNDED",
                    "depth": 10,
                }
            ],
        }
    )

    assert result["status"] == "PASS"
    assert result["summary"]["unreached_goals"] == 1


def test_assertion_unknown_produces_unknown_proof_status():
    result = normalize_formal_data(
        {
            "engine": "example-formal",
            "properties": [
                {
                    "name": "p_unknown",
                    "kind": "ASSERT",
                    "status": "UNKNOWN",
                    "scope": "UNSPECIFIED",
                }
            ],
        }
    )
    assert result["status"] == "UNKNOWN"
    assert result["properties"][0]["interpretation"] == "UNKNOWN"


def test_bounded_scope_requires_depth():
    with pytest.raises(ValueError, match="depth is required"):
        normalize_formal_data(
            {
                "engine": "example-formal",
                "properties": [
                    {
                        "name": "p_bounded",
                        "kind": "ASSERT",
                        "status": "PASS",
                        "scope": "BOUNDED",
                    }
                ],
            }
        )


@pytest.mark.parametrize(
    ("kind", "status", "field"),
    [
        ("ASSERT", "PASS", "counterexample"),
        ("COVER", "FAIL", "witness"),
    ],
)
def test_rejects_artifacts_that_do_not_match_property_semantics(
    kind: str,
    status: str,
    field: str,
):
    payload = {
        "engine": "example-formal",
        "properties": [
            {
                "name": "p",
                "kind": kind,
                "status": status,
                "scope": "UNSPECIFIED",
                field: {"path": "artifact.vcd"},
            }
        ],
    }

    with pytest.raises(ValueError, match=field):
        normalize_formal_data(payload)


def test_analyze_formal_file_writes_normalized_report(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    source = project.root / "formal.json"
    source.write_text(
        json.dumps(
            {
                "engine": "example-formal",
                "properties": [
                    {
                        "name": "p_safe",
                        "kind": "ASSERT",
                        "status": "PASS",
                        "scope": "BOUNDED",
                        "depth": 16,
                        "location": {
                            "path": "rtl/counter.sv",
                            "line": 21,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = analyze_formal_file(project, source)

    report_path = Path(result["report_path"])
    assert report_path == project.root / ".zddv" / "formal" / "latest.json"
    assert report_path.is_file()
    persisted = json.loads(report_path.read_text(encoding="utf-8"))
    assert persisted["snapshot_id"] == result["snapshot_id"]
    assert persisted["project"] == project.name
    assert persisted["properties"][0]["location"]["line"] == 21


def test_formal_run_request_validates_depth_timeout_and_property_names():
    request = FormalRunRequest(
        property_names=("p_safe",),
        depth=32,
        timeout_s=10.0,
    )
    assert request.depth == 32

    with pytest.raises(ValueError, match="depth"):
        FormalRunRequest(depth=-1)

    with pytest.raises(ValueError, match="timeout_s"):
        FormalRunRequest(timeout_s=0)

    with pytest.raises(ValueError, match="property_names"):
        FormalRunRequest(property_names=("",))


class _ExampleBackend(FormalBackend):
    name = "example"

    def version(self) -> str:
        return "example 1.0"

    def run(
        self,
        project,
        request: FormalRunRequest,
    ) -> FormalRunResult:
        run_dir = project.root / ".zddv" / "formal" / "runs" / "example"
        run_dir.mkdir(parents=True, exist_ok=True)
        log = run_dir / "formal.log"
        log.write_text("formal run complete\n", encoding="utf-8")
        return FormalRunResult(
            engine=self.name,
            engine_version=self.version(),
            command=("example-formal",),
            returncode=0,
            run_dir=run_dir,
            log_path=log,
        )

    def normalize(self, project, result: FormalRunResult):
        return {
            "engine": result.engine,
            "engine_version": result.engine_version,
            "properties": [],
        }


def test_formal_backend_contract_can_be_implemented(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    backend = _ExampleBackend()
    run = backend.run(project, FormalRunRequest(depth=8))

    assert run.completed is True
    normalized = normalize_formal_data(backend.normalize(project, run))
    assert normalized["engine"] == "example"
    assert normalized["status"] == "PASS"
