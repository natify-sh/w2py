"""w2py: WebAssembly -> CPython module (.py + .pyc) AOT translator (PoC).

Design:
  * every wasm function -> a Python function; wasm locals -> Python fast locals
  * the operand stack is eliminated at translation time (symbolic stack -> nested expressions)
  * structured control flow -> if/while/break/continue; multi-level br via a `_br` label register,
    `block ... br_if 0 ...` at the block's top level -> plain `if not cond:` nesting (no loop)
  * lazy integer wrapping: values carry a static upper bound; `& 0xFFFFFFFF` is emitted only where the
    value can actually leave [0, 2**32) and something observes it (compare, store, local.set, shift-right…)
  * linear memory is a bytearray; loads/stores via pre-bound struct.unpack_from / pack_into
    (bounds-checked -> out-of-bounds access raises, i.e. traps)
"""
import struct, sys, os, marshal, importlib.util

M32, M64 = 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF
I32, I64, F32, F64 = 0x7F, 0x7E, 0x7D, 0x7C

TY = {0x7F: 'i64', 0x7E: 'int', 0x7D: 'float', 0x7C: 'float', 'bool': 'bool'}
TZ = {'i64': '0', 'int': '0', 'float': '0.0', 'bool': 'False'}
TN = {I32: 'i32', I64: 'i64', F32: 'f32', F64: 'f64', 0x70: 'ref', 0x6F: 'ref'}


# ----------------------------------------------------------------------------- binary reader
class R:
    def __init__(s, b, p=0):
        s.b, s.p = b, p

    def u8(s):
        v = s.b[s.p]; s.p += 1; return v

    def u(s):
        r = sh = 0
        while True:
            x = s.b[s.p]; s.p += 1
            r |= (x & 0x7F) << sh; sh += 7
            if x < 0x80: return r

    def s(s, bits):
        r = sh = 0
        while True:
            x = s.b[s.p]; s.p += 1
            r |= (x & 0x7F) << sh; sh += 7
            if x < 0x80: break
        if x & 0x40: r -= 1 << sh
        return r

    def bytes(s, n):
        v = s.b[s.p:s.p + n]; s.p += n; return v

    def name(s):
        return s.bytes(s.u()).decode()


def parse(data):
    r = R(data, 8)
    assert data[:4] == b'\0asm'
    mod = dict(types=[], imports=[], funcs=[], tables=[], mems=[], globals=[], exports=[], start=None,
               elems=[], codes=[], datas=[])

    def initexpr():
        op = r.u8()
        if op == 0x41: v = ('i32', r.s(32) & M32)
        elif op == 0x42: v = ('i64', r.s(64) & M64)
        elif op == 0x43: v = ('f32', struct.unpack('<f', r.bytes(4))[0])
        elif op == 0x44: v = ('f64', struct.unpack('<d', r.bytes(8))[0])
        elif op == 0x23: v = ('global', r.u())
        elif op == 0xD2: v = ('func', r.u())
        else: raise NotImplementedError('initexpr %x' % op)
        assert r.u8() == 0x0B
        return v

    while r.p < len(data):
        sid = r.u8(); size = r.u(); end = r.p + size
        if sid == 1:
            for _ in range(r.u()):
                assert r.u8() == 0x60
                ps = [r.u8() for _ in range(r.u())]; rs = [r.u8() for _ in range(r.u())]
                mod['types'].append((ps, rs))
        elif sid == 2:
            for _ in range(r.u()):
                m, n, k = r.name(), r.name(), r.u8()
                if k == 0: mod['imports'].append(('func', m, n, r.u()))
                elif k == 2:
                    fl = r.u8(); mn = r.u(); mx = r.u() if fl & 1 else None
                    mod['imports'].append(('mem', m, n, (mn, mx)))
                elif k == 3: t = r.u8(); mut = r.u8(); mod['imports'].append(('global', m, n, (t, mut)))
                else: raise NotImplementedError('import kind %d' % k)
        elif sid == 3:
            mod['funcs'] = [r.u() for _ in range(r.u())]
        elif sid == 4:
            for _ in range(r.u()):
                t = r.u8(); fl = r.u8(); mn = r.u(); mx = r.u() if fl & 1 else None
                mod['tables'].append((mn, mx))
        elif sid == 5:
            for _ in range(r.u()):
                fl = r.u8(); mn = r.u(); mx = r.u() if fl & 1 else None
                mod['mems'].append((mn, mx))
        elif sid == 6:
            for _ in range(r.u()):
                t = r.u8(); mut = r.u8(); mod['globals'].append((t, mut, initexpr()))
        elif sid == 7:
            for _ in range(r.u()):
                n = r.name(); k = r.u8(); i = r.u(); mod['exports'].append((n, k, i))
        elif sid == 8:
            mod['start'] = r.u()
        elif sid == 9:
            for _ in range(r.u()):
                fl = r.u()
                if fl == 0:
                    off = initexpr(); fs = [r.u() for _ in range(r.u())]
                    mod['elems'].append((0, off, fs))
                elif fl == 2:
                    ti = r.u(); off = initexpr(); assert r.u8() == 0; fs = [r.u() for _ in range(r.u())]
                    mod['elems'].append((ti, off, fs))
                elif fl in (1, 3):
                    assert r.u8() == 0; [r.u() for _ in range(r.u())]  # passive/declarative: ignore
                else:
                    raise NotImplementedError('elem flags %d' % fl)
        elif sid == 10:
            for _ in range(r.u()):
                sz = r.u(); e = r.p + sz
                locs = []
                for _ in range(r.u()):
                    n = r.u(); t = r.u8(); locs += [t] * n
                mod['codes'].append((locs, data[r.p:e]))
                r.p = e
        elif sid == 11:
            for _ in range(r.u()):
                fl = r.u()
                if fl == 0: off = initexpr(); d = r.bytes(r.u()); mod['datas'].append((off, d))
                elif fl == 1: d = r.bytes(r.u()); mod['datas'].append((None, d))
                elif fl == 2: r.u(); off = initexpr(); d = r.bytes(r.u()); mod['datas'].append((off, d))
        r.p = end
    return mod


# ----------------------------------------------------------------------------- instruction decode
MEMOPS = set(range(0x28, 0x3F))


def decode(body):
    r = R(body)
    out = []
    while r.p < len(body):
        op = r.u8()
        if op in (0x02, 0x03, 0x04):
            b = body[r.p]
            if b == 0x40: r.p += 1; bt = ([], [])
            elif b in TN: r.p += 1; bt = ([], [b])
            else: bt = ('idx', r.s(33))
            out.append((op, bt))
        elif op in (0x0C, 0x0D, 0x10, 0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0xD2):
            out.append((op, r.u()))
        elif op == 0x0E:
            ls = [r.u() for _ in range(r.u())]; out.append((op, ls, r.u()))
        elif op == 0x11:
            ti = r.u(); tb = r.u(); out.append((op, ti, tb))
        elif op in MEMOPS:
            al = r.u(); off = r.u(); out.append((op, off, al))
        elif op in (0x3F, 0x40):
            r.u8(); out.append((op,))
        elif op == 0x41: out.append((op, r.s(32) & M32))
        elif op == 0x42: out.append((op, r.s(64) & M64))
        elif op == 0x43: out.append((op, struct.unpack('<f', r.bytes(4))[0]))
        elif op == 0x44: out.append((op, struct.unpack('<d', r.bytes(8))[0]))
        elif op == 0x1C:
            [r.u8() for _ in range(r.u())]; out.append((0x1B,))
        elif op == 0xD0: r.u8(); out.append((op,))
        elif op == 0xFC:
            sub = r.u()
            if sub <= 7: out.append((0xFC00 + sub,))
            elif sub == 8: seg = r.u(); r.u8(); out.append((0xFC08, seg))
            elif sub == 9: out.append((0xFC09, r.u()))
            elif sub == 10: r.u8(); r.u8(); out.append((0xFC0A,))
            elif sub == 11: r.u8(); out.append((0xFC0B,))
            else: raise NotImplementedError('0xFC %d' % sub)
        else:
            out.append((op,))
    return out


# ----------------------------------------------------------------------------- expressions
class E:
    """Symbolic stack value.  b: inclusive upper bound if value is known to lie in [0, b]
    (i.e. it is a canonical unsigned int), None if it may be out of range (needs masking).
    For floats b is unused.  Ls/m/g: what state the expression reads."""
    __slots__ = ('c', 't', 'b', 'Ls', 'm', 'g', 'k', 'w')

    def __init__(s, c, t, b=None, Ls=(), m=False, g=(), k=False, w=None):
        s.c, s.t, s.b, s.Ls, s.m, s.g, s.k, s.w = c, t, b, frozenset(Ls), m, frozenset(g), k, w
        # w: for a dirty value, 'up' = lies in [0, 2*MOD) / 'down' = lies in (-MOD, MOD):
        #    at most one wrap, so a compare+adjust replaces the (slow) & mask

    @property
    def pure_const(s):
        return not s.Ls and not s.m and not s.g


def dep(*es, **kw):
    Ls, m, g = set(), False, set()
    for e in es:
        Ls |= e.Ls; m = m or e.m; g |= e.g
    return dict(Ls=Ls, m=m or kw.get('m', False), g=g | set(kw.get('g', ())))


