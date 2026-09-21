from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, TextIO

from zddv.config import ProjectConfig
from zddv.storage import list_run_records


_SCOPE_RE = re.compile(r"^\$scope\s+(?P<type>\S+)\s+(?P<name>\S+)\s+\$end\s*$")
_VAR_RE = re.compile(
    r"^\$var\s+(?P<type>\S+)\s+(?P<width>\d+)\s+"
    r"(?P<identifier>\S+)\s+(?P<reference>.*?)\s+\$end\s*$"
)
_TIMESTAMP_RE = re.compile(r"^#(?P<time>\d+)\s*$")
_SCALAR_CHANGE_RE = re.compile(r"^[01xXzZ](?P<identifier>\S+)\s*$")
_VECTOR_CHANGE_RE = re.compile(r"^[bBrRsS]\S+\s+(?P<identifier>\S+)\s*$")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_directive(stream: TextIO, first_line: str) -> str:
    parts = [first_line.strip()]
    while "$end" not in parts[-1]:
        line = stream.readline()
        if not line:
            break
        parts.append(line.strip())
    return " ".join(part for part in parts if part)


def _directive_value(text: str, keyword: str) -> str | None:
    prefix = "$" + keyword
    if not text.startswith(prefix):
        return None
    value = text[len(prefix) :]
    if "$end" in value:
        value = value.split("$end", 1)[0]
    value = " ".join(value.split())
    return value or None


def _normalize_timescale(value: str | None) -> str | None:
    if value is None:
        return None
    compact = re.sub(r"\s+", "", value)
    return compact or None


def _signal_path(scope: list[str], reference: str) -> str:
    name = reference.split()[0]
    return ".".join([*scope, name]) if scope else name


def parse_vcd(path: str | Path) -> dict[str, Any]:
    waveform = Path(path).resolve()
    if not waveform.exists():
        raise FileNotFoundError(f"Waveform not found: {waveform}")
    if waveform.suffix.lower() != ".vcd":
        raise ValueError(
            f"Unsupported waveform format '{waveform.suffix or '(none)'}'. "
            "ZDDV waveform indexing currently supports VCD."
        )

    scopes: list[dict[str, Any]] = []
    scope_stack: list[str] = []
    signals: list[dict[str, Any]] = []
    signals_by_identifier: dict[str, list[int]] = {}

    date: str | None = None
    version: str | None = None
    timescale: str | None = None
    enddefinitions = False

    start_time: int | None = None
    end_time: int | None = None
    timestamp_count = 0
    value_change_count = 0

    with waveform.open("r", encoding="utf-8", errors="replace") as stream:
        for raw_line in stream:
            line = raw_line.strip()
            if not line:
                continue

            if not enddefinitions:
                if line.startswith("$date"):
                    directive = _read_directive(stream, line)
                    date = _directive_value(directive, "date")
                    continue
                if line.startswith("$version"):
                    directive = _read_directive(stream, line)
                    version = _directive_value(directive, "version")
                    continue
                if line.startswith("$timescale"):
                    directive = _read_directive(stream, line)
                    timescale = _normalize_timescale(
                        _directive_value(directive, "timescale")
                    )
                    continue

                scope_match = _SCOPE_RE.match(line)
                if scope_match:
                    scope_stack.append(scope_match.group("name"))
                    scopes.append(
                        {
                            "path": ".".join(scope_stack),
                            "name": scope_match.group("name"),
                            "type": scope_match.group("type"),
                            "depth": len(scope_stack) - 1,
                        }
                    )
                    continue

                if line.startswith("$upscope"):
                    if scope_stack:
                        scope_stack.pop()
                    continue

                var_match = _VAR_RE.match(line)
                if var_match:
                    reference = " ".join(var_match.group("reference").split())
                    index = len(signals)
                    signal = {
                        "path": _signal_path(scope_stack, reference),
                        "scope": ".".join(scope_stack),
                        "reference": reference,
                        "name": reference.split()[0],
                        "type": var_match.group("type"),
                        "width": int(var_match.group("width")),
                        "identifier": var_match.group("identifier"),
                        "activity_count": 0,
                    }
                    signals.append(signal)
                    signals_by_identifier.setdefault(
                        signal["identifier"], []
                    ).append(index)
                    continue

                if line.startswith("$enddefinitions"):
                    if "$end" not in line:
                        _read_directive(stream, line)
                    enddefinitions = True
                    continue

                continue

            timestamp_match = _TIMESTAMP_RE.match(line)
            if timestamp_match:
                timestamp = int(timestamp_match.group("time"))
                if start_time is None:
                    start_time = timestamp
                end_time = timestamp
                timestamp_count += 1
                continue

            identifier: str | None = None
            scalar_match = _SCALAR_CHANGE_RE.match(line)
            if scalar_match:
                identifier = scalar_match.group("identifier")
            else:
                vector_match = _VECTOR_CHANGE_RE.match(line)
                if vector_match:
                    identifier = vector_match.group("identifier")

            if identifier is None:
                continue

            value_change_count += 1
            for index in signals_by_identifier.get(identifier, []):
                signals[index]["activity_count"] += 1

    if not enddefinitions:
        raise ValueError(f"Invalid VCD: missing $enddefinitions in {waveform}")

    active_signals = sum(1 for signal in signals if signal["activity_count"] > 0)
    aliased_identifiers = sum(
        1 for indexes in signals_by_identifier.values() if len(indexes) > 1
    )

    return {
        "schema_version": 1,
        "format": "vcd",
        "path": str(waveform),
        "bytes": waveform.stat().st_size,
        "sha256": _sha256_file(waveform),
        "date": date,
        "version": version,
        "timescale": timescale,
        "start_time": start_time,
        "end_time": end_time,
        "timestamp_count": timestamp_count,
        "value_change_count": value_change_count,
        "scopes": scopes,
        "signals": signals,
        "summary": {
            "scopes": len(scopes),
            "signals": len(signals),
            "identifier_codes": len(signals_by_identifier),
            "aliased_identifiers": aliased_identifiers,
            "active_signals": active_signals,
            "inactive_signals": len(signals) - active_signals,
        },
    }


