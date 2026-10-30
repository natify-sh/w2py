# Decisions

Short records: what was decided, why, and what was rejected. Newest last.

### D1. No runtime dependencies
The runtime uses the Python standard library only. Native runtimes (wasmtime, wasm3, WAMR/iwasm)
were considered and rejected for the runtime: wasmtime is tens of MB, WAMR does not fit, and any
native piece breaks platforms like Pythonista. Development tools are free to use (D11).

### D2. Translate, do not interpret
Measured on CoreMark (CPython): pywasm (interpreter) 0.58 it/s, w2py translation 86, so ×150; on PyPy
×400, because a tracing JIT optimises straight code far better than an interpreter loop (warpy, an
RPython interpreter with a meta-tracing JIT: 192 it/s). Superinstructions were considered and do not
apply: the translation already has no dispatch left.

### D3. Python source as the `pyc` target's intermediate form, compiled by the host
The translator emits Python source and lets `compile()` make the bytecode. Hand-written bytecode would
be fragile across Python versions and gains nothing (CPython's compiler does not optimise, but neither
would we). The same source serves CPython, PyPy and MicroPython (with a flavour). Text format (WAT) as
input to the translator was rejected: the binary is stricter and simpler to parse; `.wat` may be
accepted later through a separate parser.

### D4. Speed on CPython comes from a JIT in Python, not from more Python
The ceiling of translated Python on CPython is ~1.5–2× the PoC (boxed ints, ~30–60 ns per operation,
`struct` memory access ~70–90 ns). A JIT written in Python, emitting machine code into executable
`mmap` memory and entering it through `ctypes` (both stdlib), reached 15 337 it/s (≈ 80 % of
wasmtime). Accepted with its costs: per-architecture work and platforms that forbid JIT, both handled
by D5.

### D5. Targets behind one interface, chosen automatically, falling back per module
`pyc` always; `mpy` on MicroPython; `jit-<arch>` where the architecture matches and executable memory
works. Architectures are added one by one; a module using a feature a JIT lacks runs on `pyc`.

### D6. Follow Wasmtime: model, API, features, traps
Engine/Store/Module/Linker/Instance and wasmtime-py's names; the `wasmtime` binary's CLI (wasmtime-py
50 has none); Wasmtime's feature switches and trap codes. Wasmtime is the specification of behaviour
wherever the wasm spec leaves room.

### D7. Component model: fused modules only
No component parser or canonical ABI in w2py. Components are fused with `meld` into one core module;
WASI 0.2 comes from the WASI host (D8). Requires multi-memory. p1 programs may go the same way through the
preview1 adapter.

