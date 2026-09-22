from __future__ import annotations

from pathlib import Path
import tomllib

from zddv import __version__


def test_runtime_version_matches_package_metadata():
    root = Path(__file__).resolve().parents[1]
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    assert __version__ == metadata["project"]["version"]
    assert __version__ == "0.9.0"
