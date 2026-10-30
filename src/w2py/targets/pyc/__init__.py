"""The `pyc` target: WebAssembly to Python source (internal entry points, not public API)."""

from collections.abc import Callable, Mapping
from typing import Any, cast

from w2py.targets.pyc.codegen import generate
from w2py.targets.pyc.runtime import Trap

__all__ = ["Trap", "load", "translate"]

Imports = Mapping[str, Mapping[str, Callable[..., Any]]]


def translate(wasm: bytes) -> str:
    """Translate a wasm binary to Python source. The source imports its helpers from
    `w2py.targets.pyc.runtime`."""
    return generate(wasm)


def load(wasm: bytes, imports: Imports) -> dict[str, Any]:
    """Translate, compile in memory and instantiate; returns the exports by name (functions;
    memory as a `bytearray`)."""
    code = compile(translate(wasm), "<w2py-pyc>", "exec", optimize=2)
    namespace: dict[str, Any] = {"__name__": "w2py_pyc_module"}
    exec(code, namespace)
    return cast(dict[str, Any], namespace["instantiate"](imports))
