from .base import BuildResult, RunResult, SimulatorBackend
from .questa import QuestaBackend
from .vcs import VcsBackend
from .verilator import VerilatorBackend


def get_backend(name: str) -> SimulatorBackend:
    normalized = name.strip().lower()
    if normalized == "verilator":
        return VerilatorBackend()
    if normalized in {"questa", "questasim"}:
        return QuestaBackend()
    if normalized == "vcs":
        return VcsBackend()
    raise RuntimeError(f"Unsupported simulator backend: {name}")


__all__ = [
    "BuildResult",
    "RunResult",
    "SimulatorBackend",
    "QuestaBackend",
    "VcsBackend",
    "VerilatorBackend",
    "get_backend",
]
