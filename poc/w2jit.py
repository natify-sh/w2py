"""w2jit: a stdlib-only WebAssembly JIT for CPython on x86-64 System V (Linux/macOS-Intel) — PoC.

No compiler, no C extension: machine code is assembled in Python, placed in an mmap'ed executable
page and called through ctypes.

Execution model (baseline, single pass, wasm3-style frame overlap):
  r12  value-stack frame base: slot k of the current function = qword [r12 + 8k];
       locals are slots 0..L-1, the operand stack at height h is slot L+h.
       A call with n args at height h moves r12 to slot L+h-n: the caller's arguments *are* the
       callee's first locals, and the callee's result lands in its slot 0 = the caller's slot L+h-n.
  r13  linear memory base,  r14  linear memory size (bytes),  r15  context (ctypes u64 array):
       [0] saved rsp (trap unwind)  [1] trap code  [2] memory size  [3] value-stack limit
       [4..] globals, then host function pointers.
Compile-time stack entries are lazy: constants and local reads are folded into instruction
operands, a compare feeding br_if/select/eqz becomes a flags test (fused cmp+jcc).
Memory accesses are bounds-checked explicitly (guard pages can't be used from Python).
"""
import ctypes, mmap, struct, sys
from w2py import parse, decode, I32, I64, F32, F64

RAX, RCX, RDX, RBX, RSP, RBP, RSI, RDI = range(8)
R8, R9, R10, R11, R12, R13, R14, R15 = range(8, 16)
CTX_SAVED_RSP, CTX_TRAP, CTX_MEMSIZE, CTX_VSLIMIT, CTX_GLOBALS = 0, 8, 16, 24, 32
TRAPS = {1: 'unreachable', 2: 'out of bounds memory access', 3: 'integer divide by zero',
         4: 'integer overflow', 5: 'call stack exhausted', 6: 'invalid conversion to integer'}
CC = {'o': 0, 'no': 1, 'b': 2, 'ae': 3, 'e': 4, 'ne': 5, 'be': 6, 'a': 7, 's': 8, 'ns': 9,
      'p': 10, 'np': 11, 'l': 12, 'ge': 13, 'le': 14, 'g': 15}
INV = {'e': 'ne', 'ne': 'e', 'l': 'ge', 'ge': 'l', 'g': 'le', 'le': 'g', 'b': 'ae', 'ae': 'b',
       'a': 'be', 'be': 'a'}


class Trap(Exception):
    pass


# ============================================================================ assembler
class Asm:
    def __init__(s):
        s.b = bytearray(); s.labels = {}; s.fix = []; s.nl = 0

    def label(s):
        s.nl += 1; return s.nl

    def bind(s, l): s.labels[l] = len(s.b)

    def emit(s, *bs):
        for x in bs: s.b += x if isinstance(x, (bytes, bytearray)) else bytes([x])

    def rex(s, w=0, r=0, x=0, b=0, force=False):
        v = 0x40 | (8 if w else 0) | (4 if r & 8 else 0) | (2 if x & 8 else 0) | (1 if b & 8 else 0)
        if v != 0x40 or force: s.b.append(v)

    def mem(s, reg, base, disp=0, index=None, scale=1):
        """ModRM(+SIB)(+disp) for [base + index*scale + disp]"""
        reg &= 7
        if disp == 0 and (base & 7) != 5: mod, dsz = 0, 0
        elif -128 <= disp < 128: mod, dsz = 1, 1
        else: mod, dsz = 2, 4
        if index is not None or (base & 7) == 4:
            idx = 4 if index is None else index & 7
            s.b += bytes([(mod << 6) | (reg << 3) | 4, ({1: 0, 2: 1, 4: 2, 8: 3}[scale] << 6) | (idx << 3) | (base & 7)])
        else:
            s.b.append((mod << 6) | (reg << 3) | (base & 7))
        if dsz == 1: s.b += struct.pack('<b', disp)
        elif dsz == 4: s.b += struct.pack('<i', disp)

    def op_rm(s, opc, reg, base, disp=0, w=0, index=None, scale=1, pre=b''):
        s.b += pre
        s.rex(w, reg, index or 0, base)
        s.b += opc
        s.mem(reg, base, disp, index, scale)

    def op_rr(s, opc, reg, rm, w=0, pre=b''):
        s.b += pre
        s.rex(w, reg, 0, rm)
        s.b += opc
        s.b.append(0xC0 | ((reg & 7) << 3) | (rm & 7))

    # --- common instructions
    def mov_r_m(s, r, base, disp, w=0): s.op_rm(b'\x8b', r, base, disp, w)
    def mov_m_r(s, base, disp, r, w=0): s.op_rm(b'\x89', r, base, disp, w)
    def mov_rr(s, dst, src, w=0): s.op_rr(b'\x89', src, dst, w)

    def mov_r_imm(s, r, v, w=0):
        if not w or 0 <= v < (1 << 32):
            s.rex(0, 0, 0, r); s.b.append(0xB8 + (r & 7)); s.b += struct.pack('<I', v & 0xFFFFFFFF)
        elif -(1 << 31) <= v < (1 << 31):
            s.rex(1, 0, 0, r); s.b += b'\xc7'; s.b.append(0xC0 | (r & 7)); s.b += struct.pack('<i', v)
        else:
            s.rex(1, 0, 0, r); s.b.append(0xB8 + (r & 7)); s.b += struct.pack('<Q', v & ((1 << 64) - 1))

    def mov_m_imm32(s, base, disp, v, w=0):
        s.op_rm(b'\xc7', 0, base, disp, w); s.b += struct.pack('<I', v & 0xFFFFFFFF)

    def alu_r_m(s, opc_rm, r, base, disp, w=0): s.op_rm(bytes([opc_rm]), r, base, disp, w)

    def alu_r_imm(s, ext, r, v, w=0):
        if -128 <= v < 128:
            s.rex(w, 0, 0, r); s.b += b'\x83'; s.b.append(0xC0 | (ext << 3) | (r & 7)); s.b += struct.pack('<b', v)
        else:
            s.rex(w, 0, 0, r); s.b += b'\x81'; s.b.append(0xC0 | (ext << 3) | (r & 7)); s.b += struct.pack('<I', v & 0xFFFFFFFF)

    def jmp(s, l):
        s.b += b'\xe9'; s.fix.append((len(s.b), l)); s.b += b'\0\0\0\0'

    def jcc(s, cc, l):
        s.b += bytes([0x0F, 0x80 | CC[cc]]); s.fix.append((len(s.b), l)); s.b += b'\0\0\0\0'

    def call(s, l):
        s.b += b'\xe8'; s.fix.append((len(s.b), l)); s.b += b'\0\0\0\0'

    def setcc(s, cc, r=RAX): s.rex(0, 0, 0, r, force=r >= 4); s.b += bytes([0x0F, 0x90 | CC[cc], 0xC0 | (r & 7)])
    def movzx_r_r8(s, dst, src): s.op_rr(b'\x0f\xb6', dst, src)

    def resolve(s):
        for f in s.fix:
            pos, l = f[0], f[1]
            base = s.labels[f[2]] if len(f) > 2 else pos + 4   # jump-table entries: relative to the table
            s.b[pos:pos + 4] = struct.pack('<i', s.labels[l] - base)