def flit(v):
    if v != v: return 'NAN'
    if v in (float('inf'), float('-inf')): return 'INF' if v > 0 else '(-INF)'
    r = repr(v)
    return '(%s)' % r if r.startswith('-') else r


class Ctl:
    __slots__ = ('kind', 'mode', 'id', 'res', 'rv', 'h', 'pyloop', 'nest', 'esc', 'has_else', 'live_then',
                 'mark', 'nests', 'tg')

    def __init__(s, kind, mode, id_, res, rv, h):
        s.kind, s.mode, s.id, s.res, s.rv, s.h = kind, mode, id_, res, rv, h
        s.pyloop = mode == 'loop'
        s.nests = []; s.tg = False
        s.nest = 0; s.esc = set(); s.has_else = False; s.live_then = False; s.mark = None


# ----------------------------------------------------------------------------- function compiler
class FuncGen:
    def __init__(s, mg, fidx, tidx, locs, body):
        s.mg = mg; s.fidx = fidx
        s.params, s.results = mg.types[tidx]
        s.ltypes = list(s.params) + locs
        s.ins = decode(body)
        s.lines = []; s.ind = 1
        s.stack = []; s.tmp = 0; s.dead = False
        s.globals_set = set(); s.uses_br = False
        s.ctl = []
        s.lmap = None; s.ninl = 0
        s.tmpty = {}; s.rvty = {}; s.uses_w = set()

    # --- emit helpers
    def emit(s, line):
        s.lines.append('    ' * s.ind + line)

    def open(s, line):
        s.emit(line); s.ind += 1
        return len(s.lines)

    def close(s, mark):
        if len(s.lines) == mark: s.emit('pass')
        s.ind -= 1

    def newtmp(s, t=None):
        s.tmp += 1; nm = 't%d' % s.tmp
        if t is not None: s.tmpty[nm] = t
        return nm

    def lname(s, i):
        if s.lmap is not None: return s.lmap[i]
        return 'p%d' % i if i < len(s.params) else 'l%d' % i

    # --- normalisation
    def n(s, e):
        """canonical (unsigned, in-range) code for an integer expression"""
        if e.t in (F32, F64) or e.b is not None: return e.c
        if e.w and not LEGACY:
            MAXS, MODS = ('0xFFFFFFFF', '0x100000000') if e.t == I32 else ('0xFFFFFFFFFFFFFFFF', '0x10000000000000000')
            w = '_w' if e.t == I32 else '_W'
            s.uses_w.add(w)
            if e.w == 'up': return '(%s if (%s:=%s) <= %s else %s-%s)' % (w, w, e.c, MAXS, w, MODS)
            return '(%s if (%s:=%s) >= 0 else %s+%s)' % (w, w, e.c, w, MODS)
        return '(%s&%s)' % (e.c, '0xFFFFFFFF' if e.t == I32 else '0xFFFFFFFFFFFFFFFF')

    def nb(s, e):
        if e.b is not None: return e.b
        return M32 if e.t == I32 else M64

    def sgn(s, e):
        """signed value code"""
        W = 32 if e.t == I32 else 64
        if e.b is not None and e.b < (1 << (W - 1)): return e.c
        h = '0x80000000' if W == 32 else '0x8000000000000000'
        return '((%s^%s)-%s)' % (s.n(e), h, h)

    def cond(s, e):
        if e.t == 'bool' or e.b is not None: return e.c
        return s.n(e)

    def canon(s, e):
        """turn bool into int-typed i32 canonical expression (bool already is an int)"""
        return e

    # --- stack
    def push(s, e): s.stack.append(e)

    def pop(s):
        e = s.stack.pop()
        if e.t == 'bool':  # keep ints exact (bool breaks CPython's int specialisations)
            e = E('(1 if %s else 0)' % e.c, I32, 1, e.Ls, e.m, e.g)
        return e

    def popc(s):
        """pop a value used only for its truthiness"""
        return s.stack.pop()

    def popn(s, n):
        if n == 0: return []
        v = [s.pop() for _ in range(n)]; v.reverse(); return v

    def materialize(s, i):
        e = s.stack[i]
        if e.k: return  # already a temp / plain name that is not going to change
        t = s.newtmp(e.t)
        s.emit('%s = %s' % (t, e.c))
        s.stack[i] = E(t, e.t, e.b, k=True, w=e.w)

    def flush(s, pred=lambda e: not e.pure_const or True):
        for i, e in enumerate(s.stack):
            if e.k or (e.pure_const and e.c[:1] not in 'pl'): continue
            if pred(e): s.materialize(i)

    def flush_all(s):
        for i, e in enumerate(s.stack):
            if not e.k and not e.pure_const: s.materialize(i)

    def flush_mem(s): s.flush(lambda e: e.m)

    def flush_call(s): s.flush(lambda e: e.m or e.g)

    def flush_local(s, name): s.flush(lambda e: name in e.Ls)

    def flush_global(s, gname): s.flush(lambda e: gname in e.g)

    # --- block types
    def bt(s, b):
        if b[0] == 'idx': return s.mg.types[b[1]]
        return b

    # --- prepass: who targets which block, and how
    def prepass(s):
        info = {}
        st = [-1]
        info[-1] = [False, True]  # targeted, all-toplevel
        for i, ins in enumerate(s.ins):
            op = ins[0]
            if op in (0x02, 0x03, 0x04):
                st.append(i); info[i] = [False, True]
            elif op == 0x0B:
                st.pop()
            elif op in (0x0C, 0x0D):
                t = st[-1 - ins[1]]; info[t][0] = True
                if ins[1] != 0: info[t][1] = False
            elif op == 0x0E:
                for d in ins[1] + [ins[2]]:
                    t = st[-1 - d]; info[t][0] = True; info[t][1] = False
        return info

    # --- branching
    #
    # Python loops exist only for wasm `loop`s that are branched to.  `block`/`if` exits are realised
    # structurally: a br to the innermost block at its top level turns the rest of the block into
    # `if not cond:`; anything else sets the label register `_br` and the rest of each enclosing
    # construct is guarded (an `else:`/`if not _br:` nest), breaking out of python loops on the way.
    def assign_results(s, T, n):
        if not T.rv: return
        vals = s.stack[len(s.stack) - n:] if n else []
        codes = [s.n(v) if v.t in (I32, I64) else v.c for v in vals]
        s.emit('%s = %s' % (', '.join(T.rv), ', '.join(codes)))

    def br_code(s, depth):
        """emit a branch; returns True if control is transferred by a python jump (continue/break/
        return), False if only the `_br` flag was set (caller must guard the following code)"""
        T = s.ctl[-1 - depth]
        if T.kind == 'func':
            s.emit_return(); return True
        if T.kind != 'loop': s.assign_results(T, len(T.res))
        if depth == 0 and T.kind != 'loop':
            return False  # rest of T is skipped structurally by the caller
        P = None
        for F in reversed(s.ctl[len(s.ctl) - 1 - depth:]):
            if F.pyloop: P = F; break
        if P is T:  # only possible for a loop target
            s.emit('continue'); return True
        s.uses_br = True
        s.emit('_br = %d' % T.id)
        if P is not None:
            s.emit('break'); P.esc.add(T.id); return True
        s.ctl[-1].esc.add(T.id)
        return False

    def emit_return(s):
        n = len(s.results)
        if n == 0: s.emit('return')
        else:
            vals = s.stack[len(s.stack) - n:]
            codes = [s.n(v) if v.t in (I32, I64) else v.c for v in vals]
            s.emit('return ' + ', '.join(codes))

    def nest(s, F, line):
        F.nests.append(s.open(line))

    def propagate(s, F):
        """F was just closed (popped).  Route pending `_br` exits to the new top frame."""
        esc = F.esc - {F.id}
        if not esc: return
        P = s.ctl[-1]
        if P.pyloop:
            mk = s.open('if _br:')
            if P.id in esc:
                if esc == {P.id}:
                    s.emit('_br = 0'); s.emit('continue')
                else:
                    m2 = s.open('if _br == %d:' % P.id); s.emit('_br = 0'); s.emit('continue'); s.close(m2)
                    s.emit('break')
            else:
                s.emit('break')
            s.close(mk)
            P.esc |= esc - {P.id}
        else:
            s.nest(P, 'if not _br:')
            P.esc |= esc

    # --- main
    def gen(s):
        info = s.prepass()
        ids = iter(range(1, 1 << 30))
        s.ctl.append(Ctl('func', 'func', 0, s.results, [], 0))
        ins = s.ins
        i = 0; N = len(ins); deadd = 0
        while i < N:
            x = ins[i]; op = x[0]
            if s.dead:
                if op in (0x02, 0x03, 0x04):
                    deadd += 1; i += 1; continue
                if deadd:
                    if op == 0x0B: deadd -= 1
                    i += 1; continue
                if op not in (0x0B, 0x05):
                    i += 1; continue
            i += 1
            st = s.stack
            if op == 0x0B:  # end
                if len(s.ctl) == 1:
                    if not s.dead and s.results: s.emit_return()
                    break
                s.end_frame(); continue
            if op == 0x05:  # else
                F = s.ctl[-1]
                if not s.dead: s.assign_results(F, len(F.res)); F.live_then = True
                del st[F.h:]
                for mk in reversed(F.nests): s.close(mk)
                F.nests = []
                s.close(F.mark)
                F.mark = s.open('else:')
                F.has_else = True
                s.dead = False
                continue
            if op in (0x02, 0x03, 0x04):
                ps, rs = s.bt(x[1])
                if ps: raise NotImplementedError('block params')
                tg, top = info[i - 1]
                if op == 0x04:
                    c = s.popc(); cc = s.cond(c)
                if op == 0x02: mode = 'nest' if top and not rs else 'flag'
                elif op == 0x03: mode = 'loop' if tg else 'inline'
                else: mode = 'flag'
                s.flush_all()
                kind = {2: 'block', 3: 'loop', 4: 'if'}[op]
                fid = next(ids)
                rv = ['r%d_%d' % (fid, j) for j in range(len(rs))]
                for nm_, t_ in zip(rv, rs): s.rvty[nm_] = t_
                F = Ctl(kind, mode, fid, rs, rv, len(st))
                F.tg = tg
                s.ctl.append(F)
                if F.pyloop: F.mark = s.open('while True:')
                if kind == 'if': F.mark = s.open('if %s:' % cc)
                continue
            if op == 0x0C:  # br
                d = x[1]; T = s.ctl[-1 - d]
                if not (d == 0 and T.mode == 'nest'):
                    s.br_code(d)
                s.dead = True; continue
            if op == 0x0D:  # br_if
                c = s.popc(); cc = s.cond(c); d = x[1]; T = s.ctl[-1 - d]
                if d == 0 and T.mode == 'nest':
                    s.nest(T, 'if not %s:' % cc); continue
                if T.kind != 'loop' and T.res: s.flush_all()
                m = s.open('if %s:' % cc)
                jumped = s.br_code(d)
                s.close(m)
                if not jumped:
                    s.nest(s.ctl[-1], 'else:')
                continue
            if op == 0x0E:  # br_table
                e = s.pop(); idx = s.n(e)
                s.flush_all()
                if not idx.isidentifier():
                    t = s.newtmp(I32); s.emit('%s = %s' % (t, idx)); idx = t
                # runs of consecutive indices with the same target; the last run ([n, 2**32)) is the default
                runs = []
                for k, d in enumerate(list(x[1]) + [x[2]]):
                    if not runs or runs[-1][1] != d: runs.append((k, d))
                if BRTREE and len(runs) > BRTREE:
                    # Python has no computed goto: a balanced tree of `<` tests over the runs, log2(runs)
                    # comparisons instead of a linear chain (the same lowering compilers use for sparse switches)
                    def tree(rs):
                        if len(rs) == 1: s.br_code(rs[0][1]); return
                        h = len(rs) // 2
                        m = s.open('if %s < %d:' % (idx, rs[h][0])); tree(rs[:h]); s.close(m)
                        m = s.open('else:'); tree(rs[h:]); s.close(m)
                    tree(runs)
                    s.dead = True; continue
                groups = {}
                for k, d in enumerate(x[1]):
                    if d != x[2]: groups.setdefault(d, []).append(k)
                first = True
                for d, ks in groups.items():
                    if len(ks) == 1: c = '%s == %d' % (idx, ks[0])
                    elif ks == list(range(ks[0], ks[-1] + 1)): c = '%d <= %s <= %d' % (ks[0], idx, ks[-1])
                    else: c = '%s in %r' % (idx, tuple(ks))
                    m = s.open(('if ' if first else 'elif ') + c + ':'); first = False
                    s.br_code(d); s.close(m)
                if first:
                    s.br_code(x[2])
                else:
                    m = s.open('else:'); s.br_code(x[2]); s.close(m)
                s.dead = True; continue
            if op == 0x0F:
                s.emit_return(); s.dead = True; continue
            if op == 0x00:
                s.emit('raise Trap("unreachable")'); s.dead = True; continue
            if op == 0x01: continue
            if s.dead: raise AssertionError('dead code leak')
            s.simple(x)
        body = s.lines
        fname = 'f%d' % s.fidx
        if TYPED:
            if s.results and not (body and body[-1].strip().startswith('return')):
                body.append('    raise Trap("fallthrough")')
            args = ', '.join('%s: %s' % (s.lname(j), TY[s.params[j]]) for j in range(len(s.params)))
            ret = 'None' if not s.results else TY[s.results[0]] if len(s.results) == 1 else \
                'tuple[%s]' % ', '.join(TY[t] for t in s.results)
            head = ['def %s(%s) -> %s:' % (fname, args, ret)]
            decl = [(s.lname(j), TY[s.ltypes[j]]) for j in range(len(s.params), len(s.ltypes))]
            decl += [(k, TY[v]) for k, v in s.tmpty.items()] + [(k, TY[v]) for k, v in s.rvty.items()]
            if s.uses_br: decl.append(('_br', 'i64'))
            if '_w' in s.uses_w: decl.append(('_w', 'i64'))
            if '_W' in s.uses_w: decl.append(('_W', 'int'))
            head += ['    %s: %s = %s' % (nm, t, TZ[t]) for nm, t in decl]
            if not body: body = ['    pass']
            return '\n'.join(head + body) + '\n'
        args = ', '.join(s.lname(j) for j in range(len(s.params)))
        head = ['def %s(%s):' % (fname, args)]
        if s.globals_set: head.append('    global ' + ', '.join(sorted(s.globals_set)))
        inits = [s.lname(j) for j in range(len(s.params), len(s.ltypes))]
        fl = [s.lname(j) for j in range(len(s.params), len(s.ltypes)) if s.ltypes[j] in (F32, F64)]
        il = [v for v in inits if v not in fl]
        if il: head.append('    ' + ' = '.join(il) + ' = 0')
        if fl: head.append('    ' + ' = '.join(fl) + ' = 0.0')
        if s.uses_br: head.append('    _br = 0')
        if not body: body = ['    pass']
        return '\n'.join(head + body) + '\n'

    def end_frame(s):
        F = s.ctl[-1]
        live = not s.dead
        if live: s.assign_results(F, len(F.res))
        del s.stack[F.h:]
        for mk in reversed(F.nests): s.close(mk)
        if F.kind == 'if':
            s.close(F.mark)
        if F.pyloop:
            s.emit('break'); s.close(F.mark)
        s.ctl.pop()
        if F.kind != 'loop' and F.id in F.esc:
            if F.esc - {F.id}:
                m = s.open('if _br == %d:' % F.id); s.emit('_br = 0'); s.close(m)
            else:
                s.emit('_br = 0')
        s.propagate(F)
        if F.kind == 'if':
            after = live or F.live_then or not F.has_else or F.tg
        elif F.kind == 'loop':
            after = live
        else:
            after = live or F.tg
        s.dead = not after
        for j, t in enumerate(F.res):
            s.push(E(F.rv[j], t, (M32 if t == I32 else M64) if t in (I32, I64) else None, k=True))


    def inline(s, fi):
        """splice a straight-line callee into this function: its locals become fresh caller
        locals, its operand stack continues on ours, its result stays on the stack"""
        ps, rs = s.mg.ftype(fi)
        locs, ins = s.mg.inl[fi]
        args = s.popn(len(ps))
        s.flush_call()
        s.ninl += 1
        pre = 'i%d_' % s.ninl
        lt = list(ps) + list(locs)
        names = [pre + str(j) for j in range(len(lt))]
        for nm, a in zip(names, args):
            s.emit('%s = %s' % (nm, s.n(a) if a.t in (I32, I64) else a.c))
        for j in range(len(ps), len(lt)):
            s.emit('%s = %s' % (names[j], '0.0' if lt[j] in (F32, F64) else '0'))
        saved = s.lmap, s.ltypes
        s.lmap, s.ltypes = names, lt
        try:
            for y in ins[:-1]:  # drop the final `end`
                s.simple(y)
        finally:
            s.lmap, s.ltypes = saved

    # --- straight-line ops
    def simple(s, x):
        op = x[0]
        push, pop = s.push, s.pop
        n, nb, sgn = s.n, s.nb, s.sgn
        mg = s.mg
        if op == 0x1A: pop(); return
        if op == 0x1B:
            c = s.popc(); b = pop(); a = pop()
            bb = max(a.b, b.b) if a.b is not None and b.b is not None else None
            push(E('(%s if %s else %s)' % (a.c, s.cond(c), b.c), a.t, bb, **dep(a, b, c))); return
        if op == 0x20:
            nm = s.lname(x[1]); t = s.ltypes[x[1]]
            push(E(nm, t, (M32 if t == I32 else M64) if t in (I32, I64) else None, Ls=[nm])); return
        if op in (0x21, 0x22):
            nm = s.lname(x[1]); v = pop()
            s.flush_local(nm)
            if v.t in (I32, I64) and v.b is None and v.w:
                MAXS, MODS = ('0xFFFFFFFF', '0x100000000') if v.t == I32 else ('0xFFFFFFFFFFFFFFFF', '0x10000000000000000')
                s.emit('%s = %s' % (nm, v.c))
                s.emit(('if %s > %s: %s -= %s' if v.w == 'up' else 'if %s < 0: %s += %s')
                       % ((nm, MAXS, nm, MODS) if v.w == 'up' else (nm, nm, MODS)))
                if op == 0x22:
                    t = s.ltypes[x[1]]
                    push(E(nm, t, (M32 if t == I32 else M64), Ls=[nm]))
                return
            code = n(v) if v.t in (I32, I64) else v.c
            last = s.lines[-1] if s.lines else ''
            pre = '    ' * s.ind + code + ' = '
            if v.k and code[:1] == 't' and last.startswith(pre):
                s.lines[-1] = '    ' * s.ind + nm + ' = ' + last[len(pre):]
            else:
                s.emit('%s = %s' % (nm, code))
            if op == 0x22:
                t = s.ltypes[x[1]]
                push(E(nm, t, (M32 if t == I32 else M64) if t in (I32, I64) else None, Ls=[nm]))
            return
        if op == 0x23:
            g = mg.gname(x[1]); t = mg.gtypes[x[1]]
            if mg.gmut[x[1]]:
                push(E(g, t, (M32 if t == I32 else M64) if t in (I32, I64) else None, g=[g]))
            else:
                push(E(g, t, (M32 if t == I32 else M64) if t in (I32, I64) else None, k=True))
            return
        if op == 0x24:
            g = mg.gname(x[1]); v = pop()
            s.flush_global(g)
            if not TYPED: s.globals_set.add(g)
            s.emit('%s = %s' % (g, n(v) if v.t in (I32, I64) else v.c)); return
        if op == 0x41: push(E(str(x[1]), I32, x[1])); return
        if op == 0x42: push(E(str(x[1]), I64, x[1])); return
        if op == 0x43: push(E(flit(x[1]), F32)); return
        if op == 0x44: push(E(flit(x[1]), F64)); return
        if 0x28 <= op <= 0x35:
            a = pop(); off = x[1]
            ad = n(a) if off == 0 else '%s+%d' % (n(a), off)
            d = dep(a, m=True)
            L = {
                0x28: ('U32(M,%s)[0]', I32, M32), 0x29: ('U64(M,%s)[0]', I64, M64),
                0x2A: ('F32L(M,%s)[0]', F32, None), 0x2B: ('F64L(M,%s)[0]', F64, None),
                0x2C: ('S8(M,%s)[0]', I32, None), 0x2D: ('M[%s]', I32, 255),
                0x2E: ('S16(M,%s)[0]', I32, None), 0x2F: ('U16(M,%s)[0]', I32, 0xFFFF),
                0x30: ('S8(M,%s)[0]', I64, None), 0x31: ('M[%s]', I64, 255),
                0x32: ('S16(M,%s)[0]', I64, None), 0x33: ('U16(M,%s)[0]', I64, 0xFFFF),
                0x34: ('S32(M,%s)[0]', I64, None), 0x35: ('U32(M,%s)[0]', I64, M32),
            }[op]
            if TYPED:
                L = {
                    0x28: ('ld32(%s)', I32, M32), 0x29: ('ld64(%s)', I64, M64),
                    0x2A: ('ldf32(%s)', F32, None), 0x2B: ('ldf64(%s)', F64, None),
                    0x2C: ('ld8s(%s)', I32, None), 0x2D: ('ld8(%s)', I32, 255),
                    0x2E: ('ld16s(%s)', I32, None), 0x2F: ('ld16(%s)', I32, 0xFFFF),
                    0x30: ('int(ld8s(%s))', I64, None), 0x31: ('int(ld8(%s))', I64, 255),
                    0x32: ('int(ld16s(%s))', I64, None), 0x33: ('int(ld16(%s))', I64, 0xFFFF),
                    0x34: ('int(ld32s(%s))', I64, None), 0x35: ('int(ld32(%s))', I64, M32),
                }[op]
                push(E(L[0] % ad, L[1], L[2], **d)); return
            al = x[2]
            if s.mg.trust_align and off % 4 == 0 and al >= 2 and op in (0x28, 0x35):
                push(E('V32[%s>>2]' % (n(a) if off == 0 else '(%s)' % ad), L[1], L[2], **d)); return
            if s.mg.trust_align and off % 2 == 0 and al >= 1 and op in (0x2F, 0x33):
                push(E('V16[%s>>1]' % (n(a) if off == 0 else '(%s)' % ad), L[1], L[2], **d)); return
            push(E(L[0] % ad, L[1], L[2], **d)); return
        if 0x36 <= op <= 0x3E:
            v = pop(); a = pop(); off = x[1]
            s.flush_mem()
            ad = n(a) if off == 0 else '%s+%d' % (n(a), off)
            if TYPED:  # helpers take any (wrapped) i64 and store its low bytes
                f = {0x36: 'st32(%s, %s)', 0x3A: 'st8(%s, %s)', 0x3B: 'st16(%s, %s)',
                     0x38: 'stf32(%s, %s)', 0x39: 'stf64(%s, %s)'}
                if op in f: s.emit(f[op] % (ad, v.c)); return
                f = {0x37: 'st64(%s, %s)', 0x3C: 'st8(%s, i64(%s&255))',
                     0x3D: 'st16(%s, i64(%s&0xFFFF))', 0x3E: 'st32(%s, i64(%s&0xFFFFFFFF))'}
                s.emit(f[op] % (ad, n(v) if op == 0x37 else v.c)); return
            al = x[2]
            if s.mg.trust_align and op in (0x36, 0x3E) and off % 4 == 0 and al >= 2:
                vc = n(v) if op == 0x36 else (v.c if v.b is not None and v.b <= M32 else '%s&0xFFFFFFFF' % v.c)
                s.emit('V32[%s>>2] = %s' % (n(a) if off == 0 else '(%s)' % ad, vc)); return
            if s.mg.trust_align and op in (0x3B, 0x3D) and off % 2 == 0 and al >= 1:
                vc = v.c if v.b is not None and v.b <= 0xFFFF else '%s&0xFFFF' % v.c
                s.emit('V16[%s>>1] = %s' % (n(a) if off == 0 else '(%s)' % ad, vc)); return
            if op == 0x36: s.emit('P32(M,%s,%s)' % (ad, n(v)))
            elif op == 0x37: s.emit('P64(M,%s,%s)' % (ad, n(v)))
            elif op == 0x38: s.emit('F32S(M,%s,%s)' % (ad, v.c))
            elif op == 0x39: s.emit('F64S(M,%s,%s)' % (ad, v.c))
            elif op in (0x3A, 0x3C):
                vc = v.c if v.b is not None and v.b <= 255 else '%s&255' % v.c
                s.emit('M[%s] = %s' % (ad, vc))
            elif op in (0x3B, 0x3D):
                vc = v.c if v.b is not None and v.b <= 0xFFFF else '%s&0xFFFF' % v.c
                s.emit('P16(M,%s,%s)' % (ad, vc))
            elif op == 0x3E:
                vc = v.c if v.b is not None and v.b <= M32 else '%s&0xFFFFFFFF' % v.c
                s.emit('P32(M,%s,%s)' % (ad, vc))
            return
        if op == 0x3F: push(E('(len(%s)>>16)' % ('MEM' if TYPED else 'M'), I32, 0xFFFF, m=True)); return
        if op == 0x40:
            v = pop(); s.flush_call(); t = s.newtmp(I32)
            s.emit('%s = _grow(%s)' % (t, n(v))); push(E(t, I32, M32, k=True)); return
        if op == 0x10 and x[1] in mg.inl and s.lmap is None:
            s.inline(x[1]); return
        if op == 0x10 or op == 0x11:
            if op == 0x10:
                fi = x[1]; ps, rs = mg.ftype(fi); callee = mg.fname(fi)
            else:
                ps, rs = mg.types[x[1]]
                ix = pop(); callee = 'T%d[%s]' % (x[2], n(ix))
                ixdep = ix
            args = s.popn(len(ps))
            s.flush_call()
            ac = ', '.join(n(a) if a.t in (I32, I64) else a.c for a in args)
            call = '%s(%s)' % (callee, ac)
            if not rs: s.emit(call)
            elif len(rs) == 1:
                t = s.newtmp(rs[0]); s.emit('%s = %s' % (t, call))
                push(E(t, rs[0], (M32 if rs[0] == I32 else M64) if rs[0] in (I32, I64) else None, k=True))
            else:
                ts = [s.newtmp(ty) for ty in rs]; s.emit('%s = %s' % (', '.join(ts), call))
                for t, ty in zip(ts, rs):
                    push(E(t, ty, (M32 if ty == I32 else M64) if ty in (I32, I64) else None, k=True))
            return
        # ---- integer ops
        if op in (0x45, 0x50):
            a = s.popc()
            if a.t == 'bool': push(E('(not %s)' % a.c, 'bool', 1, **dep(a))); return
            push(E('(%s==0)' % n(a), 'bool', 1, **dep(a))); return
        if 0x46 <= op <= 0x4F or 0x51 <= op <= 0x5A:
            b = pop(); a = pop(); k = op - (0x46 if op <= 0x4F else 0x51)
            W = 32 if op <= 0x4F else 64
            h = 0x80000000 if W == 32 else 0x8000000000000000
            pyop = ['==', '!=', '<', '<', '>', '>', '<=', '<=', '>=', '>='][k]
            if k in (2, 4, 6, 8) and (a.pure_const and a.c.isdigit()) != (b.pure_const and b.c.isdigit()):
                # x <op>_s C  ->  range tests on the unsigned value; stays on small-int fast paths
                MOD = 1 << W; HMAX = h - 1
                if a.pure_const and a.c.isdigit():  # C <op> x  -> x <swapped op> C
                    a, b = b, a; k = {2: 4, 4: 2, 6: 8, 8: 6}[k]
                x_ = n(a); cu = int(b.c); cs = cu - MOD if cu >= h else cu
                if k == 6 and cs < HMAX: k, cs = 2, cs + 1          # le c  == lt c+1
                elif k == 8 and cs > -h: k, cs = 4, cs - 1          # ge c  == gt c-1
                if k == 2:
                    c = ('(%s < %d or %s > %d)' % (x_, cs, x_, HMAX)) if cs >= 0 else \
                        ('(%d < %s < %d)' % (HMAX, x_, cs + MOD))
                    c = c if cs != 0 else '(%s > %d)' % (x_, HMAX)
                elif k == 4:
                    c = ('(%d < %s <= %d)' % (cs, x_, HMAX)) if cs >= 0 else \
                        ('(%s <= %d or %s > %d)' % (x_, HMAX, x_, cs + MOD))
                else:  # unreachable edge (le MAX / ge MIN): always true
                    c = 'True'
                if not x_.isidentifier() and ' or ' in c:
                    t = s.newtmp(I32 if W == 32 else I64); s.flush_all(); s.emit('%s = %s' % (t, x_))
                    c = c.replace(x_, t); dd = dep()
                else:
                    dd = dep(a, b)
                push(E(c, 'bool', 1, **dd)); return
            if k in (2, 4, 6, 8):  # signed
                lim = h
                if nb(a) < lim and nb(b) < lim:
                    c = '(%s%s%s)' % (n(a), pyop, n(b))
                else:
                    def fl(e):
                        if e.pure_const and e.c.isdigit(): return str(int(e.c) ^ h)
                        return '(%s^%s)' % (n(e), hex(h))
                    c = '(%s%s%s)' % (fl(a), pyop, fl(b))
            else:
                c = '(%s%s%s)' % (n(a), pyop, n(b))
            push(E(c, 'bool', 1, **dep(a, b))); return
        if 0x67 <= op <= 0x78 or 0x79 <= op <= 0x8A:
            W = 32 if op <= 0x78 else 64
            k = op - (0x67 if W == 32 else 0x79)
            MAX = M32 if W == 32 else M64
            T = I32 if W == 32 else I64
            mk = '0xFFFFFFFF' if W == 32 else '0xFFFFFFFFFFFFFFFF'
            if k <= 2:
                a = pop()
                f = ['_clz', '_ctz', '_popcnt'][k]
                if TYPED and W == 32:
                    push(E('%s(%s)' % (['_clz32', '_ctz32', '_popcnt32'][k], n(a)), T, W, **dep(a))); return
                if k == 2: push(E('_pc(%s)' % n(a), T, W, **dep(a)))
                elif k == 0: push(E('_clz%d(%s)' % (W, n(a)), T, W, **dep(a)))
                else: push(E('_ctz%d(%s)' % (W, n(a)), T, W, **dep(a)))
                return
            if k in (10, 11, 12) and len(s.stack) >= 2 and s.stack[-1].t == 'bool' and s.stack[-2].t == 'bool':
                b = s.popc(); a = s.popc()   # bool & bool stays a (pure) bool expression
                o = {10: 'and', 11: 'or', 12: '!='}[k]
                push(E('(%s %s %s)' % (a.c, o, b.c), 'bool', 1, **dep(a, b))); return
            b = pop(); a = pop(); d = dep(a, b)
            HALF = 1 << (W - 1); MOD = 1 << W
            def isc(e): return e.pure_const and e.c.isdigit()
            if k in (3, 5, 10, 11, 12) and isc(a) and not isc(b): a, b = b, a   # commutative: const right
            if k == 3 and isc(b) and int(b.c) >= HALF:
                push(E('(%s-%d)' % (a.c, MOD - int(b.c)), T, None, w='down' if a.b is not None else None, **d)); return
            if k == 4 and isc(b) and int(b.c) >= HALF:
                push(E('(%s+%d)' % (a.c, MOD - int(b.c)), T, None, w='up' if a.b is not None else None, **d)); return
            if k == 10 and isc(b) and int(b.c) >= HALF and int(b.c) != MAX:
                push(E('(%s&%d)' % (a.c, int(b.c) - MOD), T, a.b, **d)); return
            if k in (11, 12) and isc(b) and int(b.c) >= HALF:
                push(E('(%s%s%d)' % (a.c, '|' if k == 11 else '^', int(b.c) - MOD), T, None, **d)); return
            if k == 3:  # add
                bb = (a.b + b.b) if a.b is not None and b.b is not None and a.b + b.b <= MAX else None
                if b.pure_const and b.c == '0': push(a); return
                ww = 'up' if bb is None and a.b is not None and b.b is not None else None
                push(E('(%s+%s)' % (a.c, b.c), T, bb, w=ww, **d)); return
            if k == 4:  # sub
                if b.pure_const and b.c == '0': push(a); return
                ww = 'down' if a.b is not None and b.b is not None else None
                push(E('(%s-%s)' % (a.c, b.c), T, None, w=ww, **d)); return
            if k == 5:  # mul
                bb = (a.b * b.b) if a.b is not None and b.b is not None and a.b * b.b <= MAX else None
                push(E('(%s*%s)' % (a.c, b.c), T, bb, **d)); return
            if k == 6: push(E('_divs%d(%s,%s)' % (W, n(a), n(b)), T, MAX, **d)); return
            if k == 7: push(E('(%s//%s)' % (n(a), n(b)), T, nb(a), **d)); return
            if k == 8: push(E('_rems%d(%s,%s)' % (W, n(a), n(b)), T, MAX, **d)); return
            if k == 9: push(E('(%s%%%s)' % (n(a), n(b)), T, nb(a), **d)); return
            if k == 10:  # and
                if a.b is not None or b.b is not None:
                    bb = min(x_.b for x_ in (a, b) if x_.b is not None)
                    push(E('(%s&%s)' % (a.c, b.c), T, bb, **d))
                else:
                    push(E('(%s&%s)' % (a.c, b.c), T, None, **d))
                return
            if k in (11, 12):
                o = '|' if k == 11 else '^'
                if a.b is not None and b.b is not None:
                    bb = (1 << max(a.b, b.b).bit_length()) - 1
                    push(E('(%s%s%s)' % (a.c, o, b.c), T, bb, **d))
                else:
                    push(E('(%s%s%s)' % (a.c, o, b.c), T, None, **d))
                return
            # shifts / rotates: count
            if b.pure_const and b.c.isdigit():
                cnt = int(b.c) & (W - 1); cc = str(cnt); cb = cnt
            else:
                cc = '(%s&%d)' % (b.c, W - 1); cb = None
            if k == 13:  # shl
                if cb is not None and a.b is not None and (a.b << cb) <= MAX:
                    push(E('(%s<<%s)' % (a.c, cc), T, a.b << cb, **d))
                else:
                    push(E('(%s<<%s)' % (a.c, cc), T, None, **d))
                return
            if k == 14:  # shr_s
                push(E('(%s>>%s)' % (sgn(a), cc), T, (nb(a) >> cb) if (a.b is not None and a.b < (MAX >> 1)) and cb is not None else None, **d)); return
            if k == 15:  # shr_u
                push(E('(%s>>%s)' % (n(a), cc), T, nb(a) >> cb if cb is not None else nb(a), **d)); return
            if k == 16:
                push(E('_rotl%d(%s,%s)' % (W, n(a), cc), T, MAX, **d)); return
            if k == 17:
                push(E('_rotr%d(%s,%s)' % (W, n(a), cc), T, MAX, **d)); return
        # ---- floats
        if 0x5B <= op <= 0x66:
            b = pop(); a = pop(); k = (op - 0x5B) % 6
            pyop = ['==', '!=', '<', '>', '<=', '>='][k]
            push(E('(%s%s%s)' % (a.c, pyop, b.c), 'bool', 1, **dep(a, b))); return
        if 0x8B <= op <= 0xA6:
            is32 = op <= 0x98
            k = op - (0x8B if is32 else 0x99)
            T = F32 if is32 else F64
            r = '_f32' if is32 else ''
            if k <= 6:
                a = pop(); d = dep(a)
                f = ['abs', '-', '_fceil', '_ffloor', '_ftrunc', '_fnearest', '_fsqrt'][k]
                if k == 0: c = 'abs(%s)' % a.c
                elif k == 1: c = '(-%s)' % a.c
                else: c = '%s(%s)' % (f, a.c)
                if k == 6 and is32: c = '_f32(%s)' % c
                push(E(c, T, None, **d)); return
            b = pop(); a = pop(); d = dep(a, b)
            if k == 7: c = '(%s+%s)' % (a.c, b.c)
            elif k == 8: c = '(%s-%s)' % (a.c, b.c)
            elif k == 9: c = '(%s*%s)' % (a.c, b.c)
            elif k == 10: c = '_fdiv(%s,%s)' % (a.c, b.c)
            elif k == 11: c = '_fmin(%s,%s)' % (a.c, b.c)
            elif k == 12: c = '_fmax(%s,%s)' % (a.c, b.c)
            else: c = '_copysign(%s,%s)' % (a.c, b.c)
            if is32 and k in (7, 8, 9, 10): c = '_f32(%s)' % c
            push(E(c, T, None, **d)); return
        # ---- conversions
        if TYPED and op == 0xA7:
            a = pop(); push(E('i64(%s&0xFFFFFFFF)' % a.c, I32, M32, **dep(a))); return
        if TYPED and op == 0xAC:
            a = pop(); push(E('int(%s)' % sgn(a), I64, None, **dep(a))); return
        if TYPED and op == 0xAD:
            a = pop(); push(E('int(%s)' % n(a), I64, nb(a), **dep(a))); return
        if op == 0xA7:
            a = pop(); push(E(a.c, I32, a.b if a.b is not None and a.b <= M32 else None, **dep(a))); return
        if op in (0xA8, 0xA9, 0xAA, 0xAB, 0xAE, 0xAF, 0xB0, 0xB1):
            a = pop()
            W = 32 if op <= 0xAB else 64
            sg = 's' if op in (0xA8, 0xAA, 0xAE, 0xB0) else 'u'
            push(E('_trunc_%s%d(%s)' % (sg, W, a.c), I32 if W == 32 else I64, M32 if W == 32 else M64, **dep(a))); return
        if 0xFC00 <= op <= 0xFC07:
            a = pop(); k = op - 0xFC00
            W = 32 if k < 4 else 64
            sg = 'u' if k & 1 else 's'
            push(E('_tsat_%s%d(%s)' % (sg, W, a.c), I32 if W == 32 else I64, M32 if W == 32 else M64, **dep(a))); return
        if op == 0xAC: a = pop(); push(E(sgn(a), I64, a.b if a.b is not None and a.b < 0x80000000 else None, **dep(a))); return
        if op == 0xAD: a = pop(); push(E(n(a), I64, nb(a), **dep(a))); return
        if op in (0xB2, 0xB4, 0xB7, 0xB9):
            a = pop(); c = 'float(%s)' % sgn(a)
            if op in (0xB2, 0xB4): c = '_f32(%s)' % c
            push(E(c, F32 if op < 0xB7 else F64, None, **dep(a))); return
        if op in (0xB3, 0xB5, 0xB8, 0xBA):
            a = pop(); c = 'float(%s)' % n(a)
            if op in (0xB3, 0xB5): c = '_f32(%s)' % c
            push(E(c, F32 if op < 0xB7 else F64, None, **dep(a))); return
        if op == 0xB6: a = pop(); push(E('_f32(%s)' % a.c, F32, None, **dep(a))); return
        if op == 0xBB: a = pop(); push(E(a.c, F64, None, **dep(a))); return
        if op == 0xBC: a = pop(); push(E('_i32_f32(%s)' % a.c, I32, M32, **dep(a))); return
        if op == 0xBD: a = pop(); push(E('_i64_f64(%s)' % a.c, I64, M64, **dep(a))); return
        if op == 0xBE: a = pop(); push(E('_f32_i32(%s)' % n(a), F32, None, **dep(a))); return
        if op == 0xBF: a = pop(); push(E('_f64_i64(%s)' % n(a), F64, None, **dep(a))); return
        if 0xC0 <= op <= 0xC4:
            a = pop()
            bits, T = {0xC0: (8, I32), 0xC1: (16, I32), 0xC2: (8, I64), 0xC3: (16, I64), 0xC4: (32, I64)}[op]
            h = 1 << (bits - 1); m = (1 << bits) - 1
            push(E('(((%s&%d)^%d)-%d)' % (a.c, m, h, h), T, None, **dep(a))); return
        if op == 0xFC0A:
            nn = pop(); src = pop(); dst = pop(); s.flush_mem()
            s.emit('_mcopy(%s,%s,%s)' % (n(dst), n(src), n(nn))); return
        if op == 0xFC0B:
            nn = pop(); v = pop(); dst = pop(); s.flush_mem()
            s.emit('_mfill(%s,%s,%s)' % (n(dst), n(v), n(nn))); return
        raise NotImplementedError('opcode 0x%x' % op)


