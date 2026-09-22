from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import math
import re
import shutil
import subprocess
import time

from zddv.config import ProjectConfig

from .base import FormalBackend, FormalCheckRequest, FormalCheckResult, FormalPropertyResult


_SBY_DONE = re.compile(
    r"\bDONE\s*\(\s*(?P<status>[A-Z_]+)\s*,\s*rc=(?P<rc>-?\d+)\s*\)",
    re.IGNORECASE,
)
_HDL_SUFFIXES = {".v", ".sv"}
_AUX_SUFFIXES = {".vh", ".svh"}


@dataclass(frozen=True)
class _ProcessOutcome:
    returncode: int
    output: str
    timed_out: bool = False


def _run_process(
    command: list[str],
    *,
    cwd: Path,
    timeout_s: float | None,
) -> _ProcessOutcome:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        return _ProcessOutcome(
            returncode=int(completed.returncode),
            output=completed.stdout or "",
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        return _ProcessOutcome(returncode=-1, output=str(output), timed_out=True)


def _staged_source_name(project: ProjectConfig, source: Path, index: int) -> str:
    root = project.root.resolve()
    resolved = source.resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError:
        relative = Path("external") / f"{index:04d}_{resolved.name}"

    parts = []
    for part in relative.parts:
        clean = re.sub(r"[^A-Za-z0-9_.-]", "_", part)
        parts.append(clean or "_")
    return Path(*parts).as_posix()


def _render_sby_bounded_config(
    project: ProjectConfig,
    request: FormalCheckRequest,
) -> str:
    """Render one finite-depth SymbiYosys bmc/cover job."""
    if request.mode not in {"bmc", "cover"}:
        raise ValueError("SymbiYosys bounded execution supports bmc and cover modes only")
    if request.depth is None:
        raise ValueError("SymbiYosys bounded checks require an explicit depth")
    if request.properties:
        raise ValueError(
            "SymbiYosys property filters are not supported by the bounded backend yet"
        )

    sources = project.source_files()
    if not sources:
        raise RuntimeError("No project sources were found for formal checking")

    staged: list[tuple[Path, str]] = []
    seen_destinations: set[str] = set()
    for index, source in enumerate(sources):
        destination = _staged_source_name(project, source, index)
        if destination in seen_destinations:
            destination = f"source_{index:04d}_{Path(destination).name}"
        seen_destinations.add(destination)
        staged.append((source.resolve(), destination))

    hdl = [
        (source, destination)
        for source, destination in staged
        if source.suffix.lower() in _HDL_SUFFIXES
    ]
    if not hdl:
        raise RuntimeError("No .v or .sv sources were found for formal checking")

    script_lines: list[str] = []
    for source, destination in hdl:
        if source.suffix.lower() == ".sv":
            script_lines.append(f"read_verilog -formal -sv {destination}")
        else:
            script_lines.append(f"read_verilog -formal {destination}")
    script_lines.append(f"prep -top {project.top}")

    file_lines = [
        f"{destination} {source.as_posix()}"
        for source, destination in staged
        if source.suffix.lower() in (_HDL_SUFFIXES | _AUX_SUFFIXES)
    ]

    return "\n".join(
        [
            "[options]",
            f"mode {request.mode}",
            f"depth {request.depth}",
            *(
                [f"timeout {max(1, math.ceil(request.timeout_s))}"]
                if request.timeout_s is not None
                else []
            ),
            "",
            "[engines]",
            "smtbmc",
            "",
            "[script]",
            *script_lines,
            "",
            "[files]",
            *file_lines,
            "",
        ]
    )


def render_sby_bmc_config(
    project: ProjectConfig,
    request: FormalCheckRequest,
) -> str:
    """Render one finite-depth SymbiYosys BMC job."""
    if request.mode != "bmc":
        raise ValueError("SymbiYosys BMC config requires bmc mode only")
    return _render_sby_bounded_config(project, request)


def render_sby_cover_config(
    project: ProjectConfig,
    request: FormalCheckRequest,
) -> str:
    """Render one finite-depth SymbiYosys cover-reachability job."""
    if request.mode != "cover":
        raise ValueError("SymbiYosys cover config requires cover mode only")
    return _render_sby_bounded_config(project, request)


def _normalized_sby_status(outcome: _ProcessOutcome) -> str:
    if outcome.timed_out:
        return "UNKNOWN"

    matches = list(_SBY_DONE.finditer(outcome.output))
    if not matches:
        return "ERROR"

    status = matches[-1].group("status").upper()
    if status in {"PASS", "FAIL", "UNKNOWN", "ERROR"}:
        return status
    return "UNKNOWN"


def parse_sby_status_jsonl(
    text: str,
    *,
    mode: str = "bmc",
) -> tuple[FormalPropertyResult, ...]:
    """Normalize documented SBY JSONL rows for bmc assertions or cover goals."""

    normalized_mode = str(mode).strip().lower()
    if normalized_mode not in {"bmc", "cover"}:
        raise ValueError("SBY property-status JSONL supports bmc or cover mode")

    expected_kind = "ASSERT" if normalized_mode == "bmc" else "COVER"
    property_kind = "assert" if normalized_mode == "bmc" else "cover"
    cover_status = {
        "PASS": "COVERED",
        "FAIL": "UNCOVERED",
        "UNKNOWN": "UNKNOWN",
        "ERROR": "ERROR",
    }

    properties: dict[str, FormalPropertyResult] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue

        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"invalid SBY property-status JSONL at line {line_number}"
            ) from exc
        if not isinstance(row, dict):
            raise ValueError(
                f"SBY property-status JSONL line {line_number} must be an object"
            )

        kind = str(row.get("kind", "")).strip().upper()
        if kind != expected_kind:
            continue

        name = row.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(
                f"SBY {property_kind} status line {line_number} is missing a property name"
            )

        raw_status = str(row.get("status", "")).strip().upper()
        if raw_status not in {"PASS", "FAIL", "UNKNOWN", "ERROR"}:
            continue
        status = (
            raw_status
            if property_kind == "assert"
            else cover_status[raw_status]
        )

        depth = row.get("depth")
        if depth is not None:
            if isinstance(depth, bool) or not isinstance(depth, int) or depth < 0:
                raise ValueError(
                    f"SBY {property_kind} status line {line_number} has invalid depth"
                )

        trace_path = row.get("trace")
        if trace_path is not None:
            if not isinstance(trace_path, str) or not trace_path.strip():
                raise ValueError(
                    f"SBY {property_kind} status line {line_number} has invalid trace path"
                )
            trace_path = Path(trace_path)

        item = FormalPropertyResult(
            name=name.strip(),
            kind=property_kind,
            status=status,
            depth=depth,
            trace_path=trace_path,
        )
        previous = properties.get(item.name)
        if previous is not None and previous != item:
            raise ValueError(
                f"conflicting SBY property-status rows for {item.name!r}"
            )
        properties[item.name] = item

    return tuple(properties.values())


