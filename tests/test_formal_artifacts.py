from __future__ import annotations

import hashlib
from pathlib import Path

from zddv.formal.artifacts import collect_formal_artifact_manifest
from zddv.formal.base import (
    FormalCheckRequest,
    FormalCheckResult,
    FormalPropertyResult,
)


def _result(run_dir: Path) -> FormalCheckResult:
    return FormalCheckResult(
        backend="example",
        engine="example-formal 1.0",
        request=FormalCheckRequest(mode="bmc", depth=20),
        command=("example-formal",),
        returncode=0,
        status="FAIL",
        run_dir=run_dir,
        log_path=run_dir / "formal.log",
        properties=(
            FormalPropertyResult(
                name="p_failure",
                kind="assert",
                status="FAIL",
                depth=7,
                trace_path=Path("traces/failure.vcd"),
            ),
            FormalPropertyResult(
                name="c_reached",
                kind="cover",
                status="COVERED",
                trace_path=Path("traces/cover.fst"),
            ),
            FormalPropertyResult(
                name="p_unknown",
                kind="assert",
                status="UNKNOWN",
                trace_path=Path("traces/debug.fsdb"),
            ),
        ),
        artifacts=(
            Path("traces/failure.vcd"),
            Path("reports/summary.json"),
            Path("reports/missing.txt"),
        ),
    )


def test_collects_roles_formats_hashes_and_missing_evidence(tmp_path: Path):
    run_dir = tmp_path / "run"
    (run_dir / "traces").mkdir(parents=True)
    (run_dir / "reports").mkdir()

    failure = run_dir / "traces" / "failure.vcd"
    failure.write_bytes(b"$date\nformal counterexample\n$end\n")
    cover = run_dir / "traces" / "cover.fst"
    cover.write_bytes(b"FST-test-bytes")
    debug = run_dir / "traces" / "debug.fsdb"
    debug.write_bytes(b"FSDB-test-bytes")
    summary = run_dir / "reports" / "summary.json"
    summary.write_text('{"status":"FAIL"}\n', encoding="utf-8")

    manifest = collect_formal_artifact_manifest(_result(run_dir))

    assert manifest["analysis"] == "formal_artifact_manifest"
    assert manifest["summary"]["artifact_links"] == 5
    assert manifest["summary"]["unique_files"] == 5
    assert manifest["summary"]["existing_files"] == 4
    assert manifest["summary"]["missing_files"] == 1
    assert manifest["summary"]["verified_files"] == 4
    assert manifest["summary"]["roles"] == {
        "COUNTEREXAMPLE": 1,
        "SUPPORTING": 2,
        "TRACE_EVIDENCE": 1,
        "WITNESS": 1,
    }
    assert manifest["summary"]["formats"] == {
        "FSDB": 1,
        "FST": 1,
        "JSON": 1,
        "TEXT": 1,
        "VCD": 1,
    }

    counterexample = manifest["artifacts"][0]
    assert counterexample["role"] == "COUNTEREXAMPLE"
    assert counterexample["format"] == "VCD"
    assert counterexample["property_name"] == "p_failure"
    assert counterexample["depth"] == 7
    assert counterexample["exists"] is True
    assert counterexample["size_bytes"] == failure.stat().st_size
    assert counterexample["sha256"] == hashlib.sha256(
        failure.read_bytes()
    ).hexdigest()

    witness = manifest["artifacts"][1]
    assert witness["role"] == "WITNESS"
    assert witness["depth"] == 20

    debug_trace = manifest["artifacts"][2]
    assert debug_trace["role"] == "TRACE_EVIDENCE"

    supporting = next(
        item
        for item in manifest["artifacts"]
        if item["path"] == "reports/summary.json"
    )
    assert supporting["role"] == "SUPPORTING"
    assert supporting["format"] == "JSON"

    missing = next(
        item
        for item in manifest["artifacts"]
        if item["path"] == "reports/missing.txt"
    )
    assert missing["exists"] is False
    assert missing["sha256"] is None


def test_deduplicates_top_level_artifact_already_linked_as_property_trace(
    tmp_path: Path,
):
    run_dir = tmp_path / "run"
    (run_dir / "traces").mkdir(parents=True)
    trace = run_dir / "traces" / "failure.vcd"
    trace.write_text("trace\n", encoding="utf-8")

    result = FormalCheckResult(
        backend="example",
        request=FormalCheckRequest(mode="bmc", depth=5),
        command=(),
        returncode=0,
        status="FAIL",
        run_dir=run_dir,
        log_path=run_dir / "formal.log",
        properties=(
            FormalPropertyResult(
                name="p",
                kind="assert",
                status="FAIL",
                trace_path=Path("traces/failure.vcd"),
            ),
        ),
        artifacts=(Path("traces/failure.vcd"),),
    )

    manifest = collect_formal_artifact_manifest(result)

    assert manifest["summary"]["artifact_links"] == 1
    assert manifest["summary"]["roles"] == {"COUNTEREXAMPLE": 1}


def test_verification_can_be_disabled_without_dropping_existence(tmp_path: Path):
    run_dir = tmp_path / "run"
    (run_dir / "traces").mkdir(parents=True)
    trace = run_dir / "traces" / "failure.vcd"
    trace.write_bytes(b"trace")

    manifest = collect_formal_artifact_manifest(
        _result(run_dir),
        verify_files=False,
    )

    counterexample = manifest["artifacts"][0]
    assert counterexample["exists"] is True
    assert counterexample["size_bytes"] is None
    assert counterexample["sha256"] is None
    assert manifest["summary"]["verified_files"] == 0


def test_absolute_paths_remain_absolute(tmp_path: Path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    trace = tmp_path / "external.vcd"
    trace.write_text("trace\n", encoding="utf-8")

    result = FormalCheckResult(
        backend="example",
        request=FormalCheckRequest(mode="prove"),
        command=(),
        returncode=0,
        status="FAIL",
        run_dir=run_dir,
        log_path=run_dir / "formal.log",
        properties=(
            FormalPropertyResult(
                name="p",
                kind="assert",
                status="FAIL",
                trace_path=trace,
            ),
        ),
    )

    manifest = collect_formal_artifact_manifest(result)
    artifact = manifest["artifacts"][0]

    assert artifact["resolved_path"] == str(trace.resolve())
    assert artifact["exists"] is True
