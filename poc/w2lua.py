"""w2lua: WebAssembly -> Lua 5.4 AOT translator (PoC), sharing the parser with w2py.

Design (differences from the Python backend):
  * control flow uses `goto`: block/if exit -> label after the construct, loop -> label at its top.
    No label register, no guards.  All locals are declared at function entry, so gotos never
    jump into a local's scope; `return` is emitted as `do return ... end`.
  * i32 = canonical unsigned value held in a Lua int64 with lazy `& 0xFFFFFFFF` (int64 wrap keeps
    the low 32 bits of + - * <<); i64 = native Lua int64 (two's complement == wasm), unsigned ops
    via math.ult / helpers.
  * linear memory = Lua table of bytes, loads/stores inlined as byte compositions.
  * Lua truthiness: 0 is true, so every integer condition is `x ~= 0`; comparisons are booleans and
    become `(c and 1 or 0)` when used as values.
"""
import re, struct, sys
from w2py import parse, decode, E, dep, I32, I64, F32, F64, M32, M64

LUA_MAX_LOCALS = 180


def lflit(v):
    if v != v: return '(0.0/0.0)'
    if v == float('inf'): return '(1.0/0.0)'
    if v == float('-inf'): return '(-1.0/0.0)'
    if v == 0.0 and str(v).startswith('-'): return '(-0.0)'
    r = repr(float(v))
    if 'e' not in r and '.' not in r and 'inf' not in r: r += '.0'
    return '(%s)' % r if r.startswith('-') else r


class Ctl:
    __slots__ = ('kind', 'id', 'res', 'rv', 'h', 'tg', 'has_else', 'live_then')

    def __init__(s, kind, id_, res, rv, h, tg):
        s.kind, s.id, s.res, s.rv, s.h, s.tg = kind, id_, res, rv, h, tg
        s.has_else = False; s.live_then = False