def _query_sby_property_statuses(
    executable: str,
    *,
    project: ProjectConfig,
    run_dir: Path,
    timeout_s: float | None,
    mode: str,
) -> tuple[tuple[FormalPropertyResult, ...], Path]:
    command = [
        executable,
        "--statusfmt",
        "jsonl",
        "--latest",
        str(run_dir),
    ]
    outcome = _run_process(
        command,
        cwd=project.root,
        timeout_s=timeout_s,
    )

    if outcome.returncode == 0 and not outcome.timed_out:
        artifact = run_dir / "property-status.jsonl"
        artifact.write_text(outcome.output, encoding="utf-8")
        try:
            return parse_sby_status_jsonl(outcome.output, mode=mode), artifact
        except ValueError:
            # Keep malformed/unsupported native evidence, but do not promote it.
            return (), artifact

    artifact = run_dir / "property-status-query.log"
    output = outcome.output
    if outcome.timed_out:
        if output and not output.endswith("\n"):
            output += "\n"
        output += "ZDDV: SBY property-status query timed out\n"
    artifact.write_text(output, encoding="utf-8")
    return (), artifact


class SymbiYosysBackend(FormalBackend):
    """Finite-depth SymbiYosys backend for BMC and cover reachability."""

    name = "sby"

    def _executable(self) -> str:
        executable = shutil.which("sby")
        if executable is None:
            raise RuntimeError("SymbiYosys 'sby' was not found in PATH")
        return executable

    def version(self) -> str:
        executable = self._executable()
        completed = subprocess.run(
            [executable, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        output = (completed.stdout or "").strip()
        if completed.returncode != 0:
            raise RuntimeError(
                f"Unable to query SymbiYosys version (rc={completed.returncode})"
            )
        return output.splitlines()[0] if output else "sby"

    def check(
        self,
        project: ProjectConfig,
        request: FormalCheckRequest,
    ) -> FormalCheckResult:
        executable = self._executable()
        if request.mode == "bmc":
            config_text = render_sby_bmc_config(project, request)
        elif request.mode == "cover":
            config_text = render_sby_cover_config(project, request)
        else:
            raise ValueError(
                "SymbiYosys bounded execution supports bmc and cover modes only"
            )

        root = (project.root / ".zddv" / "formal" / "sby").resolve()
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        run_id = f"{request.mode}-{stamp}"
        config_path = root / f"{run_id}.sby"
        run_dir = root / run_id
        config_path.write_text(config_text, encoding="utf-8")

        command = [
            executable,
            "-f",
            "-d",
            str(run_dir),
            str(config_path),
        ]
        started = time.perf_counter()
        outcome = _run_process(
            command,
            cwd=project.root,
            timeout_s=request.timeout_s,
        )
        runtime_ms = (time.perf_counter() - started) * 1000.0

        run_dir.mkdir(parents=True, exist_ok=True)
        log_path = run_dir / "formal.log"
        output = outcome.output
        if outcome.timed_out:
            if output and not output.endswith("\n"):
                output += "\n"
            output += "ZDDV: formal check timed out before SymbiYosys completed\n"
        log_path.write_text(output, encoding="utf-8")

        status = _normalized_sby_status(outcome)
        properties: tuple[FormalPropertyResult, ...] = ()
        artifacts: list[Path] = [config_path]
        if status in {"PASS", "FAIL"}:
            properties, status_artifact = _query_sby_property_statuses(
                executable,
                project=project,
                run_dir=run_dir,
                timeout_s=request.timeout_s,
                mode=request.mode,
            )
            artifacts.append(status_artifact)

        return FormalCheckResult(
            backend=self.name,
            engine="smtbmc",
            request=request,
            command=tuple(command),
            returncode=outcome.returncode,
            status=status,
            run_dir=run_dir,
            log_path=log_path,
            properties=properties,
            artifacts=tuple(artifacts),
            runtime_ms=runtime_ms,
        )
