# pyright: basic, reportArgumentType=false, reportOptionalMemberAccess=false, reportAttributeAccessIssue=false, reportSelfClsParameterName=false
"""Runtime helpers imported by generated `pyc` modules (D18); ported from the poc/w2py.py prelude."""

import struct as _st, math as _m


class Trap(Exception):
    """A wasm trap. `code` is a name from Wasmtime's TrapCode (D6), e.g. "UNREACHABLE"."""

    def __init__(self, message, code=None):
        Exception.__init__(self, message)
        self.code = code


INF = float("inf")
NAN = float("nan")
_pi = _st.pack_into
_pk = _st.pack
_upk = _st.unpack
U32 = _st.Struct("<I").unpack_from
S32 = _st.Struct("<i").unpack_from
U64 = _st.Struct("<Q").unpack_from
U16 = _st.Struct("<H").unpack_from
S16 = _st.Struct("<h").unpack_from
S8 = _st.Struct("<b").unpack_from
F32L = _st.Struct("<f").unpack_from
F64L = _st.Struct("<d").unpack_from
P32 = _st.Struct("<I").pack_into
P64 = _st.Struct("<Q").pack_into
P16 = _st.Struct("<H").pack_into
F64S = _st.Struct("<d").pack_into


def F32S(m, a, v):
    try:
        _pi("<f", m, a, v)
    except OverflowError:
        _pi("<f", m, a, _m.copysign(INF, v))


def _f32(x):
    try:
        return _upk("<f", _pk("<f", x))[0]
    except OverflowError:
        return _m.copysign(INF, x)


def _grow(M, maxpages, n):
    old = len(M) >> 16
    if old + n > maxpages:
        return 0xFFFFFFFF
    M.extend(bytes(n << 16))
    return old


def _mcopy(M, d, s, n):
    if d + n > len(M) or s + n > len(M):
        raise Trap("out of bounds memory access", "MEMORY_OUT_OF_BOUNDS")
    M[d : d + n] = M[s : s + n]


def _mfill(M, d, v, n):
    if d + n > len(M):
        raise Trap("out of bounds memory access", "MEMORY_OUT_OF_BOUNDS")
    M[d : d + n] = bytes([v & 255]) * n


class _Mem(bytearray):
    """A linear memory; `max` is its declared maximum in pages (None: none). Shared by import."""

    max = None


class _Table:
    """An exported table: the list `t` (shared by import) and its declared maximum."""

    def __init__(self, t, max):
        self.t = t
        self.max = max


class _Cell:
    """A global shared between instances (imported, or exported and mutable): value in `.v`."""

    def __init__(self, v):
        self.v = v

    def get(self):
        return self.v


class _Global:
    """An exported global: `.get()` reads the current value."""

    def __init__(self, get):
        self.get = get


def _export(f):
    """Wrap an exported function: Python errors that mean a trap become `Trap` (provisional)."""

    def w(*a):
        try:
            return f(*a)
        except Trap:
            raise
        except (IndexError, _st.error):
            raise Trap("out of bounds memory access", "MEMORY_OUT_OF_BOUNDS") from None
        except ZeroDivisionError:
            raise Trap("integer divide by zero", "INTEGER_DIVISION_BY_ZERO") from None
        except RecursionError:
            raise Trap("call stack exhausted", "STACK_OVERFLOW") from None

    return w


def _pc(x):
    return x.bit_count()


def _bl(x):
    return x.bit_length()


def _clz32(x):
    return 32 - _bl(x)


def _clz64(x):
    return 64 - _bl(x)


def _ctz32(x):
    return _bl(x & -x) - 1 if x else 32


def _ctz64(x):
    return _bl(x & -x) - 1 if x else 64


def _rotl32(x, k):
    return ((x << k) | (x >> (32 - k))) & 0xFFFFFFFF


def _rotr32(x, k):
    return ((x >> k) | (x << (32 - k))) & 0xFFFFFFFF


def _rotl64(x, k):
    return ((x << k) | (x >> (64 - k))) & 0xFFFFFFFFFFFFFFFF


def _rotr64(x, k):
    return ((x >> k) | (x << (64 - k))) & 0xFFFFFFFFFFFFFFFF


def _divs32(a, b):
    a = (a ^ 0x80000000) - 0x80000000
    b = (b ^ 0x80000000) - 0x80000000
    if b == 0:
        raise Trap("integer divide by zero", "INTEGER_DIVISION_BY_ZERO")
    if a == -0x80000000 and b == -1:
        raise Trap("integer overflow", "INTEGER_OVERFLOW")
    q = abs(a) // abs(b)
    return (-q if (a < 0) != (b < 0) else q) & 0xFFFFFFFF