class LuaFuncGen:
    def __init__(s, mg, fidx, tidx, locs, body):
        s.mg = mg; s.fidx = fidx
        s.params, s.results = mg.types[tidx]
        s.ltypes = list(s.params) + locs
        s.ins = decode(body)
        s.lines = []; s.ind = 1
        s.stack = []; s.dead = False
        s.ctl = []
        s.temps = []          # all temp names ever allocated (for declarations)
        s.rvars = []
        s.uses_mem = False

    # ---------------------------------------------------------------- emit / names
    def emit(s, line): s.lines.append('  ' * s.ind + line)

    def lname(s, i): return 'p%d' % i if i < len(s.params) else 'l%d' % i

    def newtmp(s):
        live = set()
        for e in s.stack:
            live.update(re.findall(r'\bt\d+\b', e.c))
        for t in s.temps:
            if t not in live and t not in s.reserved: return t
        t = 't%d' % (len(s.temps) + 1); s.temps.append(t); return t

    # ---------------------------------------------------------------- value helpers
    def n(s, e):
        if e.t != I32 or e.b is not None: return e.c
        return '(%s & 0xFFFFFFFF)' % e.c

    def nb(s, e):
        return e.b if e.b is not None else M32

    def sgn(s, e):
        if e.t != I32: return e.c
        if e.b is not None and e.b < 0x80000000: return e.c
        return '((%s ~ 0x80000000) - 0x80000000)' % s.n(e)

    def cond(s, e):
        if e.t == 'bool': return e.c
        if e.t in (F32, F64): return '(%s ~= 0.0)' % e.c
        return '(%s ~= 0)' % s.n(e)

    def push(s, e): s.stack.append(e)

    def pop(s):
        e = s.stack.pop()
        if e.t == 'bool': e = E('(%s and 1 or 0)' % e.c, I32, 1, e.Ls, e.m, e.g)
        return e

    def popc(s): return s.stack.pop()

    def popn(s, k):
        if k == 0: return []
        v = [s.pop() for _ in range(k)]; v.reverse(); return v

    def materialize(s, i):
        e = s.stack[i]
        if e.k: return
        t = s.newtmp(); s.emit('%s = %s' % (t, e.c))
        s.stack[i] = E(t, e.t, e.b, k=True)

    def flush(s, pred):
        for i, e in enumerate(s.stack):
            if e.k or e.pure_const: continue
            if pred(e): s.materialize(i)

    def flush_all(s): s.flush(lambda e: True)

    def simple_addr(s, code):
        """make an address a plain name/number so it can be repeated inside byte loads"""
        if re.fullmatch(r'[A-Za-z_]\w*|\d+', code): return code
        t = s.newtmp(); s.emit('%s = %s' % (t, code)); return t

    def bt(s, b):
        if b[0] == 'idx': return s.mg.types[b[1]]
        return b

    def prepass(s):
        tg = {}; st = [-1]
        for i, ins in enumerate(s.ins):
            op = ins[0]
            if op in (0x02, 0x03, 0x04): st.append(i); tg[i] = False
            elif op == 0x0B: st.pop()
            elif op in (0x0C, 0x0D): tg[st[-1 - ins[1]]] = True
            elif op == 0x0E:
                for d in ins[1] + [ins[2]]: tg[st[-1 - d]] = True
        return tg

    # ---------------------------------------------------------------- branches
    def vals(s, n):
        vs = s.stack[len(s.stack) - n:] if n else []
        return [s.n(v) if v.t == I32 else v.c for v in vs]

    def assign_results(s, T):
        if T.rv: s.emit('%s = %s' % (', '.join(T.rv), ', '.join(s.vals(len(T.rv)))))

    def emit_return(s):
        v = s.vals(len(s.results))
        s.emit('do return %s end' % ', '.join(v) if v else 'do return end')

    def br(s, d):
        T = s.ctl[-1 - d]
        if T.kind == 'func': s.emit_return(); return
        if T.kind != 'loop': s.assign_results(T)
        s.emit(('goto C%d' if T.kind == 'loop' else 'goto L%d') % T.id)

    # ---------------------------------------------------------------- main
    def gen(s):
        tg = s.prepass()
        nid = iter(range(1, 1 << 30))
        s.reserved = set()
        s.ctl.append(Ctl('func', 0, s.results, [], 0, False))
        ins = s.ins; i = 0; N = len(ins); deadd = 0
        while i < N:
            x = ins[i]; op = x[0]
            if s.dead:
                if op in (0x02, 0x03, 0x04): deadd += 1; i += 1; continue
                if deadd:
                    if op == 0x0B: deadd -= 1
                    i += 1; continue
                if op not in (0x0B, 0x05): i += 1; continue
            i += 1
            if op == 0x0B:
                if len(s.ctl) == 1:
                    if not s.dead and s.results: s.emit_return()
                    break
                s.end_frame(); continue
            if op == 0x05:
                F = s.ctl[-1]
                if not s.dead: s.assign_results(F); F.live_then = True
                del s.stack[F.h:]
                s.ind -= 1; s.emit('else'); s.ind += 1
                F.has_else = True; s.dead = False
                continue
            if op in (0x02, 0x03, 0x04):
                ps, rs = s.bt(x[1])
                if ps: raise NotImplementedError('block params')
                if op == 0x04: c = s.popc(); cc = s.cond(c)
                s.flush_all()
                fid = next(nid)
                kind = {2: 'block', 3: 'loop', 4: 'if'}[op]
                rv = ['r%d_%d' % (fid, j) for j in range(len(rs))] if kind != 'loop' else []
                s.rvars += rv
                F = Ctl(kind, fid, rs, rv, len(s.stack), tg[i - 1])
                s.ctl.append(F)
                if kind == 'loop' and F.tg:  # real loop (JIT-visible); `br` = goto continue-label
                    s.emit('while true do'); s.ind += 1
                if kind == 'if': s.emit('if %s then' % cc); s.ind += 1
                continue
            if op == 0x0C:
                s.br(x[1]); s.dead = True; continue
            if op == 0x0D:
                c = s.popc(); cc = s.cond(c); T = s.ctl[-1 - x[1]]
                if T.kind not in ('loop', 'func') and T.rv: s.flush_all()
                s.emit('if %s then' % cc); s.ind += 1; s.br(x[1]); s.ind -= 1; s.emit('end')
                continue
            if op == 0x0E:
                e = s.pop(); idx = s.simple_addr(s.n(e)); s.flush_all()
                s.reserved.add(idx)
                groups = {}
                for k, d in enumerate(x[1]):
                    if d != x[2]: groups.setdefault(d, []).append(k)
                first = True
                for d, ks in groups.items():
                    if len(ks) == 1: c = '%s == %d' % (idx, ks[0])
                    elif ks == list(range(ks[0], ks[-1] + 1)): c = '%s >= %d and %s <= %d' % (idx, ks[0], idx, ks[-1])
                    else: c = ' or '.join('%s == %d' % (idx, k) for k in ks)
                    s.emit(('if ' if first else 'elseif ') + c + ' then'); first = False
                    s.ind += 1; s.br(d); s.ind -= 1
                if first: s.br(x[2])
                else:
                    s.emit('else'); s.ind += 1; s.br(x[2]); s.ind -= 1; s.emit('end')
                s.reserved.discard(idx)
                s.dead = True; continue
            if op == 0x0F: s.emit_return(); s.dead = True; continue
            if op == 0x00: s.emit('error("wasm trap: unreachable")'); s.dead = True; continue
            if op == 0x01: continue
            s.simple(x)
        return s.finish()

    def end_frame(s):
        F = s.ctl[-1]
        live = not s.dead
        if live: s.assign_results(F)
        del s.stack[F.h:]
        if F.kind == 'if':
            s.ind -= 1; s.emit('end')
        if F.kind == 'loop' and F.tg:
            if live: s.emit('do break end')
            s.emit('::C%d::' % F.id); s.ind -= 1; s.emit('end')
        s.ctl.pop()
        if F.kind != 'loop' and F.tg: s.emit('::L%d::' % F.id)
        if F.kind == 'if': after = live or F.live_then or not F.has_else or F.tg
        elif F.kind == 'loop': after = live
        else: after = live or F.tg
        s.dead = not after
        for j, t in enumerate(F.res):
            s.push(E(F.rv[j], t, M32 if t == I32 else None, k=True))

    def finish(s):
        fname = 'f%d' % s.fidx
        args = [s.lname(j) for j in range(len(s.params))]
        locs = [s.lname(j) for j in range(len(s.params), len(s.ltypes))]
        zero = ['0.0' if s.ltypes[j] in (F32, F64) else '0' for j in range(len(s.params), len(s.ltypes))]
        body = s.lines
        others = s.temps + s.rvars
        head = ['function %s(%s)' % (fname, ', '.join(args))]
        budget = LUA_MAX_LOCALS - len(args) - (1 if s.uses_mem else 0)
        spill = []
        allv = locs + others
        if len(allv) > budget:  # Lua's 200-locals limit: keep the first ones as registers, spill the rest
            spill = allv[budget - 1:]
            keep = set(allv[:budget - 1])
            locs_k = [(v, z) for v, z in zip(locs, zero) if v in keep]
            others = [v for v in others if v in keep]
            sp = {v: 'X[%d]' % (k + 1) for k, v in enumerate(spill)}
            rx = re.compile(r'\b(%s)\b' % '|'.join(map(re.escape, spill)))
            body = [rx.sub(lambda m_: sp[m_.group(1)], ln) for ln in body]
            head.append('  local X = {%s}' % ', '.join('0' for _ in spill))
        else:
            locs_k = list(zip(locs, zero))
        if s.uses_mem: head.append('  local M = MEM')
        if locs_k:
            head.append('  local %s = %s' % (', '.join(v for v, _ in locs_k), ', '.join(z for _, z in locs_k)))
        if others: head.append('  local ' + ', '.join(others))
        return '\n'.join(head + body + ['end']) + '\n'

    # ---------------------------------------------------------------- memory
    def keep(s, *codes):
        """reserve temps referenced by popped-but-not-yet-emitted expressions"""
        saved = set(s.reserved)
        for c in codes: s.reserved.update(re.findall(r'\bt\d+\b', c))
        return saved

    def load(s, op, a, off):
        s.uses_mem = True
        saved = s.keep(a.c)
        base = s.simple_addr(s.n(a))
        s.reserved = saved
        def at(k):
            o = off + k
            return 'M[%s]' % base if o == 0 else 'M[%s+%d]' % (base, o)
        def comp(nbytes):
            return '(' + ' | '.join(at(0) if k == 0 else '(%s << %d)' % (at(k), 8 * k) for k in range(nbytes)) + ')'
        d = dep(a, m=True)
        if op == 0x28: return E(comp(4), I32, M32, **d)
        if op == 0x2D: return E(at(0), I32, 255, **d)
        if op == 0x2C: return E('((%s ~ 0x80) - 0x80)' % at(0), I32, None, **d)
        if op == 0x2F: return E(comp(2), I32, 0xFFFF, **d)
        if op == 0x2E: return E('((%s ~ 0x8000) - 0x8000)' % comp(2), I32, None, **d)
        if op == 0x29: return E(comp(8), I64, None, **d)
        if op == 0x31: return E(at(0), I64, None, **d)
        if op == 0x30: return E('((%s ~ 0x80) - 0x80)' % at(0), I64, None, **d)
        if op == 0x33: return E(comp(2), I64, None, **d)
        if op == 0x32: return E('((%s ~ 0x8000) - 0x8000)' % comp(2), I64, None, **d)
        if op == 0x35: return E(comp(4), I64, None, **d)
        if op == 0x34: return E('((%s ~ 0x80000000) - 0x80000000)' % comp(4), I64, None, **d)
        if op == 0x2A: return E('ldf32(%s)' % (base if off == 0 else '%s+%d' % (base, off)), F32, None, **d)
        if op == 0x2B: return E('ldf64(%s)' % (base if off == 0 else '%s+%d' % (base, off)), F64, None, **d)
        raise NotImplementedError(op)

    def store(s, op, a, v, off):
        s.uses_mem = True
        saved = s.keep(a.c, v.c)
        base = s.simple_addr(s.n(a))
        s.reserved.add(base)
        try:
            s._store(op, a, v, off, base)
        finally:
            s.reserved = saved

    def _store(s, op, a, v, off, base):
        def at(k):
            o = off + k
            return 'M[%s]' % base if o == 0 else 'M[%s+%d]' % (base, o)
        if op in (0x38, 0x39):
            s.emit('%s(%s, %s)' % ('stf32' if op == 0x38 else 'stf64', base if off == 0 else '%s+%d' % (base, off), v.c))
            return
        nbytes = {0x36: 4, 0x37: 8, 0x3A: 1, 0x3B: 2, 0x3C: 1, 0x3D: 2, 0x3E: 4}[op]
        if nbytes == 1:
            vc = v.c if (v.t == I32 and v.b is not None and v.b <= 255) else '%s & 0xFF' % v.c
            s.emit('%s = %s' % (at(0), vc)); return
        vv = s.simple_addr(v.c)
        parts = ['%s = %s & 0xFF' % (at(0), vv)]
        parts += ['%s = (%s >> %d) & 0xFF' % (at(k), vv, 8 * k) for k in range(1, nbytes)]
        s.emit('; '.join(parts))

    # ---------------------------------------------------------------- straight-line ops
    def simple(s, x):
        op = x[0]; push, pop, n, sgn = s.push, s.pop, s.n, s.sgn
        mg = s.mg
        if op == 0x1A: pop(); return
        if op == 0x1B:
            c = s.popc(); b = pop(); a = pop()
            bb = max(a.b, b.b) if a.b is not None and b.b is not None else None
            push(E('(%s and %s or %s)' % (s.cond(c), a.c, b.c), a.t, bb, **dep(a, b, c))); return
        if op == 0x20:
            nm = s.lname(x[1]); t = s.ltypes[x[1]]
            push(E(nm, t, M32 if t == I32 else None, Ls=[nm])); return
        if op in (0x21, 0x22):
            nm = s.lname(x[1]); v = pop()
            s.flush(lambda e: nm in e.Ls)
            s.emit('%s = %s' % (nm, n(v)))
            if op == 0x22:
                t = s.ltypes[x[1]]; push(E(nm, t, M32 if t == I32 else None, Ls=[nm]))
            return
        if op == 0x23:
            g = 'G%d' % x[1]; t = mg.gtypes[x[1]]
            push(E(g, t, M32 if t == I32 else None, g=[g] if mg.gmut[x[1]] else [], k=not mg.gmut[x[1]])); return
        if op == 0x24:
            g = 'G%d' % x[1]; v = pop(); s.flush(lambda e: g in e.g)
            s.emit('%s = %s' % (g, n(v))); return
        if op == 0x41: push(E(str(x[1]), I32, x[1])); return
        if op == 0x42: push(E('0x%x' % x[1], I64, None)); return  # hex literals wrap to int64 in Lua 5.4
        if op == 0x43: push(E(lflit(x[1]), F32)); return
        if op == 0x44: push(E(lflit(x[1]), F64)); return
        if 0x28 <= op <= 0x35:
            a = pop(); push(s.load(op, a, x[1])); return
        if 0x36 <= op <= 0x3E:
            v = pop(); a = pop(); s.flush(lambda e: e.m)
            s.store(op, a, v, x[1]); return
        if op == 0x3F: push(E('MEMPAGES', I32, 0xFFFF, m=True)); return
        if op == 0x40:
            v = pop(); s.flush(lambda e: e.m or e.g); t = s.newtmp()
            s.emit('%s = mgrow(%s)' % (t, n(v))); push(E(t, I32, M32, k=True)); return
        if op == 0x10 or op == 0x11:
            if op == 0x10:
                ps, rs = mg.ftype(x[1]); callee = mg.fname(x[1])
            else:
                ps, rs = mg.types[x[1]]; ix = pop(); callee = 'TBL%d[%s]' % (x[2], n(ix))
            args = s.popn(len(ps)); s.flush(lambda e: e.m or e.g)
            call = '%s(%s)' % (callee, ', '.join(n(a) for a in args))
            if not rs: s.emit(call); return
            ts = []
            for ty in rs:
                t = s.newtmp(); s.reserved.add(t); ts.append(t)
            s.reserved.difference_update(ts)
            s.emit('%s = %s' % (', '.join(ts), call))
            for t, ty in zip(ts, rs): push(E(t, ty, M32 if ty == I32 else None, k=True))
            return
        if s.int_op(op): return
        if s.float_op(op): return
        raise NotImplementedError('opcode 0x%x' % op)

    def int_op(s, op):
        push, pop, n, nb, sgn = s.push, s.pop, s.n, s.nb, s.sgn
        if op == 0x45:
            a = s.popc()
            if a.t == 'bool': push(E('(not %s)' % a.c, 'bool', 1, **dep(a))); return True
            push(E('(%s == 0)' % n(a), 'bool', 1, **dep(a))); return True
        if op == 0x50:
            a = pop(); push(E('(%s == 0)' % a.c, 'bool', 1, **dep(a))); return True
        if 0x46 <= op <= 0x4F:  # i32 compares
            b = pop(); a = pop(); k = op - 0x46
            o = ['==', '~=', '<', '<', '>', '>', '<=', '<=', '>=', '>='][k]
            if k in (2, 4, 6, 8) and not (nb(a) < 0x80000000 and nb(b) < 0x80000000):
                def fl(e):
                    if e.pure_const and e.c.isdigit(): return str(int(e.c) ^ 0x80000000)
                    return '(%s ~ 0x80000000)' % n(e)
                c = '(%s %s %s)' % (fl(a), o, fl(b))
            else:
                c = '(%s %s %s)' % (n(a), o, n(b))
            push(E(c, 'bool', 1, **dep(a, b))); return True
        if 0x51 <= op <= 0x5A:  # i64 compares (native int64)
            b = pop(); a = pop(); k = op - 0x51; d = dep(a, b)
            if k == 0: c = '(%s == %s)' % (a.c, b.c)
            elif k == 1: c = '(%s ~= %s)' % (a.c, b.c)
            elif k == 2: c = '(%s < %s)' % (a.c, b.c)
            elif k == 3: c = 'ult(%s, %s)' % (a.c, b.c)
            elif k == 4: c = '(%s > %s)' % (a.c, b.c)
            elif k == 5: c = 'ult(%s, %s)' % (b.c, a.c)
            elif k == 6: c = '(%s <= %s)' % (a.c, b.c)
            elif k == 7: c = '(not ult(%s, %s))' % (b.c, a.c)
            elif k == 8: c = '(%s >= %s)' % (a.c, b.c)
            else: c = '(not ult(%s, %s))' % (a.c, b.c)
            push(E(c, 'bool', 1, **d)); return True
        if 0x67 <= op <= 0x78:
            k = op - 0x67
            if k <= 2:
                a = pop(); push(E('%s(%s)' % (['clz32', 'ctz32', 'popcnt32'][k], n(a)), I32, 32, **dep(a))); return True
            if k in (10, 11, 12) and s.stack[-1].t == 'bool' and s.stack[-2].t == 'bool':
                b = s.popc(); a = s.popc()
                push(E('(%s %s %s)' % (a.c, {10: 'and', 11: 'or', 12: '~='}[k], b.c), 'bool', 1, **dep(a, b))); return True
            b = pop(); a = pop(); d = dep(a, b)
            def isc(e): return e.pure_const and e.c.isdigit()
            if k == 3:
                bb = a.b + b.b if a.b is not None and b.b is not None and a.b + b.b <= M32 else None
                if isc(b) and b.c == '0': push(a); return True
                push(E('(%s + %s)' % (a.c, b.c), I32, bb, **d)); return True
            if k == 4:
                if isc(b) and b.c == '0': push(a); return True
                push(E('(%s - %s)' % (a.c, b.c), I32, None, **d)); return True
            if k == 5:
                bb = a.b * b.b if a.b is not None and b.b is not None and a.b * b.b <= M32 else None
                push(E('(%s * %s)' % (a.c, b.c), I32, bb, **d)); return True
            if k == 6: push(E('divs32(%s, %s)' % (n(a), n(b)), I32, M32, **d)); return True
            if k == 7: push(E('(%s // %s)' % (n(a), n(b)), I32, nb(a), **d)); return True
            if k == 8: push(E('rems32(%s, %s)' % (n(a), n(b)), I32, M32, **d)); return True
            if k == 9: push(E('(%s %% %s)' % (n(a), n(b)), I32, nb(a), **d)); return True
            if k == 10:
                bs = [e.b for e in (a, b) if e.b is not None]
                push(E('(%s & %s)' % (a.c, b.c), I32, min(bs) if bs else None, **d)); return True
            if k in (11, 12):
                o = '|' if k == 11 else '~'
                bb = (1 << max(a.b, b.b).bit_length()) - 1 if a.b is not None and b.b is not None else None
                push(E('(%s %s %s)' % (a.c, o, b.c), I32, bb, **d)); return True
            if isc(b): cnt = int(b.c) & 31; cc = str(cnt)
            else: cnt = None; cc = '(%s & 31)' % b.c
            if k == 13:
                bb = a.b << cnt if cnt is not None and a.b is not None and (a.b << cnt) <= M32 else None
                push(E('(%s << %s)' % (a.c, cc), I32, bb, **d)); return True
            if k == 14: push(E('(%s >> %s)' % (sgn(a), cc), I32, None, **d)); return True  # sign bits above 31 shift in
            if k == 15: push(E('(%s >> %s)' % (n(a), cc), I32, nb(a) >> cnt if cnt is not None else nb(a), **d)); return True
            if k == 16: push(E('((%s << %s) | (%s >> (32 - %s)))' % (n(a), cc, n(a), cc), I32, None, **d)); return True
            if k == 17: push(E('((%s >> %s) | (%s << (32 - %s)))' % (n(a), cc, n(a), cc), I32, None, **d)); return True
        if 0x79 <= op <= 0x8A:  # i64: native
            k = op - 0x79
            if k <= 2:
                a = pop(); push(E('%s(%s)' % (['clz64', 'ctz64', 'popcnt64'][k], a.c), I64, None, **dep(a))); return True
            b = pop(); a = pop(); d = dep(a, b)
            o = {3: '+', 4: '-', 5: '*', 10: '&', 11: '|', 12: '~'}
            if k in o: push(E('(%s %s %s)' % (a.c, o[k], b.c), I64, None, **d)); return True
            f = {6: 'divs64', 7: 'divu64', 8: 'rems64', 9: 'remu64'}
            if k in f: push(E('%s(%s, %s)' % (f[k], a.c, b.c), I64, None, **d)); return True
            cc = '(%s & 63)' % b.c
            if k == 13: push(E('(%s << %s)' % (a.c, cc), I64, None, **d)); return True
            if k == 14: push(E('sar64(%s, %s)' % (a.c, cc), I64, None, **d)); return True
            if k == 15: push(E('(%s >> %s)' % (a.c, cc), I64, None, **d)); return True
            if k == 16: push(E('rotl64(%s, %s)' % (a.c, cc), I64, None, **d)); return True
            if k == 17: push(E('rotr64(%s, %s)' % (a.c, cc), I64, None, **d)); return True
        if op == 0xA7: a = pop(); push(E(a.c, I32, None, **dep(a))); return True
        if op == 0xAC: a = pop(); push(E(sgn(a), I64, None, **dep(a))); return True
        if op == 0xAD: a = pop(); push(E(n(a), I64, None, **dep(a))); return True
        if 0xC0 <= op <= 0xC4:
            a = pop(); bits, T = {0xC0: (8, I32), 0xC1: (16, I32), 0xC2: (8, I64), 0xC3: (16, I64), 0xC4: (32, I64)}[op]
            h = 1 << (bits - 1); m = (1 << bits) - 1
            push(E('(((%s & %d) ~ %d) - %d)' % (a.c, m, h, h), T, None, **dep(a))); return True
        if op == 0xFC0A:
            nn = pop(); src = pop(); dst = pop(); s.flush(lambda e: e.m)
            s.emit('mcopy(%s, %s, %s)' % (n(dst), n(src), n(nn))); return True
        if op == 0xFC0B:
            nn = pop(); v = pop(); dst = pop(); s.flush(lambda e: e.m)
            s.emit('mfill(%s, %s, %s)' % (n(dst), n(v), n(nn))); return True
        return False

    def float_op(s, op):
        push, pop, n, sgn = s.push, s.pop, s.n, s.sgn
        if 0x5B <= op <= 0x66:
            b = pop(); a = pop(); k = (op - 0x5B) % 6
            push(E('(%s %s %s)' % (a.c, ['==', '~=', '<', '>', '<=', '>='][k], b.c), 'bool', 1, **dep(a, b))); return True
        if 0x8B <= op <= 0xA6:
            is32 = op <= 0x98; k = op - (0x8B if is32 else 0x99); T = F32 if is32 else F64
            if k <= 6:
                a = pop()
                c = ['math.abs(%s)', '(-%s)', 'fceil(%s)', 'ffloor(%s)', 'ftrunc(%s)', 'fnearest(%s)', 'fsqrt(%s)'][k] % a.c
                if is32 and k == 6: c = 'f32(%s)' % c
                push(E(c, T, None, **dep(a))); return True
            b = pop(); a = pop()
            c = {7: '(%s + %s)', 8: '(%s - %s)', 9: '(%s * %s)', 10: '(%s / %s)', 11: 'fmin(%s, %s)',
                 12: 'fmax(%s, %s)', 13: 'fcopysign(%s, %s)'}[k] % (a.c, b.c)
            if is32 and k <= 10: c = 'f32(%s)' % c
            push(E(c, T, None, **dep(a, b))); return True
        conv = {
            0xA8: ('trunc_s32(%s)', I32, 'f'), 0xA9: ('trunc_u32(%s)', I32, 'f'),
            0xAA: ('trunc_s32(%s)', I32, 'f'), 0xAB: ('trunc_u32(%s)', I32, 'f'),
            0xAE: ('trunc_s64(%s)', I64, 'f'), 0xAF: ('trunc_u64(%s)', I64, 'f'),
            0xB0: ('trunc_s64(%s)', I64, 'f'), 0xB1: ('trunc_u64(%s)', I64, 'f'),
            0xB2: ('f32(%s + 0.0)', F32, 's'), 0xB3: ('f32(%s + 0.0)', F32, 'u'),
            0xB4: ('f32(%s + 0.0)', F32, 'l'), 0xB5: ('f32(u64tof(%s))', F32, 'l'),
            0xB6: ('f32(%s)', F32, 'f'), 0xB7: ('(%s + 0.0)', F64, 's'), 0xB8: ('(%s + 0.0)', F64, 'u'),
            0xB9: ('(%s + 0.0)', F64, 'l'), 0xBA: ('u64tof(%s)', F64, 'l'), 0xBB: ('%s', F64, 'f'),
            0xBC: ('i32_f32(%s)', I32, 'f'), 0xBD: ('i64_f64(%s)', I64, 'f'),
            0xBE: ('f32_i32(%s)', F32, 'u'), 0xBF: ('f64_i64(%s)', F64, 'l'),
        }
        for k in range(8):
            conv[0xFC00 + k] = ('%s(%%s)' % ['tsat_s32', 'tsat_u32', 'tsat_s32', 'tsat_u32',
                                              'tsat_s64', 'tsat_u64', 'tsat_s64', 'tsat_u64'][k], I32 if k < 4 else I64, 'f')
        if op in conv:
            f, T, src = conv[op]; a = pop()
            arg = {'f': a.c, 's': sgn(a), 'u': n(a), 'l': a.c}[src]
            push(E(f % arg, T, M32 if T == I32 else None, **dep(a))); return True
        return False


