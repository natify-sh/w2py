import struct, timeit
M = bytearray(1<<20)
MV = memoryview(M).cast('I')
U = struct.Struct('<I').unpack_from
P = struct.Struct('<I').pack_into
fb = int.from_bytes
a = 4096; v=123456789
tests = {
 'idx8':        'M[a]',
 'from_bytes':  'fb(M[a:a+4],"little")',
 'struct':      'U(M,a)[0]',
 'bytes4':      'M[a]|M[a+1]<<8|M[a+2]<<16|M[a+3]<<24',
 'mv32':        'MV[a>>2]',
 'mv32chk':     'MV[a>>2] if not a&3 else U(M,a)[0]',
 'st_slice':    'M[a:a+4]=v.to_bytes(4,"little")',
 'st_struct':   'P(M,a,v)',
 'st_mv32':     'MV[a>>2]=v',
 'mask':        '(a+v)&0xFFFFFFFF',
 'add':         'a+v',
}
for k,s in tests.items():
    t = min(timeit.repeat(s, globals=globals(), number=2_000_000, repeat=3))
    print(f"{k:12s} {t/2e6*1e9:6.1f} ns")