# ----------------------------------------------------------------------------- module compiler
RUNTIME = r'''
import struct as _st, math as _m
class Trap(Exception): pass
INF = float('inf'); NAN = float('nan')
M = bytearray(@MEM@ * 65536)
_MAXPAGES = @MAXP@
_uf = _st.unpack_from; _pi = _st.pack_into; _pk = _st.pack; _upk = _st.unpack
if hasattr(_st, 'Struct'):
    U32 = _st.Struct('<I').unpack_from; S32 = _st.Struct('<i').unpack_from
    U64 = _st.Struct('<Q').unpack_from
    U16 = _st.Struct('<H').unpack_from; S16 = _st.Struct('<h').unpack_from
    S8 = _st.Struct('<b').unpack_from
    F32L = _st.Struct('<f').unpack_from; F64L = _st.Struct('<d').unpack_from
    P32 = _st.Struct('<I').pack_into; P64 = _st.Struct('<Q').pack_into; P16 = _st.Struct('<H').pack_into
    F64S = _st.Struct('<d').pack_into
def F32S(m, a, v):
    try: _pi('<f', m, a, v)
    except OverflowError: _pi('<f', m, a, _m.copysign(INF, v))
def _f32(x):
    try: return _upk('<f', _pk('<f', x))[0]
    except OverflowError: return _m.copysign(INF, x)
@ALIGNVIEWS@
def _grow(n):
    old = len(M) >> 16
    if old + n > _MAXPAGES: return 0xFFFFFFFF
    @GROWPRE@M.extend(bytes(n << 16))
    @GROWPOST@return old
def _mcopy(d, s, n):
    if d + n > len(M) or s + n > len(M): raise Trap('oob')
    M[d:d+n] = M[s:s+n]
def _mfill(d, v, n):
    if d + n > len(M): raise Trap('oob')
    M[d:d+n] = bytes([v & 255]) * n
if hasattr(0, 'bit_length'):
    def _bl(x): return x.bit_length()
else:  # MicroPython
    def _bl(x):
        n = 0
        while x >> 16: x >>= 16; n += 16
        while x: x >>= 1; n += 1
        return n
if hasattr(0, 'bit_count'):
    def _pc(x): return x.bit_count()
else:
    def _pc(x): return bin(x).count('1')
def _clz32(x): return 32 - _bl(x)
def _clz64(x): return 64 - _bl(x)
def _ctz32(x): return _bl(x & -x) - 1 if x else 32
def _ctz64(x): return _bl(x & -x) - 1 if x else 64
def _rotl32(x, k): return ((x << k) | (x >> (32 - k))) & 0xFFFFFFFF
def _rotr32(x, k): return ((x >> k) | (x << (32 - k))) & 0xFFFFFFFF
def _rotl64(x, k): return ((x << k) | (x >> (64 - k))) & 0xFFFFFFFFFFFFFFFF
def _rotr64(x, k): return ((x >> k) | (x << (64 - k))) & 0xFFFFFFFFFFFFFFFF
def _divs32(a, b):
    a = (a ^ 0x80000000) - 0x80000000; b = (b ^ 0x80000000) - 0x80000000
    if b == 0: raise Trap('integer divide by zero')
    if a == -0x80000000 and b == -1: raise Trap('integer overflow')
    q = abs(a) // abs(b)
    return (-q if (a < 0) != (b < 0) else q) & 0xFFFFFFFF
def _rems32(a, b):
    a = (a ^ 0x80000000) - 0x80000000; b = (b ^ 0x80000000) - 0x80000000
    if b == 0: raise Trap('integer divide by zero')
    r = abs(a) % abs(b)
    return (-r if a < 0 else r) & 0xFFFFFFFF
def _divs64(a, b):
    h = 0x8000000000000000
    a = (a ^ h) - h; b = (b ^ h) - h
    if b == 0: raise Trap('integer divide by zero')
    if a == -h and b == -1: raise Trap('integer overflow')
    q = abs(a) // abs(b)
    return (-q if (a < 0) != (b < 0) else q) & 0xFFFFFFFFFFFFFFFF
def _rems64(a, b):
    h = 0x8000000000000000
    a = (a ^ h) - h; b = (b ^ h) - h
    if b == 0: raise Trap('integer divide by zero')
    r = abs(a) % abs(b)
    return (-r if a < 0 else r) & 0xFFFFFFFFFFFFFFFF
def _fdiv(a, b):
    try: return a / b
    except ZeroDivisionError:
        if a != a or a == 0: return NAN
        return _m.copysign(INF, a) * _m.copysign(1.0, b)
def _fsqrt(x):
    if x < 0: return NAN
    return _m.sqrt(x)
def _fceil(x): return float(_m.ceil(x)) if x == x and x not in (INF, -INF) and x != 0 else x
def _ffloor(x): return float(_m.floor(x)) if x == x and x not in (INF, -INF) and x != 0 else x
def _ftrunc(x): return float(_m.trunc(x)) if x == x and x not in (INF, -INF) and x != 0 else x
def _fnearest(x):
    if x != x or x in (INF, -INF) or x == 0: return x
    return _m.copysign(float(round(x)), x)
def _fmin(a, b):
    if a != a or b != b: return NAN
    if a == b == 0: return a if _m.copysign(1, a) < 0 else b
    return a if a < b else b
def _fmax(a, b):
    if a != a or b != b: return NAN
    if a == b == 0: return b if _m.copysign(1, a) < 0 else a
    return a if a > b else b
_copysign = _m.copysign
def _tr(x, lo, hi):
    if x != x: raise Trap('invalid conversion to integer')
    t = _m.trunc(x) if x not in (INF, -INF) else None
    if t is None or not (lo <= t <= hi): raise Trap('integer overflow')
    return t
def _trunc_s32(x): return _tr(x, -2**31, 2**31-1) & 0xFFFFFFFF
def _trunc_u32(x): return _tr(x, 0, 2**32-1)
def _trunc_s64(x): return _tr(x, -2**63, 2**63-1) & 0xFFFFFFFFFFFFFFFF
def _trunc_u64(x): return _tr(x, 0, 2**64-1)
def _sat(x, lo, hi):
    if x != x: return 0
    if x >= hi: return hi
    if x <= lo: return lo
    return _m.trunc(x)
def _tsat_s32(x): return _sat(x, -2**31, 2**31-1) & 0xFFFFFFFF
def _tsat_u32(x): return _sat(x, 0, 2**32-1)
def _tsat_s64(x): return _sat(x, -2**63, 2**63-1) & 0xFFFFFFFFFFFFFFFF
def _tsat_u64(x): return _sat(x, 0, 2**64-1)
def _i32_f32(x): return _st.unpack('<I', _st.pack('<f', x))[0]
def _f32_i32(x): return _st.unpack('<f', _st.pack('<I', x))[0]
def _i64_f64(x): return _st.unpack('<Q', _st.pack('<d', x))[0]
def _f64_i64(x): return _st.unpack('<d', _st.pack('<Q', x))[0]
'''


