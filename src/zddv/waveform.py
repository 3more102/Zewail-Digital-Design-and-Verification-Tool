from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

from zddv.config import ProjectConfig
from zddv.storage import list_run_records


_SCALAR_CHANGE = re.compile(r"^(?P<value>[01xXzZ])(?P<id>\S+)$")
_VECTOR_CHANGE = re.compile(r"^(?P<kind>[bBrRsS])(?P<value>\S+)\s+(?P<id>\S+)$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_statement(statement: str) -> list[str]:
    tokens = statement.split()
    if tokens and tokens[-1] == "$end":
        tokens = tokens[:-1]
    return tokens


def _record_change(
    activity: dict[str, dict[str, Any]],
    identifier: str,
    *,
    value: str,
    time_value: int,
    line: int,
) -> None:
    item = activity.setdefault(
        identifier,
        {
            "changes": 0,
            "first_time": time_value,
            "last_time": time_value,
            "first_line": line,
            "last_line": line,
            "first_value": value,
            "last_value": value,
        },
    )
    if item["changes"] == 0:
        item["first_time"] = time_value
        item["first_line"] = line
        item["first_value"] = value
    item["changes"] += 1
    item["last_time"] = time_value
    item["last_line"] = line
    item["last_value"] = value


def index_vcd(
    path: str | Path,
    *,
    checkpoint_interval: int = 128,
) -> dict[str, Any]:
    """Build a compact signal/activity/time index for an ASCII VCD waveform."""
    if checkpoint_interval < 1:
        raise ValueError("checkpoint_interval must be >= 1")

    waveform = Path(path).resolve()
    if not waveform.exists():
        raise FileNotFoundError(f"Waveform not found: {waveform}")
    if waveform.suffix.lower() != ".vcd":
        raise RuntimeError(
            f"Unsupported waveform format '{waveform.suffix or '(none)'}'. "
            "ZDDV waveform indexing currently supports VCD."
        )

    metadata: dict[str, str | None] = {
        "date": None,
        "version": None,
        "timescale": None,
    }
    scopes: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    scope_stack: list[str] = []
    activity: dict[str, dict[str, Any]] = {}
    pending = ""
    header = True
    current_time = 0
    first_timestamp: int | None = None
    last_timestamp: int | None = None
    timestamp_count = 0
    value_changes = 0
    checkpoints: list[dict[str, int]] = []

    with waveform.open("r", encoding="utf-8", errors="replace") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            stripped = raw_line.strip()
            if not stripped:
                continue

            if header:
                if pending:
                    pending += " " + stripped
                elif stripped.startswith("$"):
                    pending = stripped
                else:
                    continue

                if "$end" not in pending:
                    continue

                tokens = _normalize_statement(pending)
                pending = ""
                if not tokens:
                    continue

                directive = tokens[0]
                if directive == "$scope" and len(tokens) >= 3:
                    scope_type = tokens[1]
                    scope_name = tokens[2]
                    scope_stack.append(scope_name)
                    scopes.append(
                        {
                            "path": ".".join(scope_stack),
                            "name": scope_name,
                            "type": scope_type,
                            "depth": len(scope_stack) - 1,
                            "line": line_number,
                        }
                    )
                elif directive == "$upscope":
                    if scope_stack:
                        scope_stack.pop()
                elif directive == "$var" and len(tokens) >= 5:
                    try:
                        width = int(tokens[2])
                    except ValueError:
                        width = 0
                    identifier = tokens[3]
                    reference = tokens[4]
                    range_text = " ".join(tokens[5:]) or None
                    scope = ".".join(scope_stack)
                    signals.append(
                        {
                            "id": identifier,
                            "type": tokens[1],
                            "width": width,
                            "name": reference,
                            "range": range_text,
                            "scope": scope,
                            "full_name": f"{scope}.{reference}" if scope else reference,
                            "declaration_line": line_number,
                        }
                    )
                elif directive in {"$date", "$version", "$timescale"}:
                    key = directive[1:]
                    metadata[key] = " ".join(tokens[1:]) or None
                elif directive == "$enddefinitions":
                    header = False
                continue

            if stripped.startswith("#"):
                try:
                    current_time = int(stripped[1:].strip())
                except ValueError:
                    continue
                timestamp_count += 1
                if first_timestamp is None:
                    first_timestamp = current_time
                last_timestamp = current_time
                if (
                    timestamp_count == 1
                    or (timestamp_count - 1) % checkpoint_interval == 0
                ):
                    checkpoints.append({"time": current_time, "line": line_number})
                continue

            if stripped.startswith("$"):
                continue

            scalar = _SCALAR_CHANGE.match(stripped)
            if scalar:
                _record_change(
                    activity,
                    scalar.group("id"),
                    value=scalar.group("value").lower(),
                    time_value=current_time,
                    line=line_number,
                )
                value_changes += 1
                continue

            vector = _VECTOR_CHANGE.match(stripped)
            if vector:
                value = vector.group("kind").lower() + vector.group("value")
                _record_change(
                    activity,
                    vector.group("id"),
                    value=value,
                    time_value=current_time,
                    line=line_number,
                )
                value_changes += 1

    declared_ids = {signal["id"] for signal in signals}
    for signal in signals:
        item = activity.get(signal["id"])
        if item is None:
            signal["activity"] = {
                "changes": 0,
                "first_time": None,
                "last_time": None,
                "first_line": None,
                "last_line": None,
                "first_value": None,
                "last_value": None,
            }
        else:
            signal["activity"] = dict(item)

    active_signals = sum(
        1 for signal in signals if signal["activity"]["changes"] > 0
    )
    unique_ids = len(declared_ids)
    unknown_ids = sorted(set(activity) - declared_ids)
    if first_timestamp is None and value_changes:
        first_timestamp = 0
        last_timestamp = current_time

    return {
        "schema_version": 1,
        "format": "vcd",
        "file": {
            "path": str(waveform),
            "bytes": waveform.stat().st_size,
            "sha256": _sha256(waveform),
        },
        "metadata": metadata,
        "scopes": scopes,
        "signals": signals,
        "time_index": {
            "first_time": first_timestamp,
            "last_time": last_timestamp,
            "duration": (
                None
                if first_timestamp is None or last_timestamp is None
                else last_timestamp - first_timestamp
            ),
            "timestamp_count": timestamp_count,
            "checkpoint_interval": checkpoint_interval,
            "checkpoints": checkpoints,
        },
        "summary": {
            "scopes": len(scopes),
            "signals": len(signals),
            "identifier_codes": unique_ids,
            "active_signals": active_signals,
            "inactive_signals": len(signals) - active_signals,
            "value_changes": value_changes,
            "unknown_identifier_codes": len(unknown_ids),
        },
        "unknown_identifiers": unknown_ids,
    }


def resolve_waveform(
    project: ProjectConfig,
    waveform_path: str | Path | None = None,
) -> tuple[Path, str | None]:
    """Resolve an explicit waveform or the newest recorded run waveform."""
    if waveform_path is not None:
        candidate = Path(waveform_path)
        if not candidate.is_absolute():
            candidate = project.root / candidate
        candidate = candidate.resolve()
        if not candidate.exists():
            raise FileNotFoundError(f"Waveform not found: {candidate}")
        return candidate, None

    for record in list_run_records(project, limit=500):
        value = record.get("waveform_path")
        if not value:
            continue
        candidate = Path(value)
        if candidate.exists():
            return candidate.resolve(), record["run_id"]

    runs_dir = (project.root / project.run_dir).resolve()
    candidates: list[Path] = []
    if runs_dir.exists():
        for pattern in ("*/waveform.vcd", "*/dump.vcd"):
            candidates.extend(runs_dir.glob(pattern))
    if candidates:
        newest = max(candidates, key=lambda item: item.stat().st_mtime_ns)
        return newest.resolve(), newest.parent.name

    raise RuntimeError(
        "No waveform artifact was found. Run a waveform-enabled simulation first "
        "or pass an explicit VCD path."
    )


def write_waveform_index(
    project: ProjectConfig,
    waveform_path: str | Path | None = None,
    *,
    output: str | Path | None = None,
    checkpoint_interval: int = 128,
) -> dict[str, Any]:
    waveform, run_id = resolve_waveform(project, waveform_path)
    index = index_vcd(waveform, checkpoint_interval=checkpoint_interval)

    if output is None:
        if run_id is not None:
            output_path = waveform.parent / "waveform.index.json"
        else:
            output_path = waveform.with_name(waveform.name + ".index.json")
    else:
        output_path = Path(output)
        if not output_path.is_absolute():
            output_path = project.root / output_path
        output_path = output_path.resolve()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **index,
        "project": project.name,
        "run_id": run_id,
    }
    output_path.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        **payload,
        "path": str(output_path),
    }
