"""The one-pass JIT core (D26), shared by every architecture: reads each validated function body
once and drives a `Backend` (common/backend.py). Nothing here names a machine register.

Frame (S0003): slot j < L holds local j (parameters first, so the caller's arguments are already
in place); operand-stack entry i lives in slot L + i when it is materialised. Results go to slots
0..m on return. A wasm call puts the callee's frame at the first argument (frame overlap).

The operand stack is lazy: an entry is a constant, a local, the value in the backend's `tos`
register (at most one entry), a materialised slot, or a pending compare (only ever on top; it is
consumed whole by `br_if`/`if`/`select`/`eqz`, else turned into a value before anything else is
emitted). Every instruction that can trap is emitted when it is reached, so `drop`, `select` and
branches never discard a trap (LESSONS 28). At every control-flow edge the whole stack is in
slots, and pinned locals never move, so merges need no reconciliation.

Test-only switch: `W2PY_JIT_NOPIN=all` or `=<function indices>` compiles those functions without
pinned locals (bisecting a pinning bug; D25 rules out mixing targets per function).
"""

import os
import struct
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from w2py.targets.base import TrapCode, Unsupported, ValidModule
from w2py.targets.jit.common.backend import NEGATE, Backend, Imm, Operand, Reg, Slot
from w2py.targets.jit.common.context import Layout

I32, I64, F32, F64 = 0x7F, 0x7E, 0x7D, 0x7C
NUM = (I32, I64, F32, F64)
WIDE = (I64, F64)
M32, M64 = 0xFFFFFFFF, (1 << 64) - 1
CONST, LOCAL, TOS, SLOT, CMP = range(5)
SLOTE = (SLOT, None)
Entry = tuple[int, Any]

_BIN = "add sub mul div_s div_u rem_s rem_u and or xor shl shr_s shr_u rotl rotr".split()
_CMP = "eq ne lt_s lt_u gt_s gt_u le_s le_u ge_s ge_u".split()
BINOPS = {**{0x6A + i: (n, False) for i, n in enumerate(_BIN)},
          **{0x7C + i: (n, True) for i, n in enumerate(_BIN)}}  # fmt: skip
CMPS = {**{0x46 + i: (n, False) for i, n in enumerate(_CMP)},
        **{0x51 + i: (n, True) for i, n in enumerate(_CMP)}}  # fmt: skip
UNOPS = {0x67: ("clz", False), 0x68: ("ctz", False), 0x69: ("popcnt", False),
         0x79: ("clz", True), 0x7A: ("ctz", True), 0x7B: ("popcnt", True), 0xA7: ("wrap", False),
         0xAC: ("extend_i32_s", True), 0xAD: ("extend_i32_u", True), 0xC0: ("extend8_s", False),
         0xC1: ("extend16_s", False), 0xC2: ("extend8_s", True), 0xC3: ("extend16_s", True),
         0xC4: ("extend32_s", True)}  # fmt: skip
LOADS = {0x28: (4, False, False), 0x29: (8, False, True), 0x2A: (4, False, False),
         0x2B: (8, False, True), 0x2C: (1, True, False), 0x2D: (1, False, False),
         0x2E: (2, True, False), 0x2F: (2, False, False), 0x30: (1, True, True),
         0x31: (1, False, True), 0x32: (2, True, True), 0x33: (2, False, True),
         0x34: (4, True, True), 0x35: (4, False, True)}  # fmt: skip
STORES = {0x36: 4, 0x37: 8, 0x38: 4, 0x39: 8, 0x3A: 1, 0x3B: 2, 0x3C: 1, 0x3D: 2, 0x3E: 4}
REINTERPRET = (0xBC, 0xBD, 0xBE, 0xBF)  # bit patterns: nothing to do
CONTROL = (0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x10, 0x1A, 0x1B)
SUPPORTED = frozenset(
    (*CONTROL, *range(0x20, 0x25), *range(0x41, 0x46), 0x50, *BINOPS, *CMPS, *UNOPS, *LOADS,
     *STORES, *REINTERPRET)
)  # fmt: skip


