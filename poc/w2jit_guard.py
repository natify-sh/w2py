"""Experiment: guard pages instead of explicit bounds checks, with no native dependency.

The linear memory is a reservation of 8 GiB with no access (`mmap` PROT_NONE + MAP_NORESERVE); only
the wasm memory's pages are made read/write. Any wasm address (a zero-extended i32 plus a static
offset < 2 GiB) lands inside the reservation, so an access past the memory faults instead of being
checked. The SIGSEGV handler is **machine code emitted here** (Python cannot run inside a signal
handler) and installed with `sigaction` through ctypes. It checks that the faulting address is in
the reservation and the faulting instruction is in this module's code; if so it rewrites RIP in the
`ucontext` to the module's out-of-bounds trap stub and returns (the kernel resumes there; the stub
unwinds to the entry trampoline as every trap does). Anything else goes to the previous handler, or
restores the default action so that a real crash stays a crash.

Linux x86-64 only (offsets of `siginfo_t` and `ucontext_t`, the glibc `struct sigaction`).
Same idea as Wasmtime's signal-based traps; here the handler is a few dozen bytes of our own code.
"""
import ctypes, struct, mmap
from poc.w2jit import Asm, RAX, RCX, RDX, RSI, RDI, R10
from poc.w2jit2 import FuncJit2, JitModule2, R14, R13
import poc.w2jit2 as w2jit2

RESERVE = 8 << 30                       # covers 0xFFFFFFFF + any offset < 2 GiB + 8 bytes
SIGSEGV, SIGBUS = 11, 7
SA_SIGINFO, SA_ONSTACK, SA_RESTORER = 4, 0x08000000, 0x04000000
SI_ADDR = 16                            # siginfo_t.si_addr
UC_RIP = 40 + 16 * 8                    # ucontext_t.uc_mcontext.gregs[REG_RIP]

libc = ctypes.CDLL(None, use_errno=True)
libc.mmap.restype = ctypes.c_void_p
libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_long]
libc.mprotect.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]


class Sigaction(ctypes.Structure):      # glibc x86-64 layout
    _fields_ = [('handler', ctypes.c_void_p), ('mask', ctypes.c_uint64 * 16), ('flags', ctypes.c_int),
                ('restorer', ctypes.c_void_p)]


class FuncJitGuard(FuncJit2):
    def address(s, e, ei, off, size):
        """no check: the reservation and the handler catch what is past the memory"""
        if off > 0x7FFFFFFF:
            return FuncJit2.address(s, e, ei, off, size)
        a = s.a; o = s.opnd(e, ei)
        if o[0] == 'reg': idx = o[1]                                  # i32 values stay zero-extended
        elif o[0] == 'imm': a.mov_r_imm(RAX, o[1] & 0xFFFFFFFF); idx = RAX
        else: a.mov_r_m(RAX, w2jit2.R12, o[1]); idx = RAX
        return idx, off


class JitModuleGuard(JitModule2):
    FuncGen = FuncJitGuard

    def __init__(s, wasm_bytes, imports):
        JitModule2.__init__(s, wasm_bytes, imports)
        mn, mx = s.mod['mems'][0]
        size = max(mn, 1) * 65536
        base = libc.mmap(None, RESERVE, 0, mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS | mmap.MAP_NORESERVE, -1, 0)
        if base in (None, ctypes.c_void_p(-1).value): raise OSError(ctypes.get_errno(), 'reservation failed')
        if libc.mprotect(base, size, mmap.PROT_READ | mmap.PROT_WRITE): raise OSError(ctypes.get_errno(), 'mprotect')
        for off, d in s.mod['datas']:
            if off is not None: ctypes.memmove(base + off[1], bytes(d), len(d))
        s.mem = (ctypes.c_char * size).from_address(base)
        s.membase = base
        s._install_handler(base)

    def _install_handler(s, base):
        old = Sigaction()
        if libc.sigaction(SIGSEGV, None, ctypes.byref(old)): raise OSError(ctypes.get_errno(), 'sigaction')
        trap = s.cbase + s.a.labels[s.trap_label[2]]
        a = Asm(); notours = a.label()
        mov_imm64 = lambda r, v: a.mov_r_imm(r, v, w=1)
        # rdi = signal, rsi = siginfo_t *, rdx = ucontext_t *
        a.mov_r_m(RAX, RSI, SI_ADDR, w=1); mov_imm64(RCX, base)
        a.op_rr(b'\x29', RCX, RAX, w=1)                               # rax -= base
        mov_imm64(RCX, RESERVE); a.op_rr(b'\x39', RCX, RAX, w=1)      # cmp rax, RESERVE
        a.jcc('ae', notours)
        a.mov_r_m(RAX, RDX, UC_RIP, w=1); mov_imm64(RCX, s.cbase)
        a.op_rr(b'\x29', RCX, RAX, w=1)
        mov_imm64(RCX, s.code_size); a.op_rr(b'\x39', RCX, RAX, w=1)
        a.jcc('ae', notours)
        mov_imm64(RAX, trap); a.mov_m_r(RDX, UC_RIP, RAX, w=1)       # resume in the trap stub
        a.emit(0xC3)
        a.bind(notours)
        if old.handler not in (None, 0, 1):
            mov_imm64(RAX, old.handler); a.op_rr(b'\xff', 4, RAX)    # jmp: the previous handler
        else:
            # the default action: rt_sigaction(SIGSEGV, {SIG_DFL}, NULL, 8), then return and fault again
            s._dfl = (ctypes.c_uint64 * 4)(0, 0, 0, 0)
            a.mov_r_imm(RAX, 13); a.mov_r_imm(RDI, SIGSEGV)
            mov_imm64(RSI, ctypes.addressof(s._dfl)); a.op_rr(b'\x31', RDX, RDX)
            a.mov_r_imm(R10, 8); a.emit(0x0F, 0x05); a.emit(0xC3)
        a.resolve()
        code = bytes(a.b)
        s._hbuf = mmap.mmap(-1, mmap.PAGESIZE, prot=mmap.PROT_READ | mmap.PROT_WRITE | mmap.PROT_EXEC)
        s._hbuf.write(code)
        h = ctypes.addressof(ctypes.c_char.from_buffer(s._hbuf))
        act = Sigaction(); act.handler = h; act.flags = SA_SIGINFO | SA_ONSTACK
        if libc.sigaction(SIGSEGV, ctypes.byref(act), None): raise OSError(ctypes.get_errno(), 'sigaction')
        s.handler_size = len(code)
