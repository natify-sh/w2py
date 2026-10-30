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


# (module (type (func (param i32) (result i32))) (type (func (param i32 i32) (result i32)))
#  (func (export "f") (param i32) (result i32) (local i32 i32) ;; sum 0..n, loop with params
#    local.get 0 i32.const 0 loop (type 1) local.set 1 local.set 2
#      local.get 2 i32.const 1 i32.sub  local.get 1 local.get 2 i32.add  local.get 2 br_if 0
#      local.set 1 drop local.get 1 end)
#  (func (export "g") (param i32 i32) (result i32)               ;; if with a param on each arm
#    local.get 0 local.get 1 if (type 0) i32.const 10 i32.add else i32.const 20 i32.sub end))
BLOCK_PARAMS = bytes.fromhex(
    HEAD
    + "010c02" + "60017f017f" + "60027f7f017f"
    + "03030200" + "01"
    + "07090201660000" + "01670001"
    + "0a3502" + "22" + "01027f" + "2000410003012101210220024101" + "6b" + "20012002" + "6a"
    + "20020d00" + "21011a2001" + "0b0b"
    + "10" + "00" + "20002001" + "0400" + "410a6a" + "05" + "41146b" + "0b0b"
)  # fmt: skip


def test_block_params_loop_and_if() -> None:
    ex = load(BLOCK_PARAMS, {})
    assert ex["f"](4) == 10
    assert ex["f"](0) == 0
    assert ex["g"](5, 1) == 15
    assert ex["g"](5, 0) == 0xFFFFFFF1
