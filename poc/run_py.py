"""Load a translated .pyc and run CoreMark's `setup`."""
import sys, time, importlib.machinery, importlib.util

pyc = sys.argv[1]
loader = importlib.machinery.SourceFileLoader('wmod', pyc)
spec = importlib.util.spec_from_loader('wmod', loader)
m = importlib.util.module_from_spec(spec)
loader.exec_module(m)

T0 = time.perf_counter()


def millis():
    return int((time.perf_counter() - T0) * 1000) & 0xFFFFFFFF


def pr(p, n):
    sys.stdout.write(bytes(m.M[p:p + n]).decode(errors='replace'))


ex = m.instantiate({'wiring': {'millis': millis, 'print': pr}})
t = time.perf_counter()
ex['setup']()
print(f"\n[w2py .py] wall {time.perf_counter()-t:.2f}s")
