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


def render_sby_bmc_config(
    project: ProjectConfig,
    request: FormalCheckRequest,
) -> str:
    """Render one bounded SymbiYosys job without inferring proof semantics."""
    if request.mode != "bmc":
        raise ValueError("SymbiYosys bounded execution currently supports bmc mode only")
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
            "mode bmc",
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
    expected_mode: str | None = None,
) -> tuple[FormalPropertyResult, ...]:
    """Normalize machine-readable SBY property status rows conservatively."""

    properties: list[FormalPropertyResult] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"invalid SymbiYosys status JSONL at line {line_number}"
            ) from exc
        if not isinstance(row, dict):
            raise ValueError(
                f"SymbiYosys status JSONL line {line_number} must be an object"
            )

        row_mode = str(row.get("mode", "")).strip().lower()
        if expected_mode is not None and row_mode and row_mode != expected_mode:
            raise ValueError(
                f"SymbiYosys status mode {row_mode!r} does not match "
                f"expected mode {expected_mode!r}"
            )

        kind = str(row.get("kind", "")).strip().lower()
        # The current backend executes BMC safety checks only. Do not invent
        # cover/liveness semantics from status rows until those modes are
        # implemented by this backend.
        if kind != "assert":
            continue

        name = str(row.get("name", "")).strip()
        if not name:
            raise ValueError(
                f"SymbiYosys status JSONL line {line_number} has no property name"
            )

        status = str(row.get("status", "UNKNOWN")).strip().upper() or "UNKNOWN"
        if status not in {"PASS", "FAIL", "UNKNOWN", "ERROR"}:
            raise ValueError(
                f"unsupported SymbiYosys assertion status {status!r} "
                f"at line {line_number}"
            )

        depth_value = row.get("depth")
        depth = None if depth_value is None else int(depth_value)

        trace_value = row.get("trace")
        trace_path = None
        if trace_value is not None and str(trace_value).strip():
            trace_path = Path(str(trace_value).strip())

        details: list[str] = ["SymbiYosys status database"]
        engine = str(row.get("engine", "")).strip()
        location = str(row.get("location", "")).strip()
        if engine:
            details.append(f"engine={engine}")
        if location:
            details.append(f"location={location}")

        properties.append(
            FormalPropertyResult(
                name=name,
                kind="assert",
                status=status,
                depth=depth,
                trace_path=trace_path,
                message="; ".join(details),
            )
        )

    return tuple(properties)


def _query_sby_property_statuses(
    executable: str,
    *,
    run_dir: Path,
    cwd: Path,
    timeout_s: float | None,
) -> tuple[tuple[FormalPropertyResult, ...], Path | None]:
    """Query SBY's status DB using its documented JSONL status interface."""

    command = [
        executable,
        "--statusfmt",
        "jsonl",
        "--latest",
        str(run_dir),
    ]
    outcome = _run_process(command, cwd=cwd, timeout_s=timeout_s)
    status_path = run_dir / "property-status.jsonl"
    status_path.write_text(outcome.output, encoding="utf-8")

    if outcome.timed_out or outcome.returncode != 0:
        return (), status_path

    try:
        properties = parse_sby_status_jsonl(
            outcome.output,
            expected_mode="bmc",
        )
    except (ValueError, TypeError):
        # Retain the raw tool output but do not create property claims from
        # malformed or unsupported machine-readable evidence.
        return (), status_path

    return properties, status_path


class SymbiYosysBackend(FormalBackend):
    """First concrete ZDDV formal backend: finite-depth SymbiYosys BMC."""

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
        config_text = render_sby_bmc_config(project, request)

        root = (project.root / ".zddv" / "formal" / "sby").resolve()
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        run_id = f"bmc-{stamp}"
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
        if not outcome.timed_out:
            properties, status_path = _query_sby_property_statuses(
                executable,
                run_dir=run_dir,
                cwd=project.root,
                timeout_s=request.timeout_s,
            )
            if status_path is not None:
                artifacts.append(status_path)
            for item in properties:
                if item.trace_path is not None and item.trace_path not in artifacts:
                    artifacts.append(item.trace_path)

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
