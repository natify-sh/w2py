import sys, time, importlib.machinery, importlib.util
t = time.perf_counter()
loader = importlib.machinery.SourceFileLoader('mnw', sys.argv[1])
m = importlib.util.module_from_spec(importlib.util.spec_from_loader('mnw', loader))
loader.exec_module(m)
ex = m.instantiate({})
print('load+compile %.1fs' % (time.perf_counter() - t), flush=True)
comp = open('meld-p2.wasm', 'rb').read()
t = time.perf_counter()
p = ex['meld_alloc'](len(comp)); m.M[p:p + len(comp)] = comp
rc = ex['meld_fuse'](p, len(comp))
op, ol = ex['meld_out_ptr'](), ex['meld_out_len']()
out = bytes(m.M[op:op + ol])
print('rc', rc, 'out', ol, 'bytes, fuse %.1fs' % (time.perf_counter() - t))
if rc: print(out[:400].decode(errors='replace'))
else:
    ref = open('fused_nowasi.wasm', 'rb').read()
    print('identical to the wasmtime run:', out == ref)
