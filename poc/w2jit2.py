"""w2jit2: the stdlib JIT with registers (x86-64 System V) — second step after w2jit's all-in-memory baseline.

What changes against w2jit:
  * pinned locals: the hottest integer locals of a function (uses weighted by loop depth) live in
    registers for the whole function (rbx rbp rsi rdi r8-r11). Because the location of a local
    never changes, control-flow merges need no reconciliation. Internally these registers are
    callee-saved (a function pushes the ones it pins); around a call into the host (C ABI) the
    C-caller-saved ones are written back to their slots and reloaded.
  * top-of-stack cache: an arithmetic result stays in rax ('rax' entry) until it is consumed;
    only if something else needs rax first is it spilled to its slot.
  * in-place updates: `x op= y` (local.get x; ...; op; local.set/tee x with x pinned) is one
    instruction on x's register (`add ebx, 4`).
  * memory access through the address register directly: [r13 + reg + offset].
Scratch registers: rax, rcx, rdx. r12 frame, r13 memory base, r14 memory size, r15 context.
"""
import struct
from collections import Counter
from w2py import decode, I32, I64, F32, F64
from poc.w2jit import (Asm, Ent, Ctl, JitModule, Trap, TRAPS, CC, INV, wide,
                   RAX, RCX, RDX, RBX, RSP, RBP, RSI, RDI, R8, R9, R10, R11, R12, R13, R14, R15,
                   CTX_VSLIMIT, CTX_GLOBALS)

POOL = [RBX, RBP, RSI, RDI, R8, R9, R10, R11]
OPT = {'pin': True, 'inplace': True, 'loadfuse': True, 'raxcache': True, 'nopin_funcs': set(),
       'jumptable': int(__import__('os').environ.get('W2PY_JT', '4'))}  # br_table: min non-default entries, 0 = off  # debugging switches
C_CALLER_SAVED = {RSI, RDI, R8, R9, R10, R11}
ARITH = {0x6A: (0x03, 0), 0x6B: (0x2B, 5), 0x71: (0x23, 4), 0x72: (0x0B, 1), 0x73: (0x33, 6)}  # opc r,r/m ; /ext imm
SHIFT = {0x74: 4, 0x75: 7, 0x76: 5, 0x77: 0, 0x78: 1}


def s32(v):
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v >= (1 << 31) else v


class CtlIf(Ctl):
    __slots__ = ('else_label', 'has_else')


