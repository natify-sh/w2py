import micropython, struct
from time import ticks_us, ticks_diff
M = bytearray(1 << 16)
_uf = struct.unpack_from
for i in range(len(M)): M[i] = i & 255

@micropython.native
def ld_struct(n):
    s = 0
    for i in range(n):
        s = _uf('<I', M, (i & 1023) << 2)[0]
    return s

@micropython.native
def ld_bytes(n):
    m = M; s = 0
    for i in range(n):
        a = (i & 1023) << 2
        s = m[a] | (m[a + 1] << 8) | (m[a + 2] << 16) | (m[a + 3] << 24)
    return s

@micropython.viper
def ld_viper(n: int) -> int:
    p = ptr32(M); s = 0; i = 0
    while i < n:
        s = p[i & 1023]
        i += 1
    return s

N = 1000000
for nm, f in (('ld_struct', ld_struct), ('ld_bytes', ld_bytes), ('ld_viper', ld_viper)):
    t = ticks_us(); r = f(N); dt = ticks_diff(ticks_us(), t)
    print('%-10s %6.1f ns/load   (check %d)' % (nm, dt * 1000 / N, r & 0xFFFFFFFF))