RUNTIME_TYPED = r'''
from typing import Final, Any, Callable
from mypy_extensions import i64, u8
from librt.strings import BytesWriter
import struct as _st, math as _m
class Trap(Exception): pass
INF: Final = float('inf')
NAN: Final = float('nan')
MEM: Final = BytesWriter()
MEM.write(bytes(@MEM@ * 65536))
_MAXPAGES: Final = @MAXP@
def ld8(a: i64) -> i64:
    return i64(MEM[a])
def ld8s(a: i64) -> i64:
    v = i64(MEM[a])
    return v - 256 if v >= 128 else v
def ld16(a: i64) -> i64:
    m = MEM
    return i64(m[a]) | (i64(m[a + 1]) << 8)
def ld16s(a: i64) -> i64:
    v = ld16(a)
    return v - 65536 if v >= 32768 else v
def ld32(a: i64) -> i64:
    m = MEM
    return i64(m[a]) | (i64(m[a + 1]) << 8) | (i64(m[a + 2]) << 16) | (i64(m[a + 3]) << 24)
def ld32s(a: i64) -> i64:
    v = ld32(a)
    return v - 4294967296 if v >= 2147483648 else v
def ld64(a: i64) -> int:
    return int(ld32(a)) | (int(ld32(a + 4)) << 32)
def st8(a: i64, v: i64) -> None:
    MEM[a] = u8(v & 255)
def st16(a: i64, v: i64) -> None:
    m = MEM
    m[a] = u8(v & 255); m[a + 1] = u8((v >> 8) & 255)
def st32(a: i64, v: i64) -> None:
    m = MEM
    m[a] = u8(v & 255); m[a + 1] = u8((v >> 8) & 255)
    m[a + 2] = u8((v >> 16) & 255); m[a + 3] = u8((v >> 24) & 255)
def st64(a: i64, v: int) -> None:
    st32(a, i64(v & 0xFFFFFFFF)); st32(a + 4, i64(v >> 32))
def _i32_f32(x: float) -> i64:
    try: return i64(_st.unpack('<I', _st.pack('<f', x))[0])
    except OverflowError: return i64(_st.unpack('<I', _st.pack('<f', _m.copysign(INF, x)))[0])
def _f32_i32(x: i64) -> float: return float(_st.unpack('<f', _st.pack('<I', x & 0xFFFFFFFF))[0])
def _i64_f64(x: float) -> int: return int(_st.unpack('<Q', _st.pack('<d', x))[0])
def _f64_i64(x: int) -> float: return float(_st.unpack('<d', _st.pack('<Q', x))[0])
def ldf32(a: i64) -> float: return _f32_i32(ld32(a))
def ldf64(a: i64) -> float: return _f64_i64(ld64(a))
def stf32(a: i64, v: float) -> None: st32(a, _i32_f32(v))
def stf64(a: i64, v: float) -> None: st64(a, _i64_f64(v))
def _f32(x: float) -> float: return _f32_i32(_i32_f32(x))
def mem_read(p: i64, n: i64) -> bytes:
    return bytes([MEM[p + j] for j in range(n)])
def _grow(n: i64) -> i64:
    old = i64(len(MEM)) >> 16
    if old + n > _MAXPAGES: return 0xFFFFFFFF
    MEM.write(bytes(n << 16)); return old
def _mcopy(d: i64, s: i64, n: i64) -> None:
    L = i64(len(MEM))
    if d + n > L or s + n > L: raise Trap('oob')
    m = MEM
    if d <= s:
        for j in range(n): m[d + j] = m[s + j]
    else:
        j = n - 1
        while j >= 0:
            m[d + j] = m[s + j]; j -= 1
def _mfill(d: i64, v: i64, n: i64) -> None:
    if d + n > i64(len(MEM)): raise Trap('oob')
    m = MEM; b = u8(v & 255)
    for j in range(n): m[d + j] = b
def _clz32(x: i64) -> i64: return 32 - i64(int(x).bit_length())
def _ctz32(x: i64) -> i64: return i64((int(x) & -int(x)).bit_length()) - 1 if x else 32
def _popcnt32(x: i64) -> i64: return i64(int(x).bit_count())
def _ctz64(x: int) -> int: return (x & -x).bit_length() - 1 if x else 64
def _rotl32(x: i64, k: i64) -> i64: return ((x << k) | (x >> (32 - k))) & 0xFFFFFFFF
def _rotr32(x: i64, k: i64) -> i64: return ((x >> k) | (x << (32 - k))) & 0xFFFFFFFF
def _rotl64(x: int, k: int) -> int: return ((x << k) | (x >> (64 - k))) & 0xFFFFFFFFFFFFFFFF
def _rotr64(x: int, k: int) -> int: return ((x >> k) | (x << (64 - k))) & 0xFFFFFFFFFFFFFFFF
def _divs32(a: i64, b: i64) -> i64:
    a = (a ^ 0x80000000) - 0x80000000; b = (b ^ 0x80000000) - 0x80000000
    if b == 0: raise Trap('integer divide by zero')
    if a == -0x80000000 and b == -1: raise Trap('integer overflow')
    q: i64 = (a if a >= 0 else -a) // (b if b >= 0 else -b)
    return (-q if (a < 0) != (b < 0) else q) & 0xFFFFFFFF
def _rems32(a: i64, b: i64) -> i64:
    a = (a ^ 0x80000000) - 0x80000000; b = (b ^ 0x80000000) - 0x80000000
    if b == 0: raise Trap('integer divide by zero')
    r: i64 = (a if a >= 0 else -a) % (b if b >= 0 else -b)
    return (-r if a < 0 else r) & 0xFFFFFFFF
def _divs64(a: int, b: int) -> int:
    h = 0x8000000000000000
    a = (a ^ h) - h; b = (b ^ h) - h
    if b == 0: raise Trap('integer divide by zero')
    if a == -h and b == -1: raise Trap('integer overflow')
    q = abs(a) // abs(b)
    return (-q if (a < 0) != (b < 0) else q) & 0xFFFFFFFFFFFFFFFF
def _rems64(a: int, b: int) -> int:
    h = 0x8000000000000000
    a = (a ^ h) - h; b = (b ^ h) - h
    if b == 0: raise Trap('integer divide by zero')
    r = abs(a) % abs(b)
    return (-r if a < 0 else r) & 0xFFFFFFFFFFFFFFFF
def _fdiv(a: float, b: float) -> float:
    if b == 0.0:
        if a != a or a == 0.0: return NAN
        return _m.copysign(INF, a) * _m.copysign(1.0, b)
    return a / b
def _fsqrt(x: float) -> float:
    if x < 0: return NAN
    return _m.sqrt(x)
def _finite(x: float) -> bool: return x == x and x != INF and x != -INF and x != 0.0
def _fceil(x: float) -> float: return float(_m.ceil(x)) if _finite(x) else x
def _ffloor(x: float) -> float: return float(_m.floor(x)) if _finite(x) else x
def _ftrunc(x: float) -> float: return float(_m.trunc(x)) if _finite(x) else x
def _fnearest(x: float) -> float:
    if not _finite(x): return x
    return _m.copysign(float(round(x)), x)
def _fmin(a: float, b: float) -> float:
    if a != a or b != b: return NAN
    if a == b == 0: return a if _m.copysign(1, a) < 0 else b
    return a if a < b else b
def _fmax(a: float, b: float) -> float:
    if a != a or b != b: return NAN
    if a == b == 0: return b if _m.copysign(1, a) < 0 else a
    return a if a > b else b
def _copysign(a: float, b: float) -> float: return _m.copysign(a, b)
def _tr(x: float, lo: int, hi: int) -> int:
    if x != x: raise Trap('invalid conversion to integer')
    if x == INF or x == -INF: raise Trap('integer overflow')
    t = int(_m.trunc(x))
    if not (lo <= t <= hi): raise Trap('integer overflow')
    return t
def _trunc_s32(x: float) -> i64: return i64(_tr(x, -2**31, 2**31-1) & 0xFFFFFFFF)
def _trunc_u32(x: float) -> i64: return i64(_tr(x, 0, 2**32-1))
def _trunc_s64(x: float) -> int: return _tr(x, -2**63, 2**63-1) & 0xFFFFFFFFFFFFFFFF
def _trunc_u64(x: float) -> int: return _tr(x, 0, 2**64-1)
def _sat(x: float, lo: int, hi: int) -> int:
    if x != x: return 0
    if x >= hi: return hi
    if x <= lo: return lo
    return int(_m.trunc(x))
def _tsat_s32(x: float) -> i64: return i64(_sat(x, -2**31, 2**31-1) & 0xFFFFFFFF)
def _tsat_u32(x: float) -> i64: return i64(_sat(x, 0, 2**32-1))
def _tsat_s64(x: float) -> int: return _sat(x, -2**63, 2**63-1) & 0xFFFFFFFFFFFFFFFF
def _tsat_u64(x: float) -> int: return _sat(x, 0, 2**64-1)
def _noop(*a: Any) -> Any: raise Trap('unresolved import')
'''


