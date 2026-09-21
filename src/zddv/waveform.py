from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

from zddv.config import ProjectConfig
from zddv.storage import get_run_record, list_run_records


_DECLARATION_RE = re.compile(
    r"\$(?P<kind>[A-Za-z][A-Za-z0-9_]*)\b(?P<body>.*?)\$end",
    re.DOTALL,
)
_SUPPORTED_VCD_SUFFIXES = {".vcd"}
_FST_SUFFIXES = {".fst"}


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_path(project: ProjectConfig, path: Path) -> str:
    try:
        return path.resolve().relative_to(project.root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _read_vcd_header(path: Path) -> str:
    """Read only the VCD declaration section, never the value-change body."""
    parts: list[str] = []
    found_enddefinitions = False

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            parts.append(line)
            if "$enddefinitions" in line:
                found_enddefinitions = True
            if found_enddefinitions and "$end" in line:
                joined = "".join(parts)
                marker = joined.find("$enddefinitions")
                if marker >= 0:
                    terminator = joined.find("$end", marker + len("$enddefinitions"))
                    if terminator >= 0:
                        return joined[: terminator + len("$end")]

    raise RuntimeError(
        f"VCD header is incomplete: '$enddefinitions $end' was not found in {path}"
    )


def parse_vcd_header(path: str | Path) -> dict[str, Any]:
    """Parse normalized scopes and signals from the declaration section of a VCD."""
    source = Path(path).resolve()
    if not source.exists():
        raise FileNotFoundError(f"Waveform file not found: {source}")
    if not source.is_file():
        raise RuntimeError(f"Waveform path is not a file: {source}")

    header = _read_vcd_header(source)
    scope_stack: list[dict[str, str]] = []
    scopes: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    metadata: dict[str, str | None] = {
        "date": None,
        "version": None,
        "timescale": None,
    }

    for match in _DECLARATION_RE.finditer(header):
        kind = match.group("kind").lower()
        body = match.group("body").strip()

        if kind in metadata:
            metadata[kind] = " ".join(body.split()) or None
            continue

        if kind == "scope":
            tokens = body.split()
            if len(tokens) < 2:
                continue
            scope_type = tokens[0]
            scope_name = tokens[1]
            scope_stack.append({"type": scope_type, "name": scope_name})
            scope_path = ".".join(item["name"] for item in scope_stack)
            scopes.append(
                {
                    "type": scope_type,
                    "name": scope_name,
                    "path": scope_path,
                    "depth": len(scope_stack) - 1,
                }
            )
            continue

        if kind == "upscope":
            if scope_stack:
                scope_stack.pop()
            continue

        if kind == "var":
            tokens = body.split()
            if len(tokens) < 4:
                continue

            var_type = tokens[0]
            try:
                width = int(tokens[1])
            except ValueError:
                continue
            id_code = tokens[2]
            name = tokens[3]
            range_text = " ".join(tokens[4:]) or None
            scope_path = ".".join(item["name"] for item in scope_stack)
            signal_path = f"{scope_path}.{name}" if scope_path else name

            signals.append(
                {
                    "path": signal_path,
                    "scope": scope_path,
                    "name": name,
                    "reference": (
                        f"{name} {range_text}" if range_text is not None else name
                    ),
                    "range": range_text,
                    "var_type": var_type,
                    "width": width,
                    "id_code": id_code,
                }
            )

    unique_ids = {signal["id_code"] for signal in signals}
    return {
        "date": metadata["date"],
        "version": metadata["version"],
        "timescale": metadata["timescale"],
        "scopes": scopes,
        "signals": signals,
        "summary": {
            "scopes": len(scopes),
            "signals": len(signals),
            "unique_value_ids": len(unique_ids),
            "declared_bits": sum(int(signal["width"]) for signal in signals),
        },
    }


def build_waveform_index(
    path: str | Path,
    *,
    run_id: str | None = None,
    project_name: str | None = None,
) -> dict[str, Any]:
    source = Path(path).resolve()
    if not source.exists():
        raise FileNotFoundError(f"Waveform file not found: {source}")
    if not source.is_file():
        raise RuntimeError(f"Waveform path is not a file: {source}")

    suffix = source.suffix.lower()
    artifact = {
        "path": str(source),
        "bytes": source.stat().st_size,
        "sha256": _sha256_file(source),
    }

    if suffix in _SUPPORTED_VCD_SUFFIXES:
        parsed = parse_vcd_header(source)
        return {
            "schema_version": 1,
            "project": project_name,
            "run_id": run_id,
            "format": "vcd",
            "parse_status": "indexed",
            "artifact": artifact,
            **parsed,
        }

    if suffix in _FST_SUFFIXES:
        return {
            "schema_version": 1,
            "project": project_name,
            "run_id": run_id,
            "format": "fst",
            "parse_status": "metadata-only",
            "artifact": artifact,
            "date": None,
            "version": None,
            "timescale": None,
            "scopes": [],
            "signals": [],
            "summary": {
                "scopes": 0,
                "signals": 0,
                "unique_value_ids": 0,
                "declared_bits": 0,
            },
            "note": (
                "FST artifact metadata is indexed, but signal/scope extraction "
                "requires a simulator or FST converter adapter."
            ),
        }

    raise RuntimeError(
        f"Unsupported waveform format '{suffix or '(no extension)'}'. "
        "ZDDV waveform indexing currently accepts VCD and FST artifacts."
    )


def select_waveform_run(
    project: ProjectConfig,
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    if run_id is not None:
        row = get_run_record(project, run_id)
        if row is None:
            raise RuntimeError(f"Run '{run_id}' was not found in the verification database.")
        if not row.get("waveform_path"):
            raise RuntimeError(f"Run '{run_id}' has no recorded waveform artifact.")
        path = Path(str(row["waveform_path"]))
        if not path.exists():
            raise RuntimeError(
                f"Recorded waveform for run '{run_id}' does not exist: {path}"
            )
        return row

    rows = list_run_records(project, limit=1_000_000)
    for row in rows:
        value = row.get("waveform_path")
        if value and Path(str(value)).exists():
            return row

    raise RuntimeError(
        "No run with an existing waveform artifact was found. "
        "Run a waveform-enabled simulation first."
    )


def write_waveform_index(
    project: ProjectConfig,
    *,
    run_id: str | None = None,
    input_path: str | Path | None = None,
    output: str | Path | None = None,
) -> dict[str, Any]:
    if run_id is not None and input_path is not None:
        raise ValueError("run_id and input_path are mutually exclusive")

    selected_run: dict[str, Any] | None = None
    if input_path is None:
        selected_run = select_waveform_run(project, run_id=run_id)
        waveform_path = Path(str(selected_run["waveform_path"])).resolve()
        effective_run_id = str(selected_run["run_id"])
    else:
        waveform_path = Path(input_path)
        if not waveform_path.is_absolute():
            waveform_path = project.root / waveform_path
        waveform_path = waveform_path.resolve()
        effective_run_id = None

    index = build_waveform_index(
        waveform_path,
        run_id=effective_run_id,
        project_name=project.name,
    )
    index["artifact"]["project_path"] = _relative_path(project, waveform_path)

    out_dir = (project.root / ".zddv" / "waveforms").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if output is None:
        label = effective_run_id or waveform_path.stem
        destination = out_dir / f"{label}.json"
    else:
        destination = Path(output)
        if not destination.is_absolute():
            destination = project.root / destination
        destination = destination.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)

    destination.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")

    latest_path = out_dir / "latest.json"
    if output is None:
        latest_path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")

    return {
        **index,
        "path": str(destination),
        "latest_path": str(latest_path) if output is None else None,
        "selected_run": selected_run,
    }



def _resolve_vcd_probe_signals(
    waveform_index: dict[str, Any],
    requested: list[str],
) -> list[dict[str, Any]]:
    if not requested:
        raise ValueError("At least one waveform signal must be requested.")

    by_path = {
        str(signal["path"]): signal
        for signal in waveform_index.get("signals", [])
    }
    by_name: dict[str, list[dict[str, Any]]] = {}
    for signal in waveform_index.get("signals", []):
        by_name.setdefault(str(signal["name"]), []).append(signal)

    resolved: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_query in requested:
        query = raw_query.strip()
        if not query:
            raise ValueError("Waveform signal queries must not be empty.")

        signal = by_path.get(query)
        if signal is None:
            matches = by_name.get(query, [])
            if len(matches) > 1:
                choices = ", ".join(sorted(str(item["path"]) for item in matches))
                raise RuntimeError(
                    f"Signal name '{query}' is ambiguous. Use a full path: {choices}"
                )
            if len(matches) == 1:
                signal = matches[0]

        if signal is None:
            raise RuntimeError(f"Signal '{query}' was not found in the VCD waveform.")

        path = str(signal["path"])
        if path in seen:
            continue
        seen.add(path)
        resolved.append({**signal, "query": query})

    return resolved


def _parse_vcd_value_change(line: str) -> tuple[str, str] | None:
    text = line.strip()
    if not text or text.startswith("$"):
        return None

    lead = text[0]
    if lead in "01xXzZuUwWlLhH-":
        identifier = text[1:].strip()
        if not identifier:
            return None
        return identifier, lead.lower()

    if lead in "bBrRsS":
        parts = text.split(maxsplit=1)
        if len(parts) != 2:
            return None
        value_token, identifier = parts
        identifier = identifier.strip()
        if not identifier:
            return None
        return identifier, value_token[1:].lower()

    return None


def probe_vcd(
    path: str | Path,
    signals: list[str],
    *,
    at_time: int,
    before: int = 0,
    after: int = 0,
    max_transitions: int = 200,
    run_id: str | None = None,
    project_name: str | None = None,
) -> dict[str, Any]:
    """Cross-probe selected VCD signals around one simulation timestamp.

    Time arguments are integer VCD ticks. The returned report includes the VCD
    timescale so callers can interpret the tick values without guessing units.
    """
    if at_time < 0:
        raise ValueError("at_time must be non-negative.")
    if before < 0 or after < 0:
        raise ValueError("before/after windows must be non-negative.")
    if max_transitions <= 0:
        raise ValueError("max_transitions must be greater than zero.")

    source = Path(path).resolve()
    waveform_index = build_waveform_index(
        source,
        run_id=run_id,
        project_name=project_name,
    )
    if waveform_index["format"] != "vcd":
        raise RuntimeError(
            "Waveform cross-probing currently requires a VCD artifact. "
            "FST remains metadata-only until an adapter/converter is available."
        )

    selected = _resolve_vcd_probe_signals(waveform_index, signals)
    window_start = max(0, at_time - before)
    window_end = at_time + after

    states: dict[str, dict[str, Any]] = {}
    signals_by_id: dict[str, list[dict[str, Any]]] = {}
    for signal in selected:
        path_name = str(signal["path"])
        states[path_name] = {
            "query": signal["query"],
            "path": path_name,
            "scope": signal.get("scope"),
            "name": signal.get("name"),
            "width": signal.get("width"),
            "range": signal.get("range"),
            "var_type": signal.get("var_type"),
            "id_code": signal.get("id_code"),
            "value_at": None,
            "last_transition": None,
            "next_transition": None,
            "transitions": [],
            "transitions_in_window": 0,
            "truncated": False,
        }
        signals_by_id.setdefault(str(signal["id_code"]), []).append(signal)

    current_time = 0
    in_body = False
    waiting_enddefinitions_end = False

    with source.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.strip()

            if not in_body:
                if waiting_enddefinitions_end:
                    if "$end" in line:
                        in_body = True
                    continue

                if "$enddefinitions" in line:
                    tail = line.split("$enddefinitions", 1)[1]
                    if "$end" in tail:
                        in_body = True
                    else:
                        waiting_enddefinitions_end = True
                    continue

                continue

            if not line:
                continue

            if line.startswith("#"):
                try:
                    current_time = int(line[1:].strip())
                except ValueError as exc:
                    raise RuntimeError(
                        f"Invalid VCD timestamp '{line}' in {source}"
                    ) from exc
                continue

            change = _parse_vcd_value_change(line)
            if change is None:
                continue
            identifier, value = change
            matched_signals = signals_by_id.get(identifier)
            if not matched_signals:
                continue

            transition = {"time": current_time, "value": value}
            for signal in matched_signals:
                state = states[str(signal["path"])]

                if current_time <= at_time:
                    state["value_at"] = value
                    state["last_transition"] = transition.copy()
                elif state["next_transition"] is None:
                    state["next_transition"] = transition.copy()

                if window_start <= current_time <= window_end:
                    state["transitions_in_window"] += 1
                    if len(state["transitions"]) < max_transitions:
                        state["transitions"].append(transition.copy())
                    else:
                        state["truncated"] = True

    probe_signals = [states[str(signal["path"])] for signal in selected]
    return {
        "schema_version": 1,
        "project": project_name,
        "run_id": run_id,
        "format": "vcd",
        "artifact": waveform_index["artifact"],
        "timescale": waveform_index.get("timescale"),
        "time": {
            "tick": at_time,
            "before": before,
            "after": after,
            "window_start": window_start,
            "window_end": window_end,
        },
        "signals": probe_signals,
        "summary": {
            "signals": len(probe_signals),
            "transitions_in_window": sum(
                int(item["transitions_in_window"]) for item in probe_signals
            ),
            "truncated_signals": sum(
                1 for item in probe_signals if item["truncated"]
            ),
        },
    }


def write_waveform_probe(
    project: ProjectConfig,
    *,
    signals: list[str],
    at_time: int,
    before: int = 0,
    after: int = 0,
    max_transitions: int = 200,
    run_id: str | None = None,
    input_path: str | Path | None = None,
    output: str | Path = ".zddv/debug/waveform-probe.json",
) -> dict[str, Any]:
    if run_id is not None and input_path is not None:
        raise ValueError("run_id and input_path are mutually exclusive")

    selected_run: dict[str, Any] | None = None
    if input_path is None:
        selected_run = select_waveform_run(project, run_id=run_id)
        waveform_path = Path(str(selected_run["waveform_path"])).resolve()
        effective_run_id = str(selected_run["run_id"])
    else:
        waveform_path = Path(input_path)
        if not waveform_path.is_absolute():
            waveform_path = project.root / waveform_path
        waveform_path = waveform_path.resolve()
        effective_run_id = None

    report = probe_vcd(
        waveform_path,
        signals,
        at_time=at_time,
        before=before,
        after=after,
        max_transitions=max_transitions,
        run_id=effective_run_id,
        project_name=project.name,
    )
    report["artifact"]["project_path"] = _relative_path(project, waveform_path)

    destination = Path(output)
    if not destination.is_absolute():
        destination = project.root / destination
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    return {
        **report,
        "path": str(destination),
        "selected_run": selected_run,
    }
