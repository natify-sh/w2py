# Read this first

This page is the briefing for anyone (person or agent) who starts work on this repository. Everything
else under `docs/` is reference material: read it when a task needs it, not before.

## What this project is, in one paragraph

**A WebAssembly compiler written in pure Python that makes fast machine code at run time, with no
native dependency.** Python can map executable memory (`mmap`) and call into it (`ctypes`), so a
compiler written in Python can emit x86-64 (later AArch64, RISC-V, …) bytes for a wasm module and run
them at native speed. The proof of concept already runs CoreMark at **86 % of Wasmtime/Cranelift with
explicit bounds checks and ~91 % with guard pages**, from a `pip install` that contains only `.py`
files. Where machine code is not allowed or no backend exists yet, the same front end translates wasm
to Python source (`pyc`), or to MicroPython code (`mpy`). Around the compiler sits a **thin runtime**
(memories, tables, instances, traps, host calls, WASI) — necessary, but not the point.

## Why it exists (the gap it fills)

| option | speed | what it costs the user |
|---|---|---|
| Wasmtime (wasmtime-py) | 100 % | a 30 MB native library per platform |
| wasm2c + a C build (natify-sh/wasmext) | ~95 % of native | a C toolchain and a build **per module per platform** |
| WAMR AOT | ~120 % | `wamrc` (LLVM) per module per platform, a native runtime, `.aot` tied to the WAMR version; modules cannot share memories or tables |
| wasm3 | ~13 % | a native extension |
| pywasm (pure-Python interpreter) | ~0.003 % | nothing |
| **this project** | **86–91 %** (JIT), ~0.8 % (`pyc` on CPython) | **nothing**: standard library only |

Measured numbers and conditions: `docs/BENCHMARKS.md`. The niche: run wasm from Python fast,
anywhere Python runs, without anyone building anything native.

## What it is NOT (read this twice)

* **Not "a reimplementation of wasmtime-py".** Wasmtime appears in the documents in two roles only:
  1. **the test oracle** — when the spec leaves room, and in differential tests, we behave as Wasmtime
     does (trap codes, NaN rules, limits);
  2. **the shape of the public Python API, later** (Engine/Store/Module/Linker/Instance), so users
     recognise it. Building that API is phase 6. **Do not start there.** API parity is not a goal by
     itself and is never worked on before the compiler and the spec tests need it.
* **Not an interpreter.** Every target translates the whole module ahead of execution (at load time).
* **Not a runtime that is embedded into generated code.** The runtime lives in the package; generated
  code is a cache artifact of it (DECISIONS.md D18).
* **Not the component model.** Components are fused into one core module beforehand (`meld fuse`).

## The architecture in five lines

```
wasm bytes -> front end (parse, validate, decode) -> HIR (trees + structured control, analyses)
   -> pyc target: Python source -> code object            (always available; the semantic reference)
   -> mpy target: MicroPython source / viper              (MicroPython)
   -> LIR (machine-neutral linear ops) -> jit-<arch>: machine code in mmap, entered through ctypes
runtime (package): memories, tables, globals, instances, traps, host calls, WASI host, cache
```

Details: `docs/ARCHITECTURE.md` ("The IR", "Memory", "Long calls and the GIL"), `docs/TARGETS.md`.

## What the PoC proves (and what it does not)

`poc/` is a set of scripts, not the package. It proves feasibility and gives the numbers; it covers
only what its test programs needed.

* `poc/w2py.py` — front end + `pyc` translator. Runs CoreMark on CPython (~150 it/s), PyPy (~3 000),
  MicroPython (~90); also ran **meld** (a 1.7 MB Rust program) byte-identically to Wasmtime.
* `poc/w2jit2.py` — the x86-64 JIT (pinned locals in registers, lazy value stack, fused compare+branch,
  jump tables for `br_table`, `if`/`else`): CoreMark ~16 800 it/s.
* `poc/w2jit_guard.py` — the same JIT with **guard pages**: an 8 GiB reservation plus a SIGSEGV handler
  that the JIT emits as machine code (no Python in the signal path): ~19 000 it/s.
* Compile speed of the JIT in Python: ~0.6–0.8 MB of wasm code per second (Cranelift on 2 cores is
  1.5–2.5× faster) — so a cache and, later, lazy per-function compilation matter.
