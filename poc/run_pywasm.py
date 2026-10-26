import sys, time, pywasm
from pywasm.core import FuncType, ValType
T0 = time.perf_counter()
rt = pywasm.core.Runtime()
mem = []
def millis(m, args): return [int((time.perf_counter()-T0)*1000) & 0xFFFFFFFF]
def pr(m, args):
    p, n = args; sys.stdout.write(bytes(mem[0].data[p:p+n]).decode(errors='replace')); return []
i32 = ValType(0x7f)
rt.imports['wiring'] = {'millis': rt.allocate_func_host(FuncType([], [i32]), millis),
                        'print': rt.allocate_func_host(FuncType([i32, i32], []), pr)}
mod = rt.instance_from_file(sys.argv[1])
mem.append(rt.exported_memory(mod, 'memory'))
t = time.perf_counter(); rt.invocate(mod, 'setup', [])
print(f"\n[pywasm] wall {time.perf_counter()-t:.2f}s")
