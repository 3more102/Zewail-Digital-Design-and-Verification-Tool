from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from typing import Any, Iterator


def _resolve_converter(executable: str | Path) -> str:
    requested = str(executable).strip()
    if not requested:
        raise ValueError("fst2vcd executable must not be empty")

    resolved = shutil.which(requested)
    if resolved is None:
        raise RuntimeError(
            f"FST conversion requested, but fst2vcd was not found: {requested}. "
            "Install GTKWave/fst2vcd or pass an explicit converter path."
        )
    return resolved


def _failure_detail(stdout: str | None, stderr: str | None, *, limit: int = 2000) -> str:
    detail = (stderr or "").strip() or (stdout or "").strip()
    if not detail:
        return "no converter diagnostics were emitted"
    if len(detail) > limit:
        return detail[:limit] + "... [truncated]"
    return detail


@contextmanager
def converted_fst_vcd(
    path: str | Path,
    *,
    executable: str | Path = "fst2vcd",
    timeout_s: float = 120.0,
) -> Iterator[tuple[Path, dict[str, Any]]]:
    """Convert one FST to a temporary VCD through an explicit fst2vcd adapter."""

    if timeout_s <= 0:
        raise ValueError("timeout_s must be > 0")

    source = Path(path).resolve()
    if not source.exists():
        raise FileNotFoundError(f"Waveform file not found: {source}")
    if not source.is_file():
        raise RuntimeError(f"Waveform path is not a file: {source}")
    if source.suffix.lower() != ".fst":
        raise RuntimeError(f"FST converter requires a .fst input: {source}")

    converter = _resolve_converter(executable)
    with TemporaryDirectory(prefix="zddv-fst-") as tmpdir:
        output = Path(tmpdir) / "converted.vcd"
        command = [
            converter,
            "-f",
            str(source),
            "-o",
            str(output),
        ]
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"fst2vcd timed out after {timeout_s:g}s while converting {source}"
            ) from exc

        if completed.returncode != 0:
            raise RuntimeError(
                "fst2vcd failed with return code "
                f"{completed.returncode}: "
                f"{_failure_detail(completed.stdout, completed.stderr)}"
            )
        if not output.is_file() or output.stat().st_size == 0:
            raise RuntimeError(
                "fst2vcd reported success but did not produce a non-empty VCD output."
            )

        yield output, {
            "adapter": "fst2vcd",
            "executable": converter,
            "returncode": completed.returncode,
            "temporary_vcd": True,
            "security_policy": (
                "ZDDV does not bypass fst2vcd input-validation or security checks."
            ),
        }