class FuncJit2:
    def __init__(s, jm, a, fidx, tidx, locs, body):
        s.jm, s.a, s.fidx = jm, a, fidx
        s.params, s.results = jm.types[tidx]
        s.ltypes = list(s.params) + list(locs)
        s.L = len(s.ltypes)
        s.ins = decode(body)
        s.st = []; s.ctl = []; s.dead = False; s.maxh = 0
        s.choose_pins()

    # ------------------------------------------------------------------ register pinning
    def choose_pins(s):
        w = Counter(); depth = 0; kinds = []
        for x in s.ins:
            op = x[0]
            if op in (0x02, 0x03, 0x04):
                kinds.append(op)
                if op == 0x03: depth += 1
            elif op == 0x0B and kinds:
                if kinds.pop() == 0x03: depth -= 1
            elif op in (0x20, 0x21, 0x22) and s.ltypes[x[1]] in (I32, I64):
                w[x[1]] += 8 ** min(depth, 5)
        n = len(POOL) if OPT['pin'] and s.fidx not in OPT['nopin_funcs'] else 0
        order = sorted(w, key=lambda j: -w[j])[:n]
        s.pin = {j: POOL[k] for k, j in enumerate(order)}
        s.used = [POOL[k] for k in range(len(order))]
        s.pad = 8 if len(s.used) % 2 == 0 else 0

    # ------------------------------------------------------------------ helpers
    def sd(s, slot): return 8 * slot
    def hslot(s, i): return s.L + i

    def push(s, e):
        s.st.append(e)
        if len(s.st) > s.maxh: s.maxh = len(s.st)

    def opnd(s, e, i):
        if e.k == 'const': return ('imm', e.v)
        if e.k == 'rax': return ('reg', RAX)
        if e.k == 'local':
            if e.v in s.pin: return ('reg', s.pin[e.v])
            return ('mem', s.sd(e.v))
        if e.k == 'slot': return ('mem', s.sd(s.hslot(i)))
        raise AssertionError('flags entry used as operand')

    def ld(s, r, e, i, w=0, keep_flags=False):
        a = s.a; o = s.opnd(e, i)
        if o[0] == 'imm':
            v = o[1]
            if v == 0 and not keep_flags: a.op_rr(b'\x31', r, r)          # xor r32, r32 (clobbers flags)
            else: a.mov_r_imm(r, v if w else v & 0xFFFFFFFF, w=w)
        elif o[0] == 'reg':
            if o[1] != r: a.mov_rr(r, o[1], w=w)
        else:
            a.mov_r_m(r, R12, o[1], w=w)

    def alu(s, opc, ext, dst, o, w=0):
        """dst <op>= operand o"""
        a = s.a
        if o[0] == 'imm': a.alu_r_imm(ext, dst, s32(o[1]) if not w else o[1], w=w)
        elif o[0] == 'reg': a.op_rr(bytes([opc]), dst, o[1], w=w)
        else: a.alu_r_m(opc, dst, R12, o[1], w=w)

    def materialize(s, i):
        e = s.st[i]
        if e.k == 'slot': return
        a, d = s.a, s.sd(s.hslot(i))
        if e.k == 'const':
            if wide(e.t) and not (-(1 << 31) <= e.v < (1 << 31)):
                a.mov_r_imm(RDX, e.v, w=1); a.mov_m_r(R12, d, RDX, w=1)
            else:
                a.mov_m_imm32(R12, d, e.v, w=1 if wide(e.t) else 0)
        elif e.k == 'local':
            if e.v in s.pin: a.mov_m_r(R12, d, s.pin[e.v], w=1)
            else: a.mov_r_m(RDX, R12, s.sd(e.v), w=1); a.mov_m_r(R12, d, RDX, w=1)
        elif e.k == 'rax':
            a.mov_m_r(R12, d, RAX, w=1)
        elif e.k == 'flags':
            a.setcc(e.v, RCX); a.movzx_r_r8(RCX, RCX); a.mov_m_r(R12, d, RCX, w=1)
        s.st[i] = Ent('slot', e.t)

    def flush(s, pred=lambda e: True):
        for i, e in enumerate(s.st):
            if e.k != 'slot' and pred(e): s.materialize(i)

    def free_rax(s):
        for i, e in enumerate(s.st):
            if e.k == 'rax': s.materialize(i)

    def pop(s):
        if s.st[-1].k == 'flags': s.materialize(len(s.st) - 1)
        e = s.st.pop()
        return e, len(s.st)

    def push_rax(s, t=I32):
        s.push(Ent('rax', t))
        if not OPT['raxcache']: s.materialize(len(s.st) - 1)

    def bt(s, b): return s.jm.types[b[1]] if b[0] == 'idx' else b

    # ------------------------------------------------------------------ prologue / epilogue
    def push_reg(s, r):
        if r >= 8: s.a.b.append(0x41)
        s.a.b.append(0x50 + (r & 7))

    def pop_reg(s, r):
        if r >= 8: s.a.b.append(0x41)
        s.a.b.append(0x58 + (r & 7))

    def epilogue(s):
        a = s.a
        if s.pad: a.alu_r_imm(0, RSP, 8, w=1)
        for r in reversed(s.used): s.pop_reg(r)
        a.b.append(0xC3)

    def emit_return(s):
        a = s.a
        if s.results:
            e = s.st[-1]; i = len(s.st) - 1
            if e.k == 'flags':
                a.setcc(e.v, RCX); a.movzx_r_r8(RCX, RCX); a.mov_m_r(R12, 0, RCX, w=1)
            else:
                o = s.opnd(e, i)
                if o[0] == 'imm':
                    if wide(e.t) and not (-(1 << 31) <= o[1] < (1 << 31)):
                        a.mov_r_imm(RDX, o[1], w=1); a.mov_m_r(R12, 0, RDX, w=1)
                    else: a.mov_m_imm32(R12, 0, o[1], w=1)
                elif o[0] == 'reg': a.mov_m_r(R12, 0, o[1], w=1)
                elif o[1] != 0: a.mov_r_m(RDX, R12, o[1], w=1); a.mov_m_r(R12, 0, RDX, w=1)
        s.epilogue()

    # ------------------------------------------------------------------ control
    def br_moves(s, T):
        n = T.arity
        if n == 0: return
        src0 = len(s.st) - n
        for j in range(n):
            if src0 + j == T.h + j: continue
            s.a.mov_r_m(RDX, R12, s.sd(s.hslot(src0 + j)), w=1)
            s.a.mov_m_r(R12, s.sd(s.hslot(T.h + j)), RDX, w=1)

    def needs_moves(s, T):
        return T.arity > 0 and len(s.st) - T.arity != T.h

    def br(s, depth):
        T = s.ctl[-1 - depth]
        if T.kind == 'func': s.emit_return(); return
        s.flush()
        if T.kind != 'loop': s.br_moves(T)
        T.tgt_used = True
        s.a.jmp(T.label)

    def gen(s):
        a, jm = s.a, s.jm
        a.bind(jm.flabel[s.fidx])
        for r in s.used: s.push_reg(r)
        if s.pad: a.alu_r_imm(5, RSP, 8, w=1)
        a.op_rm(b'\x8d', RAX, R12, 0x7FFFFFF0, w=1); patch_at = len(a.b) - 4
        a.alu_r_m(0x3B, RAX, R15, CTX_VSLIMIT, w=1)
        a.jcc('a', jm.trap_label[5])
        for j in range(s.L):
            r = s.pin.get(j)
            if j < len(s.params):
                if r is not None: a.mov_r_m(r, R12, s.sd(j), w=1 if wide(s.ltypes[j]) else 0)
            elif r is not None: a.op_rr(b'\x31', r, r)
            else: a.mov_m_imm32(R12, s.sd(j), 0, w=1)
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
            if s.st and s.st[-1].k == 'flags' and op not in (0x0D, 0x45, 0x1B, 0x0B, 0x0F, 0x0C, 0x1A):
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
            if op == 0x04:
                # if: pop the condition, flush (every entry a slot, so both arms start from one state),
                # jump to the else label when false; `else` closes the then-arm like a br to the end
                ps, rs = s.bt(x[1])
                if ps: raise NotImplementedError('block params')
                c = s.st.pop(); ci = len(s.st)
                if c.k == 'const':
                    s.ld(RCX, c, ci); a.op_rr(b'\x85', RCX, RCX); cc = 'ne'
                else:
                    cc = s.test_cond(c, ci)
                s.flush()                                     # movs only: flags survive
                T = CtlIf('if', len(s.st), len(rs), a.label())
                T.else_label = a.label(); T.has_else = False
                a.jcc(INV[cc], T.else_label)
                s.ctl.append(T)
                continue
            if op == 0x05:
                T = s.ctl[-1]
                if not s.dead:
                    s.flush(); s.br_moves(T); a.jmp(T.label); T.tgt_used = True
                del s.st[T.h:]
                a.bind(T.else_label); T.has_else = True
                s.dead = False
                continue
            if op == 0x0C: s.br(x[1]); s.dead = True; continue
            if op == 0x0D: s.br_if(x[1]); continue
            if op == 0x0E: s.br_table(x[1], x[2]); s.dead = True; continue
            if op == 0x0F: s.flush(); s.emit_return(); s.dead = True; continue
            if op == 0x00: a.jmp(jm.trap_label[1]); s.dead = True; continue
            if op == 0x01: continue
            nxt = ins[i] if i < N else (None,)
            if s.simple(x, nxt): i += 1                          # the next instruction was fused
        fb = 8 * (s.L + s.maxh + 1)
        a.b[patch_at:patch_at + 4] = struct.pack('<i', fb)

    def end_block(s):
        T = s.ctl.pop()
        if not s.dead:
            s.flush()
            if T.kind != 'loop': s.br_moves(T)
        del s.st[T.h:]
        if T.kind == 'if' and not T.has_else:
            s.a.bind(T.else_label)                       # the false path arrives here (no results)
            s.a.bind(T.label); reach = True
        elif T.kind != 'loop':
            s.a.bind(T.label); reach = (not s.dead) or T.tgt_used
        else:
            reach = not s.dead
        for _ in range(T.arity): s.push(Ent('slot', I64))
        s.dead = not reach

    def test_cond(s, c, ci):
        """set flags for a popped condition entry; return the cc meaning 'true'"""
        a = s.a
        if c.k == 'flags': return c.v
        o = s.opnd(c, ci)
        if o[0] == 'reg': a.op_rr(b'\x85', o[1], o[1])
        else: a.op_rm(b'\x83', 7, R12, o[1]); a.b.append(0)
        return 'ne'

    def br_if(s, depth):
        T = s.ctl[-1 - depth]; a = s.a
        c = s.st.pop(); ci = len(s.st)
        if c.k == 'const':
            if c.v: s.br(depth); s.dead = True
            return
        cc = s.test_cond(c, ci)
        s.flush()                                             # movs only: flags survive
        if T.kind == 'func':
            skip = a.label(); a.jcc(INV[cc], skip); s.emit_return(); a.bind(skip); return
        if T.kind != 'loop' and s.needs_moves(T):
            skip = a.label(); a.jcc(INV[cc], skip); s.br_moves(T); a.jmp(T.label); a.bind(skip)
        else:
            a.jcc(cc, T.label)
        T.tgt_used = True

    def br_table(s, tbl, default):
        e, ei = s.pop()
        s.ld(RCX, e, ei)
        s.flush()
        a = s.a
        if OPT['jumptable'] and sum(d != default for d in tbl) >= OPT['jumptable']:
            # jump table: cmp ecx,n; jae default; lea rax,[rip+T]; movsxd rdx,[rax+rcx*4]; add rax,rdx; jmp rax
            # T holds int32 offsets of per-target stubs relative to T (position-independent, as clang does);
            # ecx is zero-extended by the 32-bit load above
            stub = {d: a.label() for d in sorted(set(tbl) | {default})}
            T = a.label()
            a.alu_r_imm(7, RCX, len(tbl)); a.jcc('ae', stub[default])
            a.emit(0x48, 0x8D, 0x05); a.fix.append((len(a.b), T)); a.b += b'\0\0\0\0'
            a.emit(0x48, 0x63, 0x14, 0x88)
            a.emit(0x48, 0x01, 0xD0)
            a.emit(0xFF, 0xE0)
            while len(a.b) % 4: a.emit(0xCC)
            a.bind(T)
            for d in tbl:
                a.fix.append((len(a.b), stub[d], T)); a.b += b'\0\0\0\0'
            for d, l in stub.items():
                a.bind(l); s.br(d)
            return
        for k, d in enumerate(tbl):
            if d == default: continue
            nxt = a.label(); a.alu_r_imm(7, RCX, k); a.jcc('ne', nxt); s.br(d); a.bind(nxt)
        s.br(default)

    # ------------------------------------------------------------------ straight-line code
    def set_local(s, j, e, ei):
        """local j := entry e (already popped)"""
        a = s.a; t = s.ltypes[j]; w = 1 if wide(t) else 0
        r = s.pin.get(j)
        if r is not None:
            s.ld(r, e, ei, w=w)
        else:
            d = s.sd(j); o = s.opnd(e, ei)
            if o[0] == 'imm':
                if w and not (-(1 << 31) <= o[1] < (1 << 31)):
                    a.mov_r_imm(RDX, o[1], w=1); a.mov_m_r(R12, d, RDX, w=1)
                else: a.mov_m_imm32(R12, d, o[1], w=w)
            elif o[0] == 'reg': a.mov_m_r(R12, d, o[1], w=1)
            elif o[1] != d: a.mov_r_m(RDX, R12, o[1], w=1); a.mov_m_r(R12, d, RDX, w=1)

    def simple(s, x, nxt):
        op = x[0]; a = s.a; jm = s.jm
        if op == 0x1A: s.st.pop(); return False
        if op == 0x20: s.push(Ent('local', s.ltypes[x[1]], x[1])); return False
        if op in (0x21, 0x22):
            j = x[1]
            s.flush(lambda e: e.k == 'local' and e.v == j)
            e, ei = s.pop()
            s.set_local(j, e, ei)
            if op == 0x22: s.push(Ent('local', s.ltypes[j], j))
            return False
        if op == 0x23:
            s.free_rax()
            t = jm.gtypes[x[1]]
            a.mov_r_m(RAX, R15, CTX_GLOBALS + 8 * x[1], w=1 if wide(t) else 0); s.push_rax(t); return False
        if op == 0x24:
            e, ei = s.pop(); s.ld(RDX, e, ei, w=1); a.mov_m_r(R15, CTX_GLOBALS + 8 * x[1], RDX, w=1); return False
        if op == 0x41: s.push(Ent('const', I32, x[1])); return False
        if op == 0x42:
            v = x[1] - (1 << 64) if x[1] >= (1 << 63) else x[1]; s.push(Ent('const', I64, v)); return False
        if op == 0x44:
            s.push(Ent('const', F64, struct.unpack('<q', struct.pack('<d', x[1]))[0])); return False
        if 0x28 <= op <= 0x35: return s.load(op, x[1], nxt)
        if 0x36 <= op <= 0x3E: s.store(op, x[1]); return False
        if op == 0x10: s.call(x[1]); return False
        if op == 0x1B: s.select(); return False
        if op == 0x45:
            c = s.st.pop(); ci = len(s.st)
            if c.k == 'flags': s.push(Ent('flags', I32, INV[c.v])); return False
            if c.k == 'const': s.push(Ent('const', I32, 1 if c.v == 0 else 0)); return False
            cc = s.test_cond(c, ci); s.push(Ent('flags', I32, INV[cc])); return False
        if 0x46 <= op <= 0x4F: s.compare(op); return False
        if op in ARITH or op == 0x6C or op in SHIFT: return s.arith(op, nxt)
        if op in (0x6D, 0x6E, 0x6F, 0x70): s.divrem(op); return False
        if op in (0xC0, 0xC1):
            e, ei = s.pop()
            if e.k == 'const':
                bits = 8 if op == 0xC0 else 16; v = e.v & ((1 << bits) - 1)
                if v >> (bits - 1): v -= 1 << bits
                s.push(Ent('const', I32, v & 0xFFFFFFFF)); return False
            s.free_rax(); s.ld(RAX, e, ei)
            a.op_rr(b'\x0f\xbe' if op == 0xC0 else b'\x0f\xbf', RAX, RAX); s.push_rax(); return False
        if s.f64op(op): return False
        raise NotImplementedError('opcode 0x%x' % op)

    def compare(s, op):
        a = s.a
        cc = ['e', 'ne', 'l', 'b', 'g', 'a', 'le', 'be', 'ge', 'ae'][op - 0x46]
        b, bi = s.pop(); e, ei = s.pop()
        oa = s.opnd(e, ei); ob = s.opnd(b, bi)
        if oa[0] == 'reg':
            s.alu(0x3B, 7, oa[1], ob)
        elif oa[0] == 'mem' and ob[0] == 'imm':
            v = s32(ob[1])
            if -128 <= v < 128: a.op_rm(b'\x83', 7, R12, oa[1]); a.b += struct.pack('<b', v)
            else: a.op_rm(b'\x81', 7, R12, oa[1]); a.b += struct.pack('<i', v)
        elif oa[0] == 'mem' and ob[0] == 'reg':
            a.op_rm(b'\x39', ob[1], R12, oa[1])
        else:
            s.ld(RDX, e, ei); s.alu(0x3B, 7, RDX, ob)
        s.push(Ent('flags', I32, cc))

    def arith(s, op, nxt):
        a = s.a
        b, bi = s.pop(); e, ei = s.pop()
        # in place: `x op= y` with x pinned and the next instruction storing back into x
        if (OPT['inplace'] and e.k == 'local' and e.v in s.pin and nxt[0] in (0x21, 0x22) and nxt[1] == e.v
                and op != 0x6C and not (b.k == 'local' and b.v == e.v)):
            j = e.v; r = s.pin[j]
            s.flush(lambda q: q.k == 'local' and q.v == j)
            ob = s.opnd(b, bi)
            if op in SHIFT:
                if ob[0] == 'imm':
                    a.rex(0, 0, 0, r); a.b += bytes([0xC1, 0xC0 | (SHIFT[op] << 3) | (r & 7), ob[1] & 31])
                else:
                    s.ld(RCX, b, bi); a.rex(0, 0, 0, r); a.b += bytes([0xD3, 0xC0 | (SHIFT[op] << 3) | (r & 7)])
            else:
                opc, ext = ARITH[op]; s.alu(opc, ext, r, ob)
            if nxt[0] == 0x22: s.push(Ent('local', s.ltypes[j], j))
            return True
        s.free_rax()
        if op in SHIFT:
            if b.k == 'const':
                s.ld(RAX, e, ei); a.b += bytes([0xC1, 0xC0 | (SHIFT[op] << 3), b.v & 31])
            else:
                s.ld(RCX, b, bi); s.ld(RAX, e, ei); a.b += bytes([0xD3, 0xC0 | (SHIFT[op] << 3)])
            s.push_rax(); return False
        if b.k == 'rax': a.mov_rr(RCX, RAX); ob = ('reg', RCX)
        else: ob = s.opnd(b, bi)
        s.ld(RAX, e, ei)
        if op == 0x6C:
            if ob[0] == 'imm': a.op_rr(b'\x69', RAX, RAX); a.b += struct.pack('<I', ob[1] & 0xFFFFFFFF)
            elif ob[0] == 'reg': a.op_rr(b'\x0f\xaf', RAX, ob[1])
            else: a.op_rm(b'\x0f\xaf', RAX, R12, ob[1])
        else:
            opc, ext = ARITH[op]; s.alu(opc, ext, RAX, ob)
        s.push_rax(); return False

    def divrem(s, op):
        a = s.a; jm = s.jm
        b, bi = s.pop(); e, ei = s.pop()
        s.free_rax()
        s.ld(RCX, b, bi); s.ld(RAX, e, ei)
        a.op_rr(b'\x85', RCX, RCX); a.jcc('e', jm.trap_label[3])
        if op in (0x6E, 0x70):
            a.op_rr(b'\x31', RDX, RDX); a.op_rr(b'\xf7', 6, RCX)
            if op == 0x70: a.mov_rr(RAX, RDX)
        elif op == 0x6D:
            ok = a.label(); a.alu_r_imm(7, RCX, -1); a.jcc('ne', ok)
            a.alu_r_imm(7, RAX, -0x80000000); a.jcc('e', jm.trap_label[4]); a.bind(ok)
            a.b.append(0x99); a.op_rr(b'\xf7', 7, RCX)
        else:
            m1, done = a.label(), a.label()
            a.alu_r_imm(7, RCX, -1); a.jcc('e', m1)
            a.b.append(0x99); a.op_rr(b'\xf7', 7, RCX); a.mov_rr(RAX, RDX); a.jmp(done)
            a.bind(m1); a.op_rr(b'\x31', RAX, RAX); a.bind(done)
        s.push_rax()

    def address(s, e, ei, off, size):
        """effective address -> (index register, displacement); bounds-checked"""
        a = s.a; o = s.opnd(e, ei)
        if o[0] == 'reg': idx = o[1]
        elif o[0] == 'imm': a.mov_r_imm(RAX, (o[1] + off) & 0xFFFFFFFF); idx = RAX; off = 0
        else: a.mov_r_m(RAX, R12, o[1]); idx = RAX
        a.op_rm(b'\x8d', RDX, idx, off + size, w=1)                  # lea rdx, [idx + off + size]
        a.op_rr(b'\x39', R14, RDX, w=1)                               # cmp rdx, r14
        a.jcc('a', s.jm.trap_label[2])
        return idx, off

    def load(s, op, off, nxt):
        a = s.a
        size = {0x28: 4, 0x29: 8, 0x2A: 4, 0x2B: 8, 0x2C: 1, 0x2D: 1, 0x2E: 2, 0x2F: 2}[op]
        e, ei = s.pop()
        s.free_rax()
        idx, d = s.address(e, ei, off, size)
        # load straight into a pinned local when the next instruction stores it there
        dst = RAX; fused = False
        if OPT['loadfuse'] and nxt[0] == 0x21 and nxt[1] in s.pin and op in (0x28, 0x2D, 0x2F, 0x2C, 0x2E):
            j = nxt[1]
            if not any(q.k == 'local' and q.v == j for q in s.st):
                dst = s.pin[j]; fused = True
        opc, w = {0x28: (b'\x8b', 0), 0x2A: (b'\x8b', 0), 0x29: (b'\x8b', 1), 0x2B: (b'\x8b', 1),
                  0x2D: (b'\x0f\xb6', 0), 0x2C: (b'\x0f\xbe', 0), 0x2F: (b'\x0f\xb7', 0), 0x2E: (b'\x0f\xbf', 0)}[op]
        a.op_rm(opc, dst, R13, d, w=w, index=idx)
        if fused: return True
        s.push_rax({0x2A: F32, 0x2B: F64, 0x29: I64}.get(op, I32))
        return False

    def store(s, op, off):
        a = s.a
        size = {0x36: 4, 0x37: 8, 0x38: 4, 0x39: 8, 0x3A: 1, 0x3B: 2, 0x3C: 1, 0x3D: 2, 0x3E: 4}[op]
        v, vi = s.pop(); e, ei = s.pop()
        s.free_rax()
        ov = s.opnd(v, vi); vimm = None; vreg = None
        if ov[0] == 'imm' and (size < 8 or -(1 << 31) <= ov[1] < (1 << 31)): vimm = ov[1]
        elif ov[0] == 'reg' and ov[1] != RAX and (size >= 4 or ov[1] in (RBX, R8, R9, R10, R11)): vreg = ov[1]
        else: s.ld(RCX, v, vi, w=1 if size == 8 else 0); vreg = RCX
        idx, d = s.address(e, ei, off, size)
        if vimm is not None:
            if size == 1: a.op_rm(b'\xc6', 0, R13, d, index=idx); a.b.append(vimm & 0xFF)
            elif size == 2: a.op_rm(b'\xc7', 0, R13, d, index=idx, pre=b'\x66'); a.b += struct.pack('<H', vimm & 0xFFFF)
            else: a.op_rm(b'\xc7', 0, R13, d, w=(size == 8), index=idx); a.b += struct.pack('<I', vimm & 0xFFFFFFFF)
        elif size == 1: a.op_rm(b'\x88', vreg, R13, d, index=idx)
        elif size == 2: a.op_rm(b'\x89', vreg, R13, d, index=idx, pre=b'\x66')
        else: a.op_rm(b'\x89', vreg, R13, d, w=(size == 8), index=idx)

    def select(s):
        a = s.a
        c = s.st.pop(); ci = len(s.st)
        b, bi = s.pop(); e, ei = s.pop()
        s.free_rax()
        w = 1 if wide(e.t) else 0                     # 32-bit moves keep i32 values zero-extended
        if c.k == 'const':
            s.ld(RAX, e if c.v else b, ei if c.v else bi, w=w); s.push_rax(e.t); return
        if c.k != 'flags': s.ld(RDX, c, ci)
        s.ld(RCX, b, bi, w=w, keep_flags=True); s.ld(RAX, e, ei, w=w, keep_flags=True)
        if c.k == 'flags': cc = INV[c.v]
        else: a.op_rr(b'\x85', RDX, RDX); cc = 'e'
        a.op_rr(bytes([0x0F, 0x40 | CC[cc]]), RAX, RCX, w=w)
        s.push_rax(e.t)

    def call(s, fi):
        a, jm = s.a, s.jm
        ps, rs = jm.ftype(fi)
        n = len(ps)
        s.flush()
        h = len(s.st); base_slot = s.hslot(h - n)
        if fi < jm.nimp:
            # the C callee may clobber rsi rdi r8-r11, which can hold pinned locals of *any* wasm
            # frame up the stack (pool registers are callee-saved between wasm functions): save all six
            # (an even count, so rsp stays 16-aligned)
            keep = [RSI, RDI, R8, R9, R10, R11]
            for r in keep: s.push_reg(r)
            regs = [RDI, RSI, RDX, RCX, R8, R9]
            for k in range(n): a.mov_r_m(regs[k], R12, s.sd(base_slot + k), w=1)
            a.op_rm(b'\xff', 2, R15, jm.host_off + 8 * fi)
            for r in reversed(keep): s.pop_reg(r)
            del s.st[h - n:]
            if rs:
                if not wide(rs[0]): a.mov_rr(RAX, RAX)                # C leaves the upper half undefined
                s.push_rax(rs[0])
            return
        if base_slot: a.alu_r_imm(0, R12, 8 * base_slot, w=1)
        a.call(jm.flabel[fi])
        if base_slot: a.alu_r_imm(5, R12, 8 * base_slot, w=1)
        del s.st[h - n:]
        for t in rs: s.push(Ent('slot', t))

    def f64op(s, op):
        a = s.a
        def xmm_ld(xr, e, ei):
            o = s.opnd(e, ei)
            if o[0] == 'imm': a.mov_r_imm(RDX, o[1], w=1); a.op_rr(b'\x0f\x6e', xr, RDX, w=1, pre=b'\x66')
            elif o[0] == 'reg': a.op_rr(b'\x0f\x6e', xr, o[1], w=1, pre=b'\x66')
            else: a.op_rm(b'\x0f\x10', xr, R12, o[1], pre=b'\xf2')
        def xmm_res(xr):
            a.op_rr(b'\x0f\x7e', xr, RAX, w=1, pre=b'\x66'); s.push_rax(F64)   # movq rax, xmm
        if op in (0xA0, 0xA1, 0xA2, 0xA3):
            b, bi = s.pop(); e, ei = s.pop()
            xmm_ld(0, e, ei); xmm_ld(1, b, bi); s.free_rax()
            a.op_rr(bytes([0x0F, {0xA0: 0x58, 0xA1: 0x5C, 0xA2: 0x59, 0xA3: 0x5E}[op]]), 0, 1, pre=b'\xf2')
            xmm_res(0); return True
        if op == 0x9A:
            e, ei = s.pop(); s.free_rax(); s.ld(RAX, e, ei, w=1)
            a.rex(1, 0, 0, RAX); a.b += bytes([0x0F, 0xBA, 0xF8, 63]); s.push_rax(F64); return True
        if op == 0x9D:
            e, ei = s.pop(); xmm_ld(0, e, ei); s.free_rax()
            a.op_rr(b'\x0f\x3a\x0b', 0, 0, pre=b'\x66'); a.b.append(3); xmm_res(0); return True
        if op in (0x61, 0x62, 0x63, 0x64, 0x65, 0x66):
            b, bi = s.pop(); e, ei = s.pop()
            if op in (0x63, 0x65): xmm_ld(0, b, bi); xmm_ld(1, e, ei)
            else: xmm_ld(0, e, ei); xmm_ld(1, b, bi)
            s.free_rax()
            a.op_rr(b'\x0f\x2e', 0, 1, pre=b'\x66')
            if op == 0x61: a.setcc('e', RAX); a.setcc('np', RCX); a.op_rr(b'\x20', RCX, RAX)
            elif op == 0x62: a.setcc('ne', RAX); a.setcc('p', RCX); a.op_rr(b'\x08', RCX, RAX)
            else: a.setcc('a' if op in (0x63, 0x64) else 'ae', RAX)
            a.movzx_r_r8(RAX, RAX); s.push_rax(); return True
        if op in (0xB8, 0xB7):
            e, ei = s.pop(); s.free_rax(); s.ld(RAX, e, ei)
            if op == 0xB7: a.op_rr(b'\x63', RAX, RAX, w=1)
            a.op_rr(b'\x0f\x2a', 0, RAX, w=1, pre=b'\xf2'); xmm_res(0); return True
        if op in (0xFC02, 0xFC03):
            e, ei = s.pop(); xmm_ld(0, e, ei); s.free_rax()
            nan, inr, done = a.label(), a.label(), a.label()
            a.op_rr(b'\x0f\x2e', 0, 0, pre=b'\x66'); a.jcc('p', nan)
            a.op_rr(b'\x0f\x2c', RAX, 0, w=1, pre=b'\xf2')
            a.mov_r_imm(RDX, -(1 << 63), w=1); a.op_rr(b'\x39', RDX, RAX, w=1); a.jcc('ne', inr)
            a.op_rr(b'\x0f\x7e', 0, RCX, w=1, pre=b'\x66'); a.op_rr(b'\x85', RCX, RCX, w=1); a.jcc('s', inr)
            a.mov_r_imm(RAX, (1 << 63) - 1, w=1)
            a.bind(inr)
            lo, hi = ((-(1 << 31), (1 << 31) - 1) if op == 0xFC02 else (0, (1 << 32) - 1))
            k1, k2 = a.label(), a.label()
            a.mov_r_imm(RCX, lo, w=1); a.op_rr(b'\x39', RCX, RAX, w=1); a.jcc('ge', k1); a.mov_rr(RAX, RCX, w=1); a.bind(k1)
            a.mov_r_imm(RCX, hi, w=1); a.op_rr(b'\x39', RCX, RAX, w=1); a.jcc('le', k2); a.mov_rr(RAX, RCX, w=1); a.bind(k2)
            a.mov_rr(RAX, RAX); a.jmp(done)                                  # mov eax, eax: zero-extend
            a.bind(nan); a.op_rr(b'\x31', RAX, RAX)
            a.bind(done); s.push_rax(); return True
        return False


class JitModule2(JitModule):
    FuncGen = FuncJit2
