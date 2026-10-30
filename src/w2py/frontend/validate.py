# pyright: basic, reportArgumentType=false, reportOptionalMemberAccess=false, reportAttributeAccessIssue=false
"""Module validation (WebAssembly core spec, "Validation" and the appendix algorithm).

`validate(mod)` takes the dict of `frontend.binary.parse`, decodes every function body once (stored
in `mod["insns"]`, so a code generator does not decode again) and raises `Invalid` for a module that
breaks a validation rule. A valid construct this front end cannot validate (typed function
references, tag exports, instructions outside the core set) ends validation of that module without
a verdict (it is left to the code generator as before): never `Invalid`, because a wrong "invalid"
on a valid module would be a wrong kind. Constructs the reader does not decode (GC, SIMD,
exceptions, 64-bit memories, some table instructions) are its `NotImplementedError`.

Basic pyright mode: the module dict and the instruction tuples are untyped data of the reader.
"""

from w2py.frontend.binary import decode

I32, I64, F32, F64, FUNCREF, EXTERNREF = 0x7F, 0x7E, 0x7D, 0x7C, 0x70, 0x6F
UNKNOWN = 0  # the type of a value popped from a polymorphic stack
MAX_PAGES, MAX_TABLE = 65536, 0xFFFFFFFF


class Invalid(Exception):
    """The module is well-formed but breaks a validation rule."""


class _Unsupported(Exception):
    """A construct the validator does not cover: stop validating this module."""


# op -> (operand types, top of the stack first; result type or 0). Plain numeric, constant, load and
# store instructions: one table, one loop.
OPSIG: dict[int, tuple[tuple[int, ...], int]] = {
    0x41: ((), I32),
    0x42: ((), I64),
    0x43: ((), F32),
    0x44: ((), F64),
    0x45: ((I32,), I32),
    0x50: ((I64,), I32),
    0xA7: ((I64,), I32),
}
# loads and stores: op -> (type, natural alignment as a power of two)
LOADS = {
    0x28: (I32, 2),
    0x29: (I64, 3),
    0x2A: (F32, 2),
    0x2B: (F64, 3),
    0x2C: (I32, 0),
    0x2D: (I32, 0),
    0x2E: (I32, 1),
    0x2F: (I32, 1),
    0x30: (I64, 0),
    0x31: (I64, 0),
    0x32: (I64, 1),
    0x33: (I64, 1),
    0x34: (I64, 2),
    0x35: (I64, 2),
}
STORES = {
    0x36: (I32, 2),
    0x37: (I64, 3),
    0x38: (F32, 2),
    0x39: (F64, 3),
    0x3A: (I32, 0),
    0x3B: (I32, 1),
    0x3C: (I64, 0),
    0x3D: (I64, 1),
    0x3E: (I64, 2),
}
ALIGN = {op: a for op, (_, a) in (*LOADS.items(), *STORES.items())}
for _op, (_t, _a) in LOADS.items():
    OPSIG[_op] = ((I32,), _t)
for _op, (_t, _a) in STORES.items():
    OPSIG[_op] = ((_t, I32), 0)


def _numeric() -> None:
    def add(first: int, last: int, pops: tuple[int, ...], res: int) -> None:
        for op in range(first, last + 1):
            OPSIG[op] = (pops, res)

    for base, t in ((0x46, I32), (0x51, I64)):  # eq .. ge_u: 10 comparisons
        add(base, base + 9, (t, t), I32)
    add(0x5B, 0x60, (F32, F32), I32)
    add(0x61, 0x66, (F64, F64), I32)
    add(0x67, 0x69, (I32,), I32)
    add(0x6A, 0x78, (I32, I32), I32)
    add(0x79, 0x7B, (I64,), I64)
    add(0x7C, 0x8A, (I64, I64), I64)
    add(0x8B, 0x91, (F32,), F32)
    add(0x92, 0x98, (F32, F32), F32)
    add(0x99, 0x9F, (F64,), F64)
    add(0xA0, 0xA6, (F64, F64), F64)
    # conversions: (first, last, operand, result), and the two sign-extension groups
    for first, last, a, r in (
        (0xA8, 0xA9, F32, I32),
        (0xAA, 0xAB, F64, I32),
        (0xAC, 0xAD, I32, I64),
        (0xAE, 0xAF, F32, I64),
        (0xB0, 0xB1, F64, I64),
        (0xB2, 0xB3, I32, F32),
        (0xB4, 0xB5, I64, F32),
        (0xB6, 0xB6, F64, F32),
        (0xB7, 0xB8, I32, F64),
        (0xB9, 0xBA, I64, F64),
        (0xBB, 0xBB, F32, F64),
        (0xBC, 0xBC, F32, I32),
        (0xBD, 0xBD, F64, I64),
        (0xBE, 0xBE, I32, F32),
        (0xBF, 0xBF, I64, F64),
        (0xC0, 0xC1, I32, I32),
        (0xC2, 0xC4, I64, I64),
        (0xFC00, 0xFC01, F32, I32),  # saturating truncation
        (0xFC02, 0xFC03, F64, I32),
        (0xFC04, 0xFC05, F32, I64),
        (0xFC06, 0xFC07, F64, I64),
    ):
        add(first, last, (a,), r)