def _gen_typed(s):  # noqa
    mod = s.mod
    mem = mod['mems'][0] if mod['mems'] else (0, 0)
    out = [RUNTIME_TYPED.replace('@MEM@', str(mem[0])).replace('@MAXP@', str(mem[1] if mem[1] is not None else 65536))]
    st = ['class _State:', '    def __init__(self) -> None:']
    for i, (t, mut, init) in enumerate(zip(s.gtypes, s.gmut, s.ginit)):
        st.append('        self.g%d: %s = %r' % (i, TY[t], init[1]))
    for i in range(s.nimp):
        st.append('        self.i%d: Callable[..., Any] = _noop' % i)
    st.append('        pass')
    out.append('\n'.join(st) + '\n\nGS: Final = _State()')
    for ti, (mn, mx) in enumerate(mod['tables']):
        out.append('T%d: Final[list[Any]] = [None] * %d' % (ti, mn))
    for k, ((locs, body), tidx) in enumerate(zip(mod['codes'], mod['funcs'])):
        out.append(FuncGen(s, s.nimp + k, tidx, locs, body).gen())
    out.append('DATA: Final[list[tuple[int, bytes]]] = %r' % ([(off[1], d) for off, d in mod['datas'] if off is not None],))
    L = ['def instantiate(imports: Any) -> dict[str, Any]:']
    for i, im in enumerate(s.fimports):
        L.append('    GS.i%d = imports[%r][%r]' % (i, im[1], im[2]))
    for ti, off, fs in mod['elems']:
        L.append('    T%d[%d:%d] = [%s]' % (ti, off[1], off[1] + len(fs), ', '.join(s.fname(f) for f in fs)))
    L.append('    for o, d in DATA:')
    L.append('        for j in range(len(d)): MEM[o + j] = d[j]')
    if mod['start'] is not None:
        L.append('    %s()' % s.fname(mod['start']))
    ex = []
    for n, k, i in mod['exports']:
        if k == 0: ex.append('%r: %s' % (n, s.fname(i)))
        elif k == 2: ex.append('%r: MEM' % n)
    L.append('    return {%s}' % ', '.join(ex))
    out.append('\n'.join(L))
    return '\n\n'.join(out) + '\n'