def _rems32(a, b):
    a = (a ^ 0x80000000) - 0x80000000
    b = (b ^ 0x80000000) - 0x80000000
    if b == 0:
        raise Trap("integer divide by zero", "INTEGER_DIVISION_BY_ZERO")
    r = abs(a) % abs(b)
    return (-r if a < 0 else r) & 0xFFFFFFFF


def _divs64(a, b):
    h = 0x8000000000000000
    a = (a ^ h) - h
    b = (b ^ h) - h
    if b == 0:
        raise Trap("integer divide by zero", "INTEGER_DIVISION_BY_ZERO")
    if a == -h and b == -1:
        raise Trap("integer overflow", "INTEGER_OVERFLOW")
    q = abs(a) // abs(b)
    return (-q if (a < 0) != (b < 0) else q) & 0xFFFFFFFFFFFFFFFF


def _rems64(a, b):
    h = 0x8000000000000000
    a = (a ^ h) - h
    b = (b ^ h) - h
    if b == 0:
        raise Trap("integer divide by zero", "INTEGER_DIVISION_BY_ZERO")
    r = abs(a) % abs(b)
    return (-r if a < 0 else r) & 0xFFFFFFFFFFFFFFFF


def _fdiv(a, b):
    try:
        return a / b
    except ZeroDivisionError:
        if a != a or a == 0:
            return NAN
        return _m.copysign(INF, a) * _m.copysign(1.0, b)


def _fsqrt(x):
    if x < 0:
        return NAN
    return _m.sqrt(x)


def _fceil(x):
    return float(_m.ceil(x)) if x == x and x not in (INF, -INF) and x != 0 else x


def _ffloor(x):
    return float(_m.floor(x)) if x == x and x not in (INF, -INF) and x != 0 else x


def _ftrunc(x):
    return float(_m.trunc(x)) if x == x and x not in (INF, -INF) and x != 0 else x


def _fnearest(x):
    if x != x or x in (INF, -INF) or x == 0:
        return x
    return _m.copysign(float(round(x)), x)


def _fmin(a, b):
    if a != a or b != b:
        return NAN
    if a == b == 0:
        return a if _m.copysign(1, a) < 0 else b
    return a if a < b else b


def _fmax(a, b):
    if a != a or b != b:
        return NAN
    if a == b == 0:
        return b if _m.copysign(1, a) < 0 else a
    return a if a > b else b


_copysign = _m.copysign


def _tr(x, lo, hi):
    if x != x:
        raise Trap("invalid conversion to integer", "BAD_CONVERSION_TO_INTEGER")
    t = _m.trunc(x) if x not in (INF, -INF) else None
    if t is None or not (lo <= t <= hi):
        raise Trap("integer overflow", "INTEGER_OVERFLOW")
    return t


def _trunc_s32(x):
    return _tr(x, -(2**31), 2**31 - 1) & 0xFFFFFFFF


def _trunc_u32(x):
    return _tr(x, 0, 2**32 - 1)


def _trunc_s64(x):
    return _tr(x, -(2**63), 2**63 - 1) & 0xFFFFFFFFFFFFFFFF


def _trunc_u64(x):
    return _tr(x, 0, 2**64 - 1)


def _sat(x, lo, hi):
    if x != x:
        return 0
    if x >= hi:
        return hi
    if x <= lo:
        return lo
    return _m.trunc(x)


def _tsat_s32(x):
    return _sat(x, -(2**31), 2**31 - 1) & 0xFFFFFFFF


def _tsat_u32(x):
    return _sat(x, 0, 2**32 - 1)


def _tsat_s64(x):
    return _sat(x, -(2**63), 2**63 - 1) & 0xFFFFFFFFFFFFFFFF


def _tsat_u64(x):
    return _sat(x, 0, 2**64 - 1)


def _i32_f32(x):
    return _st.unpack("<I", _st.pack("<f", x))[0]


def _f32_i32(x):
    return _st.unpack("<f", _st.pack("<I", x))[0]


def _i64_f64(x):
    return _st.unpack("<Q", _st.pack("<d", x))[0]


def _f64_i64(x):
    return _st.unpack("<d", _st.pack("<Q", x))[0]


_NAMES = [
    n for n in dir() if n.startswith("_") and not n.startswith("__") and n not in ("_st", "_m")
]
__all__ = _NAMES + [
    "Trap",
    "INF",
    "NAN",
    "U32",
    "S32",
    "U64",
    "U16",
    "S16",
    "S8",
    "F32L",
    "F64L",
    "P32",
    "P64",
    "P16",
    "F64S",
    "F32S",
]  # pyright: ignore[reportUnsupportedDunderAll]
