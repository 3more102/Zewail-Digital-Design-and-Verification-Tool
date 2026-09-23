import subprocess
from pathlib import Path

import pytest

from zddv.fst_adapter import converted_fst_vcd


VCD = """$timescale 1ns $end
$scope module top $end
$var wire 1 ! clk $end
$upscope $end
$enddefinitions $end
#0
0!
"""


def test_explicit_fst2vcd_adapter_uses_no_shell_and_cleans_temporary_vcd(
    tmp_path: Path,
    monkeypatch,
):
    source = tmp_path / "waveform.fst"
    source.write_bytes(b"FST-placeholder")
    calls = []

    monkeypatch.setattr(
        "zddv.fst_adapter.shutil.which",
        lambda requested: "/opt/gtkwave/bin/fst2vcd" if requested == "fst2vcd" else None,
    )

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        Path(command[command.index("-o") + 1]).write_text(VCD, encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("zddv.fst_adapter.subprocess.run", fake_run)

    with converted_fst_vcd(source) as (converted, metadata):
        assert converted.is_file()
        converted_path = converted
        assert metadata["adapter"] == "fst2vcd"
        assert metadata["executable"] == "/opt/gtkwave/bin/fst2vcd"
        assert metadata["temporary_vcd"] is True
        assert "does not bypass" in metadata["security_policy"]

    assert not converted_path.exists()
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command[:2] == ["/opt/gtkwave/bin/fst2vcd", "-f"]
    assert str(source.resolve()) in command
    assert kwargs["check"] is False
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert "shell" not in kwargs


def test_fst2vcd_adapter_requires_resolvable_converter(tmp_path: Path, monkeypatch):
    source = tmp_path / "waveform.fst"
    source.write_bytes(b"FST-placeholder")
    monkeypatch.setattr("zddv.fst_adapter.shutil.which", lambda requested: None)

    with pytest.raises(RuntimeError, match="was not found"):
        with converted_fst_vcd(source):
            raise AssertionError("unreachable")


def test_fst2vcd_adapter_preserves_converter_rejection(tmp_path: Path, monkeypatch):
    source = tmp_path / "waveform.fst"
    source.write_bytes(b"FST-placeholder")
    monkeypatch.setattr(
        "zddv.fst_adapter.shutil.which",
        lambda requested: "/usr/bin/fst2vcd",
    )

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            9,
            stdout="",
            stderr="security validation rejected input",
        )

    monkeypatch.setattr("zddv.fst_adapter.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="security validation rejected input"):
        with converted_fst_vcd(source):
            raise AssertionError("unreachable")