def feature(op: int) -> str:
    """The name an `Unsupported` carries for an opcode this core does not compile (yet)."""
    if op in (0x3F, 0x40):
        return "memory.size/grow"
    if 0x5B <= op <= 0xBF or 0xFC00 <= op <= 0xFC07:
        return "float arithmetic"
    if op in (0x11, 0x25, 0x26) or 0xFC0C <= op <= 0xFC11:
        return "tables"
    if 0xD0 <= op <= 0xD6:
        return "reference types"
    if 0xFC08 <= op <= 0xFC0B:
        return "bulk memory"
    if op in (0x12, 0x13):
        return "tail calls"
    return f"opcode {op:#x}"


def unsupported(mod: ValidModule) -> str | None:
    """The first feature of `mod` this core cannot compile, or None."""
    if not mod.get("validated"):
        return "module not fully validated"
    imps: list[tuple[Any, ...]] = mod["imports"]
    if mod["tables"] or mod["elem_info"] or any(im[0] == "table" for im in imps):
        return "tables"
    if len(mod["mems"]) + sum(im[0] == "mem" for im in imps) > 1:
        return "multi-memory"
    vts: list[Any] = [t for ps, rs in mod["types"] for t in (*ps, *rs)]
    vts += [g[0] for g in mod["globals"]] + [im[3][0] for im in imps if im[0] == "global"]
    vts += [t for locs, _ in mod["codes"] for t in locs]
    if any(t not in NUM for t in vts):
        return "reference types"
    for insns in mod["insns"]:
        for ins in insns:
            op = ins[0]
            if op not in SUPPORTED:
                return feature(op)
            ts: Sequence[Any] = ()
            if op in (0x02, 0x03, 0x04) and ins[1][0] != "idx":
                ts = (*ins[1][0], *ins[1][1])
            elif op == 0x1B and len(ins) > 1:  # typed select
                ts = ins[1]
            if any(t not in NUM for t in ts):
                return "reference types"
    return None


def f32_bits(x: float) -> int:
    """A decoded f32 constant (a NaN carries its payload << 29 in the double, binary._f32c)."""
    if x != x:
        q = struct.unpack("<Q", struct.pack("<d", x))[0]
        return (q >> 32) & 0x80000000 | 0x7F800000 | (q >> 29) & 0x7FFFFF
    return struct.unpack("<I", struct.pack("<f", x))[0]


def f64_bits(x: float) -> int:
    return struct.unpack("<Q", struct.pack("<d", x))[0]


@dataclass
class Compiled:
    module: ValidModule
    code: bytes  # the code, then the data area from `data_start` (jump tables)
    data_start: int
    funcs: list[tuple[int, int]]  # (code offset, result count) per defined function
    layout: Layout


@dataclass(slots=True)
class _Ctl:
    kind: str  # func block loop if else
    label: Any  # loop: its header; func: the return path
    h: int  # stack height at entry, below the parameters
    nparams: int
    nres: int
    dst: int  # first slot a branch to this label writes its values to
    else_label: Any = None
    used: bool = False


@dataclass
class _Mod:
    b: Backend[Any, Any]
    types: list[tuple[list[Any], list[Any]]]
    ftypes: list[int]  # type index of every function, imports first
    nimp: int
    labels: list[Any]  # of the defined functions
    layout: Layout
    gtypes: list[Any]
    nimpg: int


def compile_module(mod: ValidModule, b: Backend[Any, Any], target: str) -> Compiled:
    """Compile every function or raise Unsupported with the first missing feature."""
    why = unsupported(mod)
    if why:
        raise Unsupported(target, why)
    imps: list[tuple[Any, ...]] = mod["imports"]
    fimps = [im[3] for im in imps if im[0] == "func"]
    gimps = [im[3][0] for im in imps if im[0] == "global"]
    has_mem = bool(mod["mems"]) or any(im[0] == "mem" for im in imps)
    layout = Layout(
        memories=int(has_mem), globals=len(gimps) + len(mod["globals"]), imports=len(fimps)
    )
    m = _Mod(
        b, mod["types"], fimps + mod["funcs"], len(fimps), [b.new_label() for _ in mod["funcs"]],
        layout, gimps + [g[0] for g in mod["globals"]], len(gimps),
    )  # fmt: skip
    nopin = os.environ.get("W2PY_JIT_NOPIN", "")
    funcs: list[tuple[int, int]] = []
    for k, ((locs, _), insns) in enumerate(zip(mod["codes"], mod["insns"], strict=True)):
        fidx = len(fimps) + k
        pin = not (nopin == "all" or str(fidx) in nopin.split(","))
        funcs.append((b.here(), len(m.types[m.ftypes[fidx]][1])))
        _Func(m, fidx, locs, insns, pin).run()
    code, data = b.finish()
    return Compiled(mod, code, data, funcs, layout)


