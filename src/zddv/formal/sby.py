from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid

from zddv.config import ProjectConfig
from .base import FormalBackend, FormalCheckRequest, FormalCheckResult


_DEFAULT_BMC_DEPTH = 20
_SUPPORTED_ENGINE = "smtbmc"
_TOP_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_DONE_STATUS = re.compile(
    r"\\bDONE\\s*\\(\\s*(PASS|FAIL|UNKNOWN|ERROR|TIMEOUT)\\b[^)]*\\)",
    re.IGNORECASE,
)
_COMPILE_SUFFIXES = {".v", ".sv"}
_TRACE_SUFFIXES = {".vcd", ".fst", ".yw", ".aiw"}


def _quoted_token(value: str) -> str:
    """Quote one SBY/Yosys token when whitespace or quoting requires it."""
    if not value:
        raise ValueError("SBY path token must not be empty")
    if "\\n" in value or "\\r" in value:
        raise ValueError("SBY path token must not contain newlines")
    if re.fullmatch(r"[A-Za-z0-9_./:$+@%=-]+", value):
        return value
    escaped = value.replace("\\\\", "\\\\\\\\").replace('"', '\\\"')
    return f'"{escaped}"'


def _source_mappings(project: ProjectConfig) -> list[tuple[Path, str]]:
    sources = project.source_files()
    if not sources:
        raise RuntimeError(
            "No RTL/formal sources matched the project configuration."
        )

    root = project.root.resolve()
    mappings: list[tuple[Path, str]] = []
    for index, source in enumerate(sources):
        resolved = source.resolve()
        try:
            target = resolved.relative_to(root).as_posix()
        except ValueError:
            safe_name = re.sub(
                r"[^A-Za-z0-9_.-]+",
                "-",
                resolved.name,
            ).strip("-") or f"source-{index}"
            target = f"_external/{index:03d}-{safe_name}"
        mappings.append((resolved, target))
    return mappings


def _effective_bmc_request(request: FormalCheckRequest) -> FormalCheckRequest:
    if request.mode != "bmc":
        raise ValueError(
            "SymbiYosys execution currently supports only formal mode 'bmc'"
        )
    if request.properties:
        raise ValueError(
            "SymbiYosys property filters are not implemented yet; "
            "run the complete bounded property set"
        )
    if request.depth is None:
        return replace(request, depth=_DEFAULT_BMC_DEPTH)
    return request


def render_sby_bmc_config(
    project: ProjectConfig,
    request: FormalCheckRequest,
    *,
    engine: str = _SUPPORTED_ENGINE,
) -> tuple[str, FormalCheckRequest, tuple[Path, ...]]:
    """Render an evidence-preserving SymbiYosys BMC configuration."""
    if engine.strip().lower() != _SUPPORTED_ENGINE:
        raise ValueError(
            "SymbiYosys bounded backend currently supports only the smtbmc engine"
        )
    if not _TOP_IDENTIFIER.fullmatch(project.top):
        raise ValueError(
            "SymbiYosys backend currently requires a simple Verilog top identifier"
        )

    effective = _effective_bmc_request(request)
    mappings = _source_mappings(project)
    compile_targets = [
        target
        for source, target in mappings
        if source.suffix.lower() in _COMPILE_SUFFIXES
    ]
    if not compile_targets:
        raise RuntimeError(
            "No .v or .sv compilation sources matched the project configuration."
        )

    options = [
        "[options]",
        "mode bmc",
        f"depth {effective.depth}",
    ]
    if effective.timeout_s is not None:
        options.append(f"timeout {max(1, math.ceil(effective.timeout_s))}")

    script = ["[script]"]
    script.extend(
        f"read -formal {_quoted_token(target)}"
        for target in compile_targets
    )
    script.append(f"prep -top {project.top}")

    files = ["[files]"]
    for source, target in mappings:
        files.append(
            f"{_quoted_token(target)} {_quoted_token(source.as_posix())}"
        )

    text = "\n".join(
        [
            *options,
            "",
            "[engines]",
            _SUPPORTED_ENGINE,
            "",
            *script,
            "",
            *files,
            "",
        ]
    )
    return text, effective, tuple(source for source, _ in mappings)


def _parse_done_status(output: str, returncode: int) -> str:
    matches = list(_DONE_STATUS.finditer(output))
    if not matches:
        return "ERROR"

    native = matches[-1].group(1).upper()
    if native == "TIMEOUT":
        return "UNKNOWN"
    if native == "PASS" and returncode != 0:
        return "ERROR"
    return native


