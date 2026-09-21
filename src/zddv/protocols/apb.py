from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zddv.config import ProjectConfig


_CONTROL_SIGNALS = ("PSEL", "PENABLE", "PREADY", "PWRITE", "PSLVERR")


def _logic(value: Any, *, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"0", "false", "low"}:
            return False
        if normalized in {"1", "true", "high"}:
            return True
    raise ValueError(f"{name} must be a 0/1 logic value, got {value!r}")


def _scalar(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text, 0)
        except ValueError:
            return text
    return value


def _normalize_sample(raw: dict[str, Any], index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"APB sample {index} must be an object")

    upper = {str(key).upper(): value for key, value in raw.items()}
    for required in ("PSEL", "PENABLE"):
        if required not in upper:
            raise ValueError(f"APB sample {index} is missing {required}")

    sample: dict[str, Any] = {
        "sample_index": index,
        "cycle": upper.get("CYCLE", index),
    }
    for name in _CONTROL_SIGNALS:
        if name in upper:
            sample[name] = _logic(upper[name], name=name)

    sample.setdefault("PREADY", True)
    sample.setdefault("PSLVERR", False)

    for name in (
        "PADDR",
        "PWDATA",
        "PRDATA",
        "PSTRB",
        "PPROT",
        "PAUSER",
        "PWUSER",
        "PRUSER",
        "PBUSER",
        "PNSE",
    ):
        if name in upper:
            sample[name] = _scalar(upper[name])
    return sample


def _request_fields(write: bool, setup: dict[str, Any]) -> tuple[str, ...]:
    fields = ["PADDR", "PWRITE"]
    if write:
        fields.append("PWDATA")
    for optional in ("PPROT", "PSTRB", "PAUSER", "PWUSER", "PNSE"):
        if optional in setup:
            fields.append(optional)
    return tuple(fields)