_numeric()
BULK3 = (I32, I32, I32)  # memory.copy / fill / init


def _limits(lim: tuple[int, int | None], cap: int, what: str) -> None:
    mn, mx = lim
    if mn > cap or (mx is not None and mx > cap):
        raise Invalid(f"{what} size must be at most {cap}")
    if mx is not None and mx < mn:
        raise Invalid("size minimum must not be greater than maximum")


def _vt(t: int) -> int:
    if t in (0x63, 0x64):
        raise _Unsupported("typed function references")
    return t


def _ref(ht: int) -> int:
    """Reference type of a `ref.null` heap type (-16: func, -17: extern)."""
    if ht == -16:
        return FUNCREF
    if ht == -17:
        return EXTERNREF
    raise _Unsupported("heap type")


class _Ctx:
    """The validation context of a module (spec: C)."""

    def __init__(self, mod: dict) -> None:
        self.types = mod["types"]
        imps = mod["imports"]
        self.funcs = [self.types[i[3]] for i in imps if i[0] == "func"] + [
            self.types[t] for t in mod["funcs"]
        ]
        self.ttypes = mod["ttypes"]
        self.nmems = sum(1 for i in imps if i[0] == "mem") + len(mod["mems"])
        self.gtypes = [i[3][0] for i in imps if i[0] == "global"] + [g[0] for g in mod["globals"]]
        self.gmut = [i[3][1] for i in imps if i[0] == "global"] + [g[1] for g in mod["globals"]]
        self.nelems = len(mod["elem_info"])
        self.ndatas = len(mod["datas"])
        self.refs: set[int] = set()


def _const(c: _Ctx, e: tuple, nglobals: int) -> int:
    """Type of a constant expression (parser tuples); only globals below `nglobals` are visible."""
    k = e[0]
    if k == "i32":
        return I32
    if k == "i64":
        return I64
    if k == "f32":
        return F32
    if k == "f64":
        return F64
    if k == "func":
        if e[1] >= len(c.funcs):
            raise Invalid("unknown function")
        return FUNCREF
    if k == "null":
        return _ref(e[1])
    if e[1] >= nglobals:
        raise Invalid("unknown global")
    if c.gmut[e[1]]:
        raise Invalid("constant expression required")
    return c.gtypes[e[1]]


def validate(mod: dict) -> None:
    mod["validated"] = False  # True only after a full verdict; JIT targets require it (S0002)
    try:
        _validate(mod)
    except _Unsupported:
        return
    mod["validated"] = True