class _Func:
    def __init__(self, m: _Mod, fidx: int, locs: list[Any], insns: list[Any], pin: bool) -> None:
        self.m, self.b, self.insns = m, m.b, insns
        ps, self.results = m.types[m.ftypes[fidx]]
        self.nparams = len(ps)
        self.ltypes: list[Any] = [*ps, *locs]
        self.L = len(self.ltypes)
        self.label = m.labels[fidx - m.nimp]
        self.TOS = Reg(self.b.tos)
        self.pins = self.choose_pins() if pin else {}
        self.st: list[Entry] = []
        self.ctl: list[_Ctl] = []
        self.maxh = 0
        self.dead = False
        self.dead_depth = 0
        self.f: Any = None  # the backend's frame, from run()

    def choose_pins(self) -> dict[int, int]:
        """Integer locals by uses weighted by loop depth (as w2jit2), as many as the pool holds."""
        w: Counter[int] = Counter()
        depth = 0
        kinds: list[int] = []
        for ins in self.insns:
            op = ins[0]
            if op in (0x02, 0x03, 0x04):
                kinds.append(op)
                depth += op == 0x03
            elif op == 0x0B and kinds:
                depth -= kinds.pop() == 0x03
            elif op in (0x20, 0x21, 0x22) and self.ltypes[ins[1]] in (I32, I64):
                w[ins[1]] += 8 ** min(depth, 5)
        pool = self.b.pool
        return {j: pool[k] for k, j in enumerate(sorted(w, key=lambda j: -w[j])[: len(pool)])}

    # ------------------------------------------------------------------ the lazy stack
    def loc(self, j: int) -> Reg | Slot:
        r = self.pins.get(j)
        return Slot(j) if r is None else Reg(r)

    def opnd(self, e: Entry, i: int) -> Operand:
        k = e[0]
        if k == CONST:
            return Imm(e[1])
        if k == LOCAL:
            return self.loc(e[1])
        if k == TOS:
            return self.TOS
        assert k == SLOT
        return Slot(self.L + i)

    def push(self, e: Entry) -> None:
        st = self.st
        if st and st[-1][0] == CMP:
            self.uncmp()
        st.append(e)
        if len(st) > self.maxh:
            self.maxh = len(st)

    def uncmp(self) -> None:
        """The pending compare on top becomes a value in `tos`."""
        cond, x, y, w = self.st.pop()[1]
        self.spill_tos()
        self.b.cmp_set(cond, self.TOS, x, y, w)
        self.st.append((TOS, None))

    def spill_tos(self) -> None:
        for i, e in enumerate(self.st):
            if e[0] == TOS:
                self.b.move(Slot(self.L + i), self.TOS, True)
                self.st[i] = SLOTE

    def need_tos(self) -> None:
        """Before an operation writes `tos`: nothing on the stack may still need it."""
        if self.st and self.st[-1][0] == CMP:
            self.uncmp()
        self.spill_tos()

    def pop(self) -> Operand:
        st = self.st
        if st[-1][0] == CMP:
            self.uncmp()
        e = st.pop()
        return self.opnd(e, len(st))

    def pop_cond(self) -> tuple[str, Operand, Operand, bool]:
        if self.st[-1][0] == CMP:
            return self.st.pop()[1]
        return ("ne", self.pop(), Imm(0), False)

    def flush(self) -> None:
        """Everything into its slot: before calls, branches and control-flow merges."""
        st = self.st
        if st and st[-1][0] == CMP:
            self.uncmp()
        for i, e in enumerate(st):
            if e[0] != SLOT:
                self.b.move(Slot(self.L + i), self.opnd(e, i), True)
                st[i] = SLOTE

    def set_local(self, j: int, tee: bool) -> None:
        src = self.pop()
        dst = self.loc(j)
        for i, e in enumerate(self.st):  # earlier reads of local j keep the old value
            if e[0] == LOCAL and e[1] == j:
                self.b.move(Slot(self.L + i), dst, True)
                self.st[i] = SLOTE
        if src != dst:
            self.b.move(dst, src, self.ltypes[j] in WIDE)
        if tee:
            self.push((LOCAL, j))

    # ------------------------------------------------------------------ control
    def arity(self, c: _Ctl) -> int:
        return c.nparams if c.kind == "loop" else c.nres

    def needs_moves(self, c: _Ctl) -> bool:
        n = self.arity(c)
        return n > 0 and self.L + len(self.st) - n != c.dst

    def moves(self, c: _Ctl) -> None:
        """The branch values (on top, in slots) to the label's slots; sources are never below
        their destinations, so ascending order is safe."""
        n = self.arity(c)
        src = self.L + len(self.st) - n
        for j in range(n):
            if src + j != c.dst + j:
                self.b.move(Slot(c.dst + j), Slot(src + j), True)

    def block_type(self, bt: Any) -> tuple[list[Any], list[Any]]:
        if bt[0] == "idx":
            idx: int = bt[1]
            return self.m.types[idx]
        return bt

    def enter(self, op: int, bt: Any) -> None:
        b = self.b
        ps, rs = self.block_type(bt)
        cond = self.pop_cond() if op == 0x04 else None
        self.flush()
        h = len(self.st) - len(ps)
        kind = {0x02: "block", 0x03: "loop", 0x04: "if"}[op]
        c = _Ctl(kind, b.new_label(), h, len(ps), len(rs), self.L + h)
        if cond is not None:
            c.else_label = b.new_label()
            b.cmp_branch(NEGATE[cond[0]], cond[1], cond[2], c.else_label, cond[3])
        elif op == 0x03:
            b.bind(c.label)
            b.poll(self.f)
        self.ctl.append(c)

    def else_(self) -> None:
        c = self.ctl[-1]
        if not self.dead:
            self.flush()
            self.b.jump(c.label)
            c.used = True
        self.b.bind(c.else_label)
        c.kind, self.dead = "else", False
        del self.st[c.h :]
        self.st += [SLOTE] * c.nparams

    def end(self) -> None:
        c = self.ctl.pop()
        if not self.dead:
            self.flush()
        if c.kind == "func":
            if not self.dead:
                self.moves(c)
            self.b.bind(c.label)
            self.b.ret_ok(self.f)
            return
        if c.kind == "if":  # no else: the false path arrives with the parameters as results
            self.b.bind(c.else_label)
        if c.kind != "loop":
            self.b.bind(c.label)
        self.dead = self.dead and not (c.kind == "if" or (c.kind != "loop" and c.used))
        del self.st[c.h :]
        self.st += [SLOTE] * c.nres
        self.maxh = max(self.maxh, len(self.st))

    def br(self, depth: int) -> None:
        c = self.ctl[-1 - depth]
        self.flush()
        self.moves(c)
        self.b.jump(c.label)
        c.used, self.dead = True, True

    def br_if(self, depth: int) -> None:
        b = self.b
        cond, x, y, w = self.pop_cond()
        self.flush()
        c = self.ctl[-1 - depth]
        c.used = True
        if not self.needs_moves(c):
            b.cmp_branch(cond, x, y, c.label, w)
            return
        skip = b.new_label()
        b.cmp_branch(NEGATE[cond], x, y, skip, w)
        self.moves(c)
        b.jump(c.label)
        b.bind(skip)

    def br_table(self, depths: Sequence[int], default: int) -> None:
        b = self.b
        index = self.pop()
        self.flush()
        stubs: dict[int, tuple[_Ctl, Any]] = {}

        def target(d: int) -> Any:
            c = self.ctl[-1 - d]
            c.used = True
            if not self.needs_moves(c):
                return c.label
            if d not in stubs:
                stubs[d] = (c, b.new_label())
            return stubs[d][1]

        b.jump_table(index, [target(d) for d in depths], target(default))
        for c, label in stubs.values():
            b.bind(label)
            self.moves(c)
            b.jump(c.label)
        self.dead = True

    def call(self, fi: int) -> None:
        m, b = self.m, self.b
        ps, rs = m.types[m.ftypes[fi]]
        n, nres = len(ps), len(rs)
        self.flush()
        h0 = len(self.st) - n
        del self.st[h0:]
        if fi < m.nimp:
            b.call_host(self.f, m.layout.import_(fi), self.L + h0)
        else:
            b.call_direct(self.f, m.labels[fi - m.nimp], self.L + h0)
        self.st += [SLOTE] * nres
        self.maxh = max(self.maxh, h0 + max(n, nres))

    # ------------------------------------------------------------------ the pass
    def run(self) -> None:
        b, L = self.b, self.L
        self.f = f = b.func_begin(self.label, tuple(self.pins.values()))
        for j, r in self.pins.items():
            src = Slot(j) if j < self.nparams else Imm(0)
            b.move(Reg(r), src, self.ltypes[j] in WIDE)
        for j in range(self.nparams, L):
            if j not in self.pins:
                b.move(Slot(j), Imm(0), True)
        self.ctl.append(_Ctl("func", b.new_label(), 0, 0, len(self.results), 0))
        tos, push, pop = self.TOS, self.push, self.pop
        for ins in self.insns:
            op = ins[0]
            if self.dead:  # skip to the end (or else) of the block that became unreachable
                if op in (0x02, 0x03, 0x04):
                    self.dead_depth += 1
                elif op == 0x0B:
                    if self.dead_depth:
                        self.dead_depth -= 1
                    else:
                        self.end()
                elif op == 0x05 and not self.dead_depth:
                    self.else_()
                continue
            if op == 0x20:
                push((LOCAL, ins[1]))
            elif op in BINOPS:
                name, w = BINOPS[op]
                y = pop()
                x = pop()
                self.need_tos()
                if name in ("div_s", "div_u", "rem_s", "rem_u"):
                    b.divrem(f, name, tos, x, y, w)
                else:
                    b.binop(name, tos, x, y, w)
                push((TOS, None))
            elif op == 0x41 or op == 0x42:
                push((CONST, ins[1]))
            elif op in CMPS:
                name, w = CMPS[op]
                y = pop()
                x = pop()
                push((CMP, (name, x, y, w)))
            elif op in (0x21, 0x22):
                self.set_local(ins[1], op == 0x22)
            elif op in LOADS:
                size, signed, w = LOADS[op]
                addr = pop()
                self.need_tos()
                b.load(f, size, signed, w, tos, addr, ins[1])
                push((TOS, None))
            elif op in STORES:
                v = pop()
                addr = pop()
                b.store(f, STORES[op], addr, ins[1], v)
            elif op == 0x45 or op == 0x50:
                if self.st[-1][0] == CMP:
                    cond, x, y, w = self.st[-1][1]
                    self.st[-1] = (CMP, (NEGATE[cond], x, y, w))
                else:
                    push((CMP, ("eq", pop(), Imm(0), op == 0x50)))
            elif op in UNOPS:
                name, w = UNOPS[op]
                x = pop()
                self.need_tos()
                b.unop(name, tos, x, w)
                push((TOS, None))
            elif op in (0x02, 0x03, 0x04):
                self.enter(op, ins[1])
            elif op == 0x0B:
                self.end()
            elif op == 0x0D:
                self.br_if(ins[1])
            elif op == 0x0C:
                self.br(ins[1])
            elif op == 0x05:
                self.else_()
            elif op == 0x10:
                self.call(ins[1])
            elif op == 0x23 or op == 0x24:
                g = ins[1]
                word, indirect = self.m.layout.global_(g), g < self.m.nimpg
                if op == 0x23:
                    self.need_tos()
                    b.ctx_load(tos, word, indirect)
                    push((TOS, None))
                else:
                    b.ctx_store(word, pop(), indirect)
            elif op == 0x1B:  # both values are computed already; 64-bit moves suit every type
                cond, x, y, w = self.pop_cond()
                e = pop()
                t = pop()
                self.need_tos()
                b.select_cmp(cond, x, y, w, tos, t, e, True)
                push((TOS, None))
            elif op == 0x1A:
                self.st.pop()  # every entry is a computed value or a compare: nothing can trap
            elif op == 0x0E:
                self.br_table(ins[1], ins[2])
            elif op == 0x0F:
                self.br(len(self.ctl) - 1)
            elif op == 0x43:
                push((CONST, f32_bits(ins[1])))
            elif op == 0x44:
                push((CONST, f64_bits(ins[1])))
            elif op == 0x00:
                b.trap(f, TrapCode.UNREACHABLE)
                self.dead = True
            elif op in REINTERPRET or op == 0x01:
                pass
            else:  # unsupported() already refused it
                raise AssertionError(f"opcode {op:#x}")
        assert not self.ctl
        b.func_end(f, L + self.maxh)
