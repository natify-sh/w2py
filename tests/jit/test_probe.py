"""The `jit-x86_64` probe and its registration (S0002 step 2)."""

import platform

import pytest

from tests.jit import need
from w2py.targets.base import TARGETS, Unsupported, target_order
from w2py.targets.jit.x86_64 import JitX86_64Target


def test_probe_succeeds_here() -> None:
    need(platform.machine().lower() in ("x86_64", "amd64"), "an x86-64 machine")
    assert JitX86_64Target().probe() is None


def test_probe_refuses_other_architectures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "machine", lambda: "aarch64")
    why = JitX86_64Target().probe()
    assert why is not None and "aarch64" in why


def test_registered_and_selected_by_auto() -> None:
    """Ready since step 3e (expected failures equal pyc's, tests/spec/test_runner.py): `auto`
    selects it where its probe succeeds."""
    target = TARGETS["jit-x86_64"]()
    assert target.name == "jit-x86_64"
    want = "jit-x86_64" if target.probe() is None else "pyc"
    assert target_order("auto") == [want]
    assert target.supports({}) is not None
    with pytest.raises(Unsupported):
        target.compile({})