TYPED = False   # --mypyc : annotated code + native-friendly runtime for mypyc
BRTREE = int(__import__("os").environ.get("W2PY_BT", "4"))  # br_table: balanced if-tree above this many runs, 0 = chain
INLINE_MAX = 0  # --inline N : inline straight-line leaf functions up to N instructions


class ModGen:
    def __init__(s, mod, trust_align=False):
        s.mod = mod; s.trust_align = trust_align
        s.types = mod['types']
        s.fimports = [im for im in mod['imports'] if im[0] == 'func']
        s.nimp = len(s.fimports)
        s.gtypes, s.gmut, s.ginit = [], [], []
        for g in mod['globals']:
            s.gtypes.append(g[0]); s.gmut.append(g[1]); s.ginit.append(g[2])
        # straight-line leaf functions (no branches, no calls) are inlined at every direct call
        s.inl = {}
        if INLINE_MAX:
            CTRL = {0x00, 0x02, 0x03, 0x04, 0x05, 0x0C, 0x0D, 0x0E, 0x0F, 0x10, 0x11}
            for k, (locs, body) in enumerate(mod['codes']):
                ins = decode(body)
                if len(ins) <= INLINE_MAX and not any(y[0] in CTRL for y in ins[:-1]):
                    s.inl[s.nimp + k] = (locs, ins)

    def ftype(s, fi):
        if fi < s.nimp: return s.types[s.fimports[fi][3]]
        return s.types[s.mod['funcs'][fi - s.nimp]]

    def fname(s, fi):
        if fi < s.nimp: return ('GS.i%d' if TYPED else 'I%d') % fi
        return 'f%d' % fi

    def gname(s, gi):
        return ('GS.g%d' if TYPED else 'G%d') % gi

    gen_typed = _gen_typed

    def gen(s):
        if TYPED: return s.gen_typed()
        mod = s.mod
        mem = mod['mems'][0] if mod['mems'] else (0, 0)
        rt = RUNTIME.replace('@MEM@', str(mem[0])).replace('@MAXP@', str(mem[1] if mem[1] is not None else 65536))
        if s.trust_align:
            rt = rt.replace('@ALIGNVIEWS@', "V32 = memoryview(M).cast('I'); V16 = memoryview(M).cast('H')")
            rt = rt.replace('@GROWPRE@', 'global V32, V16; V32.release(); V16.release()\n    ')
            rt = rt.replace('@GROWPOST@', "V32 = memoryview(M).cast('I'); V16 = memoryview(M).cast('H')\n    ")
        else:
            rt = rt.replace('@ALIGNVIEWS@', '').replace('@GROWPRE@', '').replace('@GROWPOST@', '')
        out = [rt]
        for i, (t, mut, init) in enumerate(zip(s.gtypes, s.gmut, s.ginit)):
            out.append('G%d = %r' % (i, init[1]))
        for ti, (mn, mx) in enumerate(mod['tables']):
            out.append('T%d = [None] * %d' % (ti, mn))
        for i in range(s.nimp):
            out.append('I%d = None' % i)
        for k, ((locs, body), tidx) in enumerate(zip(mod['codes'], mod['funcs'])):
            fi = s.nimp + k
            out.append(FuncGen(s, fi, tidx, locs, body).gen())
        # instantiate
        L = ['def instantiate(imports):']
        if s.nimp:
            L.append('    global ' + ', '.join('I%d' % i for i in range(s.nimp)))
        for i, im in enumerate(s.fimports):
            L.append('    I%d = imports[%r][%r]' % (i, im[1], im[2]))
        for ti, off, fs in mod['elems']:
            L.append('    T%d[%d:%d] = [%s]' % (ti, off[1], off[1] + len(fs), ', '.join(s.fname(f) for f in fs)))
        out.append('DATA = %r' % ([(off[1], d) for off, d in mod['datas'] if off is not None],))
        L.append('    for o, d in DATA: M[o:o+len(d)] = d')
        if mod['start'] is not None:
            L.append('    %s()' % s.fname(mod['start']))
        ex = []
        for n, k, i in mod['exports']:
            if k == 0: ex.append('%r: %s' % (n, s.fname(i)))
            elif k == 2: ex.append('%r: M' % n)
        L.append('    return {%s}' % ', '.join(ex))
        out.append('\n'.join(L))
        src = '\n\n'.join(out) + '\n'
        if MICROPYTHON:
            import re as _re
            fm = {'U32': ('_uf', '<I'), 'S32': ('_uf', '<i'), 'U64': ('_uf', '<Q'), 'U16': ('_uf', '<H'),
                  'S16': ('_uf', '<h'), 'S8': ('_uf', '<b'), 'F32L': ('_uf', '<f'), 'F64L': ('_uf', '<d'),
                  'P32': ('_pi', '<I'), 'P64': ('_pi', '<Q'), 'P16': ('_pi', '<H'), 'F64S': ('_pi', '<d')}
            src = _re.sub(r'\b(U32|S32|U64|U16|S16|S8|F32L|F64L|P32|P64|P16|F64S)\(M,',
                          lambda m: "%s('%s',M," % fm[m.group(1)], src)
        return src


