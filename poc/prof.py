import sys, cProfile, pstats, importlib.machinery, importlib.util, time
loader = importlib.machinery.SourcelessFileLoader('wmod', sys.argv[1])
spec = importlib.util.spec_from_loader('wmod', loader); m = importlib.util.module_from_spec(spec); loader.exec_module(m)
T0=time.perf_counter()
ex = m.instantiate({'wiring': {'millis': lambda: int((time.perf_counter()-T0)*1000)&0xFFFFFFFF, 'print': lambda p,n: None}})
cProfile.run("ex['setup']()", '/tmp/claude-0/p.out')
pstats.Stats('/tmp/claude-0/p.out').sort_stats('tottime').print_stats(12)
