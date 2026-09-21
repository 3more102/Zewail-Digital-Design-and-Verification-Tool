from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from zddv.config import ProjectConfig
from zddv.storage import list_run_records


_DIRECTIVE_RE = re.compile(
    r"\$(scope|upscope|var|timescale|enddefinitions)\b(.*?)\$end",
    re.IGNORECASE | re.DOTALL,
)


def parse_vcd_header(path: str | Path) -> dict:
    vcd_path = Path(path).resolve()
    if not vcd_path.exists():
        raise FileNotFoundError(vcd_path)

    text = vcd_path.read_text(encoding="utf-8", errors="replace")
    scopes: list[str] = []
    scope_paths: set[str] = set()
    signals: list[dict] = []
    timescale: str | None = None

    for match in _DIRECTIVE_RE.finditer(text):
        kind = match.group(1).lower()
        body = " ".join(match.group(2).split())

        if kind == "enddefinitions":
            break

        if kind == "timescale":
            timescale = body or None
            continue

        if kind == "scope":
            parts = body.split()
            if len(parts) < 2:
                continue
            scope_type, scope_name = parts[0], parts[1]
            scopes.append(scope_name)
            scope_paths.add(".".join(scopes))
            continue

        if kind == "upscope":
            if scopes:
                scopes.pop()
            continue

        if kind != "var":
            continue

        parts = body.split(maxsplit=3)
        if len(parts) < 4:
            continue
        var_type, width_raw, identifier_code, reference = parts
        try:
            width = int(width_raw)
        except ValueError:
            continue

        signal_name = reference.split()[0]
        scope = ".".join(scopes)
        signal_path = ".".join([*scopes, signal_name]) if scopes else signal_name
        signals.append(
            {
                "path": signal_path,
                "scope": scope,
                "name": signal_name,
                "reference": reference,
                "type": var_type,
                "width": width,
                "id_code": identifier_code,
            }
        )

    by_type = dict(sorted(Counter(item["type"] for item in signals).items()))
    return {
        "schema_version": 1,
        "waveform": str(vcd_path),
        "format": "vcd",
        "timescale": timescale,
        "scopes": sorted(scope_paths),
        "signals": signals,
        "stats": {
            "scopes": len(scope_paths),
            "signals": len(signals),
            "scalar_signals": sum(item["width"] == 1 for item in signals),
            "vector_signals": sum(item["width"] > 1 for item in signals),
            "by_type": by_type,
        },
    }


def resolve_waveform(
    project: ProjectConfig,
    *,
    run_id: str | None = None,
    waveform: str | Path | None = None,
) -> tuple[Path, str | None]:
    if waveform is not None:
        path = Path(waveform)
        if not path.is_absolute():
            path = project.root / path
        path = path.resolve()
        if not path.exists():
            raise FileNotFoundError(path)
        return path, run_id

    rows = list_run_records(project, limit=500)
    if run_id is not None:
        rows = [row for row in rows if row["run_id"] == run_id]
        if not rows:
            raise ValueError(f"Run '{run_id}' was not found in the ZDDV results database.")

    for row in rows:
        raw_path = row.get("waveform_path")
        if not raw_path:
            continue
        path = Path(raw_path)
        if path.exists():
            return path.resolve(), row["run_id"]

    if run_id is not None:
        raise ValueError(f"Run '{run_id}' has no available waveform artifact.")
    raise ValueError("No run with an available waveform artifact was found.")


def build_waveform_index(
    project: ProjectConfig,
    *,
    run_id: str | None = None,
    waveform: str | Path | None = None,
) -> dict:
    path, resolved_run_id = resolve_waveform(
        project,
        run_id=run_id,
        waveform=waveform,
    )
    if path.suffix.lower() != ".vcd":
        raise ValueError(
            f"Waveform indexing currently supports VCD files only: {path}"
        )

    index = parse_vcd_header(path)
    index.update(
        {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "project": project.name,
            "run_id": resolved_run_id,
        }
    )
    return index


def write_waveform_index(project: ProjectConfig, index: dict) -> Path:
    run_id = index.get("run_id")
    waveform = Path(index["waveform"])
    label = str(run_id or waveform.stem)
    label = re.sub(r"[^A-Za-z0-9_.-]+", "-", label).strip("-") or "waveform"

    output = (
        project.root / ".zddv" / "index" / "waveforms" / f"{label}.json"
    ).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(index, indent=2), encoding="utf-8")
    return output