def analyze_apb_trace(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("APB trace must be a JSON object")
    raw_samples = payload.get("samples")
    if not isinstance(raw_samples, list):
        raise ValueError("APB trace must contain a 'samples' list")

    samples = [_normalize_sample(sample, index) for index, sample in enumerate(raw_samples)]
    transactions: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    attempted = 0

    def add_violation(
        code: str,
        sample: dict[str, Any],
        message: str,
        *,
        transaction_index: int | None = None,
        signal: str | None = None,
        expected: Any = None,
        actual: Any = None,
    ) -> None:
        entry: dict[str, Any] = {
            "code": code,
            "sample_index": sample["sample_index"],
            "cycle": sample["cycle"],
            "message": message,
        }
        if transaction_index is not None:
            entry["transaction_index"] = transaction_index
        if signal is not None:
            entry["signal"] = signal
            entry["expected"] = expected
            entry["actual"] = actual
        violations.append(entry)

    def check_selected_fields(sample: dict[str, Any], tx: dict[str, Any]) -> None:
        setup = tx["setup"]
        for field in tx["stable_fields"]:
            if field not in setup or field not in sample:
                continue
            if sample[field] != setup[field]:
                add_violation(
                    "request_changed_during_transfer",
                    sample,
                    f"{field} changed between setup and access/completion",
                    transaction_index=tx["index"],
                    signal=field,
                    expected=setup[field],
                    actual=sample[field],
                )

    def start_setup(sample: dict[str, Any]) -> dict[str, Any]:
        nonlocal attempted
        tx_index = attempted
        attempted += 1
        write = bool(sample.get("PWRITE", False))

        if "PADDR" not in sample:
            add_violation(
                "missing_address",
                sample,
                "PADDR must be valid when PSEL is asserted in the setup phase",
                transaction_index=tx_index,
            )
        if "PWRITE" not in sample:
            add_violation(
                "missing_direction",
                sample,
                "PWRITE must be valid when PSEL is asserted",
                transaction_index=tx_index,
            )
        if write and "PWDATA" not in sample:
            add_violation(
                "missing_write_data",
                sample,
                "PWDATA must be valid for an APB write transfer",
                transaction_index=tx_index,
            )
        if not write and int(sample.get("PSTRB", 0) or 0) != 0:
            add_violation(
                "read_with_active_pstrb",
                sample,
                "PSTRB must not be active during a read transfer",
                transaction_index=tx_index,
                signal="PSTRB",
                expected=0,
                actual=sample.get("PSTRB"),
            )

        return {
            "index": tx_index,
            "phase": "SETUP",
            "setup": sample,
            "write": write,
            "stable_fields": _request_fields(write, sample),
            "access_cycles": 0,
        }

    def complete(sample: dict[str, Any], tx: dict[str, Any]) -> None:
        tx["access_cycles"] += 1
        setup = tx["setup"]
        write = tx["write"]
        transaction = {
            "index": tx["index"],
            "start_cycle": setup["cycle"],
            "end_cycle": sample["cycle"],
            "direction": "WRITE" if write else "READ",
            "address": setup.get("PADDR"),
            "write_data": setup.get("PWDATA") if write else None,
            "read_data": sample.get("PRDATA") if not write else None,
            "wait_cycles": max(0, tx["access_cycles"] - 1),
            "access_cycles": tx["access_cycles"],
            "response": "ERROR" if sample.get("PSLVERR", False) else "OK",
        }
        if "PPROT" in setup:
            transaction["pprot"] = setup["PPROT"]
        if "PSTRB" in setup:
            transaction["pstrb"] = setup["PSTRB"]
        transactions.append(transaction)

    for sample in samples:
        psel = sample["PSEL"]
        penable = sample["PENABLE"]
        pready = sample["PREADY"]

        if penable and not psel:
            add_violation(
                "penable_without_psel",
                sample,
                "PENABLE is asserted while PSEL is deasserted",
            )

        if current is None:
            if psel and not penable:
                current = start_setup(sample)
            elif psel and penable:
                add_violation(
                    "access_without_setup",
                    sample,
                    "APB access phase observed without a preceding setup phase",
                )
            continue

        if current["phase"] == "SETUP":
            if not (psel and penable):
                add_violation(
                    "setup_not_followed_by_access",
                    sample,
                    "The setup phase must be followed by an access phase",
                    transaction_index=current["index"],
                )
                current = start_setup(sample) if psel and not penable else None
                continue

            current["phase"] = "ACCESS"
            check_selected_fields(sample, current)
            if current["write"] and "PSTRB" in sample and sample["PSTRB"] != current["setup"].get("PSTRB"):
                pass
            if pready:
                complete(sample, current)
                current = None
            else:
                current["access_cycles"] += 1
            continue

        if not (psel and penable):
            add_violation(
                "access_terminated_before_ready",
                sample,
                "PSEL and PENABLE must remain asserted while PREADY is LOW",
                transaction_index=current["index"],
            )
            current = start_setup(sample) if psel and not penable else None
            continue

        check_selected_fields(sample, current)
        if pready:
            complete(sample, current)
            current = None
        else:
            current["access_cycles"] += 1

    if current is not None and samples:
        add_violation(
            "incomplete_transfer",
            samples[-1],
            "Trace ended before the APB transfer completed",
            transaction_index=current["index"],
        )

    reads = sum(1 for tx in transactions if tx["direction"] == "READ")
    writes = sum(1 for tx in transactions if tx["direction"] == "WRITE")
    errors = sum(1 for tx in transactions if tx["response"] == "ERROR")
    wait_cycles = sum(int(tx["wait_cycles"]) for tx in transactions)

    return {
        "protocol": "APB",
        "source": str(payload.get("source", "normalized-trace")),
        "status": "PASS" if not violations else "FAIL",
        "summary": {
            "samples": len(samples),
            "attempted_transfers": attempted,
            "completed_transactions": len(transactions),
            "reads": reads,
            "writes": writes,
            "error_responses": errors,
            "wait_cycles": wait_cycles,
            "violations": len(violations),
        },
        "transactions": transactions,
        "violations": violations,
    }


def analyze_apb_file(
    project: ProjectConfig,
    path: str | Path,
    *,
    output: str | Path | None = None,
) -> dict[str, Any]:
    source = Path(path)
    if not source.is_absolute():
        source = project.root / source
    source = source.resolve()
    payload = json.loads(source.read_text(encoding="utf-8"))
    report = analyze_apb_trace(payload)
    report["input_path"] = str(source)

    destination = Path(output) if output is not None else Path(".zddv/protocols/apb/latest.json")
    if not destination.is_absolute():
        destination = project.root / destination
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    report["report_path"] = str(destination)
    return report