# ============================================================================ code generator
class Ent:
    """compile-time stack entry: kind 'slot' (value in its own slot), 'const', 'local', 'flags'"""
    __slots__ = ('k', 't', 'v')

    def __init__(s, k, t, v=None): s.k, s.t, s.v = k, t, v


def wide(t): return t in (I64, F64)


class Ctl:
    __slots__ = ('kind', 'h', 'arity', 'label', 'reach_end', 'tgt_used')

    def __init__(s, kind, h, arity, label):
        s.kind, s.h, s.arity, s.label = kind, h, arity, label
        s.tgt_used = False


class FuncJit:
    def __init__(s, jm, a, fidx, tidx, locs, body):
        s.jm, s.a, s.fidx = jm, a, fidx
        s.params, s.results = jm.types[tidx]
        s.ltypes = list(s.params) + list(locs)
        s.L = len(s.ltypes)
        s.ins = decode(body)
        s.st = []; s.ctl = []; s.dead = False; s.maxh = 0

    # --- slots / operands
    def sd(s, slot): return 8 * slot                          # displacement off r12
    def hslot(s, i): return s.L + i                           # slot of stack height index i

    def push(s, e):
        s.st.append(e)
        if len(s.st) > s.maxh: s.maxh = len(s.st)

    def materialize(s, i):
        e = s.st[i]
        if e.k == 'slot': return
        a, d = s.a, s.sd(s.hslot(i))
        if e.k == 'const':
            if wide(e.t):
                if -(1 << 31) <= e.v < (1 << 31): a.mov_m_imm32(R12, d, e.v, w=1)
                else: a.mov_r_imm(RAX, e.v, w=1); a.mov_m_r(R12, d, RAX, w=1)
            else:
                a.mov_m_imm32(R12, d, e.v)
        elif e.k == 'local':
            a.mov_r_m(RAX, R12, s.sd(e.v), w=1); a.mov_m_r(R12, d, RAX, w=1)
        elif e.k == 'flags':
            a.setcc(e.v, RAX); a.movzx_r_r8(RAX, RAX); a.mov_m_r(R12, d, RAX)
        s.st[i] = Ent('slot', e.t)

    def flush(s, pred=lambda e: True):
        for i, e in enumerate(s.st):
            if e.k != 'slot' and pred(e): s.materialize(i)

    def pop(s):
        if s.st[-1].k == 'flags': s.materialize(len(s.st) - 1)
        return s.st.pop()

    def ld(s, r, e, i, w=0):
        """load stack entry e (at height index i) into register r"""
        a = s.a
        if e.k == 'const': a.mov_r_imm(r, e.v, w=w)
        elif e.k == 'local': a.mov_r_m(r, R12, s.sd(e.v), w=w)
        else: a.mov_r_m(r, R12, s.sd(s.hslot(i)), w=w)

    def operand_mem(s, e, i):
        """(base, disp) memory operand for a non-const entry"""
        return (R12, s.sd(e.v)) if e.k == 'local' else (R12, s.sd(s.hslot(i)))

    def store_res(s, r, w=0):
        """push the value in register r as a new stack slot"""
        i = len(s.st)
        s.a.mov_m_r(R12, s.sd(s.hslot(i)), r, w=w)
        s.push(Ent('slot', I64 if w else I32))

    # --- control
    def bt(s, b):
        return s.jm.types[b[1]] if b[0] == 'idx' else b

    def br_moves(s, T):
        """copy the top T.arity values into the target's result slots"""
        n = T.arity
        if n == 0: return
        src0 = len(s.st) - n
        for j in range(n):
            e = s.st[src0 + j]
            dst = s.hslot(T.h + j)
            if e.k == 'slot' and src0 + j == T.h + j: continue
            if e.k == 'const':
                if wide(e.t): s.a.mov_r_imm(RAX, e.v, w=1); s.a.mov_m_r(R12, s.sd(dst), RAX, w=1)
                else: s.a.mov_m_imm32(R12, s.sd(dst), e.v)
            elif e.k == 'flags':
                s.a.setcc(e.v); s.a.movzx_r_r8(RAX, RAX); s.a.mov_m_r(R12, s.sd(dst), RAX)
            else:
                base, disp = s.operand_mem(e, src0 + j)
                s.a.mov_r_m(RAX, base, disp, w=1); s.a.mov_m_r(R12, s.sd(dst), RAX, w=1)

    def needs_moves(s, T):
        n = T.arity
        if n == 0: return False
        src0 = len(s.st) - n
        return not all(s.st[src0 + j].k == 'slot' and src0 + j == T.h + j for j in range(n))

    def emit_return(s):
        n = len(s.results)
        if n:
            e = s.st[-1]; i = len(s.st) - 1
            if e.k == 'flags':
                s.a.setcc(e.v); s.a.movzx_r_r8(RAX, RAX); s.a.mov_m_r(R12, 0, RAX)
            elif e.k == 'const':
                if wide(e.t): s.a.mov_r_imm(RAX, e.v, w=1); s.a.mov_m_r(R12, 0, RAX, w=1)
                else: s.a.mov_m_imm32(R12, 0, e.v)
            else:
                base, disp = s.operand_mem(e, i)
                if not (base == R12 and disp == 0):
                    s.a.mov_r_m(RAX, base, disp, w=1); s.a.mov_m_r(R12, 0, RAX, w=1)
        s.a.alu_r_imm(0, RSP, 8, w=1)                              # undo the prologue's alignment
        s.a.b.append(0xC3)

    def br(s, depth):
        T = s.ctl[-1 - depth]
        if T.kind == 'func': s.emit_return(); return
        s.flush()
        if T.kind != 'loop': s.br_moves(T)
        T.tgt_used = True
        s.a.jmp(T.label)

    # --- main
    def gen(s):
        a, jm = s.a, s.jm
        a.bind(jm.flabel[s.fidx])
        a.alu_r_imm(5, RSP, 8, w=1)                                # rsp 16-aligned inside the body (SysV)
        # value-stack overflow check (patched with the final frame size below)
        a.op_rm(b'\x8d', RAX, R12, 0x7FFFFFF0, w=1)               # lea rax, [r12 + framebytes]
        patch_at = len(a.b) - 4
        a.alu_r_m(0x3B, RAX, R15, CTX_VSLIMIT, w=1)                # cmp rax, [r15+limit]
        a.jcc('a', jm.trap_label[5])
        for j in range(len(s.params), s.L):                        # zero the non-parameter locals
            a.mov_m_imm32(R12, s.sd(j), 0, w=1)
        s.ctl.append(Ctl('func', 0, len(s.results), None))
        ins = s.ins; i = 0; N = len(ins); deadd = 0
        while i < N:
            x = ins[i]; op = x[0]; i += 1
            if s.dead:
                if op in (0x02, 0x03, 0x04): deadd += 1; continue
                if deadd:
                    if op == 0x0B: deadd -= 1
                    continue
                if op not in (0x0B, 0x05): continue
            if s.st and s.st[-1].k == 'flags' and op not in (0x0D, 0x45, 0x1B, 0x0B, 0x0F, 0x0C):
                s.materialize(len(s.st) - 1)
            if op == 0x0B:
                if len(s.ctl) == 1:
                    if not s.dead: s.emit_return()
                    break
                s.end_block(); continue
            if op in (0x02, 0x03):
                ps, rs = s.bt(x[1])
                if ps: raise NotImplementedError('block params')
                s.flush()
                T = Ctl('block' if op == 0x02 else 'loop', len(s.st), len(rs), a.label())
                s.ctl.append(T)
                if op == 0x03: a.bind(T.label)
                continue
            if op == 0x04: raise NotImplementedError('if/else not needed for this PoC')
            if op == 0x0C: s.br(x[1]); s.dead = True; continue
            if op == 0x0D: s.br_if(x[1]); continue
            if op == 0x0E: s.br_table(x[1], x[2]); s.dead = True; continue
            if op == 0x0F: s.flush(); s.emit_return(); s.dead = True; continue
            if op == 0x00: a.jmp(jm.trap_label[1]); s.dead = True; continue
            if op == 0x01: continue
            s.simple(x)
        # patch the frame size of the overflow check
        fb = 8 * (s.L + s.maxh + 1)
        a.b[patch_at:patch_at + 4] = struct.pack('<i', fb)

    def end_block(s):
        T = s.ctl.pop()
        if not s.dead:
            s.flush()
            if T.kind != 'loop': s.br_moves(T)
        del s.st[T.h:]
        if T.kind != 'loop':
            s.a.bind(T.label)
            reach = (not s.dead) or T.tgt_used
        else:
            reach = not s.dead
        for j in range(T.arity):
            s.push(Ent('slot', I64))
        s.dead = not reach

    def br_if(s, depth):
        T = s.ctl[-1 - depth]
        c = s.st.pop(); ci = len(s.st)
        a = s.a
        if c.k == 'flags': cc = c.v
        else:
            if c.k == 'const':
                if c.v: s.br(depth); s.dead = True
                return
            base, disp = s.operand_mem(c, ci)
            a.op_rm(b'\x83', 7, base, disp); a.b.append(0)      # cmp dword [m], 0
            cc = 'ne'
        s.flush()                                               # movs only: flags survive
        if T.kind == 'func':
            skip = a.label(); a.jcc(INV[cc], skip); s.emit_return(); a.bind(skip); return
        if T.kind != 'loop' and s.needs_moves(T):
            skip = a.label(); a.jcc(INV[cc], skip); s.br_moves(T); a.jmp(T.label); a.bind(skip)
        else:
            a.jcc(cc, T.label)
        T.tgt_used = True

    def br_table(s, tbl, default):
        e = s.pop(); ei = len(s.st)
        s.flush()
        s.ld(RCX, e, ei)
        a = s.a
        for k, d in enumerate(tbl):
            if d == default: continue
            nxt = a.label()
            a.alu_r_imm(7, RCX, k)                                # cmp ecx, k
            a.jcc('ne', nxt)
            s.br(d)
            a.bind(nxt)
        s.br(default)

    # --- straight-line instructions
    def simple(s, x):
        op = x[0]; a = s.a; jm = s.jm
        if op == 0x1A: s.st.pop(); return
        if op == 0x20: s.push(Ent('local', s.ltypes[x[1]], x[1])); return
        if op in (0x21, 0x22):
            j = x[1]
            s.flush(lambda e: e.k == 'local' and e.v == j)
            e = s.pop(); ei = len(s.st)
            d = s.sd(j)
            if e.k == 'const':
                if wide(e.t): a.mov_r_imm(RAX, e.v, w=1); a.mov_m_r(R12, d, RAX, w=1)
                else: a.mov_m_imm32(R12, d, e.v)
            else:
                base, disp = s.operand_mem(e, ei)
                if not (base == R12 and disp == d):
                    a.mov_r_m(RAX, base, disp, w=1); a.mov_m_r(R12, d, RAX, w=1)
            if op == 0x22: s.push(Ent('local', s.ltypes[j], j))
            return
        if op == 0x23:
            g = CTX_GLOBALS + 8 * x[1]
            a.mov_r_m(RAX, R15, g, w=1); s.store_res(RAX, w=1); s.st[-1].t = jm.gtypes[x[1]]; return
        if op == 0x24:
            e = s.pop(); ei = len(s.st)
            s.ld(RAX, e, ei, w=1); a.mov_m_r(R15, CTX_GLOBALS + 8 * x[1], RAX, w=1); return
        if op == 0x41: s.push(Ent('const', I32, x[1])); return
        if op == 0x42:
            v = x[1] - (1 << 64) if x[1] >= (1 << 63) else x[1]; s.push(Ent('const', I64, v)); return
        if op == 0x44:
            v = struct.unpack('<q', struct.pack('<d', x[1]))[0]; s.push(Ent('const', F64, v)); return
        if 0x28 <= op <= 0x35: s.load(op, x[1]); return
        if 0x36 <= op <= 0x3E: s.store(op, x[1]); return
        if op == 0x10: s.call(x[1]); return
        if op == 0x1B: s.select(); return
        if op == 0x45:
            e = s.st.pop(); ei = len(s.st)
            if e.k == 'flags': s.push(Ent('flags', I32, INV[e.v])); return
            if e.k == 'const': s.push(Ent('const', I32, 1 if e.v == 0 else 0)); return
            base, disp = s.operand_mem(e, ei)
            a.op_rm(b'\x83', 7, base, disp); a.b.append(0)
            s.push(Ent('flags', I32, 'e')); return
        if 0x46 <= op <= 0x4F:
            cc = ['e', 'ne', 'l', 'b', 'g', 'a', 'le', 'be', 'ge', 'ae'][op - 0x46]
            b = s.pop(); bi = len(s.st); e = s.pop(); ei = len(s.st)
            s.ld(RAX, e, ei)
            if b.k == 'const': a.alu_r_imm(7, RAX, b.v if b.v < (1 << 31) else b.v - (1 << 32))
            else:
                base, disp = s.operand_mem(b, bi); a.alu_r_m(0x3B, RAX, base, disp)
            s.push(Ent('flags', I32, cc)); return
        if op in (0x6A, 0x6B, 0x71, 0x72, 0x73):
            ext, opc = {0x6A: (0, 0x03), 0x6B: (5, 0x2B), 0x71: (4, 0x23), 0x72: (1, 0x0B), 0x73: (6, 0x33)}[op]
            b = s.pop(); bi = len(s.st); e = s.pop(); ei = len(s.st)
            s.ld(RAX, e, ei)
            if b.k == 'const': a.alu_r_imm(ext, RAX, b.v if b.v < (1 << 31) else b.v - (1 << 32))
            else:
                base, disp = s.operand_mem(b, bi); a.alu_r_m(opc, RAX, base, disp)
            s.store_res(RAX); return
        if op == 0x6C:
            b = s.pop(); bi = len(s.st); e = s.pop(); ei = len(s.st)
            s.ld(RAX, e, ei)
            if b.k == 'const':
                a.op_rr(b'\x69', RAX, RAX); a.b += struct.pack('<I', b.v & 0xFFFFFFFF)
            else:
                base, disp = s.operand_mem(b, bi); a.op_rm(b'\x0f\xaf', RAX, base, disp)
            s.store_res(RAX); return
        if op in (0x6D, 0x6E, 0x6F, 0x70):
            b = s.pop(); bi = len(s.st); e = s.pop(); ei = len(s.st)
            s.ld(RAX, e, ei); s.ld(RCX, b, bi)
            a.op_rr(b'\x85', RCX, RCX); a.jcc('e', jm.trap_label[3])        # test ecx,ecx; jz trap
            if op in (0x6E, 0x70):
                a.op_rr(b'\x31', RDX, RDX); a.op_rr(b'\xf7', 6, RCX)          # xor edx,edx; div ecx
                s.store_res(RAX if op == 0x6E else RDX); return
            if op == 0x6D:
                ok = a.label(); a.alu_r_imm(7, RCX, -1); a.jcc('ne', ok)
                a.alu_r_imm(7, RAX, -0x80000000); a.jcc('e', jm.trap_label[4]); a.bind(ok)
                a.b.append(0x99); a.op_rr(b'\xf7', 7, RCX); s.store_res(RAX); return   # cdq; idiv ecx
            # rem_s: x % -1 == 0 (avoids the idiv overflow fault)
            m1, done = a.label(), a.label()
            a.alu_r_imm(7, RCX, -1); a.jcc('e', m1)
            a.b.append(0x99); a.op_rr(b'\xf7', 7, RCX); a.jmp(done)
            a.bind(m1); a.op_rr(b'\x31', RDX, RDX); a.bind(done)
            s.store_res(RDX); return
        if op in (0x74, 0x75, 0x76, 0x77, 0x78):
            ext = {0x74: 4, 0x75: 7, 0x76: 5, 0x77: 0, 0x78: 1}[op]
            b = s.pop(); bi = len(s.st); e = s.pop(); ei = len(s.st)
            s.ld(RAX, e, ei)
            if b.k == 'const':
                a.rex(0, 0, 0, RAX); a.b += bytes([0xC1, 0xC0 | (ext << 3)]); a.b.append(b.v & 31)
            else:
                s.ld(RCX, b, bi); a.b += bytes([0xD3, 0xC0 | (ext << 3)])
            s.store_res(RAX); return
        if op in (0xC0, 0xC1):
            e = s.pop(); ei = len(s.st)
            if e.k == 'const':
                bits = 8 if op == 0xC0 else 16; v = e.v & ((1 << bits) - 1)
                if v >> (bits - 1): v -= 1 << bits
                s.push(Ent('const', I32, v & 0xFFFFFFFF)); return
            base, disp = s.operand_mem(e, ei)
            a.op_rm(b'\x0f\xbe' if op == 0xC0 else b'\x0f\xbf', RAX, base, disp)
            s.store_res(RAX); return
        if s.f64op(op): return
        raise NotImplementedError('opcode 0x%x' % op)

    def addr(s, off, size):
        """pop an i32 address, leave the effective offset in rax (bounds-checked)"""
        a = s.a
        e = s.pop(); ei = len(s.st)
        if e.k == 'const':
            a.mov_r_imm(RAX, (e.v + off), w=1)
        else:
            s.ld(RAX, e, ei)                                         # 32-bit load zero-extends
            if off: a.alu_r_imm(0, RAX, off, w=1)
        a.op_rm(b'\x8d', RDX, RAX, size, w=1)                         # lea rdx, [rax+size]
        a.op_rr(b'\x39', R14, RDX, w=1)                               # cmp rdx, r14
        a.jcc('a', s.jm.trap_label[2])

    def load(s, op, off):
        a = s.a
        size = {0x28: 4, 0x29: 8, 0x2A: 4, 0x2B: 8, 0x2C: 1, 0x2D: 1, 0x2E: 2, 0x2F: 2,
                0x30: 1, 0x31: 1, 0x32: 2, 0x33: 2, 0x34: 4, 0x35: 4}[op]
        s.addr(off, size)
        if op in (0x28, 0x2A): a.op_rm(b'\x8b', RAX, R13, 0, index=RAX); s.store_res(RAX)
        elif op in (0x29, 0x2B): a.op_rm(b'\x8b', RAX, R13, 0, w=1, index=RAX); s.store_res(RAX, w=1)
        elif op == 0x2D: a.op_rm(b'\x0f\xb6', RAX, R13, 0, index=RAX); s.store_res(RAX)
        elif op == 0x2C: a.op_rm(b'\x0f\xbe', RAX, R13, 0, index=RAX); s.store_res(RAX)
        elif op == 0x2F: a.op_rm(b'\x0f\xb7', RAX, R13, 0, index=RAX); s.store_res(RAX)
        elif op == 0x2E: a.op_rm(b'\x0f\xbf', RAX, R13, 0, index=RAX); s.store_res(RAX)
        else: raise NotImplementedError('load 0x%x' % op)
        if op in (0x2A, 0x2B): s.st[-1].t = F32 if op == 0x2A else F64

    def store(s, op, off):
        a = s.a
        v = s.pop(); vi = len(s.st)
        w = op in (0x37, 0x39)
        s.ld(RCX, v, vi, w=1 if w else 0)
        size = {0x36: 4, 0x37: 8, 0x38: 4, 0x39: 8, 0x3A: 1, 0x3B: 2, 0x3C: 1, 0x3D: 2, 0x3E: 4}[op]
        s.addr(off, size)
        if size == 1: a.op_rm(b'\x88', RCX, R13, 0, index=RAX)
        elif size == 2: a.op_rm(b'\x89', RCX, R13, 0, index=RAX, pre=b'\x66')
        else: a.op_rm(b'\x89', RCX, R13, 0, index=RAX, w=(size == 8))

    def select(s):
        a = s.a
        c = s.st.pop(); b = s.pop(); bi = len(s.st); e = s.pop(); ei = len(s.st)
        s.ld(RAX, e, ei, w=1); s.ld(RDX, b, bi, w=1)                # movs keep flags
        if c.k == 'flags': cc = INV[c.v]
        else:
            if c.k == 'const':
                if not c.v: a.mov_rr(RAX, RDX, w=1)
                s.store_res(RAX, w=1); s.st[-1].t = e.t; return
            base, disp = s.operand_mem(c, len(s.st) + 2)
            a.op_rm(b'\x83', 7, base, disp); a.b.append(0); cc = 'e'
        a.op_rr(bytes([0x0F, 0x40 | CC[cc]]), RAX, RDX, w=1)           # cmovcc rax, rdx
        s.store_res(RAX, w=1); s.st[-1].t = e.t

    def call(s, fi):
        a, jm = s.a, s.jm
        ps, rs = jm.ftype(fi)
        n = len(ps)
        s.flush()
        h = len(s.st)
        base_slot = s.hslot(h - n)
        if fi < jm.nimp:  # host import: SysV call through the context table
            regs = [RDI, RSI, RDX, RCX, R8, R9]
            for j in range(n):
                a.mov_r_m(regs[j], R12, s.sd(base_slot + j), w=1)
            a.op_rm(b'\xff', 2, R15, jm.host_off + 8 * fi)            # call [r15 + host_i] (rsp is aligned)
            del s.st[h - n:]
            if rs: s.store_res(RAX)
            return
        if base_slot: a.alu_r_imm(0, R12, 8 * base_slot, w=1)        # r12 -> callee frame
        a.call(jm.flabel[fi])
        if base_slot: a.alu_r_imm(5, R12, 8 * base_slot, w=1)
        del s.st[h - n:]
        for t in rs: s.push(Ent('slot', t))

    def f64op(s, op):
        a = s.a
        def xmm_ld(xr, e, ei):
            if e.k == 'const':
                a.mov_r_imm(RAX, e.v, w=1); a.op_rr(b'\x0f\x6e', xr, RAX, w=1, pre=b'\x66')   # movq xmm, rax
            else:
                base, disp = s.operand_mem(e, ei); a.op_rm(b'\x0f\x10', xr, base, disp, pre=b'\xf2')
        def xmm_res(xr):
            i = len(s.st); a.op_rm(b'\x0f\x11', xr, R12, s.sd(s.hslot(i)), pre=b'\xf2')
            s.push(Ent('slot', F64))
        if op in (0xA0, 0xA1, 0xA2, 0xA3):
            b = s.pop(); bi = len(s.st); e = s.pop(); ei = len(s.st)
            xmm_ld(0, e, ei); xmm_ld(1, b, bi)
            a.op_rr(bytes([0x0F, {0xA0: 0x58, 0xA1: 0x5C, 0xA2: 0x59, 0xA3: 0x5E}[op]]), 0, 1, pre=b'\xf2')
            xmm_res(0); return True
        if op == 0x9A:
            e = s.pop(); ei = len(s.st); s.ld(RAX, e, ei, w=1)
            a.rex(1, 0, 0, RAX); a.b += bytes([0x0F, 0xBA, 0xF8, 63])                   # btc rax, 63
            s.store_res(RAX, w=1); s.st[-1].t = F64; return True
        if op == 0x9D:
            e = s.pop(); ei = len(s.st); xmm_ld(0, e, ei)
            a.op_rr(b'\x0f\x3a\x0b', 0, 0, pre=b'\x66'); a.b.append(3)                  # roundsd xmm0, xmm0, 3
            xmm_res(0); return True
        if op in (0x61, 0x62, 0x63, 0x64, 0x65, 0x66):
            b = s.pop(); bi = len(s.st); e = s.pop(); ei = len(s.st)
            if op in (0x63, 0x65): xmm_ld(0, b, bi); xmm_ld(1, e, ei)                   # b ? a
            else: xmm_ld(0, e, ei); xmm_ld(1, b, bi)
            a.op_rr(b'\x0f\x2e', 0, 1, pre=b'\x66')                                    # ucomisd xmm0, xmm1
            if op == 0x61: a.setcc('e', RAX); a.setcc('np', RCX); a.op_rr(b'\x20', RCX, RAX)  # and al, cl
            elif op == 0x62: a.setcc('ne', RAX); a.setcc('p', RCX); a.op_rr(b'\x08', RCX, RAX)
            else: a.setcc('a' if op in (0x63, 0x64) else 'ae', RAX)
            a.movzx_r_r8(RAX, RAX); s.store_res(RAX); return True
        if op == 0xB8:  # f64.convert_i32_u
            e = s.pop(); ei = len(s.st); s.ld(RAX, e, ei)
            a.op_rr(b'\x0f\x2a', 0, RAX, w=1, pre=b'\xf2')                              # cvtsi2sd xmm0, rax
            xmm_res(0); return True
        if op == 0xB7:  # f64.convert_i32_s
            e = s.pop(); ei = len(s.st); s.ld(RAX, e, ei)
            a.op_rr(b'\x63', RAX, RAX, w=1)                                            # movsxd rax, eax
            a.op_rr(b'\x0f\x2a', 0, RAX, w=1, pre=b'\xf2'); xmm_res(0); return True
        if op in (0xFC02, 0xFC03):  # i32.trunc_sat_f64_s / _u
            e = s.pop(); ei = len(s.st); xmm_ld(0, e, ei)
            nan, inr, done = a.label(), a.label(), a.label()
            a.op_rr(b'\x0f\x2e', 0, 0, pre=b'\x66'); a.jcc('p', nan)                   # NaN -> 0
            a.op_rr(b'\x0f\x2c', RAX, 0, w=1, pre=b'\xf2')                              # cvttsd2si rax, xmm0
            a.mov_r_imm(RDX, -(1 << 63), w=1); a.op_rr(b'\x39', RDX, RAX, w=1); a.jcc('ne', inr)
            a.op_rr(b'\x0f\x7e', 0, RCX, w=1, pre=b'\x66')                              # movq rcx, xmm0
            a.op_rr(b'\x85', RCX, RCX, w=1); a.jcc('s', inr)
            a.mov_r_imm(RAX, (1 << 63) - 1, w=1)
            a.bind(inr)
            lo, hi = ((-(1 << 31), (1 << 31) - 1) if op == 0xFC02 else (0, (1 << 32) - 1))
            k1, k2 = a.label(), a.label()
            a.mov_r_imm(RCX, lo, w=1); a.op_rr(b'\x39', RCX, RAX, w=1); a.jcc('ge', k1); a.mov_rr(RAX, RCX, w=1); a.bind(k1)
            a.mov_r_imm(RCX, hi, w=1); a.op_rr(b'\x39', RCX, RAX, w=1); a.jcc('le', k2); a.mov_rr(RAX, RCX, w=1); a.bind(k2)
            a.jmp(done)
            a.bind(nan); a.op_rr(b'\x31', RAX, RAX)
            a.bind(done); s.store_res(RAX); return True
        return False