LUA_RT = r'''
local ult, mtype, floor, ceil, sqrt, huge = math.ult, math.type, math.floor, math.ceil, math.sqrt, math.huge
local spack, sunpack = string.pack, string.unpack
local MEM, MEMPAGES, MAXPAGES = {}, @PAGES@, @MAXP@
for i = 0, @PAGES@ * 65536 - 1 do MEM[i] = 0 end
local function trap(msg) error("wasm trap: " .. msg, 0) end
local function mgrow(n)
  local old = MEMPAGES
  if old + n > MAXPAGES then return 0xFFFFFFFF end
  for i = old * 65536, (old + n) * 65536 - 1 do MEM[i] = 0 end
  MEMPAGES = old + n
  return old
end
local function mcopy(d, s, n)
  if d + n > MEMPAGES * 65536 or s + n > MEMPAGES * 65536 then trap("oob") end
  if n > 0 then table.move(MEM, s, s + n - 1, d) end
end
local function mfill(d, v, n)
  if d + n > MEMPAGES * 65536 then trap("oob") end
  v = v & 0xFF
  for i = d, d + n - 1 do MEM[i] = v end
end
local function clz32(x) if x == 0 then return 32 end local n = 0
  while x & 0x80000000 == 0 do x = x << 1; n = n + 1 end return n end
local function ctz32(x) if x == 0 then return 32 end local n = 0
  while x & 1 == 0 do x = x >> 1; n = n + 1 end return n end
local function popcnt32(x) local n = 0 while x ~= 0 do x = x & (x - 1); n = n + 1 end return n end
local function clz64(x) if x == 0 then return 64 end local n = 0
  while x & math.mininteger == 0 do x = x << 1; n = n + 1 end return n end
local function ctz64(x) if x == 0 then return 64 end local n = 0
  while x & 1 == 0 do x = x >> 1; n = n + 1 end return n end
local function popcnt64(x) local n = 0 while x ~= 0 do x = x & (x - 1); n = n + 1 end return n end
local function divs32(a, b)
  a = (a ~ 0x80000000) - 0x80000000; b = (b ~ 0x80000000) - 0x80000000
  if b == 0 then trap("integer divide by zero") end
  if a == -0x80000000 and b == -1 then trap("integer overflow") end
  local q = (a < 0 and -a or a) // (b < 0 and -b or b)
  if (a < 0) ~= (b < 0) then q = -q end
  return q & 0xFFFFFFFF
end
local function rems32(a, b)
  a = (a ~ 0x80000000) - 0x80000000; b = (b ~ 0x80000000) - 0x80000000
  if b == 0 then trap("integer divide by zero") end
  return math.fmod(a, b) & 0xFFFFFFFF
end
local function divs64(a, b)
  if b == 0 then trap("integer divide by zero") end
  if a == math.mininteger and b == -1 then trap("integer overflow") end
  local q = a // b
  if q < 0 and q * b ~= a then q = q + 1 end
  return q
end
local function rems64(a, b)
  if b == 0 then trap("integer divide by zero") end
  if b == -1 then return 0 end
  return math.fmod(a, b)
end
local function divu64(a, b)
  if b == 0 then trap("integer divide by zero") end
  if b < 0 then return ult(a, b) and 0 or 1 end
  if a >= 0 then return a // b end
  local q = ((a >> 1) // b) << 1
  local r = a - q * b
  if not ult(r, b) then q = q + 1 end
  return q
end
local function remu64(a, b) return a - divu64(a, b) * b end
local function sar64(x, k) if k == 0 then return x end
  if x >= 0 then return x >> k end return (x >> k) | ~(-1 >> k) end
local function rotl64(x, k) if k == 0 then return x end return (x << k) | (x >> (64 - k)) end
local function rotr64(x, k) if k == 0 then return x end return (x >> k) | (x << (64 - k)) end
local function f32(x) return (sunpack("<f", spack("<f", x))) end
local function i32_f32(x) return (sunpack("<I4", spack("<f", x))) end
local function f32_i32(x) return (sunpack("<f", spack("<I4", x))) end
local function i64_f64(x) return (sunpack("<i8", spack("<d", x))) end
local function f64_i64(x) return (sunpack("<d", spack("<i8", x))) end
local function ldf32(a) local M = MEM
  return f32_i32(M[a] | M[a+1] << 8 | M[a+2] << 16 | M[a+3] << 24) end
local function ldf64(a) local M = MEM local lo = M[a] | M[a+1] << 8 | M[a+2] << 16 | M[a+3] << 24
  local hi = M[a+4] | M[a+5] << 8 | M[a+6] << 16 | M[a+7] << 24
  return f64_i64(lo | hi << 32) end
local function stf32(a, v) local x = i32_f32(v) local M = MEM
  M[a] = x & 0xFF; M[a+1] = (x >> 8) & 0xFF; M[a+2] = (x >> 16) & 0xFF; M[a+3] = (x >> 24) & 0xFF end
local function stf64(a, v) local x = i64_f64(v) local M = MEM
  for k = 0, 7 do M[a+k] = (x >> (8 * k)) & 0xFF end end
local function u64tof(x) if x >= 0 then return x + 0.0 end return (x >> 1 | (x & 1)) * 2.0 end
local function fin(x) return x == x and x ~= huge and x ~= -huge end
local function ffloor(x) if not fin(x) or x == 0 then return x end return floor(x) + 0.0 end
local function fceil(x) if not fin(x) or x == 0 then return x end local r = ceil(x) + 0.0
  if r == 0 and x < 0 then return -0.0 end return r end
local function ftrunc(x) if x >= 0 then return ffloor(x) else return fceil(x) end end
local function fnearest(x)
  if not fin(x) or x == 0 then return x end
  local r = floor(x + 0.5)
  if r - x == 0.5 and r % 2 ~= 0 then r = r - 1 end
  r = r + 0.0
  if r == 0 and x < 0 then return -0.0 end
  return r
end
local function fsqrt(x) if x < 0 then return 0.0/0.0 end return sqrt(x) end
local function fmin(a, b) if a ~= a or b ~= b then return 0.0/0.0 end
  if a == 0 and b == 0 then return (1/a < 0) and a or b end return a < b and a or b end
local function fmax(a, b) if a ~= a or b ~= b then return 0.0/0.0 end
  if a == 0 and b == 0 then return (1/a < 0) and b or a end return a > b and a or b end
local function fcopysign(a, b) a = math.abs(a)
  if b < 0 or (b == 0 and 1/b < 0) then return -a end return a end
local function trunc_any(x, lo, hi)
  if x ~= x then trap("invalid conversion to integer") end
  local t = x >= 0 and floor(x) or ceil(x)
  if not (t >= lo and t <= hi) then trap("integer overflow") end
  return math.tointeger(t) or trap("integer overflow")
end
local function trunc_s32(x) return trunc_any(x, -2147483648.0, 2147483647.0) & 0xFFFFFFFF end
local function trunc_u32(x) return trunc_any(x, 0.0, 4294967295.0) end
local function trunc_s64(x) if x ~= x then trap("invalid conversion to integer") end
  if x < -9223372036854775808.0 or x >= 9223372036854775808.0 then trap("integer overflow") end
  return math.tointeger(x >= 0 and floor(x) or ceil(x)) end
local function trunc_u64(x) if x ~= x then trap("invalid conversion to integer") end
  if x <= -1.0 or x >= 18446744073709551616.0 then trap("integer overflow") end
  if x >= 9223372036854775808.0 then return math.tointeger(floor(x - 9223372036854775808.0)) | math.mininteger end
  return math.tointeger(floor(x)) end
local function tsat_s32(x) if x ~= x then return 0 end if x >= 2147483647.0 then return 0x7FFFFFFF end
  if x <= -2147483648.0 then return 0x80000000 end return trunc_s32(x) end
local function tsat_u32(x) if x ~= x or x <= 0 then return 0 end if x >= 4294967295.0 then return 0xFFFFFFFF end
  return trunc_u32(x) end
local function tsat_s64(x) if x ~= x then return 0 end if x >= 9223372036854775807.0 then return math.maxinteger end
  if x <= -9223372036854775808.0 then return math.mininteger end return trunc_s64(x) end
local function tsat_u64(x) if x ~= x or x <= 0 then return 0 end if x >= 18446744073709551615.0 then return -1 end
  return trunc_u64(x) end
local function memread(p, n) local M, t = MEM, {}
  for i = 0, n - 1 do t[i + 1] = string.char(M[p + i]) end return table.concat(t) end
'''


