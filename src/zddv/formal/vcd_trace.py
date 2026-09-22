from __future__ import annotations

import hashlib
from pathlib import Path
import re
from typing import Any, Iterable

from zddv.config import ProjectConfig
from zddv.waveform import parse_vcd_header

from .counterexample import normalize_formal_counterexample, persist_normalized_formal_trace


_SCALAR_CHANGE_RE = re.compile(r"^(?P<value>[01xXzZ])(?P<id>\S+)$")
_VECTOR_CHANGE_RE = re.compile(r"^[bB](?P<value>[01xXzZ]+)\s+(?P<id>\S+)$")
_REAL_CHANGE_RE = re.compile(r"^[rR](?P<value>[^\s]+)\s+(?P<id>\S+)$")


def _iter_vcd_body(path: Path):
    in_body = False
    saw_enddefinitions = False

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.strip()

            if not in_body:
                if "$enddefinitions" in line:
                    saw_enddefinitions = True
                    tail = line.split("$enddefinitions", 1)[1]
                    if "$end" in tail:
                        in_body = True
                    continue
                if saw_enddefinitions and "$end" in line:
                    in_body = True
                    continue
                continue

            if line:
                yield line

    if not in_body:
        raise RuntimeError(
            f"VCD header is incomplete: '$enddefinitions $end' was not found in {path}"
        )


def _resolve_requested_signals(
    requested: Iterable[str],
    declared: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    queries = [str(item).strip() for item in requested if str(item).strip()]
    if not queries:
        return list(declared)

    selected: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for query in queries:
        exact = [signal for signal in declared if signal["path"] == query]
        if len(exact) == 1:
            matches = exact
        else:
            by_name = [signal for signal in declared if signal["name"] == query]
            if len(by_name) > 1:
                choices = ", ".join(sorted(str(signal["path"]) for signal in by_name))
                raise RuntimeError(
                    f"Formal VCD signal '{query}' is ambiguous; use a full path: {choices}"
                )
            matches = by_name

        if not matches:
            raise RuntimeError(f"Formal VCD signal '{query}' was not found.")

        signal = matches[0]
        path = str(signal["path"])
        if path in seen_paths:
            continue
        seen_paths.add(path)
        selected.append(signal)

    return selected


def _parse_value_change(line: str) -> tuple[str, str] | None:
    scalar = _SCALAR_CHANGE_RE.match(line)
    if scalar is not None:
        return scalar.group("id"), scalar.group("value").lower()

    vector = _VECTOR_CHANGE_RE.match(line)
    if vector is not None:
        return vector.group("id"), vector.group("value").lower()

    real = _REAL_CHANGE_RE.match(line)
    if real is not None:
        return real.group("id"), real.group("value")

    return None


def parse_formal_vcd_trace(
    path: str | Path,
    *,
    property_name: str,
    property_kind: str,
    source: str = "formal-vcd",
    signals: Iterable[str] = (),
    max_steps: int = 100_000,
) -> dict[str, Any]:
    """Translate one VCD counterexample/witness into the normalized formal trace contract."""

    if max_steps < 1:
        raise ValueError("max_steps must be >= 1")

    waveform_path = Path(path).resolve()
    if not waveform_path.is_file():
        raise FileNotFoundError(waveform_path)
    if waveform_path.suffix.lower() != ".vcd":
        raise RuntimeError("Formal trace normalization currently requires a VCD file.")

    header = parse_vcd_header(waveform_path)
    declared = list(header["signals"])
    if not declared:
        raise RuntimeError("Formal VCD contains no declared signals.")

    selected = _resolve_requested_signals(signals, declared)
    if not selected:
        raise RuntimeError("Formal VCD signal selection is empty.")

    selected_ids = {str(signal["id_code"]) for signal in selected}
    current_by_id: dict[str, str] = {}
    steps: list[dict[str, Any]] = []
    current_time = 0
    changed_at_time = False

    def flush_step() -> None:
        nonlocal changed_at_time
        if not changed_at_time:
            return

        values: dict[str, str] = {}
        for signal in selected:
            id_code = str(signal["id_code"])
            if id_code in current_by_id:
                values[str(signal["path"])] = current_by_id[id_code]

        if not values:
            changed_at_time = False
            return

        if len(steps) >= max_steps:
            raise RuntimeError(
                f"Formal VCD exceeds max_steps={max_steps}; increase the explicit limit."
            )

        steps.append(
            {
                "step": len(steps),
                "time": current_time,
                "values": values,
                "metadata": {},
            }
        )
        changed_at_time = False

    for line in _iter_vcd_body(waveform_path):
        if line.startswith("#"):
            try:
                next_time = int(line[1:].strip())
            except ValueError as exc:
                raise RuntimeError(f"Invalid VCD timestamp line: {line}") from exc
            if next_time < current_time:
                raise RuntimeError("Formal VCD timestamps must be non-decreasing.")
            if next_time != current_time:
                flush_step()
                current_time = next_time
            continue

        parsed = _parse_value_change(line)
        if parsed is None:
            continue
        id_code, value = parsed
        if id_code not in selected_ids:
            continue
        current_by_id[id_code] = value
        changed_at_time = True

    flush_step()
    if not steps:
        raise RuntimeError("Formal VCD contains no value changes for the selected signals.")

    signal_records = [
        {
            "name": str(signal["path"]),
            "width": int(signal["width"]),
            "metadata": {
                "scope": signal["scope"],
                "reference": signal["reference"],
                "range": signal["range"],
                "var_type": signal["var_type"],
                "id_code": signal["id_code"],
            },
        }
        for signal in selected
    ]

    payload = {
        "property": property_name,
        "property_kind": property_kind,
        "source": source,
        "time_unit": header.get("timescale"),
        "signals": signal_records,
        "steps": steps,
        "metadata": {
            "format": "vcd",
            "waveform_path": str(waveform_path),
            "declared_signals": len(declared),
            "selected_signals": len(selected),
            "declared_scopes": len(header["scopes"]),
            "timescale": header.get("timescale"),
            "vcd_version": header.get("version"),
        },
    }
    return normalize_formal_counterexample(payload, source=source)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ingest_formal_vcd_trace(
    project: ProjectConfig,
    path: str | Path,
    *,
    property_name: str,
    property_kind: str,
    source: str = "formal-vcd",
    signals: Iterable[str] = (),
    max_steps: int = 100_000,
    output: str | Path = ".zddv/formal/counterexamples/latest.json",
) -> dict[str, Any]:
    input_path = Path(path)
    if not input_path.is_absolute():
        input_path = project.root / input_path
    input_path = input_path.resolve()

    normalized = parse_formal_vcd_trace(
        input_path,
        property_name=property_name,
        property_kind=property_kind,
        source=source,
        signals=signals,
        max_steps=max_steps,
    )

    return persist_normalized_formal_trace(
        project,
        normalized,
        input_path=input_path,
        input_sha256=_sha256_file(input_path),
        output=output,
    )
