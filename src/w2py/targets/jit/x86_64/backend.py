"""The x86-64 backend (docs/specs/S0002 D26, S0003). Step 3a: only the methods that carry the ABI:
function frames with both stack checks and the interrupt poll, traps returned through frames,
direct calls and host calls with a status test after each. The operations come in 3b.

A function: `endbr64`; push its pins (callee-saved for wasm) + pad to 16; check rsp against
NSTACK_LIMIT and r12 + 8 * frame_slots against VSTACK_LIMIT; poll INTERRUPT. A trap jumps to a
cold stub `mov eax, code; jmp unwind`; `unwind` is the epilogue, which keeps eax (R2: no rsp reset).
"""

import struct
from dataclasses import dataclass, field

from w2py.targets.base import TrapCode
from w2py.targets.jit.common.context import (
    HOST_BRIDGE,
    INTERRUPT,
    NSTACK_LIMIT,
    STATUS_INTERRUPT,
    VSTACK_LIMIT,
)
from w2py.targets.jit.x86_64.abi import MEM0
from w2py.targets.jit.x86_64.encoder import (
    R8,
    R9,
    R10,
    R11,
    R12,
    R13,
    R14,
    R15,
    RAX,
    RBP,
    RBX,
    RDI,
    RDX,
    RSI,
    RSP,
    Asm,
    Label,
    Mem,
    Reg,
)

POOL = (RBX, RBP, RSI, RDI, R8, R9, R10, R11)  # pinnable; never used by the trampolines' roles
STATUS_MISALIGNED = 255  # test builds only (`check_align`): a call site found rsp % 16 != 0


@dataclass
class Frame:
    pins: tuple[Reg, ...]
    pad: bool
    unwind: Label
    vcheck: int  # position of the disp32 of `lea rax, [r12 + 8 * frame_slots]`
    traps: dict[int, Label] = field(default_factory=lambda: {})


class Backend:
    def __init__(self, check_align: bool = False) -> None:
        self.a = Asm()
        self.check_align = check_align

    def new_label(self) -> Label:
        return self.a.label()

    def func_begin(self, label: Label, pins: tuple[Reg, ...] = ()) -> Frame:
        a = self.a
        assert all(r in POOL for r in pins)
        a.bind(label)
        a.endbr64()  # reached from the entry through a register (and from call_indirect later)
        for r in pins:
            a.push(r)
        pad = len(pins) % 2 == 0  # rsp = 8 mod 16 on entry
        if pad:
            a.alu_imm("sub", RSP, 8, w=True)
        f = Frame(pins, pad, a.label(), 0)
        a.alu("cmp", RSP, Mem(R15, 8 * NSTACK_LIMIT), w=True)
        a.jcc("b", self._stub(f, TrapCode.STACK_OVERFLOW))
        a.lea(RAX, Mem(R12, 1 << 20))  # disp32, patched by func_end
        f.vcheck = len(a.buf) - 4
        a.alu("cmp", RAX, Mem(R15, 8 * VSTACK_LIMIT), w=True)
        a.jcc("a", self._stub(f, TrapCode.STACK_OVERFLOW))
        self.poll(f)
        return f

    def func_end(self, f: Frame, frame_slots: int) -> None:
        """Patch the value-stack check, emit the cold trap stubs and the epilogue."""
        a = self.a
        struct.pack_into("<i", a.buf, f.vcheck, 8 * frame_slots)
        for code, label in f.traps.items():
            a.bind(label)
            a.mov_imm(RAX, code)
            a.jmp(f.unwind, short=True)
        a.bind(f.unwind)
        if f.pad:
            a.alu_imm("add", RSP, 8, w=True)
        for r in reversed(f.pins):
            a.pop(r)
        a.ret()

    def _stub(self, f: Frame, code: int) -> Label:
        if code not in f.traps:
            f.traps[code] = self.a.label()
        return f.traps[code]

    def poll(self, f: Frame) -> None:
        """At function entry and loop headers (D14, LESSONS 8)."""
        self.a.alu_imm("cmp", Mem(R15, 8 * INTERRUPT), 0, w=True)
        self.a.jcc("ne", self._stub(f, STATUS_INTERRUPT))

    def trap(self, f: Frame, code: TrapCode) -> None:
        self.a.jmp(self._stub(f, code))

    def ret_ok(self, f: Frame) -> None:
        self.a.alu("xor", RAX, RAX)
        self.a.jmp(f.unwind)

    def _call(self, f: Frame, target: Label | Mem) -> None:
        if self.check_align:
            self.a.test_imm(RSP, 15)
            self.a.jcc("ne", self._stub(f, STATUS_MISALIGNED))
        self.a.call(target)

    def call_direct(self, f: Frame, label: Label, shift_slots: int) -> None:
        """The callee's frame starts `shift_slots` above ours (frame overlap: its arguments are
        our slots there). A non-zero status unwinds this frame too."""
        a = self.a
        if shift_slots:
            a.lea(R12, Mem(R12, 8 * shift_slots))
        self._call(f, label)
        if shift_slots:
            a.lea(R12, Mem(R12, -8 * shift_slots))
        a.test(RAX, RAX)
        a.jcc("ne", f.unwind)

    def call_host(self, f: Frame, word: int, slot: int) -> None:
        """Call the host function (import or libcall) in context word `word`; its arguments and
        results are slots `slot`, `slot + 1`, ... of this frame."""
        a = self.a
        a.lea(RDX, Mem(R12, 8 * slot))
        a.mov_imm(RAX, word)
        self._call(f, Mem(R15, 8 * HOST_BRIDGE))
        a.mov(R13, Mem(R15, MEM0), w=True)  # memory.grow may have run
        a.mov(R14, Mem(R15, MEM0 + 8), w=True)
        a.test(RAX, RAX)
        a.jcc("ne", f.unwind)
