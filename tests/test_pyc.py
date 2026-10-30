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


HEAD = "0061736d01000000"
# A: (memory (export "m") 1) (global (export "g") (mut i32) (i32.const 1))
SHARE_A = HEAD + "05030100" + "01" + "0606017f0141010b" + "070902016d0200" + "01670300"
# B: imports A.m and A.g (mut i32); (func (export "f") (result i32)
#   (i32.store8 (i32.const 0) (i32.const 7)) (global.set 0 (i32.const 9))
#   (i32.load8_u (i32.const 0)))
SHARE_B = (
    HEAD
    + "0105016000017f"
    + "020f02"
    + "0141016d020001"
    + "014101"
    + "67037f01"
    + "03020100"
    + "07050101660000"
    + "0a14011200"
    + "41004107"
    + "3a0000"
    + "410924"
    + "00"
    + "4100"
    + "2d0000"
    + "0b"
)


def test_shared_memory_and_mutable_global_between_instances() -> None:
    a = load(bytes.fromhex(SHARE_A), {})
    b = load(bytes.fromhex(SHARE_B), {"A": {"m": a["m"], "g": a["g"]}})
    assert b["f"]() == 7
    assert a["m"][0] == 7 and a["g"].get() == 9