def _validate(mod: dict) -> None:
    # decode all bodies first: an unsupported instruction wins over any verdict
    mod["insns"] = [decode(body) for _, body in mod["codes"]]
    types = mod["types"]
    for ps, rs in types:
        for t in (*ps, *rs):
            _vt(t)
    imps = mod["imports"]
    for im in imps:
        if im[0] == "global":
            _vt(im[3][0])
    for g in mod["globals"]:
        _vt(g[0])
    for t in mod["ttypes"]:
        _vt(t)
    for locs, _ in mod["codes"]:
        for t in locs:
            _vt(t)
    for i in (*(im[3] for im in imps if im[0] == "func"), *mod["funcs"]):
        if i >= len(types):
            raise Invalid("unknown type")
    c = _Ctx(mod)
    nimpg = sum(1 for i in imps if i[0] == "global")
    for im in imps:
        if im[0] == "table":
            _limits(im[3], MAX_TABLE, "table")
        elif im[0] == "mem":
            _limits(im[3], MAX_PAGES, "memory")
    for lim in mod["tables"]:
        _limits(lim, MAX_TABLE, "table")
    for lim in mod["mems"]:
        _limits(lim, MAX_PAGES, "memory")
    # function references allowed in function bodies: those named outside of them (spec: C.refs)
    for _, _, _, items in mod["elem_info"]:
        c.refs.update(e[1] for e in items if e[0] == "func")
    for g in mod["globals"]:
        if g[2][0] == "func":
            c.refs.add(g[2][1])
    for _, kind, idx in mod["exports"]:
        if kind == 0:
            c.refs.add(idx)
    for i, (t, _, init) in enumerate(mod["globals"]):
        if _const(c, init, nimpg + i) != t:
            raise Invalid("type mismatch")
    ng = len(c.gtypes)
    seen = set()
    counts = (len(c.funcs), len(c.ttypes), c.nmems, ng)
    for name, kind, idx in mod["exports"]:
        if kind == 4:
            raise _Unsupported("tag export")
        if name in seen:
            raise Invalid("duplicate export name")
        seen.add(name)
        if idx >= counts[kind]:
            raise Invalid("unknown export index")
    if mod["start"] is not None:
        if mod["start"] >= len(c.funcs):
            raise Invalid("unknown function")
        if c.funcs[mod["start"]] != ([], []):
            raise Invalid("start function")
    for ti, off, rt, items in mod["elem_info"]:
        if ti is not None:
            if ti >= len(c.ttypes):
                raise Invalid("unknown table")
            if c.ttypes[ti] != rt:
                raise Invalid("type mismatch")
            if _const(c, off, ng) != I32:
                raise Invalid("type mismatch")
        for e in items:
            if _const(c, e, ng) != rt:
                raise Invalid("type mismatch")
    for (off, _), mi in zip(mod["datas"], mod["data_mem"], strict=True):
        if mi is not None:
            if mi >= c.nmems:
                raise Invalid("unknown memory")
            if _const(c, off, ng) != I32:
                raise Invalid("type mismatch")
    nimpf = len(c.funcs) - len(mod["funcs"])
    for k, ((locs, _), insns) in enumerate(zip(mod["codes"], mod["insns"], strict=True)):
        ps, rs = c.funcs[nimpf + k]
        _body(c, ps + locs, len(ps), rs, insns)


