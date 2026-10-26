"""Run a w2lua-translated module (CoreMark) inside lupa: lua54 / luajit21."""
import sys, time, importlib
src_path, rt = sys.argv[1], sys.argv[2]
lupa = importlib.import_module('lupa.' + rt)
L = lupa.LuaRuntime(unpack_returned_tuples=True, encoding=None)
T0 = time.perf_counter()
mod = {}
def millis(): return int((time.perf_counter() - T0) * 1000) & 0xFFFFFFFF
def pr(p, n): sys.stdout.write(bytes(mod['ex'][b'memread'](p, n)).decode(errors='replace'))
imports = L.table_from({b'wiring': L.table_from({b'millis': millis, b'print': pr})})
inst = L.execute(open(src_path).read())
t = time.perf_counter(); mod['ex'] = inst(imports)
setup = mod['ex'][b'setup']
print(f"[{rt}] instantiate {time.perf_counter()-t:.2f}s")
t = time.perf_counter(); setup()
print(f"\n[{rt}] wall {time.perf_counter()-t:.2f}s")
