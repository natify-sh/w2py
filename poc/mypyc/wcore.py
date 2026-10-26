
from typing import Final, Any, Callable
from mypy_extensions import i64, u8
from librt.strings import BytesWriter
import struct as _st, math as _m
class Trap(Exception): pass
INF: Final = float('inf')
NAN: Final = float('nan')
MEM: Final = BytesWriter()
MEM.write(bytes(17 * 65536))
_MAXPAGES: Final = 65536
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


class _State:
    def __init__(self) -> None:
        self.g0: i64 = 1048576
        self.i0: Callable[..., Any] = _noop
        self.i1: Callable[..., Any] = _noop
        pass

GS: Final = _State()

def f2(p0: i64, p1: i64) -> i64:
    l2: i64 = 0
    l3: i64 = 0
    l4: i64 = 0
    t1: i64 = 0
    t2: i64 = 0
    t3: i64 = 0
    t4: i64 = 0
    _br: i64 = 0
    _w: i64 = 0
    l2 = ld16(p0)
    if not ((l2&128)==0):
        return (l2&127)
    l3 = (((l2>>3)&15)*17)
    l4 = l2
    t1 = (l2&7)
    if t1 == 0:
        pass
    elif t1 == 1:
        _br = 3
    else:
        _br = 2
    if not _br:
        l4 = f10(ld32(p1+24), ld32(p1+20), (ld16s(p1)&0xFFFFFFFF), (ld16s(p1+2)&0xFFFFFFFF), (l3 if (l3>34) else 34), ld16(p1+56))
        if ld16(p1+62):
            _br = 2
        else:
            st16(p1+62, l4)
            _br = 2
    if _br == 3:
        _br = 0
    if not _br:
        l4 = f7((_w if (_w:=(p1+40)) <= 0xFFFFFFFF else _w-0x100000000), l3, ld16(p1+56))
        if ld16(p1+60):
            pass
        else:
            st16(p1+60, l4)
    _br = 0
    t4 = f14(l4, ld16(p1+56))
    st16(p1+56, t4)
    p1 = (l4&127)
    st16(p0, (((l2&65280)|p1)|128))
    return p1


def f3(p0: i64, p1: i64) -> i64:
    l2: i64 = 0
    l3: i64 = 0
    l4: i64 = 0
    l5: i64 = 0
    l6: i64 = 0
    l7: i64 = 0
    l8: i64 = 0
    l9: i64 = 0
    l10: i64 = 0
    l11: i64 = 0
    l12: i64 = 0
    l13: i64 = 0
    l14: i64 = 0
    l15: i64 = 0
    t1: i64 = 0
    t2: i64 = 0
    t3: i64 = 0
    t4: i64 = 0
    t5: i64 = 0
    _br: i64 = 0
    l2 = ld32(p0+36)
    l3 = (ld16s(p0+4)&0xFFFFFFFF)
    if not (0 < l3 <= 2147483647):
        l4 = 0
        l5 = p1
        l6 = 0
        _br = 1
    if not _br:
        l7 = 0
        l8 = 0
        l9 = 0
        l10 = 0
        l5 = p1
        while True:
            l11 = l2
            if not (((((l5&65535)^32768)-32768)&0xFFFFFFFF) > 2147483647):
                l12 = (l5&65535)
                l11 = l2
                while True:
                    if (ld16(ld32(l11+4)+2)==l12):
                        _br = 4
                        break
                    l11 = ld32(l11)
                    if l11:
                        continue
                    _br = 5
                    break
                    break
                if not _br:
                    pass
            if not _br:
                while True:
                    if ((ld8(ld32(l11+4))^(l7&255))==0):
                        _br = 4
                        break
                    l11 = ld32(l11)
                    if l11:
                        continue
                    break
                if not _br:
                    pass
            if _br == 5:
                _br = 0
            if not _br:
                l11 = 0
            _br = 0
            if not l2:
                l2 = 0
                _br = 9
            if not _br:
                l12 = l2
                l2 = 0
                while True:
                    l13 = l2
                    l2 = l12
                    l12 = ld32(l2)
                    st32(l2, l13)
                    if l12:
                        continue
                    break
            _br = 0
            if not l11:
                l10 = (l10+1)
                if l10 > 0xFFFFFFFF: l10 -= 0x100000000
                l12 = (ld8(ld32(ld32(l2)+4)+1)&1)
                _br = 12
            if not _br:
                l9 = (l9+1)
                if l9 > 0xFFFFFFFF: l9 -= 0x100000000
                l12 = ld16(ld32(l11+4))
                l12 = ((l12&(l12>>9))&1)
                l13 = ld32(l11)
                if (l13==0):
                    pass
                else:
                    st32(l11, ld32(l13))
                    st32(l13, ld32(l2))
                    st32(l2, l13)
            _br = 0
            l8 = (l12+l8)
            if l8 > 0xFFFFFFFF: l8 -= 0x100000000
            l5 = (l5+(((l5^-1)&32768)>>15))
            if l5 > 0xFFFFFFFF: l5 -= 0x100000000
            l7 = (((((l7+1)&65535)^32768)-32768)&0xFFFFFFFF)
            if ((l7^0x80000000)<(l3^0x80000000)):
                continue
            break
        l4 = ((l3-1)&255)
        l6 = ((((l9<<2)-l10)+l8)&0xFFFFFFFF)
    _br = 0
    if not (0 < p1 <= 2147483647):
        l12 = l2
        _br = 14
    if not _br:
        p1 = 1
        while True:
            l14 = 0
            l7 = 0
            l12 = 0
            while True:
                l15 = l14
                if not (0 < p1 <= 2147483647):
                    l8 = 0
                    l13 = l2
                    _br = 18
                if not _br:
                    l11 = 0
                    l13 = l2
                    while True:
                        l13 = ld32(l13)
                        if (l13==0):
                            _br = 20
                            break
                        l11 = (l11+1)
                        if l11 > 0xFFFFFFFF: l11 -= 0x100000000
                        if (p1!=l11):
                            continue
                        break
                    if not _br:
                        l8 = p1
                        _br = 18
                    if _br == 20:
                        _br = 0
                    if not _br:
                        l8 = (l11+1)
                        if l8 > 0xFFFFFFFF: l8 -= 0x100000000
                        l13 = 0
                _br = 0
                l14 = (l15+1)
                if l14 > 0xFFFFFFFF: l14 -= 0x100000000
                l11 = l2
                l2 = l13
                l10 = p1
                while True:
                    if not (0 < l8 <= 2147483647):
                        if (l10 < 1 or l10 > 2147483647):
                            _br = 22
                            break
                        if (l2==0):
                            _br = 22
                            break
                        if not l8:
                            l10 = (l10-1)
                            if l10 < 0: l10 += 0x100000000
                            l3 = ld32(l2)
                            l8 = 0
                            _br = 26
                    if not _br:
                        if (l10==0):
                            _br = 25
                        else:
                            if (l2==0):
                                _br = 25
                            else:
                                l13 = ld32(l2+4)
                                t1 = f2(ld32(l11+4), p0)
                                t2 = f2(l13, p0)
                                if (t1<=t2):
                                    _br = 25
                                else:
                                    l10 = (l10-1)
                                    if l10 < 0: l10 += 0x100000000
                                    l3 = ld32(l2)
                    if _br == 26:
                        _br = 0
                    if not _br:
                        l9 = l11
                        l13 = l2
                        _br = 24
                    if _br == 25:
                        _br = 0
                    if not _br:
                        l8 = (l8-1)
                        if l8 < 0: l8 += 0x100000000
                        l9 = ld32(l11)
                        l3 = l2
                        l13 = l11
                    _br = 0
                    if not l7:
                        l12 = l13
                        _br = 28
                    if not _br:
                        st32(l7, l13)
                    _br = 0
                    l11 = l9
                    l2 = l3
                    l7 = l13
                    continue
                    break
                if not _br:
                    pass
                _br = 0
                if l2:
                    continue
                break
            st32(l7, 0)
            p1 = ((p1<<1)&0xFFFFFFFF)
            l2 = l12
            if l15:
                continue
            break
    _br = 0
    l2 = ld32(l12)
    l7 = ld32(l2+4)
    l13 = ld32(l2)
    st32(l2+4, ld32(l13+4))
    st32(l2, ld32(l13))
    st32(l13+4, l7)
    st32(l13, 0)
    t3 = ((((l5&65535)^32768)-32768)&0xFFFFFFFF)
    if not (t3 <= 2147483647 or t3 > 4294967295):
        l2 = l12
        while True:
            if (ld8(ld32(l2+4))==l4):
                _br = 31
                break
            l2 = ld32(l2)
            if l2:
                continue
            _br = 32
            break
            break
        if not _br:
            pass
    if not _br:
        l11 = (l5&65535)
        l2 = l12
        while True:
            if (ld16(ld32(l2+4)+2)==l11):
                _br = 31
                break
            l2 = ld32(l2)
            if l2:
                continue
            break
        if not _br:
            pass
    if _br == 32:
        _br = 0
    if not _br:
        l2 = ld32(l12)
        if (l2==0):
            _br = 30
        else:
            pass
    if _br == 31:
        _br = 0
    if not _br:
        while True:
            l6 = f16((ld16s(ld32(l12+4))&0xFFFFFFFF), (l6&65535))
            l2 = ld32(l2)
            if l2:
                continue
            break
        l7 = ld32(l13+4)
    _br = 0
    l2 = ld32(l12)
    st32(l13+4, ld32(l2+4))
    st32(l13, ld32(l2))
    st32(l2+4, l7)
    st32(l2, l13)
    l10 = 1
    while True:
        p1 = 0
        l13 = 0
        p0 = 0
        while True:
            l14 = p1
            if not (0 < l10 <= 2147483647):
                l7 = 0
                l11 = l12
                _br = 39
            if not _br:
                l2 = 0
                l11 = l12
                while True:
                    l11 = ld32(l11)
                    if (l11==0):
                        _br = 41
                        break
                    l2 = (l2+1)
                    if l2 > 0xFFFFFFFF: l2 -= 0x100000000
                    if (l10!=l2):
                        continue
                    break
                if not _br:
                    l7 = l10
                    _br = 39
                if _br == 41:
                    _br = 0
                if not _br:
                    l7 = (l2+1)
                    if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                    l11 = 0
            _br = 0
            p1 = (l14+1)
            if p1 > 0xFFFFFFFF: p1 -= 0x100000000
            l2 = l12
            l12 = l11
            l3 = l10
            while True:
                if not (0 < l7 <= 2147483647):
                    if (l3 < 1 or l3 > 2147483647):
                        _br = 43
                        break
                    if (l12==0):
                        _br = 43
                        break
                    if not l7:
                        l3 = (l3-1)
                        if l3 < 0: l3 += 0x100000000
                        l8 = ld32(l12)
                        l7 = 0
                        _br = 47
                if not _br:
                    if (l3==0):
                        _br = 46
                    else:
                        if (l12==0):
                            _br = 46
                        else:
                            l11 = ld32(l2+4)
                            st8(l11, ld8(l11+1))
                            l8 = ld32(l12+4)
                            st8(l8, ld8(l8+1))
                            if (((ld16s(l11+2)&0xFFFFFFFF)^0x80000000)<=((ld16s(l8+2)&0xFFFFFFFF)^0x80000000)):
                                _br = 46
                            else:
                                l3 = (l3-1)
                                if l3 < 0: l3 += 0x100000000
                                l8 = ld32(l12)
                if _br == 47:
                    _br = 0
                if not _br:
                    l9 = l2
                    l11 = l12
                    _br = 45
                if _br == 46:
                    _br = 0
                if not _br:
                    l7 = (l7-1)
                    if l7 < 0: l7 += 0x100000000
                    l9 = ld32(l2)
                    l8 = l12
                    l11 = l2
                _br = 0
                if not l13:
                    p0 = l11
                    _br = 49
                if not _br:
                    st32(l13, l11)
                _br = 0
                l2 = l9
                l12 = l8
                l13 = l11
                continue
                break
            if not _br:
                pass
            _br = 0
            if l12:
                continue
            break
        st32(l13, 0)
        l10 = ((l10<<1)&0xFFFFFFFF)
        l12 = p0
        if l14:
            continue
        break
    l12 = ld32(p0)
    if not (l12==0):
        while True:
            l6 = f16((ld16s(ld32(p0+4))&0xFFFFFFFF), (l6&65535))
            l12 = ld32(l12)
            if l12:
                continue
            break
    return (l6&65535)


