"""Compile time of the PoC code generators on large modules.
python3 bench/compile/jit_compile_time.py a.wasm [b.wasm ...] [--pyc]
JIT (w2jit2): codegen only (no execution). Functions the PoC cannot compile yet are replaced by a trap
stub and counted, so the time is measured on the part it covers and extrapolated by body bytes."""
import sys, os, time, collections, traceback
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
from w2py import parse
import w2py
from poc.w2jit import Asm, TRAPS
from poc.w2jit2 import FuncJit2, JitModule2


class Probe(JitModule2):
    """JitModule2's code generation only, tolerant to unsupported functions."""
    def __init__(s, wasm):
        t0 = time.perf_counter()
        mod = s.mod = parse(wasm); s.types = mod['types']
        s.t_parse = time.perf_counter() - t0
        s.fimports = [im for im in mod['imports'] if im[0] == 'func']
        s.nimp = len(s.fimports); s.gtypes = [g[0] for g in mod['globals']]
        a = s.a = Asm(); nf = s.nimp + len(mod['codes'])
        s.flabel = {fi: a.label() for fi in range(s.nimp, nf)}
        s.trap_label = {k: a.label() for k in TRAPS}
        s.host_off = 32 + 8 * len(s.gtypes)
        for l in s.trap_label.values(): a.bind(l); a.emit(0xCC)
        s.ok = s.bad = s.ok_bytes = s.bad_bytes = 0; s.why = collections.Counter()
        t0 = time.perf_counter(); s.t_ok = 0.0
        for k, ((locs, body), tidx) in enumerate(zip(mod['codes'], mod['funcs'])):
            fi = s.nimp + k; nb, nfix = len(a.b), len(a.fix); t = time.perf_counter()
            try:
                s.FuncGen(s, a, fi, tidx, locs, body).gen()
                s.ok += 1; s.ok_bytes += len(body); s.t_ok += time.perf_counter() - t
            except Exception as e:
                del a.b[nb:]; del a.fix[nfix:]
                a.bind(s.flabel[fi]); a.jmp(next(iter(s.trap_label.values())))
                s.bad += 1; s.bad_bytes += len(body)
                tb = traceback.extract_tb(e.__traceback__)[-1]
                s.why[('%s: %s' % (type(e).__name__, str(e)[:40]), tb.name)] += 1
        a.resolve()
        s.t_gen = time.perf_counter() - t0
        s.code_size = len(a.b)


for path in [p for p in sys.argv[1:] if not p.startswith('--')]:
    data = open(path, 'rb').read()
    p = Probe(data)
    tot = p.ok_bytes + p.bad_bytes
    proj = p.t_ok / p.ok_bytes * tot if p.ok_bytes else float('nan')
    print('%-28s %6.0f KB wasm, %5d funcs | parse %.2fs | JIT ok %d/%d funcs (%.0f%% of body bytes) '
          'in %.2fs -> projected full %.2fs (%.0f KB/s), %d KB x86'
          % (os.path.basename(path), len(data) / 1024, p.ok + p.bad, p.t_parse, p.ok, p.ok + p.bad,
             100 * p.ok_bytes / tot, p.t_ok, proj, tot / 1024 / proj, p.code_size / 1024))
    for (w, where), n in p.why.most_common(4): print('      %4d x %s  [%s]' % (n, w, where))
    if '--pyc' in sys.argv:
        t = time.perf_counter()
        try:
            src = w2py.ModGen(parse(data), False).gen(); t1 = time.perf_counter() - t
            t = time.perf_counter(); compile(src, path, 'exec'); t2 = time.perf_counter() - t
            print('      pyc: translate %.2fs + compile() %.2fs, %.1f MB source' % (t1, t2, len(src) / 1e6))
        except Exception as e:
            print('      pyc: failed after %.2fs: %s: %s' % (time.perf_counter() - t, type(e).__name__, str(e)[:80]))
    sys.stdout.flush()
