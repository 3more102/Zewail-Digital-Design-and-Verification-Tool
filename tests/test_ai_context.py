from __future__ import annotations

from pathlib import Path

from zddv.ai_context import build_ai_rca_context, write_ai_rca_context
from zddv.cli import main
from zddv.config import initialize_project


def _ranking(run_id: str) -> dict:
    return {
        "run": {
            "run_id": run_id,
            "created_at": "2026-09-22T19:00:00+00:00",
            "status": "FAIL",
            "test_name": "smoke",
            "seed": 7,
            "simulator": "verilator",
            "top": "tb_top",
            "log_path": "/tmp/simulation.log",
            "waveform_path": "/tmp/waveform.vcd",
        },
        "failure_signature": "assertion:count_guard",
        "candidates": [
            {
                "rank": 1,
                "kind": "rtl_driver",
                "subject": "rtl/counter.sv:8 count",
                "evidence_score": 95,
                "score_basis": [{"reason": "explicit_driver_edge", "points": 35}],
                "evidence": [{"signal": "TOP.tb_top.dut.count"}],
            },
            {
                "rank": 2,
                "kind": "assertion_anchor",
                "subject": "count_guard",
                "evidence_score": 55,
                "score_basis": [{"reason": "failing_assertion", "points": 15}],
                "evidence": [{"assertion_name": "count_guard"}],
            },
        ],
        "summary": {
            "candidates": 2,
            "rtl_driver_candidates": 1,
            "assertion_events": 1,
            "assertions_with_waveform": 1,
            "assertions_with_signal_hints": 1,
        },
        "limitations": ["example limitation"],
    }


def _probes(run_id: str) -> dict:
    return {
        "run": _ranking(run_id)["run"],
        "suggestions": [
            {
                "rank": 1,
                "kind": "waveform_probe",
                "signal": "TOP.tb_top.dut.count",
                "run_id": run_id,
            }
        ],
        "blockers": [],
        "summary": {
            "suggestions": 1,
            "unique_signals": 1,
            "candidates_considered": 1,
            "blockers": 0,
        },
    }


def _patch_sources(monkeypatch, run_id: str) -> None:
    monkeypatch.setattr(
        "zddv.ai_context.rank_root_cause_candidates",
        lambda *args, **kwargs: _ranking(run_id),
    )
    monkeypatch.setattr(
        "zddv.ai_context.suggest_debug_probes_from_ranking",
        lambda *args, **kwargs: _probes(run_id),
    )


def test_ai_rca_context_is_evidence_only_and_provider_neutral(
    tmp_path: Path,
    monkeypatch,
):
    project = initialize_project(tmp_path / "demo")
    _patch_sources(monkeypatch, "run-fail")

    first = build_ai_rca_context(
        project,
        run_id="run-fail",
        candidate_limit=1,
    )
    second = build_ai_rca_context(
        project,
        run_id="run-fail",
        candidate_limit=1,
    )

    assert first["analysis"] == "ai_rca_context"
    assert first["summary"]["candidates_included"] == 1
    assert first["summary"]["candidates_available"] == 2
    assert first["policy"]["provider_neutral"] is True
    assert first["policy"]["external_transmission"] is False
    assert first["policy"]["automatic_model_invocation"] is False
    assert first["policy"]["automatic_command_execution"] is False
    assert first["policy"]["review_required_before_external_use"] is True
    assert first["provenance"]["deterministic"] is True
    assert len(first["provenance"]["evidence_sha256"]) == 64
    assert (
        first["provenance"]["evidence_sha256"]
        == second["provenance"]["evidence_sha256"]
    )
    assert "causal probability" in first["semantics"]


def test_ai_rca_context_write_and_cli(tmp_path: Path, monkeypatch, capsys):
    project = initialize_project(tmp_path / "demo")
    _patch_sources(monkeypatch, "run-cli")

    result = write_ai_rca_context(project, run_id="run-cli")
    assert Path(result["path"]).is_file()

    rc = main(
        [
            "--project",
            str(project.root),
            "ai-rca-context",
            "--run",
            "run-cli",
            "--candidate-limit",
            "1",
        ]
    )
    assert rc == 0
    output = capsys.readouterr().out
    assert "AI RCA CONTEXT:" in output
    assert "External transmission: disabled" in output
    assert "Automatic model invocation: disabled" in output
    assert "Evidence SHA-256:" in output
    assert (project.root / ".zddv" / "debug" / "ai-rca-context.json").is_file()
