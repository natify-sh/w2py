# pyright: reportPrivateUsage=false, reportUnknownMemberType=false, reportUnknownVariableType=false
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


def _tail_call_module() -> bytes:
    def fn(ret: int, tail: str) -> str:
        return f"00200045044041{ret:02x}0f0b200041016b" + tail + "0b"

    code = "04" + "".join(
        f"{len(b) // 2:02x}{b}"
        for b in (fn(7, "1200"), fn(1, "1202"), fn(0, "1201"), fn(9, "4100130000"))
    )
    return bytes.fromhex(
        "0061736d01000000"
        "0106"
        "0160017f017f"
        "0305"
        "0400000000"
        "0404"
        "01700001"
        "070d"
        "03"
        "01630000"
        "01650001"
        "01690003"
        "0907"
        "01"
        "004100"
        "0b"
        "0103"
        "0a%02x" % (len(code) // 2) + code
    )


def test_tail_calls_run_in_constant_stack() -> None:
    ex = load(_tail_call_module(), {})
    assert ex["c"](1_000_000) == 7  # self return_call
    assert ex["e"](1_000_000) == 1  # mutual even/odd
    assert ex["e"](100_001) == 0
    assert ex["i"](100_000) == 9  # return_call_indirect


def _wasm_module() -> bytes:
    def sec(i: int, body: bytes) -> bytes:
        return bytes([i, len(body)]) + body

    def vec(items: list[bytes]) -> bytes:
        return bytes([len(items)]) + b"".join(items)

    types = vec([b"\x60\x00\x01\x7f", b"\x60\x01\x7f\x01\x7f", b"\x60\x00\x00"])
    funcs = bytes([6, 0, 2, 1, 1, 0, 1])  # f0 t0, f1 t2, f2 t1, f3 t1, f4 t0, f5 t1
    table = vec([b"\x70\x01\x02\x05"])  # funcref, min 2, max 5
    names = ["init", "isnull", "grow", "size", "call"]
    exports = vec([bytes([len(n)]) + n.encode() + bytes([0, i + 1]) for i, n in enumerate(names)])
    elem = vec([b"\x01\x00\x01\x00"])  # passive segment: func 0
    bodies = [
        b"\x41\x07",  # i32.const 7
        b"\x41\x01\x41\x00\x41\x01\xfc\x0c\x00\x00",  # table.init: segment 0, table 0, (1, 0, 1)
        b"\x20\x00\x25\x00\xd1",  # table.get, ref.is_null
        b"\xd0\x70\x20\x00\xfc\x0f\x00",  # table.grow with null
        b"\xfc\x10\x00",  # table.size
        b"\x20\x00\x11\x00\x00",  # call_indirect (type 0) table 0
    ]
    code = vec([bytes([len(b) + 2, 0]) + b + b"\x0b" for b in bodies])
    return (
        b"\0asm\1\0\0\0"
        + sec(1, types)
        + sec(3, funcs)
        + sec(4, table)
        + sec(7, exports)
        + sec(9, elem)
        + sec(10, code)
    )


def test_table_init_get_grow_size_and_bounds() -> None:
    ex = load(_wasm_module(), {})
    assert ex["isnull"](1) == 1
    with pytest.raises(Trap):
        ex["call"](1)  # null element
    ex["init"]()
    assert ex["isnull"](1) == 0
    assert ex["call"](1) == 7
    with pytest.raises(Trap):
        ex["isnull"](2)  # out of bounds table.get
    assert ex["size"]() == 2
    assert ex["grow"](3) == 2
    assert ex["grow"](1) == 0xFFFFFFFF  # above the maximum of 5
    assert ex["size"]() == 5


def test_extended_const_global_initializer() -> None:
    # (global (export "g") i32 (i32.add (i32.const 40) (i32.const 2)))
    ex = load(
        bytes.fromhex(HEAD + "060901" + "7f00" + "41284102" + "6a0b" + "07050101" + "6703" + "00"),
        {},
    )
    assert ex["g"].get() == 42


def test_dropped_out_of_bounds_load_traps() -> None:
    # (memory 1) (func (export "f") (param i32)
    #   (drop (i32.load8_u offset=4294967295 (local.get 0))))
    mod = HEAD + "0105016001" + "7f00" + "03020100" + "05030100" + "01" + "07050101" + "6600" + "00"
    mod += "0a0e010c00" + "20002d00ffffffff0f1a0b"
    with pytest.raises(Trap):
        load(bytes.fromhex(mod), {})["f"](0)


# (func (export "f") (param i32) (result i32)
#   local.get 0 f32.reinterpret_i32 f32.neg i32.reinterpret_f32)
NEG32 = bytes.fromhex(
    "0061736d0100000001060160017f017f03020100" + "07050101660000" + "0a0901070020" + "00be8cbc0b"
)


def test_f32_nan_payload_kept() -> None:
    from w2py.targets.pyc import runtime as rt

    assert load(NEG32, {})["f"](0x7FA00001) == 0xFFA00001  # sNaN stays signalling, payload kept
    assert rt._i32_f32(rt._f32_i32(0x7FA00000)) == 0x7FA00000
    assert rt._i64_f64(rt._f64_i64(0x7FF4000000000001)) == 0x7FF4000000000001
    m = bytearray(8)
    rt.F32S(m, 0, rt._f32_i32(0xFFA00003))
    assert rt._i32_f32(rt._f32_i32(rt.U32(m, 0)[0])) == 0xFFA00003 == rt.U32(m, 0)[0]


def test_float_rounding_helpers() -> None:
    from w2py.targets.pyc import runtime as rt

    assert str(rt._fceil(-0.5)) == "-0.0" and str(rt._ftrunc(-0.5)) == "-0.0"
    assert rt._i64_f64(rt._fnearest(rt._f64_i64(0x7FF4000000000000))) == 0x7FFC000000000000
    assert rt._i64_f64(rt._f64_i64(0x7FF4000000000000) * 1.0) == 0x7FFC000000000000  # promote
    # 2^53 + 2^29 + 1 is just above a f32 tie: single rounding goes up, double rounding down
    x = (1 << 53) + (1 << 29) + 1
    assert rt._i64_f32(x) == float((1 << 53) + (1 << 30))
    assert rt._i64_f32(-x) == -float((1 << 53) + (1 << 30))


def test_discarded_operands_keep_their_traps_and_effects() -> None:
    # (func (export "f") (param i32 i32) local.get 0 local.get 1 i32.div_s drop)
    div = (
        HEAD
        + "010601"
        + "60027f7f00"
        + "03020100"
        + "07050101660000"
        + "0a0a0108"
        + "00200020016d1a0b"
    )
    with pytest.raises(Trap):
        load(bytes.fromhex(div), {})["f"](1, 0)
    # (func (export "f") (param i32 i32) (result i32)
    #   i32.const 1 (i32.div_s (local.get 0) (local.get 1)) i32.const 1 select)  ;; arm not selected
    sel = HEAD + "0107016002" + "7f7f017f" + "03020100" + "07050101660000" + "0a0e010c"
    sel += "00410120002001" + "6d41011b0b"
    with pytest.raises(Trap):
        load(bytes.fromhex(sel), {})["f"](1, 0)
    # (global (mut i32)) (func $h (result i32) i32.const 9 global.set 0 i32.const 0)
    # (func (export "f") (result i32) call $h drop)  ;; the dropped call still runs
    call = HEAD + "010501" + "60000" + "17f" + "03030200" + "00" + "0606017f0141000b"
    call += (
        "070902016600"
        + "01"
        + "01670300"
        + "0a1202"
        + "08004109240041000b"
        + "07"
        + "0010001a41000b"
    )
    ex = load(bytes.fromhex(call), {})
    ex["f"]()
    assert ex["g"].get() == 9


# (type $t (func (param i32) (result i32))) with call_ref / return_call_ref / ref.as_non_null /
# br_on_null / br_on_non_null on (ref $t) and (ref null $t)
TYPED_REFS = bytes.fromhex(
    "0061736d0100000001100360017f017f6000017f60016300017f030807000001020002020730060463616c6c"
    "0001046e756c6c0002026e6e0003047461696c00040662726e756c6c00050962726e6f6e6e756c6c00060905"
    "01030001000a51070700200041016a0b08002000d20014000b08004101d00014000b090041012000d414000b"
    "08002000d20015000b1000024041052000d50014000f0b417f0b110041050264002000d600417f0f0b14000b"
    "001e046e616d65010401000166030b02050100016c060100016c040401000174"
)


def test_typed_function_references() -> None:
    ex = load(TYPED_REFS, {})
    assert ex["call"](4) == 5
    assert ex["tail"](9) == 10
    f = ex["call"]
    assert ex["nn"](f) == 2
    assert ex["brnull"](f) == 6
    assert ex["brnull"](None) == 0xFFFFFFFF
    assert ex["brnonnull"](f) == 6
    assert ex["brnonnull"](None) == 0xFFFFFFFF
    with pytest.raises(Trap, match="null function reference"):
        ex["null"]()
    with pytest.raises(Trap, match="null reference"):
        ex["nn"](None)