def _trace_artifacts(work_dir: Path) -> tuple[Path, ...]:
    if not work_dir.exists():
        return ()
    traces = [
        path.resolve()
        for path in work_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in _TRACE_SUFFIXES
    ]
    return tuple(sorted(traces, key=lambda path: path.as_posix()))


class SymbiYosysBackend(FormalBackend):
    """Bounded formal execution through SymbiYosys with explicit status evidence."""

    name = "sby"

    def __init__(self, *, engine: str = _SUPPORTED_ENGINE) -> None:
        normalized = str(engine).strip().lower()
        if normalized != _SUPPORTED_ENGINE:
            raise ValueError(
                "SymbiYosys bounded backend currently supports only smtbmc"
            )
        self.engine = normalized

    def _tool(self) -> str:
        tool = shutil.which("sby")
        if tool is None:
            raise RuntimeError(
                "SymbiYosys 'sby' was not found in PATH. "
                "Install/configure SymbiYosys and retry."
            )
        return tool

    def version(self) -> str:
        completed = subprocess.run(
            [self._tool(), "--version"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        output = (completed.stdout or "").strip()
        if completed.returncode != 0:
            raise RuntimeError(output or "Unable to query SymbiYosys version.")
        return output

    def check(
        self,
        project: ProjectConfig,
        request: FormalCheckRequest,
    ) -> FormalCheckResult:
        config_text, effective, sources = render_sby_bmc_config(
            project,
            request,
            engine=self.engine,
        )

        now = datetime.now(timezone.utc)
        run_id = (
            now.strftime("%Y%m%dT%H%M%SZ")
            + f"-bmc-d{effective.depth}-"
            + uuid.uuid4().hex[:8]
        )
        run_dir = (
            project.root / ".zddv" / "formal" / "runs" / run_id
        ).resolve()
        run_dir.mkdir(parents=True, exist_ok=False)

        config_path = run_dir / "check.sby"
        work_dir = run_dir / "sby-work"
        log_path = run_dir / "formal.log"
        manifest_path = run_dir / "formal.json"
        config_path.write_text(config_text, encoding="utf-8")

        command = [
            self._tool(),
            "-f",
            "-d",
            str(work_dir),
            str(config_path),
        ]
        outer_timeout = None
        if effective.timeout_s is not None:
            outer_timeout = max(1, math.ceil(effective.timeout_s)) + 5

        timed_out = False
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                command,
                cwd=run_dir,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=outer_timeout,
            )
            returncode = completed.returncode
            output = completed.stdout or ""
            status = _parse_done_status(output, returncode)
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            returncode = 124
            output = exc.stdout or ""
            if isinstance(output, bytes):
                output = output.decode(errors="replace")
            output += (
                "\nZDDV_FORMAL_TIMEOUT "
                f"after {outer_timeout} seconds\n"
            )
            status = "UNKNOWN"

        runtime_ms = (time.perf_counter() - started) * 1000.0
        log_path.write_text(output, encoding="utf-8")
        traces = _trace_artifacts(work_dir)
        done_matches = _DONE_STATUS.findall(output)

        manifest = {
            "created_at": now.isoformat(),
            "project": project.name,
            "backend": self.name,
            "engine": self.engine,
            "status": status,
            "native_status_evidence": (
                "process-timeout"
                if timed_out
                else (done_matches[-1].upper() if done_matches else None)
            ),
            "request": {
                "mode": effective.mode,
                "requested_depth": request.depth,
                "depth": effective.depth,
                "timeout_s": effective.timeout_s,
                "properties": list(effective.properties),
            },
            "command": command,
            "returncode": returncode,
            "runtime_ms": round(runtime_ms, 3),
            "run_dir": str(run_dir),
            "config": str(config_path),
            "work_dir": str(work_dir),
            "log": str(log_path),
            "sources": [str(path) for path in sources],
            "traces": [str(path) for path in traces],
        }
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        return FormalCheckResult(
            backend=self.name,
            request=effective,
            command=tuple(command),
            returncode=returncode,
            status=status,
            run_dir=run_dir,
            log_path=log_path,
            properties=(),
            artifacts=(config_path, manifest_path, *traces),
            engine=self.engine,
            runtime_ms=runtime_ms,
        )
