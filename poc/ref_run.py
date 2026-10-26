"""Reference runs of CoreMark wasm: wasmtime and pywasm3."""
import sys, time

PATH = sys.argv[1]
which = sys.argv[2]
T0 = time.perf_counter()


def millis():
    return int((time.perf_counter() - T0) * 1000) & 0xFFFFFFFF


if which == "wasmtime":
    import wasmtime as w
    store = w.Store()
    mod = w.Module.from_file(store.engine, PATH)
    mem_holder = []

    def pr(p, n):
        m = mem_holder[0]
        sys.stdout.write(bytes(m.read(store, p, p + n)).decode(errors="replace"))

    imps = []
    for i in mod.imports:
        f = {"millis": millis, "print": pr}[i.name]
        imps.append(w.Func(store, i.type, f))
    inst = w.Instance(store, mod, imps)
    mem_holder.append(inst.exports(store)["memory"])
    t = time.perf_counter()
    inst.exports(store)["setup"](store)
    print(f"\n[wasmtime] wall {time.perf_counter()-t:.2f}s")

elif which == "wasm3":
    import wasm3
    env = wasm3.Environment()
    rt = env.new_runtime(1 << 20)
    mod = env.parse_module(open(PATH, "rb").read())
    rt.load(mod)

    def pr(p, n):
        m = rt.get_memory(0)
        sys.stdout.write(bytes(m[p:p + n]).decode(errors="replace"))

    mod.link_function("wiring", "millis", "i()", millis)
    mod.link_function("wiring", "print", "v(ii)", pr)
    f = rt.find_function("setup")
    t = time.perf_counter()
    f()
    print(f"\n[wasm3] wall {time.perf_counter()-t:.2f}s")