def lua_str(b):
    return '"' + ''.join('\\%03d' % c for c in b) + '"'


class LuaModGen:
    def __init__(s, mod):
        s.mod = mod; s.types = mod['types']
        s.fimports = [im for im in mod['imports'] if im[0] == 'func']
        s.nimp = len(s.fimports)
        s.gtypes = [g[0] for g in mod['globals']]; s.gmut = [g[1] for g in mod['globals']]
        s.ginit = [g[2] for g in mod['globals']]

    def ftype(s, fi):
        if fi < s.nimp: return s.types[s.fimports[fi][3]]
        return s.types[s.mod['funcs'][fi - s.nimp]]

    def fname(s, fi): return 'I%d' % fi if fi < s.nimp else 'f%d' % fi

    FuncGen = LuaFuncGen
    RT = LUA_RT

    def data_line(s, off, d):
        return 'do local d, o = %s, %d for i = 1, #d do MEM[o + i - 1] = string.byte(d, i) end end' % (lua_str(d), off)

    def gconst(s, t, v):
        if t == I64: return '0x%x' % (v & M64)
        if t in (F32, F64): return lflit(v)
        return str(v)

    def gen(s):
        mod = s.mod
        mem = mod['mems'][0] if mod['mems'] else (0, 0)
        out = ['-- generated by w2lua from WebAssembly', 'return function(imports)']
        out.append(s.RT.replace('@PAGES@', str(mem[0])).replace('@MAXP@', str(mem[1] if mem[1] is not None else 65536)))
        if s.gtypes:
            out.append('local ' + ', '.join('G%d' % i for i in range(len(s.gtypes))) + ' = ' +
                       ', '.join(s.gconst(t, init[1]) for t, init in zip(s.gtypes, s.ginit)))
        for ti, (mn, mx) in enumerate(mod['tables']):
            out.append('local TBL%d = {}' % ti)
        for i, im in enumerate(s.fimports):
            out.append('local I%d = imports[%r][%r]' % (i, im[1], im[2]))
        nf = len(mod['codes'])
        out.append('local ' + ', '.join('f%d' % (s.nimp + k) for k in range(nf)))
        for k, ((locs, body), tidx) in enumerate(zip(mod['codes'], mod['funcs'])):
            out.append(s.FuncGen(s, s.nimp + k, tidx, locs, body).gen())
        for ti, off, fs in mod['elems']:
            for j, f in enumerate(fs):
                out.append('TBL%d[%d] = %s' % (ti, off[1] + j, s.fname(f)))
        for off, d in mod['datas']:
            if off is None: continue
            out.append(s.data_line(off[1], d))
        if mod['start'] is not None: out.append('%s()' % s.fname(mod['start']))
        ex = []
        for n_, k, i in mod['exports']:
            if k == 0: ex.append('[%r] = %s' % (n_, s.fname(i)))
        ex.append('memread = memread')
        out.append('return {%s}' % ', '.join(ex))
        out.append('end')
        return '\n'.join(out) + '\n'


def translate(wasm_path, out_path, gen_cls=LuaModGen):
    mod = parse(open(wasm_path, 'rb').read())
    src = gen_cls(mod).gen()
    open(out_path, 'w').write(src)
    return out_path


if __name__ == '__main__':
    print(translate(sys.argv[1], sys.argv[2]))
