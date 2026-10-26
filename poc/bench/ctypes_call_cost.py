import ctypes, mmap, time, sys
# int f(int a) { return a; }  x86-64 SysV: mov eax, edi ; ret
buf = mmap.mmap(-1, mmap.PAGESIZE, prot=mmap.PROT_READ | mmap.PROT_WRITE | mmap.PROT_EXEC)
buf.write(b'\x89\xF8\xC3')
addr = ctypes.addressof(ctypes.c_char.from_buffer(buf))
variants = {
    'CFUNCTYPE (releases GIL)': ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int)(addr),
    'PYFUNCTYPE (keeps GIL)':   ctypes.PYFUNCTYPE(ctypes.c_int, ctypes.c_int)(addr),
}
def pyfn(a): return a
N = 300_000
t = time.perf_counter()
for i in range(N): pyfn(i)
base = (time.perf_counter() - t) / N * 1e9
print('%-28s %6.0f ns' % ('plain Python call', base))
for k, f in variants.items():
    t = time.perf_counter()
    for i in range(N): f(i)
    print('%-28s %6.0f ns' % (k, (time.perf_counter() - t) / N * 1e9))
