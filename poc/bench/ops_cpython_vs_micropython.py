import struct, sys
try:
    from time import perf_counter as _t
    def now(): return _t()
except ImportError:
    from time import ticks_us, ticks_diff
    _T0 = ticks_us()
    def now(): return ticks_diff(ticks_us(), _T0) / 1e6

M = bytearray(1 << 16)
_uf = struct.unpack_from; _pi = struct.pack_into

def arith_small(n):          # values stay < 256: CPython uses cached small ints
    l = 0
    for i in range(n):
        l = (l + 3) & 0xFF
    return l
def arith_i32(n):            # i32-sized values: CPython allocates a new int per result
    l = 1000
    for i in range(n):
        l = (l + 12345) & 0xFFFFFFFF
    return l
def mem_load(n):
    s = 0
    for i in range(n):
        s = _uf('<I', M, (i & 1023) << 2)[0]
    return s
def mem_store(n):
    for i in range(n):
        _pi('<I', M, (i & 1023) << 2, i)
def g(a, b): return a
def calls(n):
    s = 0
    for i in range(n):
        s = g(i, s)
    return s
def loop_only(n):
    for i in range(n):
        pass

tests = [arith_small, arith_i32, mem_load, mem_store, calls, loop_only]
mode = sys.argv[-1] if len(sys.argv) > 1 else 'bc'
N = 1000000
base = None
for f in tests:
    t = now(); f(N); dt = now() - t
    print('%-12s %6.1f ns/iter' % (f.__name__ if hasattr(f, '__name__') else '?', dt / N * 1e9))