def f4(p0: i64, p1: i64, p2: i64) -> i64:
    l3: i64 = 0
    l4: i64 = 0
    l5: i64 = 0
    l6: i64 = 0
    l7: i64 = 0
    l8: i64 = 0
    l9: i64 = 0
    l10: i64 = 0
    l11: i64 = 0
    _br: i64 = 0
    l3 = 0
    st32(p1, 0)
    l4 = ((p0//20)-2)
    if l4 < 0: l4 += 0x100000000
    l5 = ((p1+(l4<<3))&0xFFFFFFFF)
    st32(p1+4, l5)
    st32(l5, 32896)
    p0 = (p1+8)
    if p0 > 0xFFFFFFFF: p0 -= 0x100000000
    l6 = (l5+4)
    if l6 > 0xFFFFFFFF: l6 -= 0x100000000
    if not (l4>=3):
        l7 = p0
        _br = 1
    if not _br:
        st32(p1+12, l6)
        st32(p1, p0)
        st32(p1+8, 0)
        st32(l5+4, 2147483647)
        l6 = (l5+8)
        if l6 > 0xFFFFFFFF: l6 -= 0x100000000
        l7 = (p1+16)
        if l7 > 0xFFFFFFFF: l7 -= 0x100000000
        l3 = p0
    _br = 0
    if not (l4==0):
        l8 = ((l5+(l4<<2))&0xFFFFFFFF)
        p0 = 0
        while True:
            l9 = (l7+8)
            if l9 > 0xFFFFFFFF: l9 -= 0x100000000
            if not (l9>=l5):
                l10 = (l6+4)
                if l10 > 0xFFFFFFFF: l10 -= 0x100000000
                if not (l10>=l8):
                    st32(l7, l3)
                    st32(l7+4, l6)
                    st16(l6+2, 32767)
                    st16(l6, (((((p2^p0)<<3)&120)|(p0&7))*257))
                    st32(p1, l7)
                    l3 = l7
                    l6 = l10
                    l7 = l9
            p0 = (p0+1)
            if p0 > 0xFFFFFFFF: p0 -= 0x100000000
            if (l4!=p0):
                continue
            break
    l7 = ld32(l3)
    if not (l7==0):
        l9 = (l4//5)
        p0 = 2
        while True:
            l7 = l7
            l6 = (p0-1)
            if l6 < 0: l6 += 0x100000000
            if not (l6<l9):
                l6 = (((p0<<8)&1792)|((p2^l6)&16383))
            st16(ld32(l3+4)+2, l6)
            p0 = (p0+1)
            if p0 > 0xFFFFFFFF: p0 -= 0x100000000
            l3 = l7
            l7 = ld32(l7)
            if l7:
                continue
            break
    p2 = 1
    while True:
        l8 = 0
        l6 = 0
        l10 = 0
        while True:
            l11 = l8
            if not (0 < p2 <= 2147483647):
                l3 = 0
                l7 = p1
                _br = 11
            if not _br:
                p0 = 0
                l7 = p1
                while True:
                    l7 = ld32(l7)
                    if (l7==0):
                        _br = 13
                        break
                    p0 = (p0+1)
                    if p0 > 0xFFFFFFFF: p0 -= 0x100000000
                    if (p2!=p0):
                        continue
                    break
                if not _br:
                    l3 = p2
                    _br = 11
                if _br == 13:
                    _br = 0
                if not _br:
                    l3 = (p0+1)
                    if l3 > 0xFFFFFFFF: l3 -= 0x100000000
                    l7 = 0
            _br = 0
            l8 = (l11+1)
            if l8 > 0xFFFFFFFF: l8 -= 0x100000000
            p0 = p1
            p1 = l7
            l4 = p2
            while True:
                if not (0 < l3 <= 2147483647):
                    if (l4 < 1 or l4 > 2147483647):
                        _br = 15
                        break
                    if (p1==0):
                        _br = 15
                        break
                    if not l3:
                        l4 = (l4-1)
                        if l4 < 0: l4 += 0x100000000
                        l9 = ld32(p1)
                        l3 = 0
                        _br = 19
                if not _br:
                    if (l4==0):
                        _br = 18
                    else:
                        if (p1==0):
                            _br = 18
                        else:
                            l7 = ld32(p0+4)
                            st8(l7, ld8(l7+1))
                            l9 = ld32(p1+4)
                            st8(l9, ld8(l9+1))
                            if (((ld16s(l7+2)&0xFFFFFFFF)^0x80000000)<=((ld16s(l9+2)&0xFFFFFFFF)^0x80000000)):
                                _br = 18
                            else:
                                l4 = (l4-1)
                                if l4 < 0: l4 += 0x100000000
                                l9 = ld32(p1)
                if _br == 19:
                    _br = 0
                if not _br:
                    l5 = p0
                    l7 = p1
                    _br = 17
                if _br == 18:
                    _br = 0
                if not _br:
                    l3 = (l3-1)
                    if l3 < 0: l3 += 0x100000000
                    l5 = ld32(p0)
                    l9 = p1
                    l7 = p0
                _br = 0
                if not l6:
                    l10 = l7
                    _br = 21
                if not _br:
                    st32(l6, l7)
                _br = 0
                p0 = l5
                p1 = l9
                l6 = l7
                continue
                break
            if not _br:
                pass
            _br = 0
            if p1:
                continue
            break
        st32(l6, 0)
        p2 = ((p2<<1)&0xFFFFFFFF)
        p1 = l10
        if l11:
            continue
        break
    return l10


def f5(p0: i64) -> i64:
    l1: i64 = 0
    l2: i64 = 0
    t1: i64 = 0
    t2: i64 = 0
    t3: i64 = 0
    t4: i64 = 0
    t5: i64 = 0
    t6: i64 = 0
    t7: i64 = 0
    t8: i64 = 0
    st64(p0+56, 0)
    l1 = ld32(p0+28)
    if not (l1==0):
        t1 = f3(p0, 1)
        t2 = f14(t1, ld16(p0+56))
        st16(p0+56, t2)
        t3 = f3(p0, 4294967295)
        l2 = f14(t3, ld16(p0+56))
        st16(p0+58, l2)
        st16(p0+56, l2)
        if not (l1==1):
            l1 = (l1-1)
            if l1 < 0: l1 += 0x100000000
            while True:
                t5 = f3(p0, 1)
                t6 = f14(t5, ld16(p0+56))
                st16(p0+56, t6)
                t7 = f3(p0, 4294967295)
                t8 = f14(t7, ld16(p0+56))
                st16(p0+56, t8)
                l1 = (l1-1)
                if l1 < 0: l1 += 0x100000000
                if l1:
                    continue
                break
    return 0


def f6() -> i64:
    l0: i64 = 0
    l1: i64 = 0
    l2: i64 = 0
    l3: i64 = 0
    l4: i64 = 0
    l5: i64 = 0
    l6: i64 = 0
    l7: i64 = 0
    l8: float = 0.0
    l9: i64 = 0
    l10: i64 = 0
    l11: i64 = 0
    l12: i64 = 0
    l13: i64 = 0
    l14: i64 = 0
    l15: i64 = 0
    l16: i64 = 0
    t1: i64 = 0
    t2: i64 = 0
    t3: i64 = 0
    t4: i64 = 0
    t5: i64 = 0
    t6: i64 = 0
    t7: i64 = 0
    t8: i64 = 0
    t9: i64 = 0
    t10: float = 0.0
    t11: i64 = 0
    t12: i64 = 0
    t13: i64 = 0
    t14: i64 = 0
    t15: i64 = 0
    t16: i64 = 0
    t17: i64 = 0
    t18: i64 = 0
    t19: i64 = 0
    t20: i64 = 0
    t21: i64 = 0
    t22: i64 = 0
    t23: i64 = 0
    t24: float = 0.0
    t25: i64 = 0
    t26: float = 0.0
    t27: float = 0.0
    t28: float = 0.0
    t29: i64 = 0
    t30: float = 0.0
    t31: i64 = 0
    t32: i64 = 0
    t33: i64 = 0
    t34: i64 = 0
    t35: i64 = 0
    t36: i64 = 0
    t37: i64 = 0
    t38: i64 = 0
    t39: i64 = 0
    t40: i64 = 0
    t41: i64 = 0
    t42: float = 0.0
    t43: i64 = 0
    t44: i64 = 0
    t45: i64 = 0
    _br: i64 = 0
    _w: i64 = 0
    l0 = (GS.g0-368)
    if l0 < 0: l0 += 0x100000000
    GS.g0 = l0
    l1 = 0
    st32(l0+364, 0)
    l2 = (l0+354)
    if l2 > 0xFFFFFFFF: l2 -= 0x100000000
    f26(l2, (_w if (_w:=(l0+364)) <= 0xFFFFFFFF else _w-0x100000000), (_w if (_w:=(l0+360)) <= 0xFFFFFFFF else _w-0x100000000))
    t1 = f13(1)
    st16(l0+288, t1)
    t2 = f13(2)
    st16(l0+290, t2)
    t3 = f13(3)
    st16(l0+292, t3)
    t4 = f13(4)
    st32(l0+316, t4)
    l3 = f13(5)
    l3 = (l3 if l3 else 7)
    st32(l0+320, l3)
    l4 = ld16(l0+292)
    l5 = ld16(l0+290)
    l6 = ld16(l0+288)
    if not l6:
        if not (l5&65535):
            if ((l4&65535)==0):
                _br = 2
            else:
                pass
    if not _br:
        if (l6!=1):
            _br = 1
        else:
            if (l5&65535):
                _br = 1
            else:
                l1 = 13333
                if (l4&65535):
                    _br = 1
                else:
                    pass
    if _br == 2:
        _br = 0
    if not _br:
        st16(l0+292, 102)
        st16(l0+290, l1)
        st16(l0+288, l1)
        l6 = l1
    _br = 0
    l1 = 0
    st16(l0+352, 0)
    st32(l0+296, 1050512)
    l5 = (l3&1)
    l4 = (2000//((((l3>>2)&1)+(((l3>>1)&1)+l5))&0xFFFFFFFF))
    st32(l0+312, l4)
    if not (l5==0):
        st32(l0+300, 1050512)
        l1 = 1
    l7 = (l3&2)
    if not (l7==0):
        st32(l0+304, ((l4*l1)+1050512))
        l1 = (l1+1)
        if l1 > 0xFFFFFFFF: l1 -= 0x100000000
    if not ((l3&4)==0):
        st32(l0+308, ((l4*l1)+1050512))
    if not (l5==0):
        t6 = f4(l4, ld32(l0+300), ((((l6&65535)^32768)-32768)&0xFFFFFFFF))
        st32(l0+324, t6)
        l3 = ld32(l0+320)
        l7 = (l3&2)
    if not (l7==0):
        t7 = f9(ld32(l0+312), ld32(l0+304), (((ld16(l0+290)<<16)|ld16s(l0+288))&0xFFFFFFFF), (_w if (_w:=(l0+328)) <= 0xFFFFFFFF else _w-0x100000000))
        l3 = ld32(l0+320)
    if not ((l3&4)==0):
        f12(ld32(l0+312), (ld16s(l0+288)&0xFFFFFFFF), ld32(l0+308))
    if not ld32(l0+316):
        st32(l0+316, 1)
        while True:
            st32(l0+316, (ld32(l0+316)*10))
            f22()
            t8 = f5((_w if (_w:=(l0+288)) <= 0xFFFFFFFF else _w-0x100000000))
            f23()
            t9 = f24()
            l8 = f25(t9)
            if (l8<1.0):
                continue
            break
        l3 = _tsat_u32(l8)
        st32(l0+316, (ld32(l0+316)*((10//(l3 if (l3>1) else 1))+1)))
    f22()
    t11 = f5((_w if (_w:=(l0+288)) <= 0xFFFFFFFF else _w-0x100000000))
    f23()
    l9 = 0
    l10 = f24()
    l3 = f16((ld16s(l0+288)&0xFFFFFFFF), 0)
    l3 = f16((ld16s(l0+290)&0xFFFFFFFF), l3)
    l3 = f16((ld16s(l0+292)&0xFFFFFFFF), l3)
    l4 = 65535
    l11 = f16((ld16s(l0+312)&0xFFFFFFFF), l3)
    if not (31492 < l11 <= 2147483647):
        if (l11==6386):
            _br = 14
        else:
            if (l11!=20143):
                _br = 12
            else:
                l1 = 2
                l9 = 0
                l3 = 1049510
                _br = 13
    if not _br:
        if not (l11==59893):
            l3 = 1049647
            if not (l11!=35330):
                l1 = 0
                _br = 13
            if not _br:
                if (l11!=31493):
                    _br = 12
                else:
                    l1 = 1
                    l9 = 0
                    l3 = 1049559
                    _br = 13
        if not _br:
            l1 = 3
            l9 = 1
            l3 = 1049692
            _br = 13
    if _br == 14:
        _br = 0
    if not _br:
        l1 = 4
        l9 = 0
        l3 = 1049603
    if _br == 13:
        _br = 0
    if not _br:
        l4 = 0
        t17 = f18(l3, 0)
        if (ld32(0+1050504)==0):
            pass
        else:
            l3 = ((l1<<1)&0xFFFFFFFF)
            l12 = (l3+1049820)
            if l12 > 0xFFFFFFFF: l12 -= 0x100000000
            l13 = (l3+1049810)
            if l13 > 0xFFFFFFFF: l13 -= 0x100000000
            l14 = (l3+1049800)
            if l14 > 0xFFFFFFFF: l14 -= 0x100000000
            l5 = 0
            l7 = 0
            l4 = 0
            while True:
                l3 = (((l0+288)+(l5*68))&0xFFFFFFFF)
                st16(l3+64, 0)
                l1 = ld32(l3+32)
                if not (l1&1):
                    l6 = 0
                    _br = 19
                if not _br:
                    l6 = 0
                    l15 = ld16(l3+58)
                    l16 = ld16(l14)
                    if (l15==l16):
                        pass
                    else:
                        st32(l0+280, l16)
                        st32(l0+276, l15)
                        st32(l0+272, l5)
                        t18 = f18(1048900, (_w if (_w:=(l0+272)) <= 0xFFFFFFFF else _w-0x100000000))
                        l6 = (ld16(l3+64)+1)
                        st16(l3+64, l6)
                        l1 = ld32(l3+32)
                _br = 0
                if not ((l1&2)==0):
                    l15 = ld16(l3+60)
                    l16 = ld16(l13)
                    if not (l15==l16):
                        st32(l0+264, l16)
                        st32(l0+260, l15)
                        st32(l0+256, l5)
                        t19 = f18(1048851, (_w if (_w:=(l0+256)) <= 0xFFFFFFFF else _w-0x100000000))
                        l6 = (ld16(l3+64)+1)
                        st16(l3+64, l6)
                        l1 = ld32(l3+32)
                if not ((l1&4)==0):
                    l1 = ld16(l3+62)
                    l15 = ld16(l12)
                    if not (l1==l15):
                        st32(l0+248, l15)
                        st32(l0+244, l1)
                        st32(l0+240, l5)
                        t20 = f18(1048947, (_w if (_w:=(l0+240)) <= 0xFFFFFFFF else _w-0x100000000))
                        l6 = (ld16(l3+64)+1)
                        st16(l3+64, l6)
                l4 = (l6+l4)
                if l4 > 0xFFFFFFFF: l4 -= 0x100000000
                l7 = (l7+1)
                if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                l5 = (l7&65535)
                if (ld32(0+1050504)>l5):
                    continue
                break
    _br = 0
    l3 = f17()
    st32(l0+224, ld32(l0+312))
    t22 = f18(1049134, (_w if (_w:=(l0+224)) <= 0xFFFFFFFF else _w-0x100000000))
    st32(l0+208, l10)
    t23 = f18(1049158, (_w if (_w:=(l0+208)) <= 0xFFFFFFFF else _w-0x100000000))
    t24 = f25(l10)
    stf64(l0+192, t24)
    t25 = f18(1049275, (_w if (_w:=(l0+192)) <= 0xFFFFFFFF else _w-0x100000000))
    t26 = f25(l10)
    if not (not (t26>0.0)):
        t27 = float(((ld32(l0+316)*ld32(0+1050504))&0xFFFFFFFF))
        t28 = f25(l10)
        stf64(l0+176, _fdiv(t27,t28))
        t29 = f18(1049298, (_w if (_w:=(l0+176)) <= 0xFFFFFFFF else _w-0x100000000))
    l6 = (l4+l3)
    if l6 > 0xFFFFFFFF: l6 -= 0x100000000
    t30 = f25(l10)
    if not (not (t30<10.0)):
        t31 = f18(1049737, 0)
        l6 = (l6+1)
        if l6 > 0xFFFFFFFF: l6 -= 0x100000000
    st32(l0+160, (ld32(l0+316)*ld32(0+1050504)))
    t32 = f18(1049182, (_w if (_w:=(l0+160)) <= 0xFFFFFFFF else _w-0x100000000))
    st32(l0+144, 1048755)
    t33 = f18(1049206, (_w if (_w:=(l0+144)) <= 0xFFFFFFFF else _w-0x100000000))
    st32(l0+128, 1048742)
    t34 = f18(1049252, (_w if (_w:=(l0+128)) <= 0xFFFFFFFF else _w-0x100000000))
    st32(l0+112, 1048718)
    t35 = f18(1049229, (_w if (_w:=(l0+112)) <= 0xFFFFFFFF else _w-0x100000000))
    st32(l0+96, l11)
    t36 = f18(1049107, (_w if (_w:=(l0+96)) <= 0xFFFFFFFF else _w-0x100000000))
    l3 = ld32(0+1050504)
    l1 = ld32(l0+320)
    if not ((l1&1)==0):
        if not (l3==0):
            l1 = 0
            l5 = 0
            while True:
                st32(l0+84, ld16((((l0+288)+(l1*68))&0xFFFFFFFF)+58))
                st32(l0+80, l1)
                t37 = f18(1049079, (_w if (_w:=(l0+80)) <= 0xFFFFFFFF else _w-0x100000000))
                l3 = ld32(0+1050504)
                l5 = (l5+1)
                if l5 > 0xFFFFFFFF: l5 -= 0x100000000
                l1 = (l5&65535)
                if (l3>l1):
                    continue
                break
            l1 = ld32(l0+320)
    if not ((l1&2)==0):
        if not (l3==0):
            l1 = 0
            l5 = 0
            while True:
                st32(l0+68, ld16((((l0+288)+(l1*68))&0xFFFFFFFF)+60))
                st32(l0+64, l1)
                t38 = f18(1048995, (_w if (_w:=(l0+64)) <= 0xFFFFFFFF else _w-0x100000000))
                l3 = ld32(0+1050504)
                l5 = (l5+1)
                if l5 > 0xFFFFFFFF: l5 -= 0x100000000
                l1 = (l5&65535)
                if (l3>l1):
                    continue
                break
            l1 = ld32(l0+320)
    if not ((l1&4)==0):
        if not (l3==0):
            l1 = 0
            l5 = 0
            while True:
                st32(l0+52, ld16((((l0+288)+(l1*68))&0xFFFFFFFF)+62))
                st32(l0+48, l1)
                t39 = f18(1049051, (_w if (_w:=(l0+48)) <= 0xFFFFFFFF else _w-0x100000000))
                l3 = ld32(0+1050504)
                l5 = (l5+1)
                if l5 > 0xFFFFFFFF: l5 -= 0x100000000
                l1 = (l5&65535)
                if (l3>l1):
                    continue
                break
    if not (l3==0):
        l3 = 0
        l1 = 0
        while True:
            st32(l0+36, ld16((((l0+288)+(l3*68))&0xFFFFFFFF)+56))
            st32(l0+32, l3)
            t40 = f18(1049023, (_w if (_w:=(l0+32)) <= 0xFFFFFFFF else _w-0x100000000))
            l1 = (l1+1)
            if l1 > 0xFFFFFFFF: l1 -= 0x100000000
            l3 = (l1&65535)
            if (ld32(0+1050504)>l3):
                continue
            break
    if not (l6&65535):
        t41 = f18(1049338, 0)
        if (l9==0):
            _br = 33
        else:
            l3 = ld32(0+1050504)
            l1 = ld32(l0+316)
            l8 = f25(l10)
            st32(l0+28, 1048742)
            st32(l0+24, 1048755)
            stf64(l0+16, _fdiv(float(((l1*l3)&0xFFFFFFFF)),l8))
            t43 = f18(1048622, (_w if (_w:=(l0+16)) <= 0xFFFFFFFF else _w-0x100000000))
            st32(l0, 1048718)
            t44 = f18(1048648, l0)
            l3 = 1049797
            _br = 34
    if not _br:
        l3 = (1049321 if (0 < ((((l6&65535)^32768)-32768)&0xFFFFFFFF) <= 2147483647) else 1049411)
    if _br == 34:
        _br = 0
    if not _br:
        t45 = f18(l3, 0)
    _br = 0
    f27(l2)
    GS.g0 = (_w if (_w:=(l0+368)) <= 0xFFFFFFFF else _w-0x100000000)
    return 0


def f7(p0: i64, p1: i64, p2: i64) -> i64:
    t1: i64 = 0
    t2: i64 = 0
    t1 = f8(ld32(p0), ld32(p0+12), ld32(p0+4), ld32(p0+8), p1)
    t2 = f16(t1, p2)
    return t2


def f8(p0: i64, p1: i64, p2: i64, p3: i64, p4: i64) -> i64:
    l5: i64 = 0
    l6: i64 = 0
    l7: i64 = 0
    l8: i64 = 0
    l9: i64 = 0
    l10: i64 = 0
    l11: i64 = 0
    l12: i64 = 0
    l13: i64 = 0
    l14: i64 = 0
    l15: i64 = 0
    l16: i64 = 0
    l17: i64 = 0
    l18: i64 = 0
    l19: i64 = 0
    l20: i64 = 0
    l21: i64 = 0
    l22: i64 = 0
    t1: i64 = 0
    t2: i64 = 0
    t3: i64 = 0
    t4: i64 = 0
    t5: i64 = 0
    t6: i64 = 0
    t7: i64 = 0
    t8: i64 = 0
    t9: i64 = 0
    t10: i64 = 0
    t11: i64 = 0
    t12: i64 = 0
    t13: i64 = 0
    _br: i64 = 0
    _w: i64 = 0
    if not (p0==0):
        l5 = ((p4|-4096)&0xFFFFFFFF)
        l6 = ((p0<<1)&0xFFFFFFFF)
        l7 = (p0&-4)
        l8 = (p0&3)
        l9 = (p0-1)
        if l9 < 0: l9 += 0x100000000
        l10 = 0
        l11 = p2
        while True:
            l12 = 0
            if not (l9<3):
                l12 = 0
                l13 = l11
                while True:
                    st16(l13, (ld16(l13)+p4))
                    l14 = (l13+2)
                    if l14 > 0xFFFFFFFF: l14 -= 0x100000000
                    st16(l14, (ld16(l14)+p4))
                    l14 = (l13+4)
                    if l14 > 0xFFFFFFFF: l14 -= 0x100000000
                    st16(l14, (ld16(l14)+p4))
                    l14 = (l13+6)
                    if l14 > 0xFFFFFFFF: l14 -= 0x100000000
                    st16(l14, (ld16(l14)+p4))
                    l13 = (l13+8)
                    if l13 > 0xFFFFFFFF: l13 -= 0x100000000
                    l12 = (l12+4)
                    if l12 > 0xFFFFFFFF: l12 -= 0x100000000
                    if (l7!=l12):
                        continue
                    break
                if (l8==0):
                    _br = 3
                else:
                    pass
            if not _br:
                l13 = ((l11+(l12<<1))&0xFFFFFFFF)
                l12 = l8
                while True:
                    st16(l13, (ld16(l13)+p4))
                    l13 = (l13+2)
                    if l13 > 0xFFFFFFFF: l13 -= 0x100000000
                    l12 = (l12-1)
                    if l12 < 0: l12 += 0x100000000
                    if l12:
                        continue
                    break
            _br = 0
            l11 = (l11+l6)
            if l11 > 0xFFFFFFFF: l11 -= 0x100000000
            l10 = (l10+1)
            if l10 > 0xFFFFFFFF: l10 -= 0x100000000
            if (l10!=p0):
                continue
            break
        l15 = ((p0<<1)&0xFFFFFFFF)
        l16 = ((p0<<2)&0xFFFFFFFF)
        l7 = (p0&-4)
        l17 = (p0&3)
        l11 = 0
        l10 = p2
        l8 = p1
        l6 = 0
        while True:
            l14 = 0
            if not (l9<3):
                l14 = 0
                l13 = l10
                l12 = l8
                while True:
                    st32(l12, (ld16s(l13)*p4))
                    st32((_w if (_w:=(l12+4)) <= 0xFFFFFFFF else _w-0x100000000), (ld16s((_w if (_w:=(l13+2)) <= 0xFFFFFFFF else _w-0x100000000))*p4))
                    st32((_w if (_w:=(l12+8)) <= 0xFFFFFFFF else _w-0x100000000), (ld16s((_w if (_w:=(l13+4)) <= 0xFFFFFFFF else _w-0x100000000))*p4))
                    st32((_w if (_w:=(l12+12)) <= 0xFFFFFFFF else _w-0x100000000), (ld16s((_w if (_w:=(l13+6)) <= 0xFFFFFFFF else _w-0x100000000))*p4))
                    l13 = (l13+8)
                    if l13 > 0xFFFFFFFF: l13 -= 0x100000000
                    l12 = (l12+16)
                    if l12 > 0xFFFFFFFF: l12 -= 0x100000000
                    l14 = (l14+4)
                    if l14 > 0xFFFFFFFF: l14 -= 0x100000000
                    if (l7!=l14):
                        continue
                    break
                if (l17==0):
                    _br = 8
                else:
                    pass
            if not _br:
                l12 = (l14+l11)
                if l12 > 0xFFFFFFFF: l12 -= 0x100000000
                l13 = ((p2+(l12<<1))&0xFFFFFFFF)
                l12 = ((p1+(l12<<2))&0xFFFFFFFF)
                l14 = l17
                while True:
                    st32(l12, (ld16s(l13)*p4))
                    l13 = (l13+2)
                    if l13 > 0xFFFFFFFF: l13 -= 0x100000000
                    l12 = (l12+4)
                    if l12 > 0xFFFFFFFF: l12 -= 0x100000000
                    l14 = (l14-1)
                    if l14 < 0: l14 += 0x100000000
                    if l14:
                        continue
                    break
            _br = 0
            l11 = (l11+p0)
            if l11 > 0xFFFFFFFF: l11 -= 0x100000000
            l10 = (l10+l15)
            if l10 > 0xFFFFFFFF: l10 -= 0x100000000
            l8 = (l8+l16)
            if l8 > 0xFFFFFFFF: l8 -= 0x100000000
            l6 = (l6+1)
            if l6 > 0xFFFFFFFF: l6 -= 0x100000000
            if (l6!=p0):
                continue
            break
        l18 = ((p0<<2)&0xFFFFFFFF)
        l15 = (p0&-2)
        l19 = (p0&1)
        l20 = p1
        l16 = 0
        l11 = 0
        l12 = 0
        l10 = 0
        while True:
            if not l9:
                l7 = 0
                _br = 14
            if not _br:
                l7 = 0
                l13 = l20
                while True:
                    l8 = l12
                    l12 = ld32((_w if (_w:=(l13+4)) <= 0xFFFFFFFF else _w-0x100000000))
                    l14 = ld32(l13)
                    l10 = (l14+l10)
                    if l10 > 0xFFFFFFFF: l10 -= 0x100000000
                    l6 = (1 if ((l10^0x80000000)>(l5^0x80000000)) else 0)
                    l10 = (l12+(0 if l6 else l10))
                    if l10 > 0xFFFFFFFF: l10 -= 0x100000000
                    l17 = (1 if ((l10^0x80000000)>(l5^0x80000000)) else 0)
                    l10 = (0 if l17 else l10)
                    l11 = (((10 if l17 else (1 if ((l12^0x80000000)>(l14^0x80000000)) else 0))+((10 if l6 else (1 if ((l14^0x80000000)>(l8^0x80000000)) else 0))+l11))&0xFFFFFFFF)
                    l13 = (l13+8)
                    if l13 > 0xFFFFFFFF: l13 -= 0x100000000
                    l7 = (l7+2)
                    if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                    if (l15!=l7):
                        continue
                    break
                if (l19==0):
                    _br = 13
                else:
                    pass
            if _br == 14:
                _br = 0
            if not _br:
                l13 = ld32((((p1+((l16*p0)<<2))+(l7<<2))&0xFFFFFFFF))
                l14 = (l13+l10)
                if l14 > 0xFFFFFFFF: l14 -= 0x100000000
                t1 = l14
                l14 = (1 if ((l14^0x80000000)>(l5^0x80000000)) else 0)
                l10 = (0 if l14 else t1)
                l11 = ((10 if l14 else (1 if ((l13^0x80000000)>(l12^0x80000000)) else 0))+l11)
                if l11 > 0xFFFFFFFF: l11 -= 0x100000000
                l12 = l13
            _br = 0
            l20 = (l20+l18)
            if l20 > 0xFFFFFFFF: l20 -= 0x100000000
            l16 = (l16+1)
            if l16 > 0xFFFFFFFF: l16 -= 0x100000000
            if (l16!=p0):
                continue
            break
        l15 = ((p0<<1)&0xFFFFFFFF)
        l8 = (p0&-4)
        l17 = (p0&3)
        l6 = 0
        l21 = f16(((((l11&65535)^32768)-32768)&0xFFFFFFFF), 0)
        l11 = p2
        while True:
            l10 = 0
            l12 = 0
            if not (l9<3):
                l13 = 0
                l10 = 0
                l12 = 0
                while True:
                    l14 = (p3+l13)
                    if l14 > 0xFFFFFFFF: l14 -= 0x100000000
                    l7 = (l11+l13)
                    if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                    l12 = (((ld16s((_w if (_w:=(l14+6)) <= 0xFFFFFFFF else _w-0x100000000))*ld16s((_w if (_w:=(l7+6)) <= 0xFFFFFFFF else _w-0x100000000)))+((ld16s((_w if (_w:=(l14+4)) <= 0xFFFFFFFF else _w-0x100000000))*ld16s((_w if (_w:=(l7+4)) <= 0xFFFFFFFF else _w-0x100000000)))+((ld16s((_w if (_w:=(l14+2)) <= 0xFFFFFFFF else _w-0x100000000))*ld16s((_w if (_w:=(l7+2)) <= 0xFFFFFFFF else _w-0x100000000)))+((ld16s(l14)*ld16s(l7))+l12))))&0xFFFFFFFF)
                    l13 = (l13+8)
                    if l13 > 0xFFFFFFFF: l13 -= 0x100000000
                    l10 = (l10+4)
                    if l10 > 0xFFFFFFFF: l10 -= 0x100000000
                    if (l8!=l10):
                        continue
                    break
                if (l17==0):
                    _br = 18
                else:
                    pass
            if not _br:
                l13 = ((l10<<1)&0xFFFFFFFF)
                l14 = l17
                while True:
                    l12 = (((ld16s((_w if (_w:=(p3+l13)) <= 0xFFFFFFFF else _w-0x100000000))*ld16s((_w if (_w:=(l11+l13)) <= 0xFFFFFFFF else _w-0x100000000)))+l12)&0xFFFFFFFF)
                    l13 = (l13+2)
                    if l13 > 0xFFFFFFFF: l13 -= 0x100000000
                    l14 = (l14-1)
                    if l14 < 0: l14 += 0x100000000
                    if l14:
                        continue
                    break
            _br = 0
            st32(((p1+(l6<<2))&0xFFFFFFFF), l12)
            l11 = (l11+l15)
            if l11 > 0xFFFFFFFF: l11 -= 0x100000000
            l6 = (l6+1)
            if l6 > 0xFFFFFFFF: l6 -= 0x100000000
            if (l6!=p0):
                continue
            break
        l18 = ((p0<<2)&0xFFFFFFFF)
        l15 = (p0&-2)
        l19 = (p0&1)
        l20 = p1
        l16 = 0
        l6 = 0
        l12 = 0
        l11 = 0
        while True:
            if not l9:
                l7 = 0
                _br = 24
            if not _br:
                l7 = 0
                l13 = l20
                while True:
                    l10 = l12
                    l12 = ld32((_w if (_w:=(l13+4)) <= 0xFFFFFFFF else _w-0x100000000))
                    l14 = ld32(l13)
                    l11 = (l14+l11)
                    if l11 > 0xFFFFFFFF: l11 -= 0x100000000
                    l8 = (1 if ((l11^0x80000000)>(l5^0x80000000)) else 0)
                    l11 = (l12+(0 if l8 else l11))
                    if l11 > 0xFFFFFFFF: l11 -= 0x100000000
                    l17 = (1 if ((l11^0x80000000)>(l5^0x80000000)) else 0)
                    l11 = (0 if l17 else l11)
                    l6 = (((10 if l17 else (1 if ((l12^0x80000000)>(l14^0x80000000)) else 0))+((10 if l8 else (1 if ((l14^0x80000000)>(l10^0x80000000)) else 0))+l6))&0xFFFFFFFF)
                    l13 = (l13+8)
                    if l13 > 0xFFFFFFFF: l13 -= 0x100000000
                    l7 = (l7+2)
                    if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                    if (l15!=l7):
                        continue
                    break
                if (l19==0):
                    _br = 23
                else:
                    pass
            if _br == 24:
                _br = 0
            if not _br:
                l13 = ld32((((p1+((l16*p0)<<2))+(l7<<2))&0xFFFFFFFF))
                l14 = (l13+l11)
                if l14 > 0xFFFFFFFF: l14 -= 0x100000000
                t3 = l14
                l14 = (1 if ((l14^0x80000000)>(l5^0x80000000)) else 0)
                l11 = (0 if l14 else t3)
                l6 = ((10 if l14 else (1 if ((l13^0x80000000)>(l12^0x80000000)) else 0))+l6)
                if l6 > 0xFFFFFFFF: l6 -= 0x100000000
                l12 = l13
            _br = 0
            l20 = (l20+l18)
            if l20 > 0xFFFFFFFF: l20 -= 0x100000000
            l16 = (l16+1)
            if l16 > 0xFFFFFFFF: l16 -= 0x100000000
            if (l16!=p0):
                continue
            break
        l10 = ((p0<<2)&0xFFFFFFFF)
        l11 = ((p0<<1)&0xFFFFFFFF)
        l8 = (p0&-2)
        l18 = (p0&1)
        l19 = 0
        l22 = f16(((((l6&65535)^32768)-32768)&0xFFFFFFFF), l21)
        l20 = p2
        while True:
            l13 = ((l19*p0)&0xFFFFFFFF)
            l16 = ((p2+(l13<<1))&0xFFFFFFFF)
            l15 = ((p1+(l13<<2))&0xFFFFFFFF)
            l17 = p3
            l6 = 0
            while True:
                l7 = 0
                if not l9:
                    l14 = 0
                    _br = 30
                if not _br:
                    l13 = l17
                    l12 = l20
                    l14 = 0
                    while True:
                        l14 = (((ld16s((_w if (_w:=(l13+l11)) <= 0xFFFFFFFF else _w-0x100000000))*ld16s((_w if (_w:=(l12+2)) <= 0xFFFFFFFF else _w-0x100000000)))+((ld16s(l13)*ld16s(l12))+l14))&0xFFFFFFFF)
                        l13 = (l13+l10)
                        if l13 > 0xFFFFFFFF: l13 -= 0x100000000
                        l12 = (l12+4)
                        if l12 > 0xFFFFFFFF: l12 -= 0x100000000
                        l7 = (l7+2)
                        if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                        if (l8!=l7):
                            continue
                        break
                    if (l18==0):
                        _br = 29
                    else:
                        pass
                if _br == 30:
                    _br = 0
                if not _br:
                    l14 = (((ld16s((((p3+(l6<<1))+((l7*p0)<<1))&0xFFFFFFFF))*ld16s(((l16+(l7<<1))&0xFFFFFFFF)))+l14)&0xFFFFFFFF)
                _br = 0
                st32(((l15+(l6<<2))&0xFFFFFFFF), l14)
                l17 = (l17+2)
                if l17 > 0xFFFFFFFF: l17 -= 0x100000000
                l6 = (l6+1)
                if l6 > 0xFFFFFFFF: l6 -= 0x100000000
                if (l6!=p0):
                    continue
                break
            l20 = (l20+l11)
            if l20 > 0xFFFFFFFF: l20 -= 0x100000000
            l19 = (l19+1)
            if l19 > 0xFFFFFFFF: l19 -= 0x100000000
            if (l19!=p0):
                continue
            break
        l18 = ((p0<<2)&0xFFFFFFFF)
        l15 = (p0&-2)
        l19 = (p0&1)
        l20 = p1
        l16 = 0
        l11 = 0
        l12 = 0
        l10 = 0
        while True:
            if not l9:
                l7 = 0
                _br = 35
            if not _br:
                l7 = 0
                l13 = l20
                while True:
                    l8 = l12
                    l12 = ld32((_w if (_w:=(l13+4)) <= 0xFFFFFFFF else _w-0x100000000))
                    l14 = ld32(l13)
                    l10 = (l14+l10)
                    if l10 > 0xFFFFFFFF: l10 -= 0x100000000
                    l6 = (1 if ((l10^0x80000000)>(l5^0x80000000)) else 0)
                    l10 = (l12+(0 if l6 else l10))
                    if l10 > 0xFFFFFFFF: l10 -= 0x100000000
                    l17 = (1 if ((l10^0x80000000)>(l5^0x80000000)) else 0)
                    l10 = (0 if l17 else l10)
                    l11 = (((10 if l17 else (1 if ((l12^0x80000000)>(l14^0x80000000)) else 0))+((10 if l6 else (1 if ((l14^0x80000000)>(l8^0x80000000)) else 0))+l11))&0xFFFFFFFF)
                    l13 = (l13+8)
                    if l13 > 0xFFFFFFFF: l13 -= 0x100000000
                    l7 = (l7+2)
                    if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                    if (l15!=l7):
                        continue
                    break
                if (l19==0):
                    _br = 34
                else:
                    pass
            if _br == 35:
                _br = 0
            if not _br:
                l13 = ld32((((p1+((l16*p0)<<2))+(l7<<2))&0xFFFFFFFF))
                l14 = (l13+l10)
                if l14 > 0xFFFFFFFF: l14 -= 0x100000000
                t5 = l14
                l14 = (1 if ((l14^0x80000000)>(l5^0x80000000)) else 0)
                l10 = (0 if l14 else t5)
                l11 = ((10 if l14 else (1 if ((l13^0x80000000)>(l12^0x80000000)) else 0))+l11)
                if l11 > 0xFFFFFFFF: l11 -= 0x100000000
                l12 = l13
            _br = 0
            l20 = (l20+l18)
            if l20 > 0xFFFFFFFF: l20 -= 0x100000000
            l16 = (l16+1)
            if l16 > 0xFFFFFFFF: l16 -= 0x100000000
            if (l16!=p0):
                continue
            break
        l8 = ((p0<<2)&0xFFFFFFFF)
        l10 = ((p0<<1)&0xFFFFFFFF)
        l6 = (p0&-2)
        l19 = (p0&1)
        l21 = 0
        l22 = f16(((((l11&65535)^32768)-32768)&0xFFFFFFFF), l22)
        l18 = p2
        while True:
            l13 = ((l21*p0)&0xFFFFFFFF)
            l20 = ((p2+(l13<<1))&0xFFFFFFFF)
            l16 = ((p1+(l13<<2))&0xFFFFFFFF)
            l15 = p3
            l17 = 0
            while True:
                l7 = 0
                if not l9:
                    l14 = 0
                    _br = 41
                if not _br:
                    l13 = l15
                    l12 = l18
                    l14 = 0
                    while True:
                        l11 = (ld16((_w if (_w:=(l13+l10)) <= 0xFFFFFFFF else _w-0x100000000))*ld16((_w if (_w:=(l12+2)) <= 0xFFFFFFFF else _w-0x100000000)))
                        t7 = (((l11>>2)&15)*((l11>>5)&127))
                        l11 = (ld16(l13)*ld16(l12))
                        l14 = ((t7+((((l11>>2)&15)*((l11>>5)&127))+l14))&0xFFFFFFFF)
                        l13 = (l13+l8)
                        if l13 > 0xFFFFFFFF: l13 -= 0x100000000
                        l12 = (l12+4)
                        if l12 > 0xFFFFFFFF: l12 -= 0x100000000
                        l7 = (l7+2)
                        if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                        if (l6!=l7):
                            continue
                        break
                    if (l19==0):
                        _br = 40
                    else:
                        pass
                if _br == 41:
                    _br = 0
                if not _br:
                    l13 = (ld16((((p3+(l17<<1))+((l7*p0)<<1))&0xFFFFFFFF))*ld16(((l20+(l7<<1))&0xFFFFFFFF)))
                    l14 = ((((l13>>2)&15)*((l13>>5)&127))+l14)
                    if l14 > 0xFFFFFFFF: l14 -= 0x100000000
                _br = 0
                st32(((l16+(l17<<2))&0xFFFFFFFF), l14)
                l15 = (l15+2)
                if l15 > 0xFFFFFFFF: l15 -= 0x100000000
                l17 = (l17+1)
                if l17 > 0xFFFFFFFF: l17 -= 0x100000000
                if (l17!=p0):
                    continue
                break
            l18 = (l18+l10)
            if l18 > 0xFFFFFFFF: l18 -= 0x100000000
            l21 = (l21+1)
            if l21 > 0xFFFFFFFF: l21 -= 0x100000000
            if (l21!=p0):
                continue
            break
        l20 = ((p0<<2)&0xFFFFFFFF)
        p3 = (p0&-2)
        l18 = (p0&1)
        l16 = p1
        l15 = 0
        l11 = 0
        l12 = 0
        l10 = 0
        while True:
            if not l9:
                l7 = 0
                _br = 46
            if not _br:
                l7 = 0
                l13 = l16
                while True:
                    l8 = l12
                    l12 = ld32((_w if (_w:=(l13+4)) <= 0xFFFFFFFF else _w-0x100000000))
                    l14 = ld32(l13)
                    l10 = (l14+l10)
                    if l10 > 0xFFFFFFFF: l10 -= 0x100000000
                    l6 = (1 if ((l10^0x80000000)>(l5^0x80000000)) else 0)
                    l10 = (l12+(0 if l6 else l10))
                    if l10 > 0xFFFFFFFF: l10 -= 0x100000000
                    l17 = (1 if ((l10^0x80000000)>(l5^0x80000000)) else 0)
                    l10 = (0 if l17 else l10)
                    l11 = (((10 if l17 else (1 if ((l12^0x80000000)>(l14^0x80000000)) else 0))+((10 if l6 else (1 if ((l14^0x80000000)>(l8^0x80000000)) else 0))+l11))&0xFFFFFFFF)
                    l13 = (l13+8)
                    if l13 > 0xFFFFFFFF: l13 -= 0x100000000
                    l7 = (l7+2)
                    if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                    if (p3!=l7):
                        continue
                    break
                if (l18==0):
                    _br = 45
                else:
                    pass
            if _br == 46:
                _br = 0
            if not _br:
                l13 = ld32((((p1+((l15*p0)<<2))+(l7<<2))&0xFFFFFFFF))
                l14 = (l13+l10)
                if l14 > 0xFFFFFFFF: l14 -= 0x100000000
                t8 = l14
                l14 = (1 if ((l14^0x80000000)>(l5^0x80000000)) else 0)
                l10 = (0 if l14 else t8)
                l11 = ((10 if l14 else (1 if ((l13^0x80000000)>(l12^0x80000000)) else 0))+l11)
                if l11 > 0xFFFFFFFF: l11 -= 0x100000000
                l12 = l13
            _br = 0
            l16 = (l16+l20)
            if l16 > 0xFFFFFFFF: l16 -= 0x100000000
            l15 = (l15+1)
            if l15 > 0xFFFFFFFF: l15 -= 0x100000000
            if (l15!=p0):
                continue
            break
        l6 = ((p0<<1)&0xFFFFFFFF)
        l7 = (p0&-4)
        l8 = (p0&3)
        l10 = 0
        l11 = f16(((((l11&65535)^32768)-32768)&0xFFFFFFFF), l22)
        while True:
            l12 = 0
            if not (l9<3):
                l12 = 0
                l13 = p2
                while True:
                    st16(l13, (ld16(l13)-p4))
                    l14 = (l13+2)
                    if l14 > 0xFFFFFFFF: l14 -= 0x100000000
                    st16(l14, (ld16(l14)-p4))
                    l14 = (l13+4)
                    if l14 > 0xFFFFFFFF: l14 -= 0x100000000
                    st16(l14, (ld16(l14)-p4))
                    l14 = (l13+6)
                    if l14 > 0xFFFFFFFF: l14 -= 0x100000000
                    st16(l14, (ld16(l14)-p4))
                    l13 = (l13+8)
                    if l13 > 0xFFFFFFFF: l13 -= 0x100000000
                    l12 = (l12+4)
                    if l12 > 0xFFFFFFFF: l12 -= 0x100000000
                    if (l7!=l12):
                        continue
                    break
                if (l8==0):
                    _br = 50
                else:
                    pass
            if not _br:
                l13 = ((p2+(l12<<1))&0xFFFFFFFF)
                l12 = l8
                while True:
                    st16(l13, (ld16(l13)-p4))
                    l13 = (l13+2)
                    if l13 > 0xFFFFFFFF: l13 -= 0x100000000
                    l12 = (l12-1)
                    if l12 < 0: l12 += 0x100000000
                    if l12:
                        continue
                    break
            _br = 0
            p2 = (p2+l6)
            if p2 > 0xFFFFFFFF: p2 -= 0x100000000
            l10 = (l10+1)
            if l10 > 0xFFFFFFFF: l10 -= 0x100000000
            if (l10!=p0):
                continue
            break
        return ((((l11&65535)^32768)-32768)&0xFFFFFFFF)
    t10 = f16(0, 0)
    t11 = f16(0, t10)
    t12 = f16(0, t11)
    t13 = f16(0, t12)
    return ((((t13&65535)^32768)-32768)&0xFFFFFFFF)


def f9(p0: i64, p1: i64, p2: i64, p3: i64) -> i64:
    l4: i64 = 0
    l5: i64 = 0
    l6: i64 = 0
    l7: i64 = 0
    l8: i64 = 0
    l9: i64 = 0
    l10: i64 = 0
    l11: i64 = 0
    l12: i64 = 0
    l13: i64 = 0
    _br: i64 = 0
    _w: i64 = 0
    if not (p0==0):
        l4 = 4294967295
        l5 = 8
        while True:
            l6 = l4
            l4 = (l6+1)
            if l4 > 0xFFFFFFFF: l4 -= 0x100000000
            l6 = ((l5*(l6+2))&0xFFFFFFFF)
            l5 = (l5+8)
            if l5 > 0xFFFFFFFF: l5 -= 0x100000000
            if (l6<p0):
                continue
            break
        l7 = ((((p1-1)&-4)+4)&0xFFFFFFFF)
        l8 = ((l4*l4)&0xFFFFFFFF)
        l9 = ((l7+(l8<<1))&0xFFFFFFFF)
        if l4:
            _br = 2
        else:
            l8 = 0
            l4 = 0
            _br = 1
    if not _br:
        l4 = 4294967295
        l5 = (((p1-1)&-4)&0xFFFFFFFF)
        l9 = (l5+6)
        if l9 > 0xFFFFFFFF: l9 -= 0x100000000
        l7 = (l5+4)
        if l7 > 0xFFFFFFFF: l7 -= 0x100000000
        l8 = 1
    if _br == 2:
        _br = 0
    if not _br:
        p2 = (p2 if (p2>1) else 1)
        l10 = ((l4<<1)&0xFFFFFFFF)
        l11 = 0
        l12 = 0
        l13 = 1
        while True:
            p0 = ((l13<<1)&0xFFFFFFFF)
            l5 = l11
            l6 = 0
            while True:
                p1 = (l13+l6)
                if p1 > 0xFFFFFFFF: p1 -= 0x100000000
                p2 = _rems32(((p2*p1)&0xFFFFFFFF),65536)
                st16((_w if (_w:=(l9+l5)) <= 0xFFFFFFFF else _w-0x100000000), (p1+p2))
                st16((_w if (_w:=(l7+l5)) <= 0xFFFFFFFF else _w-0x100000000), ((p0+p2)&255))
                p0 = (p0+2)
                if p0 > 0xFFFFFFFF: p0 -= 0x100000000
                l5 = (l5+2)
                if l5 > 0xFFFFFFFF: l5 -= 0x100000000
                l6 = (l6+1)
                if l6 > 0xFFFFFFFF: l6 -= 0x100000000
                if (l4!=l6):
                    continue
                break
            l13 = (l13+l6)
            if l13 > 0xFFFFFFFF: l13 -= 0x100000000
            l11 = (l11+l10)
            if l11 > 0xFFFFFFFF: l11 -= 0x100000000
            l12 = (l12+1)
            if l12 > 0xFFFFFFFF: l12 -= 0x100000000
            if (l12!=l4):
                continue
            break
    _br = 0
    st32(p3+8, l9)
    st32(p3+4, l7)
    st32(p3, l4)
    st32(p3+12, ((((l9+(l8<<1))-1)&-4)+4))
    return l4


def f10(p0: i64, p1: i64, p2: i64, p3: i64, p4: i64, p5: i64) -> i64:
    l6: i64 = 0
    l7: i64 = 0
    l8: i64 = 0
    l9: i64 = 0
    t1: i64 = 0
    t2: i64 = 0
    t3: i64 = 0
    t4: i64 = 0
    t5: i64 = 0
    t6: i64 = 0
    t7: i64 = 0
    t8: i64 = 0
    t9: i64 = 0
    t10: i64 = 0
    t11: i64 = 0
    t12: i64 = 0
    t13: i64 = 0
    t14: i64 = 0
    t15: i64 = 0
    t16: i64 = 0
    t17: i64 = 0
    t18: i64 = 0
    _w: i64 = 0
    l6 = (GS.g0-80)
    if l6 < 0: l6 += 0x100000000
    GS.g0 = l6
    st64(l6+48, 0)
    st64(l6+56, 0)
    st64(l6+64, 0)
    st64(l6+72, 0)
    st64(l6+16, 0)
    st64(l6+24, 0)
    st64(l6+32, 0)
    st64(l6+40, 0)
    st32(l6+12, p1)
    l7 = ld8(p1)
    if not (l7==0):
        while True:
            t1 = f11((_w if (_w:=(l6+12)) <= 0xFFFFFFFF else _w-0x100000000), (_w if (_w:=(l6+16)) <= 0xFFFFFFFF else _w-0x100000000))
            l8 = (((l6+48)+(t1<<2))&0xFFFFFFFF)
            st32(l8, (ld32(l8)+1))
            if ld8(ld32(l6+12)):
                continue
            break
    l9 = (p1+p0)
    if l9 > 0xFFFFFFFF: l9 -= 0x100000000
    if not (p0==0):
        l8 = p1
        while True:
            l7 = ld8(l8)
            if not (l7==44):
                st8(l8, (l7^p2))
            l8 = (l8+p4)
            if l8 > 0xFFFFFFFF: l8 -= 0x100000000
            if (l8<l9):
                continue
            break
        l7 = ld8(p1)
    st32(l6+12, p1)
    if not ((l7&255)==0):
        while True:
            t2 = f11((_w if (_w:=(l6+12)) <= 0xFFFFFFFF else _w-0x100000000), (_w if (_w:=(l6+16)) <= 0xFFFFFFFF else _w-0x100000000))
            l8 = (((l6+48)+(t2<<2))&0xFFFFFFFF)
            st32(l8, (ld32(l8)+1))
            if ld8(ld32(l6+12)):
                continue
            break
    if not (p0==0):
        while True:
            l8 = ld8(p1)
            if not (l8==44):
                st8(p1, (l8^p3))
            p1 = (p1+p4)
            if p1 > 0xFFFFFFFF: p1 -= 0x100000000
            if (p1<l9):
                continue
            break
    p1 = f15(ld32(l6+48), p5)
    p1 = f15(ld32(l6+16), p1)
    p1 = f15(ld32(l6+52), p1)
    p1 = f15(ld32(l6+20), p1)
    p1 = f15(ld32(l6+56), p1)
    p1 = f15(ld32(l6+24), p1)
    p1 = f15(ld32(l6+60), p1)
    p1 = f15(ld32(l6+28), p1)
    p1 = f15(ld32(l6+64), p1)
    p1 = f15(ld32(l6+32), p1)
    p1 = f15(ld32(l6+68), p1)
    p1 = f15(ld32(l6+36), p1)
    p1 = f15(ld32(l6+72), p1)
    p1 = f15(ld32(l6+40), p1)
    p1 = f15(ld32(l6+76), p1)
    p1 = f15(ld32(l6+44), p1)
    GS.g0 = (_w if (_w:=(l6+80)) <= 0xFFFFFFFF else _w-0x100000000)
    return p1


def f11(p0: i64, p1: i64) -> i64:
    l2: i64 = 0
    l3: i64 = 0
    l4: i64 = 0
    l5: i64 = 0
    l6: i64 = 0
    t1: i64 = 0
    t2: i64 = 0
    _br: i64 = 0
    _w: i64 = 0
    l2 = ld32(p0)
    l3 = ld8(l2)
    if not l3:
        st32(p0, l2)
        return 0
    l2 = (l2+1)
    if l2 > 0xFFFFFFFF: l2 -= 0x100000000
    l4 = 0
    while True:
        l5 = (l3&255)
        if (l5==44):
            _br = 4
            break
        if l4 == 0:
            pass
        elif l4 == 2:
            _br = 13
        elif l4 == 3:
            _br = 10
        elif l4 == 4:
            _br = 12
        elif l4 == 5:
            _br = 11
        elif l4 == 6:
            _br = 9
        elif l4 == 7:
            _br = 8
        else:
            _br = 6
        if not _br:
            if not (((l3-58)&255)<=245):
                l4 = 4
                _br = 15
            if not _br:
                l4 = 2
                t1 = (_w if (_w:=(l5-43)) >= 0 else _w+0x100000000)
                if t1 in (0, 2):
                    _br = 15
                elif t1 == 3:
                    pass
                else:
                    _br = 17
                if not _br:
                    l4 = 5
                    _br = 15
                if _br == 17:
                    _br = 0
                if not _br:
                    l4 = 1
                    st32(p1+4, (ld32(p1+4)+1))
            _br = 0
            st32(p1, (ld32(p1)+1))
            _br = 6
        if _br == 13:
            _br = 0
        if not _br:
            if not (((l3-58)&255)<246):
                st32(p1+8, (ld32(p1+8)+1))
                l4 = 4
                _br = 6
            if not _br:
                l6 = 1
                st32(p1+8, (ld32(p1+8)+1))
                if (l5!=46):
                    _br = 3
                    break
                l4 = 5
                _br = 6
        if _br == 12:
            _br = 0
        if not _br:
            if not (l5!=46):
                st32(p1+16, (ld32(p1+16)+1))
                l4 = 5
                _br = 6
            if not _br:
                if not (((l3-58)&255)<=245):
                    l4 = 4
                    _br = 6
                if not _br:
                    st32(p1+16, (ld32(p1+16)+1))
                    st32(p0, l2)
                    return 1
        if _br == 11:
            _br = 0
        if not _br:
            if not ((l3&223)!=69):
                st32(p1+20, (ld32(p1+20)+1))
                l4 = 3
                _br = 6
            if not _br:
                if not (((l3-58)&255)<=245):
                    l4 = 5
                    _br = 6
                if not _br:
                    st32(p1+20, (ld32(p1+20)+1))
                    st32(p0, l2)
                    return 1
        if _br == 10:
            _br = 0
        if not _br:
            l6 = 1
            st32(p1+12, (ld32(p1+12)+1))
            l4 = 6
            t2 = (_w if (_w:=(l5-43)) >= 0 else _w+0x100000000)
            if t2 in (0, 2):
                _br = 6
            else:
                _br = 3
                break
        if _br == 9:
            _br = 0
        if not _br:
            l6 = 1
            st32(p1+24, (ld32(p1+24)+1))
            if (((l3-58)&255)>=246):
                _br = 7
            else:
                _br = 3
                break
        if _br == 8:
            _br = 0
        if not _br:
            if (((l3-58)&255)<=245):
                _br = 2
                break
        if _br == 7:
            _br = 0
        if not _br:
            l4 = 7
        _br = 0
        l5 = (l2+1)
        if l5 > 0xFFFFFFFF: l5 -= 0x100000000
        l3 = ld8(l2)
        if not (l3==0):
            l2 = l5
            if (l4!=1):
                continue
        break
    if not _br:
        l2 = (l5-1)
        if l2 < 0: l2 += 0x100000000
    if _br == 4:
        _br = 0
    if not _br:
        l6 = l4
    if _br == 3:
        _br = 0
    if not _br:
        st32(p0, l2)
        return l6
    _br = 0
    st32(p1+4, (ld32(p1+4)+1))
    st32(p0, l2)
    return 1


def f12(p0: i64, p1: i64, p2: i64) -> None:
    l3: i64 = 0
    l4: i64 = 0
    l5: i64 = 0
    l6: i64 = 0
    l7: i64 = 0
    l8: i64 = 0
    l9: i64 = 0
    l10: i64 = 0
    l11: i64 = 0
    l12: i64 = 0
    _br: i64 = 0
    _w: i64 = 0
    l3 = 0
    l4 = (p0-1)
    if l4 < 0: l4 += 0x100000000
    if not (l4<2):
        l3 = 0
        l5 = 0
        l6 = 0
        while True:
            if not (l6==0):
                l7 = (l6&3)
                l8 = (p2+l3)
                if l8 > 0xFFFFFFFF: l8 -= 0x100000000
                l9 = 0
                if not (l6<4):
                    l10 = (l6&-4)
                    l9 = 0
                    while True:
                        l11 = (l8+l9)
                        if l11 > 0xFFFFFFFF: l11 -= 0x100000000
                        l12 = (l5+l9)
                        if l12 > 0xFFFFFFFF: l12 -= 0x100000000
                        st8(l11, ld8(l12))
                        st8((_w if (_w:=(l11+1)) <= 0xFFFFFFFF else _w-0x100000000), ld8((_w if (_w:=(l12+1)) <= 0xFFFFFFFF else _w-0x100000000)))
                        st8((_w if (_w:=(l11+2)) <= 0xFFFFFFFF else _w-0x100000000), ld8((_w if (_w:=(l12+2)) <= 0xFFFFFFFF else _w-0x100000000)))
                        st8((_w if (_w:=(l11+3)) <= 0xFFFFFFFF else _w-0x100000000), ld8((_w if (_w:=(l12+3)) <= 0xFFFFFFFF else _w-0x100000000)))
                        l9 = (l9+4)
                        if l9 > 0xFFFFFFFF: l9 -= 0x100000000
                        if (l10!=l9):
                            continue
                        break
                    if (l7==0):
                        _br = 4
                    else:
                        pass
                if not _br:
                    l11 = (l5+l9)
                    if l11 > 0xFFFFFFFF: l11 -= 0x100000000
                    l9 = ((p2+(l3+l9))&0xFFFFFFFF)
                    while True:
                        st8(l9, ld8(l11))
                        l11 = (l11+1)
                        if l11 > 0xFFFFFFFF: l11 -= 0x100000000
                        l9 = (l9+1)
                        if l9 > 0xFFFFFFFF: l9 -= 0x100000000
                        l7 = (l7-1)
                        if l7 < 0: l7 += 0x100000000
                        if l7:
                            continue
                        break
                _br = 0
                st8((_w if (_w:=(l8+l6)) <= 0xFFFFFFFF else _w-0x100000000), 44)
                l3 = (((l3+l6)+1)&0xFFFFFFFF)
            p1 = (p1+1)
            if p1 > 0xFFFFFFFF: p1 -= 0x100000000
            l11 = ((p1&7)<<2)
            l5 = ld32((_w if (_w:=(ld32(l11+1049904)+((p1>>1)&12))) <= 0xFFFFFFFF else _w-0x100000000))
            l6 = ld32((_w if (_w:=(l11+1049936)) <= 0xFFFFFFFF else _w-0x100000000))
            if ((((l3+l6)+1)&0xFFFFFFFF)<l4):
                continue
            break
    if not (l3>=p0):
        l11 = l3
        l9 = ((p0-l3)&7)
        if not (l9==0):
            l11 = l3
            while True:
                st8((_w if (_w:=(p2+l11)) <= 0xFFFFFFFF else _w-0x100000000), 0)
                l11 = (l11+1)
                if l11 > 0xFFFFFFFF: l11 -= 0x100000000
                l9 = (l9-1)
                if l9 < 0: l9 += 0x100000000
                if l9:
                    continue
                break
        if not ((_w if (_w:=(l3-p0)) >= 0 else _w+0x100000000)>4294967288):
            while True:
                st64((_w if (_w:=(p2+l11)) <= 0xFFFFFFFF else _w-0x100000000), 0)
                p2 = (p2+8)
                if p2 > 0xFFFFFFFF: p2 -= 0x100000000
                p0 = (p0-8)
                if p0 < 0: p0 += 0x100000000
                if (l11!=p0):
                    continue
                break


def f13(p0: i64) -> i64:
    l1: i64 = 0
    l1 = 0
    p0 = (p0-1)
    if p0 < 0: p0 += 0x100000000
    if not (p0>4):
        l1 = ld32(ld32(((p0<<2)&0xFFFFFFFF)+1050480))
    return l1


def f14(p0: i64, p1: i64) -> i64:
    p0 = (p1^p0)
    p1 = ld16(((p0&255)<<1)+1049968)
    return ((p1>>8)^ld16((((p1&255)^(p0>>8))<<1)+1049968))


def f15(p0: i64, p1: i64) -> i64:
    l2: i64 = 0
    p1 = (p1^p0)
    l2 = ld16(((p1&255)<<1)+1049968)
    p0 = (ld16((((l2&255)^((p1&65280)>>8))<<1)+1049968)^(p0>>16))
    p1 = ld16((((p0&255)^(l2>>8))<<1)+1049968)
    return ((p1>>8)^ld16(((((p0&65280)>>8)^(p1&255))<<1)+1049968))


def f16(p0: i64, p1: i64) -> i64:
    p0 = (p1^p0)
    p1 = ld16(((p0&255)<<1)+1049968)
    return ((p1>>8)^ld16((((p1&255)^((p0&65280)>>8))<<1)+1049968))


def f17() -> i64:
    return 0


def f18(p0: i64, p1: i64) -> i64:
    l2: i64 = 0
    l3: i64 = 0
    l4: i64 = 0
    l5: i64 = 0
    l6: i64 = 0
    l7: i64 = 0
    l8: i64 = 0
    l9: i64 = 0
    l10: i64 = 0
    l11: i64 = 0
    l12: i64 = 0
    l13: i64 = 0
    l14: i64 = 0
    l15: i64 = 0
    l16: i64 = 0
    l17: i64 = 0
    l18: i64 = 0
    l19: i64 = 0
    l20: i64 = 0
    l21: i64 = 0
    l22: i64 = 0
    l23: i64 = 0
    l24: float = 0.0
    t1: i64 = 0
    t2: i64 = 0
    t3: i64 = 0
    t4: i64 = 0
    t5: i64 = 0
    t6: i64 = 0
    t7: i64 = 0
    t8: i64 = 0
    _br: i64 = 0
    _w: i64 = 0
    l2 = (GS.g0-1216)
    if l2 < 0: l2 += 0x100000000
    GS.g0 = l2
    st32(l2+12, p1)
    l3 = ((0-(l2+1040))&0xFFFFFFFF)
    l4 = (((l2+1040)|1)&0xFFFFFFFF)
    l5 = (((l2+1120)|1)&0xFFFFFFFF)
    l6 = (((l2+1120)|2)&0xFFFFFFFF)
    l7 = (l2+16)
    if l7 > 0xFFFFFFFF: l7 -= 0x100000000
    while True:
        l8 = ld8(p0)
        if not (l8==37):
            if (l8==0):
                _br = 1
                break
            st8(l7, l8)
            l7 = (l7+1)
            if l7 > 0xFFFFFFFF: l7 -= 0x100000000
            p0 = (p0+1)
            if p0 > 0xFFFFFFFF: p0 -= 0x100000000
            continue
        l8 = (p0+1)
        if l8 > 0xFFFFFFFF: l8 -= 0x100000000
        l9 = 0
        while True:
            l10 = 4
            l11 = ld8(l8)
            t1 = (_w if (_w:=(l11-32)) >= 0 else _w+0x100000000)
            if t1 == 0:
                _br = 9
            elif t1 == 3:
                _br = 8
            elif t1 == 11:
                _br = 5
            elif t1 == 13:
                pass
            elif t1 == 16:
                _br = 7
            else:
                _br = 6
            if not _br:
                l10 = 16
                _br = 5
            if _br == 9:
                _br = 0
            if not _br:
                l10 = 8
                _br = 5
            if _br == 8:
                _br = 0
            if not _br:
                l10 = 32
                _br = 5
            if _br == 7:
                _br = 0
            if not _br:
                l10 = 1
                _br = 5
            if _br == 6:
                _br = 0
            if not _br:
                if not (((l11-48)&255)>9):
                    l10 = 0
                    while True:
                        l10 = ((((l11&255)+(l10*10))-48)&0xFFFFFFFF)
                        l8 = (l8+1)
                        if l8 > 0xFFFFFFFF: l8 -= 0x100000000
                        l11 = ld8(l8)
                        if (((l11-48)&255)<10):
                            continue
                        break
                    l12 = p1
                    _br = 11
                if not _br:
                    l10 = 4294967295
                    if not (l11==42):
                        l12 = p1
                        _br = 14
                    if not _br:
                        l12 = (p1+4)
                        if l12 > 0xFFFFFFFF: l12 -= 0x100000000
                        l8 = (l8+1)
                        if l8 > 0xFFFFFFFF: l8 -= 0x100000000
                        l10 = ld32(p1)
                        if (l10 <= 2147483647 or l10 > 4294967295):
                            pass
                        else:
                            l9 = (l9|16)
                            l10 = (0-l10)
                            if l10 < 0: l10 += 0x100000000
                    _br = 0
                    l11 = ld8(l8)
                _br = 0
                l13 = 4294967295
                if not ((l11&255)==46):
                    p0 = l8
                    l14 = l12
                    _br = 16
                if not _br:
                    p0 = (l8+1)
                    if p0 > 0xFFFFFFFF: p0 -= 0x100000000
                    l11 = ld8(l8+1)
                    if not (((l11-48)&255)>9):
                        l13 = 0
                        while True:
                            l13 = ((((l11&255)+(l13*10))-48)&0xFFFFFFFF)
                            p0 = (p0+1)
                            if p0 > 0xFFFFFFFF: p0 -= 0x100000000
                            l11 = ld8(p0)
                            if (((l11-48)&255)<10):
                                continue
                            break
                        l14 = l12
                        _br = 18
                    if not _br:
                        l13 = 4294967295
                        if not ((l11&255)==42):
                            l14 = l12
                            _br = 18
                        if not _br:
                            l14 = (l12+4)
                            if l14 > 0xFFFFFFFF: l14 -= 0x100000000
                            p0 = (l8+2)
                            if p0 > 0xFFFFFFFF: p0 -= 0x100000000
                            l11 = ld8(l8+2)
                            l13 = ld32(l12)
                    _br = 0
                    l13 = (l13 if (0 < l13 <= 2147483647) else 0)
                _br = 0
                if not (((l11|32)&255)==108):
                    l12 = 0
                    _br = 22
                if not _br:
                    l12 = (1 if ((l11&255)==108) else 0)
                    l11 = ld8(p0+1)
                    p0 = (p0+1)
                    if p0 > 0xFFFFFFFF: p0 -= 0x100000000
                _br = 0
                l8 = 10
                t2 = (_w if (_w:=((l11&255)-37)) >= 0 else _w+0x100000000)
                if t2 == 0:
                    _br = 33
                elif t2 == 28:
                    _br = 40
                elif t2 == 51:
                    _br = 38
                elif t2 == 60:
                    _br = 39
                elif t2 == 62:
                    pass
                elif t2 in (63, 68):
                    _br = 36
                elif t2 == 65:
                    _br = 35
                elif t2 == 74:
                    _br = 31
                elif t2 == 75:
                    _br = 41
                elif t2 == 78:
                    _br = 42
                elif t2 == 80:
                    _br = 30
                elif t2 == 83:
                    _br = 37
                else:
                    _br = 34
                if not _br:
                    if not (l9&16):
                        if not (l10 < 2 or l10 > 2147483647):
                            l9 = (l10-2)
                            if l9 < 0: l9 += 0x100000000
                            if not ((l10&7)==1):
                                l8 = ((l10-1)&7)
                                l10 = (l10-l8)
                                if l10 < 0: l10 += 0x100000000
                                while True:
                                    st8(l7, 32)
                                    l7 = (l7+1)
                                    if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                                    l8 = (l8-1)
                                    if l8 < 0: l8 += 0x100000000
                                    if l8:
                                        continue
                                    break
                            if not (l9<7):
                                l8 = (l10+8)
                                if l8 > 0xFFFFFFFF: l8 -= 0x100000000
                                while True:
                                    st64(l7, 2314885530818453536)
                                    l7 = (l7+8)
                                    if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                                    l8 = (l8-8)
                                    if l8 < 0: l8 += 0x100000000
                                    if (9 < l8 <= 2147483647):
                                        continue
                                    break
                        st8(l7, ld32(l14))
                        l7 = (l7+1)
                        if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                        p1 = (l14+4)
                        if p1 > 0xFFFFFFFF: p1 -= 0x100000000
                        p0 = (p0+1)
                        if p0 > 0xFFFFFFFF: p0 -= 0x100000000
                        _br = 2
                        break
                    st8(l7, ld32(l14))
                    l7 = (l7+1)
                    if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                    p1 = (l14+4)
                    if p1 > 0xFFFFFFFF: p1 -= 0x100000000
                    if (l10 < 2 or l10 > 2147483647):
                        _br = 24
                    else:
                        l9 = (l10-2)
                        if l9 < 0: l9 += 0x100000000
                        if not ((l10&7)==1):
                            l8 = ((l10-1)&7)
                            l10 = (l10-l8)
                            if l10 < 0: l10 += 0x100000000
                            while True:
                                st8(l7, 32)
                                l7 = (l7+1)
                                if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                                l8 = (l8-1)
                                if l8 < 0: l8 += 0x100000000
                                if l8:
                                    continue
                                break
                        if (l9<7):
                            _br = 24
                        else:
                            l8 = (l10+8)
                            if l8 > 0xFFFFFFFF: l8 -= 0x100000000
                            while True:
                                st64(l7, 2314885530818453536)
                                l7 = (l7+8)
                                if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                                l8 = (l8-8)
                                if l8 < 0: l8 += 0x100000000
                                if (9 < l8 <= 2147483647):
                                    continue
                                _br = 24
                                break
                                break
                            if not _br:
                                pass
                if _br == 42:
                    _br = 0
                if not _br:
                    l8 = ld32(l14)
                    l15 = (l8 if l8 else 1048725)
                    l11 = l15
                    if (l13==0):
                        pass
                    else:
                        l11 = l15
                        if ((ld8(l15)&255)==0):
                            pass
                        else:
                            l13 = (l13-1)
                            if l13 < 0: l13 += 0x100000000
                            l11 = l15
                            while True:
                                l8 = l13
                                l11 = (l11+1)
                                if l11 > 0xFFFFFFFF: l11 -= 0x100000000
                                if (ld8(l11)==0):
                                    _br = 52
                                    break
                                l13 = (l8-1)
                                if l13 < 0: l13 += 0x100000000
                                if l8:
                                    continue
                                break
                            if not _br:
                                pass
                    _br = 0
                    l12 = (l11-l15)
                    if l12 < 0: l12 += 0x100000000
                    if (l9&16):
                        pass
                    else:
                        l9 = (l10-1)
                        if l9 < 0: l9 += 0x100000000
                        if not ((l12^0x80000000)<(l10^0x80000000)):
                            l10 = l9
                            _br = 54
                        if not _br:
                            l8 = (((l10+l15)-l11)&0xFFFFFFFF)
                            l13 = (l8-1)
                            if l13 < 0: l13 += 0x100000000
                            l8 = (l8&7)
                            if not (l8==0):
                                l9 = (((l8^-1)+l10)&0xFFFFFFFF)
                                while True:
                                    st8(l7, 32)
                                    l7 = (l7+1)
                                    if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                                    l8 = (l8-1)
                                    if l8 < 0: l8 += 0x100000000
                                    if l8:
                                        continue
                                    break
                            if not (l13<7):
                                l8 = (l9+1)
                                if l8 > 0xFFFFFFFF: l8 -= 0x100000000
                                while True:
                                    st64(l7, 2314885530818453536)
                                    l7 = (l7+8)
                                    if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                                    l8 = (l8-8)
                                    if l8 < 0: l8 += 0x100000000
                                    if ((l12^0x80000000)<(l8^0x80000000)):
                                        continue
                                    break
                            l10 = ((l11+(l15^-1))&0xFFFFFFFF)
                    _br = 0
                    if (l12 < 1 or l12 > 2147483647):
                        _br = 25
                    else:
                        l9 = (l12&7)
                        if not ((_w if (_w:=(l15-l11)) >= 0 else _w+0x100000000)<=4294967288):
                            l8 = l15
                            _br = 26
                        if not _br:
                            l13 = (l12&2147483640)
                            l8 = l15
                            while True:
                                st8(l7, ld8(l8))
                                st8((_w if (_w:=(l7+1)) <= 0xFFFFFFFF else _w-0x100000000), ld8((_w if (_w:=(l8+1)) <= 0xFFFFFFFF else _w-0x100000000)))
                                st8((_w if (_w:=(l7+2)) <= 0xFFFFFFFF else _w-0x100000000), ld8((_w if (_w:=(l8+2)) <= 0xFFFFFFFF else _w-0x100000000)))
                                st8((_w if (_w:=(l7+3)) <= 0xFFFFFFFF else _w-0x100000000), ld8((_w if (_w:=(l8+3)) <= 0xFFFFFFFF else _w-0x100000000)))
                                st8((_w if (_w:=(l7+4)) <= 0xFFFFFFFF else _w-0x100000000), ld8((_w if (_w:=(l8+4)) <= 0xFFFFFFFF else _w-0x100000000)))
                                st8((_w if (_w:=(l7+5)) <= 0xFFFFFFFF else _w-0x100000000), ld8((_w if (_w:=(l8+5)) <= 0xFFFFFFFF else _w-0x100000000)))
                                st8((_w if (_w:=(l7+6)) <= 0xFFFFFFFF else _w-0x100000000), ld8((_w if (_w:=(l8+6)) <= 0xFFFFFFFF else _w-0x100000000)))
                                st8((_w if (_w:=(l7+7)) <= 0xFFFFFFFF else _w-0x100000000), ld8((_w if (_w:=(l8+7)) <= 0xFFFFFFFF else _w-0x100000000)))
                                l8 = (l8+8)
                                if l8 > 0xFFFFFFFF: l8 -= 0x100000000
                                l7 = (l7+8)
                                if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                                l13 = (l13-8)
                                if l13 < 0: l13 += 0x100000000
                                if l13:
                                    continue
                                _br = 27
                                break
                                break
                            if not _br:
                                pass
                if _br == 41:
                    _br = 0
                if not _br:
                    p1 = (l14+4)
                    if p1 > 0xFFFFFFFF: p1 -= 0x100000000
                    l8 = (1 if (l10==4294967295) else 0)
                    l7 = f19(l7, ld32(l14), 16, (8 if l8 else l10), l13, (l9|l8))
                    p0 = (p0+1)
                    if p0 > 0xFFFFFFFF: p0 -= 0x100000000
                    _br = 2
                    break
                if _br == 40:
                    _br = 0
                if not _br:
                    l9 = (l9|64)
                if _br == 39:
                    _br = 0
                if not _br:
                    p1 = (l14+4)
                    if p1 > 0xFFFFFFFF: p1 -= 0x100000000
                    l8 = ld32(l14)
                    if not (l12==0):
                        l11 = (1048576 if (l9<64) else 1048672)
                        l13 = ld8(l8+5)
                        l14 = ld8((_w if (_w:=(l11+(l13&15))) <= 0xFFFFFFFF else _w-0x100000000))
                        l13 = ld8((_w if (_w:=(l11+(l13>>4))) <= 0xFFFFFFFF else _w-0x100000000))
                        l12 = ld8(l8+4)
                        l15 = ld8((_w if (_w:=(l11+(l12&15))) <= 0xFFFFFFFF else _w-0x100000000))
                        l12 = ld8((_w if (_w:=(l11+(l12>>4))) <= 0xFFFFFFFF else _w-0x100000000))
                        l16 = ld8(l8+3)
                        l17 = ld8((_w if (_w:=(l11+(l16&15))) <= 0xFFFFFFFF else _w-0x100000000))
                        l16 = ld8((_w if (_w:=(l11+(l16>>4))) <= 0xFFFFFFFF else _w-0x100000000))
                        l18 = ld8(l8+2)
                        l19 = ld8((_w if (_w:=(l11+(l18&15))) <= 0xFFFFFFFF else _w-0x100000000))
                        l18 = ld8((_w if (_w:=(l11+(l18>>4))) <= 0xFFFFFFFF else _w-0x100000000))
                        l20 = ld8(l8+1)
                        l21 = ld8((_w if (_w:=(l11+(l20&15))) <= 0xFFFFFFFF else _w-0x100000000))
                        l20 = ld8((_w if (_w:=(l11+(l20>>4))) <= 0xFFFFFFFF else _w-0x100000000))
                        l8 = ld8(l8)
                        l22 = ld8((_w if (_w:=(l11+(l8&15))) <= 0xFFFFFFFF else _w-0x100000000))
                        l11 = ld8((_w if (_w:=(l11+(l8>>4))) <= 0xFFFFFFFF else _w-0x100000000))
                        if (l9&16):
                            pass
                        else:
                            l9 = (l10-1)
                            if l9 < 0: l9 += 0x100000000
                            if not (17 < l10 <= 2147483647):
                                l10 = l9
                                _br = 63
                            if not _br:
                                l23 = (l10-18)
                                if l23 < 0: l23 += 0x100000000
                                if not ((l10&7)==1):
                                    l8 = ((l10-1)&7)
                                    l9 = (((l8^-1)+l10)&0xFFFFFFFF)
                                    while True:
                                        st8(l7, 32)
                                        l7 = (l7+1)
                                        if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                                        l8 = (l8-1)
                                        if l8 < 0: l8 += 0x100000000
                                        if l8:
                                            continue
                                        break
                                l10 = 16
                                if (l23<7):
                                    pass
                                else:
                                    l8 = (l9+8)
                                    if l8 > 0xFFFFFFFF: l8 -= 0x100000000
                                    while True:
                                        st64(l7, 2314885530818453536)
                                        l7 = (l7+8)
                                        if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                                        l8 = (l8-8)
                                        if l8 < 0: l8 += 0x100000000
                                        if (24 < l8 <= 2147483647):
                                            continue
                                        break
                        _br = 0
                        st8(l7+16, l14)
                        st8(l7+15, l13)
                        st8(l7+14, 58)
                        st8(l7+13, l15)
                        st8(l7+12, l12)
                        st8(l7+11, 58)
                        st8(l7+10, l17)
                        st8(l7+9, l16)
                        st8(l7+8, 58)
                        st8(l7+7, l19)
                        st8(l7+6, l18)
                        st8(l7+5, 58)
                        st8(l7+4, l21)
                        st8(l7+3, l20)
                        st8(l7+2, 58)
                        st8(l7+1, l22)
                        st8(l7, l11)
                        l7 = (l7+17)
                        if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                        if (l10 < 18 or l10 > 2147483647):
                            _br = 24
                        else:
                            l9 = (l10-18)
                            if l9 < 0: l9 += 0x100000000
                            if not ((l10&7)==1):
                                l8 = ((l10-1)&7)
                                l10 = (l10-l8)
                                if l10 < 0: l10 += 0x100000000
                                while True:
                                    st8(l7, 32)
                                    l7 = (l7+1)
                                    if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                                    l8 = (l8-1)
                                    if l8 < 0: l8 += 0x100000000
                                    if l8:
                                        continue
                                    break
                            if (l9<7):
                                _br = 24
                            else:
                                l8 = (l10+8)
                                if l8 > 0xFFFFFFFF: l8 -= 0x100000000
                                while True:
                                    st64(l7, 2314885530818453536)
                                    l7 = (l7+8)
                                    if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                                    l8 = (l8-8)
                                    if l8 < 0: l8 += 0x100000000
                                    if (25 < l8 <= 2147483647):
                                        continue
                                    _br = 24
                                    break
                                    break
                                if not _br:
                                    pass
                    if not _br:
                        l11 = ld8(l8)
                        if not (l11==0):
                            if not (l11>99):
                                if not (l11>=10):
                                    l14 = 1
                                    l13 = (l2+1120)
                                    if l13 > 0xFFFFFFFF: l13 -= 0x100000000
                                    _br = 73
                                if not _br:
                                    l13 = ((l11&255)//10)
                                    st8(l2+1120, ld8(l13+1048576))
                                    l11 = ((l11-(l13*10))&0xFFFFFFFF)
                                    l14 = 2
                                    l13 = l5
                                    _br = 73
                            if not _br:
                                l13 = ((l11&255)//100)
                                st8(l2+1120, ld8(l13+1048576))
                                l11 = ((l11-(l13*100))&0xFFFFFFFF)
                                l13 = ((l11&255)//10)
                                st8(l2+1121, ld8(l13+1048576))
                                l11 = ((l11-(l13*10))&0xFFFFFFFF)
                                l14 = 3
                                l13 = l6
                            _br = 0
                            st8(l13, ld8((l11&255)+1048576))
                            _br = 71
                        if not _br:
                            st8(l2+1120, 48)
                            l14 = 1
                        _br = 0
                        st8((((l2+1120)|l14)&0xFFFFFFFF), 46)
                        l11 = (l14+1)
                        if l11 > 0xFFFFFFFF: l11 -= 0x100000000
                        l12 = 48
                        l13 = ld8(l8+1)
                        if not l13:
                            l13 = 48
                            _br = 76
                        if not _br:
                            if not (l13<100):
                                l11 = (((l2+1120)|l11)&0xFFFFFFFF)
                                l15 = ((l13&255)//100)
                                st8(l11, ld8(l15+1048576))
                                t4 = (l11+1)
                                l11 = ((l13-(l15*100))&0xFFFFFFFF)
                                l13 = ((l11&255)//10)
                                st8((_w if (_w:=t4) <= 0xFFFFFFFF else _w-0x100000000), ld8(l13+1048576))
                                l13 = ((l11-(l13*10))&0xFFFFFFFF)
                                l11 = (l14+3)
                                if l11 > 0xFFFFFFFF: l11 -= 0x100000000
                                _br = 78
                            if not _br:
                                if (l13<10):
                                    pass
                                else:
                                    l15 = ((l13&255)//10)
                                    st8((((l2+1120)|l11)&0xFFFFFFFF), ld8(l15+1048576))
                                    l11 = (l14+2)
                                    if l11 > 0xFFFFFFFF: l11 -= 0x100000000
                                    l13 = ((l13-(l15*10))&0xFFFFFFFF)
                            _br = 0
                            l13 = ld8((l13&255)+1048576)
                        _br = 0
                        l14 = (((l2+1120)|l11)&0xFFFFFFFF)
                        st8(l14, l13)
                        st8((_w if (_w:=(l14+1)) <= 0xFFFFFFFF else _w-0x100000000), 46)
                        l13 = (l11+2)
                        if l13 > 0xFFFFFFFF: l13 -= 0x100000000
                        l14 = ld8(l8+2)
                        if not (l14==0):
                            if not (l14>99):
                                if (l14<10):
                                    _br = 81
                                else:
                                    l12 = ((l14&255)//10)
                                    st8((((l2+1120)|l13)&0xFFFFFFFF), ld8(l12+1048576))
                                    l13 = (l11+3)
                                    if l13 > 0xFFFFFFFF: l13 -= 0x100000000
                                    l14 = ((l14-(l12*10))&0xFFFFFFFF)
                                    _br = 81
                            if not _br:
                                l13 = (((l2+1120)|l13)&0xFFFFFFFF)
                                l12 = ((l14&255)//100)
                                st8(l13, ld8(l12+1048576))
                                t5 = (l13+1)
                                l13 = ((l14-(l12*100))&0xFFFFFFFF)
                                l14 = ((l13&255)//10)
                                st8((_w if (_w:=t5) <= 0xFFFFFFFF else _w-0x100000000), ld8(l14+1048576))
                                l14 = ((l13-(l14*10))&0xFFFFFFFF)
                                l13 = (l11+4)
                                if l13 > 0xFFFFFFFF: l13 -= 0x100000000
                            _br = 0
                            l12 = ld8((l14&255)+1048576)
                        l11 = (((l2+1120)|l13)&0xFFFFFFFF)
                        st8(l11, l12)
                        st8((_w if (_w:=(l11+1)) <= 0xFFFFFFFF else _w-0x100000000), 46)
                        l11 = (l13+2)
                        if l11 > 0xFFFFFFFF: l11 -= 0x100000000
                        l8 = ld8(l8+3)
                        if not l8:
                            l8 = 48
                            _br = 83
                        if not _br:
                            if not (l8>99):
                                if (l8<10):
                                    _br = 85
                                else:
                                    l14 = ((l8&255)//10)
                                    st8((((l2+1120)+l11)&0xFFFFFFFF), ld8(l14+1048576))
                                    l11 = (l13+3)
                                    if l11 > 0xFFFFFFFF: l11 -= 0x100000000
                                    l8 = ((l8-(l14*10))&0xFFFFFFFF)
                                    _br = 85
                            if not _br:
                                l11 = (((l2+1120)+l11)&0xFFFFFFFF)
                                l14 = ((l8&255)//100)
                                st8(l11, ld8(l14+1048576))
                                l8 = ((l8-(l14*100))&0xFFFFFFFF)
                                t6 = (l11+1)
                                l11 = ((l8&255)//10)
                                st8((_w if (_w:=t6) <= 0xFFFFFFFF else _w-0x100000000), ld8(l11+1048576))
                                l8 = ((l8-(l11*10))&0xFFFFFFFF)
                                l11 = (l13+4)
                                if l11 > 0xFFFFFFFF: l11 -= 0x100000000
                            _br = 0
                            l8 = ld8((l8&255)+1048576)
                        _br = 0
                        st8((((l2+1120)+l11)&0xFFFFFFFF), l8)
                        l14 = (l11+1)
                        if l14 > 0xFFFFFFFF: l14 -= 0x100000000
                        if (l9&16):
                            pass
                        else:
                            l9 = (l10-1)
                            if l9 < 0: l9 += 0x100000000
                            if not ((l14^0x80000000)<(l10^0x80000000)):
                                l10 = l9
                                _br = 87
                            if not _br:
                                l12 = (l9 if (l9<l14) else l14)
                                l13 = ((l10-l12)&7)
                                if not (l13==0):
                                    l8 = (0-l13)
                                    if l8 < 0: l8 += 0x100000000
                                    l9 = (((l13^-1)+l10)&0xFFFFFFFF)
                                    while True:
                                        st8(l7, 32)
                                        l7 = (l7+1)
                                        if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                                        l8 = (l8+1)
                                        if l8 > 0xFFFFFFFF: l8 -= 0x100000000
                                        if l8:
                                            continue
                                        break
                                l8 = (l12-l10)
                                if l8 < 0: l8 += 0x100000000
                                l10 = l11
                                if (l8>4294967288):
                                    pass
                                else:
                                    l8 = (l9+1)
                                    if l8 > 0xFFFFFFFF: l8 -= 0x100000000
                                    while True:
                                        st64(l7, 2314885530818453536)
                                        l7 = (l7+8)
                                        if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                                        l8 = (l8-8)
                                        if l8 < 0: l8 += 0x100000000
                                        if (l14<l8):
                                            continue
                                        break
                                    l10 = l11
                        _br = 0
                        l13 = (l14&3)
                        l8 = 0
                        if not (l11<3):
                            l12 = (l14&60)
                            l8 = 0
                            while True:
                                l9 = (l7+l8)
                                if l9 > 0xFFFFFFFF: l9 -= 0x100000000
                                l11 = (((l2+1120)+l8)&0xFFFFFFFF)
                                st8(l9, ld8(l11))
                                st8((_w if (_w:=(l9+1)) <= 0xFFFFFFFF else _w-0x100000000), ld8((_w if (_w:=(l11+1)) <= 0xFFFFFFFF else _w-0x100000000)))
                                st8((_w if (_w:=(l9+2)) <= 0xFFFFFFFF else _w-0x100000000), ld8((_w if (_w:=(l11+2)) <= 0xFFFFFFFF else _w-0x100000000)))
                                st8((_w if (_w:=(l9+3)) <= 0xFFFFFFFF else _w-0x100000000), ld8((_w if (_w:=(l11+3)) <= 0xFFFFFFFF else _w-0x100000000)))
                                l8 = (l8+4)
                                if l8 > 0xFFFFFFFF: l8 -= 0x100000000
                                if (l12!=l8):
                                    continue
                                break
                            l7 = (l7+l8)
                            if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                            if (l13==0):
                                _br = 92
                            else:
                                pass
                        if not _br:
                            l8 = (((l2+1120)+l8)&0xFFFFFFFF)
                            while True:
                                st8(l7, ld8(l8))
                                l8 = (l8+1)
                                if l8 > 0xFFFFFFFF: l8 -= 0x100000000
                                l7 = (l7+1)
                                if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                                l13 = (l13-1)
                                if l13 < 0: l13 += 0x100000000
                                if l13:
                                    continue
                                break
                        _br = 0
                        if ((l14^0x80000000)>=(l10^0x80000000)):
                            _br = 24
                        else:
                            while True:
                                st8(l7, 32)
                                l7 = (l7+1)
                                if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                                l10 = (l10-1)
                                if l10 < 0: l10 += 0x100000000
                                if (l14<l10):
                                    continue
                                _br = 24
                                break
                                break
                            if not _br:
                                pass
                if _br == 38:
                    _br = 0
                if not _br:
                    l9 = (l9|64)
                if _br == 37:
                    _br = 0
                if not _br:
                    l8 = 16
                    _br = 30
                if _br == 36:
                    _br = 0
                if not _br:
                    l9 = (l9|2)
                    _br = 30
                if _br == 35:
                    _br = 0
                if not _br:
                    l8 = (l9|2)
                    l16 = ((l8&62) if (l9&16) else l8)
                    l17 = (((l14+7)&-8)&0xFFFFFFFF)
                    l24 = ldf64(l17)
                    if not (not (l24<0.0)):
                        l10 = (l10-1)
                        if l10 < 0: l10 += 0x100000000
                        l24 = (-l24)
                        l19 = 45
                        _br = 98
                    if not _br:
                        if not ((l16&4)==0):
                            l10 = (l10-1)
                            if l10 < 0: l10 += 0x100000000
                            l19 = 43
                            _br = 98
                        if not _br:
                            if not (l16&8):
                                l19 = 0
                                l18 = 1
                                _br = 97
                            if not _br:
                                l10 = (l10-1)
                                if l10 < 0: l10 += 0x100000000
                                l19 = 32
                    if _br == 98:
                        _br = 0
                    if not _br:
                        l18 = 0
                    _br = 0
                    p1 = (6 if (l13 > 2147483647) else l13)
                    l14 = f21(l24, p1, (_w if (_w:=(l2+1212)) <= 0xFFFFFFFF else _w-0x100000000), (_w if (_w:=(l2+1208)) <= 0xFFFFFFFF else _w-0x100000000), (_w if (_w:=(l2+1120)) <= 0xFFFFFFFF else _w-0x100000000))
                    if not ld32(l2+1208):
                        l8 = (l2+1040)
                        if l8 > 0xFFFFFFFF: l8 -= 0x100000000
                        _br = 102
                    if not _br:
                        st8(l2+1040, 45)
                        l8 = l4
                    _br = 0
                    l11 = ld8(l14)
                    if not (l11==0):
                        l9 = ld32(l2+1212)
                        if not (l9 < 1 or l9 > 2147483647):
                            l9 = 0
                            while True:
                                l13 = (l14+l9)
                                if l13 > 0xFFFFFFFF: l13 -= 0x100000000
                                if not (l9!=ld32(l2+1212)):
                                    st8(l8, 46)
                                    l8 = (l8+1)
                                    if l8 > 0xFFFFFFFF: l8 -= 0x100000000
                                    l11 = ld8(l13)
                                st8(l8, l11)
                                l9 = (l9+1)
                                if l9 > 0xFFFFFFFF: l9 -= 0x100000000
                                l8 = (l8+1)
                                if l8 > 0xFFFFFFFF: l8 -= 0x100000000
                                l11 = ld8((_w if (_w:=(l13+1)) <= 0xFFFFFFFF else _w-0x100000000))
                                if l11:
                                    continue
                                _br = 29
                                break
                                break
                            if not _br:
                                pass
                        if not _br:
                            st16(l8, 11824)
                            l8 = (l8+2)
                            if l8 > 0xFFFFFFFF: l8 -= 0x100000000
                            if not (l9 <= 2147483647 or l9 > 4294967295):
                                l9 = 0
                                while True:
                                    st8((_w if (_w:=(l8+l9)) <= 0xFFFFFFFF else _w-0x100000000), 48)
                                    l9 = (l9+1)
                                    if l9 > 0xFFFFFFFF: l9 -= 0x100000000
                                    if ((l9^0x80000000)<((_w if (_w:=(0-ld32(l2+1212))) >= 0 else _w+0x100000000)^0x80000000)):
                                        continue
                                    break
                                l8 = (l8+l9)
                                if l8 > 0xFFFFFFFF: l8 -= 0x100000000
                            l9 = ld8(l14)
                            if (l9==0):
                                _br = 29
                            else:
                                l11 = (l14+1)
                                if l11 > 0xFFFFFFFF: l11 -= 0x100000000
                                while True:
                                    st8(l8, l9)
                                    l8 = (l8+1)
                                    if l8 > 0xFFFFFFFF: l8 -= 0x100000000
                                    l9 = ld8(l11)
                                    l11 = (l11+1)
                                    if l11 > 0xFFFFFFFF: l11 -= 0x100000000
                                    if l9:
                                        continue
                                    _br = 29
                                    break
                                    break
                                if not _br:
                                    pass
                    if not _br:
                        st8(l8, 48)
                        if not (0 < p1 <= 2147483647):
                            l8 = (l8+1)
                            if l8 > 0xFFFFFFFF: l8 -= 0x100000000
                            _br = 29
                        if not _br:
                            st8(l8+1, 46)
                            l9 = (p1&7)
                            l8 = (l8+2)
                            if l8 > 0xFFFFFFFF: l8 -= 0x100000000
                            if not (p1<8):
                                l11 = (p1&2147483640)
                                while True:
                                    st64(l8, 3472328296227680304)
                                    l8 = (l8+8)
                                    if l8 > 0xFFFFFFFF: l8 -= 0x100000000
                                    l11 = (l11-8)
                                    if l11 < 0: l11 += 0x100000000
                                    if l11:
                                        continue
                                    break
                                if (l9==0):
                                    _br = 112
                                else:
                                    pass
                            if not _br:
                                while True:
                                    st8(l8, 48)
                                    l8 = (l8+1)
                                    if l8 > 0xFFFFFFFF: l8 -= 0x100000000
                                    l9 = (l9-1)
                                    if l9 < 0: l9 += 0x100000000
                                    if l9:
                                        continue
                                    break
                            _br = 0
                            st8(l8, 0)
                            _br = 28
                if _br == 34:
                    _br = 0
                if not _br:
                    st8(l7, 37)
                    l7 = (l7+1)
                    if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                    l11 = ld8(p0)
                    if (l11==0):
                        _br = 32
                    else:
                        pass
                if _br == 33:
                    _br = 0
                if not _br:
                    st8(l7, l11)
                    l7 = (l7+1)
                    if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                    p1 = l14
                    p0 = (p0+1)
                    if p0 > 0xFFFFFFFF: p0 -= 0x100000000
                    _br = 2
                    break
                if _br == 32:
                    _br = 0
                if not _br:
                    p1 = l14
                    p0 = (((p0-1)+1)&0xFFFFFFFF)
                    _br = 2
                    break
                if _br == 31:
                    _br = 0
                if not _br:
                    l8 = 8
                if _br == 30:
                    _br = 0
                if not _br:
                    p1 = (l14+4)
                    if p1 > 0xFFFFFFFF: p1 -= 0x100000000
                    l7 = f19(l7, ld32(l14), l8, l10, l13, l9)
                    _br = 24
                if _br == 29:
                    _br = 0
                if not _br:
                    st8(l8, 0)
                    if (l16<32):
                        pass
                    else:
                        if p1:
                            pass
                        else:
                            l13 = (l2+1040)
                            if l13 > 0xFFFFFFFF: l13 -= 0x100000000
                            l11 = l4
                            while True:
                                l8 = ld8(l13)
                                if not (68 < l8 <= 2147483647):
                                    if (l8==0):
                                        _br = 118
                                    else:
                                        if (l8==46):
                                            _br = 28
                                            break
                                        _br = 117
                                if not _br:
                                    if not (l8==101):
                                        if (l8!=69):
                                            _br = 117
                                        else:
                                            pass
                                    if not _br:
                                        l8 = 0
                                        p1 = 1
                                        while True:
                                            l9 = l11
                                            l12 = p1
                                            l14 = (l8+1)
                                            if l14 > 0xFFFFFFFF: l14 -= 0x100000000
                                            if (ld8((((l13+l8)+1)&0xFFFFFFFF))==0):
                                                _br = 121
                                                break
                                            p1 = (l12+1)
                                            if p1 > 0xFFFFFFFF: p1 -= 0x100000000
                                            l11 = (l9+1)
                                            if l11 > 0xFFFFFFFF: l11 -= 0x100000000
                                            l15 = (1 if (l8!=255) else 0)
                                            l8 = l14
                                            if l15:
                                                continue
                                            break
                                        if not _br:
                                            pass
                                        _br = 0
                                        if not (l14 < 1 or l14 > 2147483647):
                                            l8 = l14
                                            if not ((l14&3)==0):
                                                l11 = (l12&3)
                                                l8 = 0
                                                while True:
                                                    st8((_w if (_w:=(l9+1)) <= 0xFFFFFFFF else _w-0x100000000), ld8(l9))
                                                    l9 = (l9-1)
                                                    if l9 < 0: l9 += 0x100000000
                                                    l8 = (l8+1)
                                                    if l8 > 0xFFFFFFFF: l8 -= 0x100000000
                                                    if (l11!=l8):
                                                        continue
                                                    break
                                                l8 = (l14-l8)
                                                if l8 < 0: l8 += 0x100000000
                                            if not ((_w if (_w:=(l14-1)) >= 0 else _w+0x100000000)<3):
                                                l8 = (l8+4)
                                                if l8 > 0xFFFFFFFF: l8 -= 0x100000000
                                                while True:
                                                    l9 = (l13+l8)
                                                    if l9 > 0xFFFFFFFF: l9 -= 0x100000000
                                                    st32((_w if (_w:=(l9-6)) >= 0 else _w+0x100000000), ld32((_w if (_w:=(l9-7)) >= 0 else _w+0x100000000)))
                                                    l8 = (l8-4)
                                                    if l8 < 0: l8 += 0x100000000
                                                    if (4 < l8 <= 2147483647):
                                                        continue
                                                    break
                                        st8(l13, 46)
                                        _br = 28
                                        break
                                if _br == 118:
                                    _br = 0
                                if not _br:
                                    st16(l13, 46)
                                    _br = 28
                                    break
                                _br = 0
                                l11 = (l11+1)
                                if l11 > 0xFFFFFFFF: l11 -= 0x100000000
                                l13 = (l13+1)
                                if l13 > 0xFFFFFFFF: l13 -= 0x100000000
                                continue
                                break
                            if not _br:
                                pass
                if _br == 28:
                    _br = 0
                if not _br:
                    if not ld8(l2+1040):
                        l9 = (l2+1040)
                        if l9 > 0xFFFFFFFF: l9 -= 0x100000000
                        _br = 127
                    if not _br:
                        l8 = 0
                        while True:
                            l9 = (l8+1)
                            if l9 > 0xFFFFFFFF: l9 -= 0x100000000
                            if (ld8((_w if (_w:=(l4+l8)) <= 0xFFFFFFFF else _w-0x100000000))==0):
                                _br = 129
                                break
                            l11 = (1 if (l8!=255) else 0)
                            l8 = l9
                            if l11:
                                continue
                            break
                        if not _br:
                            pass
                        _br = 0
                        l9 = (((l2+1040)+l9)&0xFFFFFFFF)
                    _br = 0
                    l11 = ((l9-(l2+1040))&0xFFFFFFFF)
                    l14 = (l10-l11)
                    if l14 < 0: l14 += 0x100000000
                    if (l16&17):
                        pass
                    else:
                        l8 = (l14-1)
                        if l8 < 0: l8 += 0x100000000
                        if not (0 < l14 <= 2147483647):
                            l14 = l8
                            _br = 131
                        if not _br:
                            l10 = (l14&7)
                            if not (l10==0):
                                while True:
                                    st8(l7, 32)
                                    l8 = (l8-1)
                                    if l8 < 0: l8 += 0x100000000
                                    l7 = (l7+1)
                                    if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                                    l10 = (l10-1)
                                    if l10 < 0: l10 += 0x100000000
                                    if l10:
                                        continue
                                    break
                            l10 = (1 if (l14<8) else 0)
                            l14 = 4294967295
                            if l10:
                                pass
                            else:
                                l8 = (l8+1)
                                if l8 > 0xFFFFFFFF: l8 -= 0x100000000
                                while True:
                                    st64(l7, 2314885530818453536)
                                    l7 = (l7+8)
                                    if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                                    l8 = (l8-8)
                                    if l8 < 0: l8 += 0x100000000
                                    if l8:
                                        continue
                                    break
                    _br = 0
                    if not l18:
                        st8(l7, l19)
                        l7 = (l7+1)
                        if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                    if (l16&16):
                        pass
                    else:
                        l13 = (l14-1)
                        if l13 < 0: l13 += 0x100000000
                        if not (0 < l14 <= 2147483647):
                            l14 = l13
                            _br = 137
                        if not _br:
                            l8 = (48 if (l16&1) else 32)
                            l10 = (l14&7)
                            if not (l10==0):
                                l13 = (((l10^-1)+l14)&0xFFFFFFFF)
                                while True:
                                    st8(l7, l8)
                                    l7 = (l7+1)
                                    if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                                    l10 = (l10-1)
                                    if l10 < 0: l10 += 0x100000000
                                    if l10:
                                        continue
                                    break
                            l10 = (1 if (l14<8) else 0)
                            l14 = 4294967295
                            if l10:
                                pass
                            else:
                                l10 = (l13+1)
                                if l10 > 0xFFFFFFFF: l10 -= 0x100000000
                                while True:
                                    st8(l7, l8)
                                    st8((_w if (_w:=(l7+7)) <= 0xFFFFFFFF else _w-0x100000000), l8)
                                    st8((_w if (_w:=(l7+6)) <= 0xFFFFFFFF else _w-0x100000000), l8)
                                    st8((_w if (_w:=(l7+5)) <= 0xFFFFFFFF else _w-0x100000000), l8)
                                    st8((_w if (_w:=(l7+4)) <= 0xFFFFFFFF else _w-0x100000000), l8)
                                    st8((_w if (_w:=(l7+3)) <= 0xFFFFFFFF else _w-0x100000000), l8)
                                    st8((_w if (_w:=(l7+2)) <= 0xFFFFFFFF else _w-0x100000000), l8)
                                    st8((_w if (_w:=(l7+1)) <= 0xFFFFFFFF else _w-0x100000000), l8)
                                    l7 = (l7+8)
                                    if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                                    l10 = (l10-8)
                                    if l10 < 0: l10 += 0x100000000
                                    if l10:
                                        continue
                                    break
                    _br = 0
                    if (l11 < 1 or l11 > 2147483647):
                        pass
                    else:
                        l11 = (l9&3)
                        l8 = 0
                        if not ((((l2+1040)-l9)&0xFFFFFFFF)>4294967292):
                            l13 = (l3+(l9&-4))
                            if l13 > 0xFFFFFFFF: l13 -= 0x100000000
                            l8 = 0
                            while True:
                                l10 = (l7+l8)
                                if l10 > 0xFFFFFFFF: l10 -= 0x100000000
                                l9 = (((l2+1040)+l8)&0xFFFFFFFF)
                                st8(l10, ld8(l9))
                                st8((_w if (_w:=(l10+1)) <= 0xFFFFFFFF else _w-0x100000000), ld8((_w if (_w:=(l9+1)) <= 0xFFFFFFFF else _w-0x100000000)))
                                st8((_w if (_w:=(l10+2)) <= 0xFFFFFFFF else _w-0x100000000), ld8((_w if (_w:=(l9+2)) <= 0xFFFFFFFF else _w-0x100000000)))
                                st8((_w if (_w:=(l10+3)) <= 0xFFFFFFFF else _w-0x100000000), ld8((_w if (_w:=(l9+3)) <= 0xFFFFFFFF else _w-0x100000000)))
                                l8 = (l8+4)
                                if l8 > 0xFFFFFFFF: l8 -= 0x100000000
                                if (l13!=l8):
                                    continue
                                break
                            l7 = (l7+l8)
                            if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                            if (l11==0):
                                _br = 142
                            else:
                                pass
                        if not _br:
                            l8 = (((l2+1040)+l8)&0xFFFFFFFF)
                            while True:
                                st8(l7, ld8(l8))
                                l8 = (l8+1)
                                if l8 > 0xFFFFFFFF: l8 -= 0x100000000
                                l7 = (l7+1)
                                if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                                l11 = (l11-1)
                                if l11 < 0: l11 += 0x100000000
                                if l11:
                                    continue
                                break
                    _br = 0
                    if not (l14 < 1 or l14 > 2147483647):
                        l8 = (l14&7)
                        if not l8:
                            l10 = l14
                            _br = 147
                        if not _br:
                            l10 = (l14&-8)
                            while True:
                                st8(l7, 32)
                                l7 = (l7+1)
                                if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                                l8 = (l8-1)
                                if l8 < 0: l8 += 0x100000000
                                if l8:
                                    continue
                                break
                        _br = 0
                        if not (l14<8):
                            l8 = (l10+8)
                            if l8 > 0xFFFFFFFF: l8 -= 0x100000000
                            while True:
                                st64(l7, 2314885530818453536)
                                l7 = (l7+8)
                                if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                                l8 = (l8-8)
                                if l8 < 0: l8 += 0x100000000
                                if (8 < l8 <= 2147483647):
                                    continue
                                break
                    p1 = (l17+8)
                    if p1 > 0xFFFFFFFF: p1 -= 0x100000000
                    p0 = (p0+1)
                    if p0 > 0xFFFFFFFF: p0 -= 0x100000000
                    _br = 2
                    break
                if _br == 27:
                    _br = 0
                if not _br:
                    if (l9==0):
                        _br = 25
                    else:
                        pass
                if _br == 26:
                    _br = 0
                if not _br:
                    while True:
                        st8(l7, ld8(l8))
                        l7 = (l7+1)
                        if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                        l8 = (l8+1)
                        if l8 > 0xFFFFFFFF: l8 -= 0x100000000
                        l9 = (l9-1)
                        if l9 < 0: l9 += 0x100000000
                        if l9:
                            continue
                        break
                if _br == 25:
                    _br = 0
                if not _br:
                    p1 = (l14+4)
                    if p1 > 0xFFFFFFFF: p1 -= 0x100000000
                    if ((l12^0x80000000)>=(l10^0x80000000)):
                        pass
                    else:
                        l8 = (((l10+l15)-l11)&0xFFFFFFFF)
                        l9 = (l8-1)
                        if l9 < 0: l9 += 0x100000000
                        l8 = (l8&7)
                        if not (l8==0):
                            l10 = (l10-l8)
                            if l10 < 0: l10 += 0x100000000
                            while True:
                                st8(l7, 32)
                                l7 = (l7+1)
                                if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                                l8 = (l8-1)
                                if l8 < 0: l8 += 0x100000000
                                if l8:
                                    continue
                                break
                        if (l9<7):
                            pass
                        else:
                            while True:
                                st64(l7, 2314885530818453536)
                                l7 = (l7+8)
                                if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                                l10 = (l10-8)
                                if l10 < 0: l10 += 0x100000000
                                if ((l12^0x80000000)<(l10^0x80000000)):
                                    continue
                                break
                _br = 0
                p0 = (p0+1)
                if p0 > 0xFFFFFFFF: p0 -= 0x100000000
                _br = 2
                break
            _br = 0
            l8 = (l8+1)
            if l8 > 0xFFFFFFFF: l8 -= 0x100000000
            l9 = (l10|l9)
            continue
            break
        if _br:
            _br = 0
            continue
        break
    if not _br:
        pass
    _br = 0
    l8 = 0
    st8(l7, 0)
    l10 = ld8(l2+16)
    if not (l10==0):
        l9 = (((l2+16)|1)&0xFFFFFFFF)
        l8 = 0
        while True:
            f28(((((l10&255)^128)-128)&0xFFFFFFFF))
            l10 = l8
            l8 = (l10+1)
            if l8 > 0xFFFFFFFF: l8 -= 0x100000000
            l10 = ld8((_w if (_w:=(l9+l10)) <= 0xFFFFFFFF else _w-0x100000000))
            if l10:
                continue
            break
    GS.g0 = (_w if (_w:=(l2+1216)) <= 0xFFFFFFFF else _w-0x100000000)
    return l8


def f19(p0: i64, p1: i64, p2: i64, p3: i64, p4: i64, p5: i64) -> i64:
    l6: i64 = 0
    l7: i64 = 0
    l8: i64 = 0
    l9: i64 = 0
    l10: i64 = 0
    l11: i64 = 0
    l12: i64 = 0
    l13: i64 = 0
    t1: i64 = 0
    t2: i64 = 0
    _br: i64 = 0
    _w: i64 = 0
    l6 = (GS.g0-80)
    if l6 < 0: l6 += 0x100000000
    l7 = 0
    l8 = ((p5&-2) if (p5&16) else p5)
    if not (l8&2):
        l9 = 1
        _br = 1
    if not _br:
        if not (p1 <= 2147483647 or p1 > 4294967295):
            p3 = (p3-1)
            if p3 < 0: p3 += 0x100000000
            l9 = 0
            p1 = (0-p1)
            if p1 < 0: p1 += 0x100000000
            l7 = 45
            _br = 1
        if not _br:
            if not ((l8&4)==0):
                p3 = (p3-1)
                if p3 < 0: p3 += 0x100000000
                l7 = 43
                _br = 4
            if not _br:
                if not (l8&8):
                    l9 = 1
                    _br = 1
                if not _br:
                    p3 = (p3-1)
                    if p3 < 0: p3 += 0x100000000
                    l7 = 32
            if _br == 4:
                _br = 0
            if not _br:
                l9 = 0
    _br = 0
    l10 = (l8&32)
    if (l10==0):
        pass
    else:
        t1 = (_w if (_w:=(p2-8)) >= 0 else _w+0x100000000)
        if t1 == 0:
            _br = 8
        elif t1 == 8:
            pass
        else:
            _br = 7
        if not _br:
            p3 = (p3-2)
            if p3 < 0: p3 += 0x100000000
            _br = 7
        if _br == 8:
            _br = 0
        if not _br:
            p3 = (p3-1)
            if p3 < 0: p3 += 0x100000000
    _br = 0
    if not (p1==0):
        l11 = (1048672 if (p5&64) else 1048576)
        p5 = 0
        while True:
            l12 = (p1//p2)
            st8((_w if (_w:=(l6+p5)) <= 0xFFFFFFFF else _w-0x100000000), ld8(((l11+(p1-(l12*p2)))&0xFFFFFFFF)))
            p5 = (p5+1)
            if p5 > 0xFFFFFFFF: p5 -= 0x100000000
            l13 = (1 if (p2<=p1) else 0)
            p1 = l12
            if l13:
                continue
            _br = 10
            break
            break
        if not _br:
            pass
    if not _br:
        st8(l6, 48)
        p5 = 1
    _br = 0
    l11 = (p5 if ((p5^0x80000000)>(p4^0x80000000)) else p4)
    l13 = (p3-l11)
    if l13 < 0: l13 += 0x100000000
    if (l8&17):
        pass
    else:
        p1 = (l13-1)
        if p1 < 0: p1 += 0x100000000
        if not (0 < l13 <= 2147483647):
            l13 = p1
            _br = 13
        if not _br:
            l12 = (l13&7)
            if not (l12==0):
                while True:
                    st8(p0, 32)
                    p1 = (p1-1)
                    if p1 < 0: p1 += 0x100000000
                    p0 = (p0+1)
                    if p0 > 0xFFFFFFFF: p0 -= 0x100000000
                    l12 = (l12-1)
                    if l12 < 0: l12 += 0x100000000
                    if l12:
                        continue
                    break
            l12 = (1 if (l13<8) else 0)
            l13 = 4294967295
            if l12:
                pass
            else:
                p1 = (p1+1)
                if p1 > 0xFFFFFFFF: p1 -= 0x100000000
                while True:
                    st64(p0, 2314885530818453536)
                    p0 = (p0+8)
                    if p0 > 0xFFFFFFFF: p0 -= 0x100000000
                    p1 = (p1-8)
                    if p1 < 0: p1 += 0x100000000
                    if p1:
                        continue
                    break
    _br = 0
    if not l9:
        st8(p0, l7)
        p0 = (p0+1)
        if p0 > 0xFFFFFFFF: p0 -= 0x100000000
    if (l10==0):
        pass
    else:
        t2 = (_w if (_w:=(p2-8)) >= 0 else _w+0x100000000)
        if t2 == 0:
            pass
        elif t2 == 8:
            _br = 20
        else:
            _br = 19
        if not _br:
            st8(p0, 48)
            p0 = (p0+1)
            if p0 > 0xFFFFFFFF: p0 -= 0x100000000
            _br = 19
        if _br == 20:
            _br = 0
        if not _br:
            st16(p0, 30768)
            p0 = (p0+2)
            if p0 > 0xFFFFFFFF: p0 -= 0x100000000
    _br = 0
    if (l8&16):
        pass
    else:
        l12 = (l13-1)
        if l12 < 0: l12 += 0x100000000
        if not (0 < l13 <= 2147483647):
            l13 = l12
            _br = 22
        if not _br:
            p1 = (48 if (l8&1) else 32)
            p2 = (l13&7)
            if not (p2==0):
                l12 = (((p2^-1)+l13)&0xFFFFFFFF)
                while True:
                    st8(p0, p1)
                    p0 = (p0+1)
                    if p0 > 0xFFFFFFFF: p0 -= 0x100000000
                    p2 = (p2-1)
                    if p2 < 0: p2 += 0x100000000
                    if p2:
                        continue
                    break
            p2 = (1 if (l13<8) else 0)
            l13 = 4294967295
            if p2:
                pass
            else:
                p2 = (l12+1)
                if p2 > 0xFFFFFFFF: p2 -= 0x100000000
                while True:
                    st8(p0, p1)
                    st8((_w if (_w:=(p0+7)) <= 0xFFFFFFFF else _w-0x100000000), p1)
                    st8((_w if (_w:=(p0+6)) <= 0xFFFFFFFF else _w-0x100000000), p1)
                    st8((_w if (_w:=(p0+5)) <= 0xFFFFFFFF else _w-0x100000000), p1)
                    st8((_w if (_w:=(p0+4)) <= 0xFFFFFFFF else _w-0x100000000), p1)
                    st8((_w if (_w:=(p0+3)) <= 0xFFFFFFFF else _w-0x100000000), p1)
                    st8((_w if (_w:=(p0+2)) <= 0xFFFFFFFF else _w-0x100000000), p1)
                    st8((_w if (_w:=(p0+1)) <= 0xFFFFFFFF else _w-0x100000000), p1)
                    p0 = (p0+8)
                    if p0 > 0xFFFFFFFF: p0 -= 0x100000000
                    p2 = (p2-8)
                    if p2 < 0: p2 += 0x100000000
                    if p2:
                        continue
                    break
    _br = 0
    if not ((p4^0x80000000)<=(p5^0x80000000)):
        p1 = ((l11-p5)&7)
        if not p1:
            p2 = l11
            _br = 28
        if not _br:
            p2 = (l11-p1)
            if p2 < 0: p2 += 0x100000000
            while True:
                st8(p0, 48)
                p0 = (p0+1)
                if p0 > 0xFFFFFFFF: p0 -= 0x100000000
                p1 = (p1-1)
                if p1 < 0: p1 += 0x100000000
                if p1:
                    continue
                break
        _br = 0
        if not ((_w if (_w:=(p5-l11)) >= 0 else _w+0x100000000)>4294967288):
            while True:
                st64(p0, 3472328296227680304)
                p0 = (p0+8)
                if p0 > 0xFFFFFFFF: p0 -= 0x100000000
                p2 = (p2-8)
                if p2 < 0: p2 += 0x100000000
                if ((p5^0x80000000)<(p2^0x80000000)):
                    continue
                break
    p1 = (p5+1)
    if p1 > 0xFFFFFFFF: p1 -= 0x100000000
    while True:
        st8(p0, ld8((((l6+p1)-2)&0xFFFFFFFF)))
        p0 = (p0+1)
        if p0 > 0xFFFFFFFF: p0 -= 0x100000000
        p1 = (p1-1)
        if p1 < 0: p1 += 0x100000000
        if (1 < p1 <= 2147483647):
            continue
        break
    if not (l13 < 1 or l13 > 2147483647):
        p5 = (l13&7)
        if not p5:
            p1 = l13
            _br = 34
        if not _br:
            p1 = 0
            while True:
                st8((_w if (_w:=(p0+p1)) <= 0xFFFFFFFF else _w-0x100000000), 32)
                p1 = (p1+1)
                if p1 > 0xFFFFFFFF: p1 -= 0x100000000
                if (p5!=p1):
                    continue
                break
            p0 = (p0+p1)
            if p0 > 0xFFFFFFFF: p0 -= 0x100000000
            p1 = (l13-p1)
            if p1 < 0: p1 += 0x100000000
        _br = 0
        if not (l13<8):
            p1 = (p1+8)
            if p1 > 0xFFFFFFFF: p1 -= 0x100000000
            while True:
                st64(p0, 2314885530818453536)
                p0 = (p0+8)
                if p0 > 0xFFFFFFFF: p0 -= 0x100000000
                p1 = (p1-8)
                if p1 < 0: p1 += 0x100000000
                if (8 < p1 <= 2147483647):
                    continue
                break
    return p0


def f20(p0: float, p1: i64, p2: i64, p3: i64, p4: i64, p5: i64) -> i64:
    l6: i64 = 0
    l7: i64 = 0
    l8: i64 = 0
    l9: float = 0.0
    t1: float = 0.0
    t2: float = 0.0
    t3: i64 = 0
    t4: float = 0.0
    t5: i64 = 0
    _br: i64 = 0
    _w: i64 = 0
    l6 = (GS.g0-16)
    if l6 < 0: l6 += 0x100000000
    GS.g0 = l6
    st32(p3, 0)
    p1 = (p1 if (0 < p1 <= 2147483647) else 0)
    l7 = (1 if (p1 < 78 or p1 > 2147483647) else 0)
    if not (not (p0<0.0)):
        st32(p3, 1)
        p0 = (-p0)
    l8 = (p1 if l7 else 78)
    l9 = f30(p0, (_w if (_w:=(l6+8)) <= 0xFFFFFFFF else _w-0x100000000))
    p0 = ldf64(l6+8)
    if not (p0==0.0):
        p1 = (p4+79)
        if p1 > 0xFFFFFFFF: p1 -= 0x100000000
        p3 = 0
        while True:
            p0 = f30(_fdiv(p0,10.0), (_w if (_w:=(l6+8)) <= 0xFFFFFFFF else _w-0x100000000))
            st8((_w if (_w:=(p1+p3)) <= 0xFFFFFFFF else _w-0x100000000), (_tsat_s32(((p0+0.03)*10.0))+48))
            stf64(l6, p0)
            p3 = (p3-1)
            if p3 < 0: p3 += 0x100000000
            p0 = ldf64(l6+8)
            if (p0!=0.0):
                continue
            break
        p1 = 0
        l7 = (0-p3)
        if l7 < 0: l7 += 0x100000000
        t3 = (_w if (_w:=(p3+81)) <= 0xFFFFFFFF else _w-0x100000000)
        if not (t3 < 81 or t3 > 2147483647):
            p0 = l9
            _br = 2
        if not _br:
            p3 = (p3+79)
            if p3 > 0xFFFFFFFF: p3 -= 0x100000000
            p1 = 0
            while True:
                st8((_w if (_w:=(p4+p1)) <= 0xFFFFFFFF else _w-0x100000000), ld8((((p4+p3)+1)&0xFFFFFFFF)))
                p1 = (p1+1)
                if p1 > 0xFFFFFFFF: p1 -= 0x100000000
                p3 = (p3+1)
                if p3 > 0xFFFFFFFF: p3 -= 0x100000000
                if (p3 < 79 or p3 > 2147483647):
                    continue
                break
            p0 = l9
            _br = 2
    if not _br:
        p1 = 0
        if not (l9>0.0):
            p0 = l9
            l7 = 0
            _br = 2
        if not _br:
            l7 = 1
            while True:
                l7 = (l7-1)
                if l7 < 0: l7 += 0x100000000
                p0 = l9
                l9 = (p0*10.0)
                if (l9<1.0):
                    continue
                break
            stf64(l6, l9)
    _br = 0
    st32(p2, l7)
    l8 = ((0 if p5 else l7)+l8)
    if l8 > 0xFFFFFFFF: l8 -= 0x100000000
    if not (l8 > 2147483647):
        if not ((p1^0x80000000)>(l8^0x80000000)):
            if not (p1>79):
                l7 = (l8 if (l8<79) else 79)
                while True:
                    p0 = f30((p0*10.0), l6)
                    st8((_w if (_w:=(p4+p1)) <= 0xFFFFFFFF else _w-0x100000000), (_tsat_s32(ldf64(l6))+48))
                    p3 = (1 if (p1<l7) else 0)
                    p1 = (p1+1)
                    if p1 > 0xFFFFFFFF: p1 -= 0x100000000
                    if p3:
                        continue
                    break
        if not (l8<80):
            st8(p4+79, 0)
            _br = 9
        if not _br:
            l7 = (p4+l8)
            if l7 > 0xFFFFFFFF: l7 -= 0x100000000
            p1 = (ld8(l7)+5)
            st8(l7, p1)
            t5 = ((((p1&255)^128)-128)&0xFFFFFFFF)
            if not (t5 < 58 or t5 > 2147483647):
                p1 = l7
                while True:
                    st8(p1, 48)
                    if not (p1<=p4):
                        p1 = (p1-1)
                        if p1 < 0: p1 += 0x100000000
                        p3 = (ld8(p1)+1)
                        st8(p1, p3)
                        _br = 16
                    if not _br:
                        st8(p1, 49)
                        st32(p2, (ld32(p2)+1))
                        if not p5:
                            if not (l7<=p4):
                                st8(l7, 48)
                            l7 = (l7+1)
                            if l7 > 0xFFFFFFFF: l7 -= 0x100000000
                        p3 = ld8(p1)
                    _br = 0
                    if (57 < ((((p3&255)^128)-128)&0xFFFFFFFF) <= 2147483647):
                        continue
                    break
            st8(l7, 0)
            _br = 9
    if not _br:
        st8(p4, 0)
    _br = 0
    GS.g0 = (_w if (_w:=(l6+16)) <= 0xFFFFFFFF else _w-0x100000000)
    return p4


def f21(p0: float, p1: i64, p2: i64, p3: i64, p4: i64) -> i64:
    t1: i64 = 0
    t1 = f20(p0, p1, p2, p3, p4, 0)
    return t1


def f22() -> None:
    t1: i64 = 0
    t1 = GS.i0()
    st32(0+1052528, t1)


def f23() -> None:
    t1: i64 = 0
    t1 = GS.i0()
    st32(0+1052532, t1)


def f24() -> i64:
    _w: i64 = 0
    return (_w if (_w:=(ld32(0+1052532)-ld32(0+1052528))) >= 0 else _w+0x100000000)


def f25(p0: i64) -> float:
    return _fdiv(float(p0),1000.0)


def f26(p0: i64, p1: i64, p2: i64) -> None:
    st8(p0, 1)


def f27(p0: i64) -> None:
    st8(p0, 0)


def f28(p0: i64) -> None:
    l1: i64 = 0
    _br: i64 = 0
    _w: i64 = 0
    l1 = ld32(0+1052800)
    st8((_w if (_w:=(l1+1052544)) <= 0xFFFFFFFF else _w-0x100000000), p0)
    l1 = (l1+1)
    if l1 > 0xFFFFFFFF: l1 -= 0x100000000
    st32(0+1052800, l1)
    if not (p0==10):
        if (l1!=256):
            _br = 1
        else:
            pass
    if not _br:
        GS.i1(1052544, l1)
        st32(0+1052800, 0)
    _br = 0


def f29() -> None:
    t1: i64 = 0
    t1 = f6()


def f30(p0: float, p1: i64) -> float:
    l2: float = 0.0
    l2 = _ftrunc(p0)
    stf64(p1, l2)
    return (p0-l2)


DATA: Final[list[tuple[int, bytes]]] = [(1048576, b'0123456789abcdefghijklmnopqrstuvwxyz\x001T3.4e4z\x00CoreMark 1.0 : %f / %s %s\x00 / %s\x00-T.T++Tq\x0034.0e-T^\x000123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ\x00T0.3e-1F\x00STATIC\x00<NULL>\x00-874\x001234\x00-O3\x005.500e+3\x00zig cc (clang), wasm32\x00-87e+832\x00+122\x005012\x00+0.6e-12\x00-.123e-2\x00-110.700\x00.1234500\x00+0.64400\x0035.54400\x00[%u]ERROR! matrix crc 0x%04x - should be 0x%04x\n\x00[%u]ERROR! list crc 0x%04x - should be 0x%04x\n\x00[%u]ERROR! state crc 0x%04x - should be 0x%04x\n\x00[%d]crcmatrix     : 0x%04x\n\x00[%d]crcfinal      : 0x%04x\n\x00[%d]crcstate      : 0x%04x\n\x00[%d]crclist       : 0x%04x\n\x00seedcrc          : 0x%04x\n\x00CoreMark Size    : %lu\n\x00Total ticks      : %lu\n\x00Iterations       : %lu\n\x00Compiler version : %s\n\x00Memory location  : %s\n\x00Compiler flags   : %s\n\x00Total time (secs): %f\n\x00Iterations/Sec   : %f\n\x00Errors detected\n\x00Correct operation validated. See README.md for run and reporting rules.\n\x00Cannot validate operation for these seed values, please compare with results on a known platform.\n\x00Profile generation run parameters for coremark.\n\x006k validation run parameters for coremark.\n\x002K validation run parameters for coremark.\n\x006k performance run parameters for coremark.\n\x002K performance run parameters for coremark.\n\x00ERROR! Must execute for at least 10 secs for a valid result!\n\x00\x00\xb0\xd4@3yj\x14\xe7\xc1\xe3R\xbe\x99\x11\x08V\xd7\x1fG\x07G^\xbf9\xa4\xe5:\x8e\x84\x8d\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xd8\x00\x10\x00\xa1\x00\x10\x00\x9c\x00\x10\x00\xd3\x00\x10\x00\n\x01\x10\x00\xf8\x00\x10\x00\xef\x00\x10\x00\x01\x01\x10\x00\xaa\x00\x10\x00\xe6\x00\x10\x00\xca\x00\x10\x00\xdd\x00\x10\x00\x85\x00\x10\x00N\x00\x10\x00%\x00\x10\x00W\x00\x10\x00\xf0\x04\x10\x00\xf0\x04\x10\x00\xf0\x04\x10\x00\x00\x05\x10\x00\x00\x05\x10\x00\x10\x05\x10\x00\x10\x05\x10\x00 \x05\x10\x00\x04\x00\x00\x00\x04\x00\x00\x00\x04\x00\x00\x00\x08\x00\x00\x00\x08\x00\x00\x00\x08\x00\x00\x00\x08\x00\x00\x00\x08\x00\x00\x00\x00\x00\xc1\xc0\x81\xc1@\x01\x01\xc3\xc0\x03\x80\x02A\xc2\x01\xc6\xc0\x06\x80\x07A\xc7\x00\x05\xc1\xc5\x81\xc4@\x04\x01\xcc\xc0\x0c\x80\rA\xcd\x00\x0f\xc1\xcf\x81\xce@\x0e\x00\n\xc1\xca\x81\xcb@\x0b\x01\xc9\xc0\t\x80\x08A\xc8\x01\xd8\xc0\x18\x80\x19A\xd9\x00\x1b\xc1\xdb\x81\xda@\x1a\x00\x1e\xc1\xde\x81\xdf@\x1f\x01\xdd\xc0\x1d\x80\x1cA\xdc\x00\x14\xc1\xd4\x81\xd5@\x15\x01\xd7\xc0\x17\x80\x16A\xd6\x01\xd2\xc0\x12\x80\x13A\xd3\x00\x11\xc1\xd1\x81\xd0@\x10\x01\xf0\xc00\x801A\xf1\x003\xc1\xf3\x81\xf2@2\x006\xc1\xf6\x81\xf7@7\x01\xf5\xc05\x804A\xf4\x00<\xc1\xfc\x81\xfd@=\x01\xff\xc0?\x80>A\xfe\x01\xfa\xc0:\x80;A\xfb\x009\xc1\xf9\x81\xf8@8\x00(\xc1\xe8\x81\xe9@)\x01\xeb\xc0+\x80*A\xea\x01\xee\xc0.\x80/A\xef\x00-\xc1\xed\x81\xec@,\x01\xe4\xc0$\x80%A\xe5\x00\'\xc1\xe7\x81\xe6@&\x00"\xc1\xe2\x81\xe3@#\x01\xe1\xc0!\x80 A\xe0\x01\xa0\xc0`\x80aA\xa1\x00c\xc1\xa3\x81\xa2@b\x00f\xc1\xa6\x81\xa7@g\x01\xa5\xc0e\x80dA\xa4\x00l\xc1\xac\x81\xad@m\x01\xaf\xc0o\x80nA\xae\x01\xaa\xc0j\x80kA\xab\x00i\xc1\xa9\x81\xa8@h\x00x\xc1\xb8\x81\xb9@y\x01\xbb\xc0{\x80zA\xba\x01\xbe\xc0~\x80\x7fA\xbf\x00}\xc1\xbd\x81\xbc@|\x01\xb4\xc0t\x80uA\xb5\x00w\xc1\xb7\x81\xb6@v\x00r\xc1\xb2\x81\xb3@s\x01\xb1\xc0q\x80pA\xb0\x00P\xc1\x90\x81\x91@Q\x01\x93\xc0S\x80RA\x92\x01\x96\xc0V\x80WA\x97\x00U\xc1\x95\x81\x94@T\x01\x9c\xc0\\\x80]A\x9d\x00_\xc1\x9f\x81\x9e@^\x00Z\xc1\x9a\x81\x9b@[\x01\x99\xc0Y\x80XA\x98\x01\x88\xc0H\x80IA\x89\x00K\xc1\x8b\x81\x8a@J\x00N\xc1\x8e\x81\x8f@O\x01\x8d\xc0M\x80LA\x8c\x00D\xc1\x84\x81\x85@E\x01\x87\xc0G\x80FA\x86\x01\x82\xc0B\x80CA\x83\x00A\xc1\x81\x81\x80@@`\x0f\x10\x00d\x0f\x10\x00\x84\x07\x10\x00h\x0f\x10\x00l\x0f\x10\x00'), (1050500, b'f\x00\x00\x00\x01\x00\x00\x00')]

def instantiate(imports: Any) -> dict[str, Any]:
    GS.i0 = imports['wiring']['millis']
    GS.i1 = imports['wiring']['print']
    for o, d in DATA:
        for j in range(len(d)): MEM[o + j] = d[j]
    return {'memory': MEM, 'setup': f29}