* Gaps the PoC does not cover: the validator, most of the opcode set in the JIT (`call_indirect`,
  full i64, floats beyond CoreMark, bulk memory, `memory.grow`), block parameters, reference types,
  SIMD, the Wasm 3.0 features (GC, exceptions, tail calls, memory64, multi-memory, …); the runtime
  objects; WASI.

## Where to start (in this order)

1. **Read** this page, then `docs/BENCHMARKS.md` (the numbers), then `docs/ARCHITECTURE.md`. Skip
   `docs/API.md` and `docs/FEATURES.md` until phase 6.
2. **Run the PoC** to see it work (Linux x86-64, any CPython ≥ 3.8):
   ```
   cd poc
   python3 run_jit2.py in/core.wasm                 # the JIT
   W2PY_GUARD=1 python3 run_jit_guard.py in/core.wasm   # the JIT with guard pages
   python3 w2py.py in/core.wasm out/core.py && python3 run_pyc.py out/core.pyc   # pyc
   python3 ref_run.py in/core.wasm wasmtime         # the reference (pip install wasmtime)
   ```
3. **Skeleton** (`docs/ROADMAP.md`, phase 0): package layout, `pyproject.toml` with **no runtime
   dependencies** (wasmtime, pytest, wabt-package only in the dev group), CI on Linux x86-64.
4. **The spec-suite runner — the metric.** `WebAssembly/spec` `test/core` at a pinned commit, converted
   with `wast2json`, executed through a **tiny internal engine interface** — `load(bytes)`,
   `instantiate(module, imports)`, `call(instance, name, args)`, `get_global(instance, name)` — not
   through a wasmtime-like API. Plug Wasmtime behind the same tiny interface to check the runner itself.
5. **Port the PoC front end and `pyc`** behind that interface; record the first number (spec commands
   passing). From then on the work is a loop: take the largest group of failures, fix it, the number
   grows. `pyc` is the semantic floor every other target must match.
6. **The JIT, integer core first** (`jit-x86_64`, ROADMAP phase 3) behind the same interface, compared
   command by command with `pyc` and Wasmtime, with explicit bounds checks. First decide and record
   the shape of the shared JIT core (LIR with an allocator, or one pass over HIR into a small
   `MacroAssembler`-like backend interface), then split `w2jit2` into the shared core and the x86-64
   backend.
7. **The second architecture's integer core** (aarch64 or riscv64 under QEMU, phase 4) **before** the
   opcode set is widened: it is what proves the split, and the interface is frozen after it. Only then
   the rest of the opcodes, guard pages and the artifact contract on all JIT targets (phase 5).
8. **Only then** the public runtime API in the shape of wasmtime-py, WASI (fusedwasi is the candidate
   host), the cache and serialized files (thin/fat), the CLI.

Every speed claim goes into `docs/BENCHMARKS.md` with its conditions; every behaviour claim comes with
a test. "Measure, do not estimate."

## Hard rules (short form; full list in `CLAUDE.md`)

* Package imports nothing outside the standard library.
* Never crash the interpreter: bad accesses, deep recursion, host exceptions become traps/exceptions.
* One target interface, one test suite for all targets.
* The JIT artifact format and context ABI are versioned contracts (`docs/ARTIFACT.md`).
* The shared layers (front end, HIR, LIR) contain nothing Python-specific.
* Read how others did a hard thing (`docs/REFERENCES.md`: Winch, V8 Liftoff, wasm2c, TinyCC, …) and
  write our own; never translate their code.

## Map of the documents

| when you need | read |
|---|---|
| the numbers | `docs/BENCHMARKS.md` |
| how it is built | `docs/ARCHITECTURE.md`, `docs/TARGETS.md` |
| the order of work | `docs/ROADMAP.md` |
| how it is tested | `docs/TESTING.md` |
| why a choice was made | `docs/DECISIONS.md` (D1–D18) |
| what went wrong before | `docs/LESSONS.md` |
| risks and possible answers | `docs/RISKS.md` |
| the JIT artifact contract | `docs/ARTIFACT.md` |
| the public API and CLI (phase 6) | `docs/API.md`, `docs/FEATURES.md` |
| how others did it | `docs/REFERENCES.md` |

The maintainer writes in Ukrainian; code, comments, commits and documents are in English. The name
`w2py` is a working name.
