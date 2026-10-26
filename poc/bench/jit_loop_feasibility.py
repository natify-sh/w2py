"""Feasibility: stdlib-only JIT on CPython (mmap + ctypes), x86-64 SysV.
Same loop as the earlier microbenchmark: l = (l + 12345) & 0xFFFFFFFF, n times, plus one i32 load per iteration."""
import ctypes, mmap, struct, time

# uint32_t f(uint32_t n /*edi*/, uint8_t *mem /*rsi*/)
#   eax = 1000; ecx = 0
# loop: eax += 12345 ; edx = [rsi + ((ecx & 1023) << 2)] ; eax ^= edx ; ecx += 1 ; if ecx != edi goto loop
def asm(parts):
    """parts: bytes or ('jz'|'jne', label) or ('label', name); rel8 jumps resolved in two passes"""
    pos = {}; out = b''; n = 0
    for p in parts:
        if isinstance(p, tuple) and p[0] == 'label': pos[p[1]] = n
        elif isinstance(p, tuple): n += 2
        else: n += len(p)
    n = 0
    for p in parts:
        if isinstance(p, tuple) and p[0] == 'label': continue
        if isinstance(p, tuple):
            op = {'jz': 0x74, 'jne': 0x75}[p[0]]; rel = pos[p[1]] - (n + 2)
            out += bytes([op, rel & 0xFF]); n += 2
        else:
            out += p; n += len(p)
    return out

code = asm([
    b'\xB8' + struct.pack('<I', 1000),          # mov eax, 1000
    b'\x31\xC9',                                # xor ecx, ecx
    b'\x85\xFF',                                # test edi, edi
    ('jz', 'done'),
    ('label', 'loop'),
    b'\x05' + struct.pack('<I', 12345),         # add eax, 12345   (i32 wrap is free)
    b'\x89\xCA',                                # mov edx, ecx
    b'\x81\xE2' + struct.pack('<I', 1023),     # and edx, 1023
    b'\x8B\x14\x96',                            # mov edx, [rsi + rdx*4]   (i32.load)
    b'\x31\xD0',                                # xor eax, edx
    b'\xFF\xC1',                                # inc ecx
    b'\x39\xF9',                                # cmp ecx, edi
    ('jne', 'loop'),
    ('label', 'done'),
    b'\xC3',                                    # ret
])

buf = mmap.mmap(-1, mmap.PAGESIZE, prot=mmap.PROT_READ | mmap.PROT_WRITE | mmap.PROT_EXEC)
buf.write(code)
addr = ctypes.addressof(ctypes.c_char.from_buffer(buf))
fn = ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p)(addr)

M = bytearray(1 << 16)
for i in range(0, len(M), 4): struct.pack_into('<I', M, i, i * 2654435761 & 0xFFFFFFFF)
mem_ptr = ctypes.addressof((ctypes.c_char * len(M)).from_buffer(M))

def py(n):
    l = 1000; U = struct.Struct('<I').unpack_from
    for i in range(n):
        l = ((l + 12345) & 0xFFFFFFFF) ^ U(M, (i & 1023) << 2)[0]
    return l

N = 2_000_000
t = time.perf_counter(); r1 = py(N); tp = time.perf_counter() - t
t = time.perf_counter(); r2 = fn(N, mem_ptr); tj = time.perf_counter() - t
print('same result:', r1 == r2, hex(r1))
print('CPython   : %7.2f ns/iter' % (tp / N * 1e9))
print('mmap JIT  : %7.2f ns/iter   -> x%.0f' % (tj / N * 1e9, tp / tj))
t = time.perf_counter()
for _ in range(200_000): fn(0, mem_ptr)
print('ctypes call overhead: %.0f ns/call' % ((time.perf_counter() - t) / 200_000 * 1e9))