def _safe_label(value: str) -> str:
    label = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")
    return label[:96] or "waveform"


def _resolve_manual_path(project: ProjectConfig, value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = project.root / path
    return path.resolve()


def find_waveform_run(
    project: ProjectConfig,
    *,
    run_id: str | None = None,
    limit: int = 1000,
) -> dict[str, Any]:
    rows = list_run_records(project, limit=limit)
    if run_id is not None:
        for row in rows:
            if row["run_id"] == run_id:
                if not row.get("waveform_path"):
                    raise RuntimeError(f"Run {run_id} has no waveform artifact.")
                return row
        raise RuntimeError(f"Run not found in recent history: {run_id}")

    for row in rows:
        if row.get("waveform_path"):
            return row
    raise RuntimeError("No waveform artifact found in run history.")


def write_waveform_index(
    project: ProjectConfig,
    *,
    run_id: str | None = None,
    waveform_path: str | Path | None = None,
) -> dict[str, Any]:
    if run_id is not None and waveform_path is not None:
        raise ValueError("Use either run_id or waveform_path, not both.")

    if waveform_path is None:
        selected_run = find_waveform_run(project, run_id=run_id)
        waveform = Path(selected_run["waveform_path"]).resolve()
        effective_run_id = str(selected_run["run_id"])
    else:
        waveform = _resolve_manual_path(project, waveform_path)
        effective_run_id = None

    result = parse_vcd(waveform)
    result["project"] = project.name
    result["run_id"] = effective_run_id

    out_dir = (project.root / ".zddv" / "waveforms").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    if effective_run_id:
        filename = f"{_safe_label(effective_run_id)}.json"
    else:
        filename = (
            f"manual-{_safe_label(waveform.stem)}-"
            f"{result['sha256'][:8]}.json"
        )

    output = out_dir / filename
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return {**result, "index_path": str(output)}
