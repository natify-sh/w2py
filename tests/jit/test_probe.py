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


def test_registered_but_not_selected_yet() -> None:
    target = TARGETS["jit-x86_64"]()
    assert target.name == "jit-x86_64"
    assert target_order("auto") == ["pyc"]  # the step-2 readiness guard
    assert target.supports({}) is not None
    with pytest.raises(Unsupported):
        target.compile({})
