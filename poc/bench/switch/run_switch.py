"""br_table micro-benchmark: a bytecode VM with a 32-case switch (vm.c -> vm.wasm).
python3 bench/switch/run_switch.py [pyc|jit|jitg|jit1|wasmtime] [iters]"""
import sys, os, time, importlib.machinery, importlib.util
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..'))
WASM = os.path.join(HERE, 'vm.wasm')
mode = sys.argv[1] if len(sys.argv) > 1 else 'pyc'
iters = int(sys.argv[2]) if len(sys.argv) > 2 else 200

if mode == 'pyc':
    import w2py
    py = os.path.join(HERE, 'out_vm.py')
    w2py.translate(WASM, py)
    loader = importlib.machinery.SourceFileLoader('wvm', py)
    m = importlib.util.module_from_spec(importlib.util.spec_from_loader('wvm', loader))
    loader.exec_module(m)
    run = m.instantiate({})['run']
elif mode in ('jit', 'jit1', 'jitg'):
    if mode == 'jit': from w2jit2 import JitModule2 as JM
    elif mode == 'jitg': from w2jit_guard import JitModuleGuard as JM
    else: from w2jit import JitModule as JM
    jm = JM(open(WASM, 'rb').read(), {})
    run = lambda n: jm.call('run', n)
else:
    import wasmtime
    st = wasmtime.Store(); mod = wasmtime.Module.from_file(st.engine, WASM)
    inst = wasmtime.Instance(st, mod, [])
    f = inst.exports(st)['run']; run = lambda n: f(st, n) & 0xFFFFFFFF

run(1)
t = time.perf_counter(); r = run(iters); dt = time.perf_counter() - t
ops = iters * 256
print('%-8s result=%08x  %8.1f M ops/s  (%.3fs)' % (mode, r, ops / dt / 1e6, dt))
