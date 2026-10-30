import os

import pytest


def need(ok: object, what: str) -> None:
    """Skip a test whose dev tool or capability is missing; fail it instead under
    W2PY_JIT_REQUIRE=1 (the CI `jit` job), so a skip cannot hide there."""
    if not ok:
        if os.environ.get("W2PY_JIT_REQUIRE") == "1":
            pytest.fail(f"{what} missing (W2PY_JIT_REQUIRE=1)")
        pytest.skip(f"{what} missing")
