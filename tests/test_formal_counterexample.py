from __future__ import annotations

import json
from pathlib import Path

import pytest

from zddv.cli import main
from zddv.config import initialize_project
from zddv.formal.counterexample import (
    COUNTEREXAMPLE_SCHEMA,
    ingest_formal_counterexample,
    normalize_formal_counterexample,
)


def _payload() -> dict[str, object]:
    return {
        "source": "unit-formal",
        "property": "p_req_ack",
        "property_kind": "assert",
        "time_unit": "ns",
        "signals": [
            {"name": "dut.clk", "width": 1},
            {"name": "dut.req", "width": 1},
            {"name": "dut.ack", "width": 1},
        ],
        "steps": [
            {
                "step": 0,
                "time": 0,
                "cycle": 0,
                "values": {"dut.clk": 0, "dut.req": True, "dut.ack": "0"},
            },
            {
                "step": 1,
                "time": 5,
                "cycle": 1,
                "values": {"dut.clk": 1, "dut.req": 1, "dut.ack": "0"},
            },
            {
                "step": 2,
                "time": 10,
                "cycle": 2,
                "values": {"dut.clk": 0, "dut.req": 1},
            },
        ],
        "metadata": {"engine": "unit"},
    }


def test_normalizes_formal_counterexample_contract():
    result = normalize_formal_counterexample(_payload())

    assert result["schema"] == COUNTEREXAMPLE_SCHEMA
    assert result["analysis"] == "formal_counterexample"
    assert result["property"] == "p_req_ack"
    assert result["property_kind"] == "assert"
    assert result["trace_kind"] == "counterexample"
    assert result["source"] == "unit-formal"
    assert result["summary"] == {
        "signals": 3,
        "steps": 3,
        "complete_signal_steps": 2,
        "partial_signal_steps": 1,
        "first_time": 0,
        "last_time": 10,
        "first_cycle": 0,
        "last_cycle": 2,
    }
    assert result["steps"][0]["values"]["dut.req"] == "1"
    assert result["steps"][0]["values"]["dut.clk"] == "0"


def test_infers_signal_catalog_and_cover_witness_kind():
    result = normalize_formal_counterexample(
        {
            "property": "c_recovery",
            "property_kind": "cover",
            "steps": [
                {"values": {"state": "IDLE", "valid": 0}},
                {"values": {"state": "RECOVER", "valid": 1}},
            ],
        },
        source="normalized-test",
    )

    assert result["trace_kind"] == "witness"
    assert result["source"] == "normalized-test"
    assert [item["name"] for item in result["signals"]] == ["state", "valid"]
    assert result["summary"]["complete_signal_steps"] == 2


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {
                "property": "p_bad",
                "property_kind": "assert",
                "signals": ["a"],
                "steps": [{"values": {"b": 1}}],
            },
            "undeclared signal",
        ),
        (
            {
                "property": "p_bad",
                "property_kind": "assert",
                "steps": [
                    {"step": 1, "time": 5, "values": {"a": 0}},
                    {"step": 2, "time": 4, "values": {"a": 1}},
                ],
            },
            "non-decreasing",
        ),
        (
            {
                "property": "p_bad",
                "property_kind": "assert",
                "steps": [],
            },
            "non-empty list",
        ),
    ],
)
def test_rejects_invalid_counterexample_evidence(payload, message):
    with pytest.raises(ValueError, match=message):
        normalize_formal_counterexample(payload)


def test_ingest_writes_normalized_artifact_with_input_provenance(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    source_path = project.root / "cex.json"
    source_path.write_text(json.dumps(_payload()), encoding="utf-8")

    result = ingest_formal_counterexample(project, source_path)

    normalized = Path(result["normalized_path"])
    assert normalized.is_file()
    saved = json.loads(normalized.read_text(encoding="utf-8"))
    assert saved["project"] == "demo"
    assert saved["property"] == "p_req_ack"
    assert saved["input_path"] == str(source_path.resolve())
    assert len(saved["input_sha256"]) == 64


def test_cli_imports_formal_counterexample(tmp_path: Path, capsys):
    project = initialize_project(tmp_path / "demo")
    source_path = project.root / "cex.json"
    source_path.write_text(json.dumps(_payload()), encoding="utf-8")

    rc = main(
        [
            "--project",
            str(project.root),
            "formal-counterexample-import",
            "cex.json",
        ]
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "FORMAL COUNTEREXAMPLE" in output
    assert "property=p_req_ack" in output
    assert "steps=3" in output
    assert (
        project.root / ".zddv" / "formal" / "counterexamples" / "latest.json"
    ).is_file()