def _body(c: _Ctx, lt: list[int], nparams: int, results: list[int], insns: list[tuple]) -> None:
    types, funcs, ttypes, gtypes = c.types, c.funcs, c.ttypes, c.gtypes
    nl = len(lt)
    vals: list[int] = []
    ctl: list[list] = [
        [0x02, [], results, 0, False]
    ]  # frames: opcode, start, end, height, unreachable

    def pop(expect: int = UNKNOWN) -> int:
        f = ctl[-1]
        if len(vals) == f[3]:
            if f[4]:
                return UNKNOWN
            raise Invalid("type mismatch")
        x = vals.pop()
        if expect and x and x != expect:
            raise Invalid("type mismatch")
        return x

    def popn(ts: list[int]) -> list[int]:
        return [pop(t) for t in reversed(ts)][::-1]

    def label(n: int) -> list[int]:
        if n >= len(ctl):
            raise Invalid("unknown label")
        f = ctl[-1 - n]
        return f[1] if f[0] == 0x03 else f[2]

    def enter(op: int, ps: list[int], rs: list[int]) -> None:
        popn(ps)
        ctl.append([op, ps, rs, len(vals), False])
        vals.extend(ps)

    def exit_() -> list:
        f = ctl[-1]
        popn(f[2])
        if len(vals) != f[3]:
            raise Invalid("type mismatch")
        return ctl.pop()

    def unreachable() -> None:
        f = ctl[-1]
        del vals[f[3] :]
        f[4] = True

    def table(t: int) -> int:
        if t >= len(ttypes):
            raise Invalid("unknown table")
        return ttypes[t]

    def need_mem() -> None:
        if c.nmems == 0:
            raise Invalid("unknown memory")

    for ins in insns:
        op = ins[0]
        sig = OPSIG.get(op)
        if sig is not None:
            if 0x28 <= op <= 0x3E:
                if c.nmems == 0:
                    raise Invalid("unknown memory")
                if ins[2] > ALIGN[op]:
                    raise Invalid("alignment must not be larger than natural")
                if ins[1] > 0xFFFFFFFF:
                    raise Invalid("offset out of range")
            fr = ctl[-1]
            for t in sig[0]:
                if len(vals) > fr[3]:
                    x = vals.pop()
                    if x != t and x:
                        raise Invalid("type mismatch")
                elif not fr[4]:
                    raise Invalid("type mismatch")
            if sig[1]:
                vals.append(sig[1])
        elif op == 0x20:
            if ins[1] >= nl:
                raise Invalid("unknown local")
            vals.append(lt[ins[1]])
        elif op == 0x21:
            if ins[1] >= nl:
                raise Invalid("unknown local")
            pop(lt[ins[1]])
        elif op == 0x22:
            if ins[1] >= nl:
                raise Invalid("unknown local")
            pop(lt[ins[1]])
            vals.append(lt[ins[1]])
        elif op in (0x02, 0x03, 0x04):
            if op == 0x04:
                pop(I32)
            bt = ins[1]
            if bt[0] == "idx":
                if bt[1] >= len(types):
                    raise Invalid("unknown type")
                bt = types[bt[1]]
            enter(op, bt[0], bt[1])
        elif op == 0x05:
            f = exit_()
            if f[0] != 0x04:
                raise Invalid("else without if")
            ctl.append([0x05, f[1], f[2], len(vals), False])
            vals.extend(f[1])
        elif op == 0x0B:
            f = exit_()
            if f[0] == 0x04 and f[1] != f[2]:
                raise Invalid("type mismatch")
            vals.extend(f[2])
        elif op == 0x0C:
            popn(label(ins[1]))
            unreachable()
        elif op == 0x0D:
            pop(I32)
            ts = label(ins[1])
            popn(ts)
            vals.extend(ts)
        elif op == 0x0E:
            pop(I32)
            arity = len(label(ins[2]))
            for n in ins[1]:
                ts = label(n)
                if len(ts) != arity:
                    raise Invalid("type mismatch")
                vals.extend(popn(ts))
            popn(label(ins[2]))
            unreachable()
        elif op == 0x0F:
            popn(results)
            unreachable()
        elif op == 0x00:
            unreachable()
        elif op == 0x01:
            pass
        elif op == 0x10:
            if ins[1] >= len(funcs):
                raise Invalid("unknown function")
            popn(funcs[ins[1]][0])
            vals.extend(funcs[ins[1]][1])
        elif op in (0x11, 0x13):
            if table(ins[2]) != FUNCREF:
                raise Invalid("type mismatch")
            if ins[1] >= len(types):
                raise Invalid("unknown type")
            pop(I32)
            ps, rs = types[ins[1]]
            popn(ps)
            if op == 0x11:
                vals.extend(rs)
            else:
                if rs != results:
                    raise Invalid("type mismatch")
                unreachable()
        elif op == 0x12:
            if ins[1] >= len(funcs):
                raise Invalid("unknown function")
            ps, rs = funcs[ins[1]]
            if rs != results:
                raise Invalid("type mismatch")
            popn(ps)
            unreachable()
        elif op == 0x1A:
            pop()
        elif op == 0x1B:
            pop(I32)
            if len(ins) == 2:  # typed select
                if len(ins[1]) != 1:
                    raise Invalid("invalid result arity")
                t = ins[1][0]
                pop(t)
                pop(t)
                vals.append(t)
            else:
                a, b = pop(), pop()
                if a in (FUNCREF, EXTERNREF) or b in (FUNCREF, EXTERNREF) or (a and b and a != b):
                    raise Invalid("type mismatch")
                vals.append(a or b)
        elif op in (0x23, 0x24):
            if ins[1] >= len(gtypes):
                raise Invalid("unknown global")
            if op == 0x23:
                vals.append(gtypes[ins[1]])
            else:
                if not c.gmut[ins[1]]:
                    raise Invalid("immutable global")
                pop(gtypes[ins[1]])
        elif op == 0x25:
            t = table(ins[1])
            pop(I32)
            vals.append(t)
        elif op == 0x26:
            t = table(ins[1])
            pop(t)
            pop(I32)
        elif op == 0x3F:
            need_mem()
            vals.append(I32)
        elif op == 0x40:
            need_mem()
            pop(I32)
            vals.append(I32)
        elif op == 0xD0:
            vals.append(_ref(ins[1]))
        elif op == 0xD1:
            if pop() in (I32, I64, F32, F64):
                raise Invalid("type mismatch")
            vals.append(I32)
        elif op == 0xD2:
            if ins[1] >= len(funcs):
                raise Invalid("unknown function")
            if ins[1] not in c.refs:
                raise Invalid("undeclared function reference")
            vals.append(FUNCREF)
        elif op in (0xFC08, 0xFC09):
            if ins[1] >= c.ndatas:
                raise Invalid("unknown data segment")
            if op == 0xFC08:
                need_mem()
                popn(list(BULK3))
        elif op in (0xFC0A, 0xFC0B):
            need_mem()
            popn(list(BULK3))
        else:
            raise _Unsupported(f"opcode {op:#x}")
    if ctl:
        raise Invalid("function body not closed")
