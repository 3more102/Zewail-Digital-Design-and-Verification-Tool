from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zddv.config import ProjectConfig
from zddv.waveform import parse_vcd_header, select_waveform_run
from zddv.waveform_probe import sample_vcd_on_clock


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
    if "TIME" in upper:
        sample["time"] = upper["TIME"]
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


def _active(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text, 0) != 0
        except ValueError:
            return bool(text)
    return bool(value)


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
        if "time" in sample:
            entry["time"] = sample["time"]
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
        read_pstrb_reported = False
        if not write and _active(sample.get("PSTRB")):
            add_violation(
                "read_with_active_pstrb",
                sample,
                "PSTRB must not be active during a read transfer",
                transaction_index=tx_index,
                signal="PSTRB",
                expected=0,
                actual=sample.get("PSTRB"),
            )
            read_pstrb_reported = True

        return {
            "index": tx_index,
            "phase": "SETUP",
            "setup": sample,
            "write": write,
            "stable_fields": _request_fields(write, sample),
            "access_cycles": 0,
            "read_pstrb_reported": read_pstrb_reported,
        }

    def check_read_pstrb(sample: dict[str, Any], tx: dict[str, Any]) -> None:
        if tx["write"] or tx["read_pstrb_reported"] or not _active(sample.get("PSTRB")):
            return
        add_violation(
            "read_with_active_pstrb",
            sample,
            "PSTRB must not be active during a read transfer",
            transaction_index=tx["index"],
            signal="PSTRB",
            expected=0,
            actual=sample.get("PSTRB"),
        )
        tx["read_pstrb_reported"] = True

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
        if "time" in setup:
            transaction["start_time"] = setup["time"]
        if "time" in sample:
            transaction["end_time"] = sample["time"]
        if "PPROT" in setup:
            transaction["pprot"] = setup["PPROT"]
        if "PSTRB" in setup:
            transaction["pstrb"] = setup["PSTRB"]
        transactions.append(transaction)

    for sample in samples:
        psel = sample["PSEL"]
        penable = sample["PENABLE"]
        pready = sample["PREADY"]

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
            check_read_pstrb(sample, current)
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
        check_read_pstrb(sample, current)
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

    result = {
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
    if isinstance(payload.get("waveform"), dict):
        result["waveform"] = payload["waveform"]
    return result


def _scope_signal_names(header: dict[str, Any], scope: str) -> dict[str, str]:
    return {
        str(item["name"]).upper(): str(item["name"])
        for item in header.get("signals", [])
        if item.get("scope") == scope
    }


def _resolve_apb_scope(
    path: str | Path,
    *,
    scope: str | None = None,
    clock: str = "PCLK",
) -> tuple[str, dict[str, str]]:
    header = parse_vcd_header(path)
    required = {clock.upper(), "PSEL", "PENABLE"}

    if scope is not None:
        names = _scope_signal_names(header, scope)
        missing = sorted(required - set(names))
        if missing:
            raise RuntimeError(
                f"VCD scope '{scope}' is not an APB scope; missing: "
                + ", ".join(missing)
            )
        return scope, names

    candidates: list[tuple[str, dict[str, str]]] = []
    for item in header.get("scopes", []):
        candidate = str(item["path"])
        names = _scope_signal_names(header, candidate)
        if required.issubset(names):
            candidates.append((candidate, names))

    if not candidates:
        raise RuntimeError(
            "No APB waveform scope was found. Expected one scope containing "
            f"{clock}, PSEL, and PENABLE; use --scope when needed."
        )
    if len(candidates) > 1:
        choices = ", ".join(candidate for candidate, _ in candidates)
        raise RuntimeError(
            "Multiple APB waveform scopes were found; select one with --scope. "
            f"Candidates: {choices}"
        )
    return candidates[0]


def extract_apb_trace_from_vcd(
    path: str | Path,
    *,
    scope: str | None = None,
    clock: str = "PCLK",
    edge: str = "rising",
) -> dict[str, Any]:
    source = Path(path).resolve()
    resolved_scope, available = _resolve_apb_scope(
        source,
        scope=scope,
        clock=clock,
    )

    canonical_names = (
        "PSEL",
        "PENABLE",
        "PREADY",
        "PWRITE",
        "PSLVERR",
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
    )

    def full_path(name: str) -> str:
        return f"{resolved_scope}.{name}" if resolved_scope else name

    actual_clock = available[clock.upper()]
    clock_path = full_path(actual_clock)
    path_to_canonical = {
        full_path(available[name]): name
        for name in canonical_names
        if name in available
    }

    sampled = sample_vcd_on_clock(
        source,
        clock=clock_path,
        signals=list(path_to_canonical),
        edge=edge,
    )

    normalized_samples: list[dict[str, Any]] = []
    for raw in sampled["samples"]:
        sample: dict[str, Any] = {
            "cycle": raw["cycle"],
            "time": raw["time"],
        }
        values = raw["values"]
        for signal_path, canonical in path_to_canonical.items():
            if signal_path in values:
                sample[canonical] = values[signal_path]
        normalized_samples.append(sample)

    return {
        "source": "vcd-waveform",
        "waveform": {
            "path": str(source),
            "timescale": sampled.get("timescale"),
            "scope": resolved_scope,
            "clock": actual_clock,
            "clock_path": clock_path,
            "edge": edge,
        },
        "samples": normalized_samples,
    }


def analyze_apb_waveform(
    project: ProjectConfig,
    *,
    run_id: str | None = None,
    input_path: str | Path | None = None,
    scope: str | None = None,
    clock: str = "PCLK",
    edge: str = "rising",
    trace_output: str | Path = ".zddv/protocols/apb/waveform-trace.json",
    output: str | Path = ".zddv/protocols/apb/waveform-latest.json",
) -> dict[str, Any]:
    if run_id is not None and input_path is not None:
        raise ValueError("run_id and input_path are mutually exclusive")

    selected_run: dict[str, Any] | None = None
    if input_path is None:
        selected_run = select_waveform_run(project, run_id=run_id)
        source = Path(str(selected_run["waveform_path"])).resolve()
    else:
        source = Path(input_path)
        if not source.is_absolute():
            source = project.root / source
        source = source.resolve()

    trace = extract_apb_trace_from_vcd(
        source,
        scope=scope,
        clock=clock,
        edge=edge,
    )

    trace_path = Path(trace_output)
    if not trace_path.is_absolute():
        trace_path = project.root / trace_path
    trace_path = trace_path.resolve()
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text(json.dumps(trace, indent=2) + "\n", encoding="utf-8")

    report = analyze_apb_trace(trace)
    report["input_path"] = str(source)
    report["trace_path"] = str(trace_path)
    report["run_id"] = (
        str(selected_run["run_id"]) if selected_run is not None else None
    )

    destination = Path(output)
    if not destination.is_absolute():
        destination = project.root / destination
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    report["report_path"] = str(destination)
    return report


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