LEGACY = False  # --legacy: no assignment expressions (Python < 3.8): wrap with & masks instead
MICROPYTHON = False  # --micropython: no struct.Struct / memoryview.cast; emit unpack_from('<fmt', ...) calls


def translate(wasm_path, py_path, trust_align=False):
    mod = parse(open(wasm_path, 'rb').read())
    src = ModGen(mod, trust_align).gen()
    open(py_path, 'w').write(src)
    if TYPED: return py_path
    code = compile(src, os.path.basename(py_path), 'exec', optimize=2)
    pyc = py_path[:-3] + '.pyc'
    from importlib._bootstrap_external import _code_to_timestamp_pyc
    st = os.stat(py_path)
    open(pyc, 'wb').write(_code_to_timestamp_pyc(code, int(st.st_mtime), st.st_size))
    return pyc


if __name__ == '__main__':
    argv = sys.argv[1:]
    if '--legacy' in argv:
        LEGACY = True; argv.remove('--legacy')
    if '--micropython' in argv:
        MICROPYTHON = True; argv.remove('--micropython')
    if '--mypyc' in argv:
        TYPED = True; argv.remove('--mypyc')
    if '--inline' in argv:
        k = argv.index('--inline'); INLINE_MAX = int(argv[k + 1]); del argv[k:k + 2]
    a = [x for x in argv if not x.startswith('--')]
    print(translate(a[0], a[1], trust_align='--trust-align' in sys.argv))