### D8. WASI host: open
Not decided. The candidate is the Python twin of fusedwasi (natify-sh/fusedwasi): one function per
import, memories as buffers, the same code as the C headers used by wasmext. It has not been used with
w2py yet. Whatever is chosen, w2py only binds names and memories (`Linker.define_wasi`), and the
choice must not set the minimum Python of the package (fusedwasi's twin declares 3.10+).

### D9. Cache compiled artifacts
Keyed by the module hash, w2py version, target and target environment, and code-affecting config. The
same format is `Module.serialize`. Measured: `pyc` cold 111 ms → 0.5 ms cached. Serialized files and
fat files (embedding the wasm): D18.

### D10. Never crash the interpreter on deep recursion
Stack depth is bounded per store with a deterministic `STACK_OVERFLOW` trap; `sys.setrecursionlimit` is
not changed globally; `RecursionError` is a safety net only (ARCHITECTURE.md).

### D11. wasmtime is a development dependency: the test oracle
Differential tests run every target against a pinned wasmtime; wabt, wasm-tools, pywasm, meld,
MicroPython and QEMU are development tools too. None of them is a runtime dependency.

### D12. Dropped and parked
* **mypyc target** — dropped: needs `librt` at run time (a dependency) and a native build is better
  served by wasm2c/wasmext.
* **Lua / LuaJIT backends** — parked as an experiment proving the IR is language-neutral (LuaJIT
  1 873 it/s); not part of w2py.
* **`--trust-align` memoryview access** — rejected: no gain on CPython, ×20 slower on PyPy.
* **Function inlining in `pyc`** — opt-in only: neutral on CPython 3.13, ~+5 % on PyPy.
* **pywasm as the base** — rejected as a base (interpreter, ×150–400 slower, needs Python ≥ 3.11 — `match`, `typing.Self` — no
  MicroPython); used as a second oracle and a reference.

### D13. MicroPython where possible
A separate `mpy` target, best effort, ahead-of-time `.mpy` first; `@native` measured faster than
CPython; viper is the research direction.

### D14. Long calls: JIT releases the GIL and polls for interrupts; `pyc` needs yield points
Measured: a ctypes `CFUNCTYPE` entry releases the GIL (another thread ran throughout a 19.6 s guest
call) but a SIGINT cannot stop machine code (the call ran to its end). So JIT code polls an
interrupt/epoch word; `pyc` holds the GIL by nature and gets yield points (and, if parallelism is
needed, subinterpreters / free-threaded builds / a process). One thread per store at a time
(ARCHITECTURE.md, "Long calls and the GIL").

### D15. The JIT artifact is a language-neutral, versioned contract
Compiled code depends on nothing Python-specific (byte-identical output under CPython 3.8 and 3.13), so
its boundary is fixed and versioned in ARTIFACT.md: (1) **ISA levels** — portable artifacts use the
architecture's baseline (x86-64-v1, ARMv8.0-A) with baseline sequences for higher-level instructions;
`native` only for local caches; the loader checks the CPU; (2) **artifact format v1** — self-contained,
with entry trampolines for every calling convention of the architecture, function/trap tables,
segments; (3) **context ABI v1** — entry signature, context words, host-function signature, trap
codes. Hosts in other languages (C, Dart, LuaJIT, …) may load artifacts; a C reference loader keeps the
contract honest. *Narrowed by D18*: the contract is first of all the format of cache entries and
serialized files read by the w2py runtime; other-language loaders and the C loader are optional.

### D16. Our own thin IR; wasm is the portable IR; no LLVM
Wasm already is a typed, validated, producer-optimised IR, so no LLVM-class optimiser is needed (a
baseline JIT reached ~80 % of Cranelift). A thin IR of our own carries what wasm lacks: **HIR** (trees,
structured control flow, analyses) shared by all targets, and **LIR** (machine-neutral linear
operations) shared by all JIT architectures. Structured rather than a CFG (Python has no `goto`); no SSA
(little gain on optimised wasm, and it would have to be deconstructed for Python). Optimisations only
when measured to pay; bounds-check elimination first. LLVM, Cranelift IR and Binaryen IR were rejected
as runtime dependencies (LLVM stays a candidate build-time tool for copy-and-patch stencils).

### D17. The shared layers stay language-neutral; other languages are not in scope
HIR, LIR and the JIT artifact keep nothing Python-specific: the target's integer model is a parameter of
the analyses, backends hold everything language-specific, artifacts are loadable from any FFI-capable
language. Evidence that it works: the PoC's Lua 5.4 / LuaJIT backends reuse the front end. Only
Python-family targets (`pyc`, `mpy`, JITs loaded from Python) are in scope; the principle is cheap
insurance, not a commitment.

### D18. No embedded runtime: the Wasmtime model, not the wasmext model
w2py generates code when a module is loaded, so what it generates is a derivative owned by the engine,
as in Wasmtime — not a product that lives without the tool, as wasm2c + a C build is in wasmext (there
the output must carry its runtime). Consequences:

* **The runtime lives only in the package**: `Store`, memories, tables, globals, traps, WASI, the JIT
  entry and context. Generated code (a `pyc` code object, a JIT artifact) holds code and metadata and
  uses the runtime of its own w2py version. The PoC's `RUNTIME` string emitted into every module is a
  PoC shortcut, not the design.
* **Portable form: a serialized file** (`Module.serialize`, `w2py compile`), like Wasmtime's `.cwasm`;
  it runs only on a w2py runtime. Named `<name>.<target>.<ext>`, the target including what decides
  whether it can run there (`core.jit-x86_64-v1.w2a`, `core.pyc-cp313.w2a`; `.w2a` is a working
  extension until the project has a name). The header, not the file name, is authoritative.
* **Fat files**: optionally the file embeds the original wasm (ARTIFACT.md, section `WASM`). A fat file
  is never refused: on any mismatch — w2py or target version, bytecode magic, ISA level, a known-bad
  compiler version — the runtime regenerates from the embedded wasm (and may cache the result), so the
  file always runs, only slower the first time. A thin file is refused on a mismatch, as in Wasmtime.
* **Version skew (RISKS.md, R4) reduces to one rule**: accept an exact match, otherwise regenerate from
  the wasm (cache: the wasm is at hand; fat file: embedded) or refuse (thin file). No compatibility
  windows and no versioned helper namespaces; the interface between generated code and the runtime is
  internal and may change between any two versions.
* ARTIFACT.md stays the format of cache entries and serialized files; loaders in other languages and
  the C reference loader become optional (D15 narrowed).
* Possible exception, later and optional: a standalone export with minimal embedded helpers for
  MicroPython devices, where the runtime package costs flash.