# ============================================================================ module
class JitModule:
    VS_SIZE = 64 << 20
    FuncGen = FuncJit

    def __init__(s, wasm_bytes, imports):
        mod = parse(wasm_bytes)
        s.mod = mod; s.types = mod['types']
        s.fimports = [im for im in mod['imports'] if im[0] == 'func']
        s.nimp = len(s.fimports)
        s.gtypes = [g[0] for g in mod['globals']]
        a = s.a = Asm()
        nf = s.nimp + len(mod['codes'])
        s.flabel = {fi: a.label() for fi in range(s.nimp, nf)}
        s.trap_label = {k: a.label() for k in TRAPS}
        s.host_off = CTX_GLOBALS + 8 * len(s.gtypes)
        # entry trampoline: int entry(ctx, frame, membase, fn)
        a.emit(0x53, 0x55, b'\x41\x54', b'\x41\x55', b'\x41\x56', b'\x41\x57')      # push rbx,rbp,r12-r15
        a.alu_r_imm(5, RSP, 8, w=1)
        a.mov_rr(R15, RDI, w=1); a.mov_rr(R12, RSI, w=1); a.mov_rr(R13, RDX, w=1)
        a.mov_r_m(R14, R15, CTX_MEMSIZE, w=1)
        a.mov_m_r(R15, CTX_SAVED_RSP, RSP, w=1)
        a.op_rr(b'\xff', 2, RCX)                                                    # call rcx
        a.alu_r_imm(0, RSP, 8, w=1)
        a.op_rr(b'\x31', RAX, RAX)
        epi = len(a.b)
        a.emit(b'\x41\x5f', b'\x41\x5e', b'\x41\x5d', b'\x41\x5c', 0x5D, 0x5B, 0xC3)
        # trap stubs: record code, unwind to the entry frame, return the code
        unwind = a.label()
        for k, l in s.trap_label.items():
            a.bind(l); a.mov_m_imm32(R15, CTX_TRAP, k, w=1); a.mov_r_imm(RAX, k); a.jmp(unwind)
        a.bind(unwind)
        a.mov_r_m(RSP, R15, CTX_SAVED_RSP, w=1); a.alu_r_imm(0, RSP, 8, w=1)
        a.emit(b'\x41\x5f', b'\x41\x5e', b'\x41\x5d', b'\x41\x5c', 0x5D, 0x5B, 0xC3)
        for k, ((locs, body), tidx) in enumerate(zip(mod['codes'], mod['funcs'])):
            s.FuncGen(s, a, s.nimp + k, tidx, locs, body).gen()
        a.resolve()
        code = bytes(a.b)
        s.code_size = len(code)
        # executable memory (W^X: write, then flip to read+exec)
        s.cbuf = mmap.mmap(-1, (len(code) + mmap.PAGESIZE - 1) // mmap.PAGESIZE * mmap.PAGESIZE,
                           prot=mmap.PROT_READ | mmap.PROT_WRITE | mmap.PROT_EXEC)
        s.cbuf.write(code)
        s.cbase = ctypes.addressof(ctypes.c_char.from_buffer(s.cbuf))
        s.entry = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(s.cbase)
        # linear memory, value stack, context
        mn, mx = mod['mems'][0]
        s.mem = mmap.mmap(-1, max(mn, 1) * 65536)
        s.membase = ctypes.addressof(ctypes.c_char.from_buffer(s.mem))
        s.vs = mmap.mmap(-1, s.VS_SIZE)
        s.vsbase = ctypes.addressof(ctypes.c_char.from_buffer(s.vs))
        s.ctx = (ctypes.c_uint64 * (s.host_off // 8 + s.nimp + 1))()
        s.ctx[CTX_MEMSIZE // 8] = mn * 65536
        s.ctx[CTX_VSLIMIT // 8] = s.vsbase + s.VS_SIZE
        for gi, g in enumerate(mod['globals']):
            v = g[2][1]
            if g[0] == F64: v = struct.unpack('<Q', struct.pack('<d', v))[0]
            s.ctx[CTX_GLOBALS // 8 + gi] = v & ((1 << 64) - 1)
        s._cbs = []
        ct = {I32: ctypes.c_uint32, I64: ctypes.c_uint64, F64: ctypes.c_double, F32: ctypes.c_float}
        for fi, im in enumerate(s.fimports):
            ps, rs = s.types[im[3]]
            fn = imports[im[1]][im[2]]
            cb = ctypes.CFUNCTYPE(ct[rs[0]] if rs else None, *[ct[p] for p in ps])(fn)
            s._cbs.append(cb)
            s.ctx[s.host_off // 8 + fi] = ctypes.cast(cb, ctypes.c_void_p).value
        for off, d in mod['datas']:
            if off is not None: s.mem[off[1]:off[1] + len(d)] = d
        s.exports = {n: i for n, k, i in mod['exports'] if k == 0}

    def ftype(s, fi):
        if fi < s.nimp: return s.types[s.fimports[fi][3]]
        return s.types[s.mod['funcs'][fi - s.nimp]]

    def call(s, name, *args):
        fi = s.exports[name]
        ps, rs = s.ftype(fi)
        for j, (t, v) in enumerate(zip(ps, args)):
            if t == F64: v = struct.unpack('<Q', struct.pack('<d', v))[0]
            struct.pack_into('<Q', s.vs, 8 * j, v & ((1 << 64) - 1))
        fn_addr = s.cbase + s.a.labels[s.flabel[fi]]
        rc = s.entry(ctypes.addressof(s.ctx), s.vsbase, s.membase, fn_addr)
        if rc: raise Trap(TRAPS.get(rc, 'trap %d' % rc))
        if rs:
            raw = struct.unpack_from('<Q', s.vs, 0)[0]
            t = rs[0]
            if t == I32: return raw & 0xFFFFFFFF
            if t == F64: return struct.unpack('<d', struct.pack('<Q', raw))[0]
            return raw
        return None
