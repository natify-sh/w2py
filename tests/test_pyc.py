import os

import pytest

from w2py.targets.pyc import Trap, load, translate

COREMARK = os.path.join(os.path.dirname(__file__), "..", "poc", "in", "core.wasm")

# (module (func (export "div") (param i32 i32) (result i32) local.get 0 local.get 1 i32.div_s))
DIV = bytes.fromhex(
    "0061736d0100000001070160027f7f017f030201000707010364697600000a09010700200020016d0b"
)


def test_tiny_module_call_and_trap() -> None:
    ex = load(DIV, {})
    assert ex["div"](7, 2) == 3
    with pytest.raises(Trap):
        ex["div"](1, 0)


def test_generated_source_imports_runtime() -> None:
    src = translate(DIV)
    assert "from w2py.targets.pyc.runtime import" in src
    assert "class Trap" not in src


@pytest.mark.skipif(not os.path.exists(COREMARK), reason="poc/in/core.wasm missing")
def test_coremark_translates_and_compiles() -> None:
    with open(COREMARK, "rb") as f:
        src = translate(f.read())
    compile(src, "<coremark>", "exec")
