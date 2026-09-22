from __future__ import annotations

import json
from pathlib import Path

import pytest

from zddv.cli import main
from zddv.config import initialize_project
from zddv.storage import list_uvm_item_handshake_events
from zddv.uvm_item_markers import (
    analyze_uvm_item_log,
    extract_uvm_item_markers,
)


def _marker(event: dict[str, object]) -> str:
    return "ZDDV_UVM_ITEM " + json.dumps(event, separators=(",", ":"))


def test_extracts_embedded_uvm_item_markers_with_log_line_provenance(tmp_path: Path):
    log = tmp_path / "sim.log"
    log.write_text(
        "\n".join(
            [
                "UVM_INFO @ 0: reporter [RNTST] Running test...",
                "UVM_INFO seq.svh(10) @ 1 ns: seq [ZDDV] "
                + _marker(
                    {
                        "item_id": "item-1",
                        "event": "GRANT",
                        "sequence_id": "seq-1",
                        "metadata": {"origin": "sequence"},
                    }
                ),
                _marker(
                    {
                        "item_id": "item-1",
                        "event": "REQUEST",
                        "sequence_id": "seq-1",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    trace = extract_uvm_item_markers(log, source="questa-marker")

    assert trace["source"] == "questa-marker"
    assert trace["source_log"] == str(log.resolve())
    assert trace["marker"] == "ZDDV_UVM_ITEM"
    assert len(trace["events"]) == 2
    assert trace["events"][0]["metadata"] == {
        "origin": "sequence",
        "log_line": 2,
    }
    assert trace["events"][1]["metadata"]["log_line"] == 3


def test_analyzes_marker_log_through_existing_item_pipeline(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    log = project.root / "sim.log"
    log.write_text(
        "\n".join(
            [
                _marker(
                    {
                        "item_id": "item-7",
                        "event": "GRANT",
                        "sequence_id": "seq-7",
                        "sequencer": "uvm_test_top.env.seqr",
                        "transaction_id": 7,
                        "time": "10 ns",
                    }
                ),
                _marker(
                    {
                        "item_id": "item-7",
                        "event": "REQUEST",
                        "sequence_id": "seq-7",
                        "sequencer": "uvm_test_top.env.seqr",
                        "transaction_id": 7,
                        "time": "10 ns",
                    }
                ),
                _marker(
                    {
                        "item_id": "item-7",
                        "event": "ITEM_DONE",
                        "sequence_id": "seq-7",
                        "sequencer": "uvm_test_top.env.seqr",
                        "transaction_id": 7,
                        "time": "18 ns",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = analyze_uvm_item_log(project, log, source="portable-marker")

    assert result["status"] == "PASS"
    assert result["summary"]["completed"] == 1
    assert result["arbitration"]["summary"]["grant_events"] == 1
    assert result["marker_events"] == 3
    assert result["source_log_path"] == str(log.resolve())
    assert Path(result["marker_trace_path"]).is_file()

    persisted = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
    assert persisted["marker_events"] == 3
    assert persisted["source_log_path"] == str(log.resolve())

    events = list_uvm_item_handshake_events(project, result["snapshot_id"])
    assert [event["event"] for event in events] == [
        "GRANT",
        "REQUEST",
        "ITEM_DONE",
    ]


def test_rejects_malformed_marker_json_with_source_line(tmp_path: Path):
    log = tmp_path / "sim.log"
    log.write_text(
        "noise\nZDDV_UVM_ITEM {not-json}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"sim\.log:2: invalid JSON"):
        extract_uvm_item_markers(log)


def test_rejects_log_without_explicit_markers(tmp_path: Path):
    log = tmp_path / "sim.log"
    log.write_text("UVM_INFO normal report only\n", encoding="utf-8")

    with pytest.raises(ValueError, match="No ZDDV_UVM_ITEM marker events found"):
        extract_uvm_item_markers(log)


def test_cli_uvm_item_log_analyze(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    log = project.root / "sim.log"
    log.write_text(
        "\n".join(
            [
                _marker({"item_id": "item-1", "event": "GRANT"}),
                _marker({"item_id": "item-1", "event": "REQUEST"}),
                _marker({"item_id": "item-1", "event": "ITEM_DONE"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    rc = main(
        [
            "--project",
            str(project.root),
            "uvm-item-log-analyze",
            str(log),
            "--source",
            "cli-marker-test",
        ]
    )

    assert rc == 0
    report = json.loads(
        (project.root / ".zddv/uvm/items/latest.json").read_text(encoding="utf-8")
    )
    assert report["source"] == "cli-marker-test"
    assert report["marker_events"] == 3
    assert report["summary"]["violations"] == 0
