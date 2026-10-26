"""Run CoreMark from a translated module importable by name (e.g. a mypyc-built extension)."""
import sys, time, importlib
sys.path.insert(0, sys.argv[1])
m = importlib.import_module(sys.argv[2])
T0 = time.perf_counter()
def millis(): return int((time.perf_counter() - T0) * 1000) & 0xFFFFFFFF
def pr(p, n): sys.stdout.write((m.mem_read(p, n) if hasattr(m, 'mem_read') else bytes(m.M[p:p + n])).decode(errors='replace'))
ex = m.instantiate({'wiring': {'millis': millis, 'print': pr}})
t = time.perf_counter(); ex['setup']()
print(f"\n[{sys.argv[2]}] wall {time.perf_counter()-t:.2f}s")
