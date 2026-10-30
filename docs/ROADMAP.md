# Roadmap

Phases in order; each ends with tests green for what it claims. Numbers are measured, never estimated
(BENCHMARKS.md).

## Where to start

The first goal is not a feature but a **measurement**: every later change should move a number.

1. **Skeleton** (phase 0): the repository under natify-sh with the working name `w2py` (renaming later is
   cheap); `pyproject.toml` with no runtime dependencies and a development group — `wasmtime` (the
   oracle), `pytest`, `wabt-package` (for `wast2json`); CI on CPython 3.8 and the latest, plus PyPy, on
   Linux x86-64. Minimum Python 3.8 for now (open question 1).
2. **The spec-suite runner** (phase 1, the most important step): convert `WebAssembly/spec` at a pinned
   commit with `wast2json`; a runner for the commands (`module`, `assert_return`, `assert_trap`,
   `assert_invalid`, …) over a thin interface — load a module, call an export, read a global.
   **Run it against wasmtime first**: this tests the runner itself, and the oracle should pass nearly
   everything.
3. **Port the PoC front end** into `frontend/` unchanged (reader and decoder from `poc/w2py.py`), connect
   the PoC's `pyc` code generator as it is, and record the **first number**: how many spec commands
   pass. The per-target expected-failures file (TESTING.md) is the task list from then on.
4. **Loop**: take the largest group of failures, fix it, watch the number grow. The validator, block
   parameters, tables, trap conversion come in the order the failures dictate.

Why this order: the spec suite plus the oracle give an objective metric from day one; `pyc` is the
simplest target and the one every other must match, so semantics are pinned there; the JIT (phase 4)
then only reproduces checked semantics faster, and the same runner shows at once where it deviates.
The open questions below do not block this start — the name, the WASI host and parallelism are needed
only in phases 2–3.

## Order of the wasm features

Dictated by the modules that exist in practice, and by what fused WASI 0.2 needs:

1. **MVP + Wasm 2.0**: mutable globals, sign-extension, non-trapping float-to-int, multi-value (block
   parameters too), bulk memory, reference types, SIMD — what clang, rustc and zig emit by default.
2. **multi-memory** — without it there is no fused WASI 0.2.
3. The runtime API and **WASI 0.1**, then **fused WASI 0.2**.
4. **memory64**, **tail-call**, **extended-const**.
5. **exception handling**.
6. **function references** and **GC** — needed by Kotlin, Dart, OCaml, Java-to-wasm; less by C/Rust.
7. **relaxed SIMD**, **wide arithmetic**.
8. **threads** (shared memory, atomics), **custom page sizes** (Wasmtime Tier 2).

Tier 3 proposals (branch-hinting, stack-switching, compact-imports) after all of these. Sources and
counters: TESTING.md, "Sources of truth and the completeness counters".

## Exit criteria in numbers

| phase | counters (TESTING.md) |
|---|---|
| 1 | `assert_invalid` / `assert_malformed` of the pinned spec suite pass (the validator) |
| 2 | spec suite for features 1–2 above passes on `pyc` (CPython and PyPy); the rest are listed expected failures |
| 3 | wasmtime-py's own tests pass through the shim, except the listed deviations; the WASI testsuite (p1) passes; the CLI accepts the scraped option list |
| 4 | the same spec commands pass on `jit-x86_64` as on `pyc` for the stages it claims; differential fuzzing finds no divergence over an agreed number of hours |
| later | each new proposal enters with its spec tests green on every target that claims it |

## Phase 0 — repository from the PoC
* Package layout of ARCHITECTURE.md; move the PoC's reader, decoder and stack-to-tree builder out of
  `w2py.py` into `frontend/` and `ir/`; the code generators become `targets/pyc` and `targets/jit/x86_64`.
* `pyproject.toml` with **no** runtime dependencies and a development group (wasmtime pinned as the
  oracle, pywasm, pytest; wabt / wasm-tools / meld as external tools).
* CI skeleton: CPython 3.8–latest + PyPy on Linux x86-64.

## Phase 1 — frontend complete
* Full binary reader for every proposal Wasmtime supports (FEATURES.md); the validator (type checking
  of function bodies); constant expressions (extended const).
* Spec-suite harness over the public API (TESTING.md) running `assert_invalid` / `assert_malformed`.
* HIR (ARCHITECTURE.md, "The IR"): trees, structured control, the analyses (bounds/wrap, local
  weights, effect order, trap points, call graph); documented in code.

## Phase 2 — `pyc` target complete
* All core features of FEATURES.md, SIMD correct before fast; NaN rules settled against the oracle.
* Trap conversion at the boundary; stack-depth counter and `STACK_OVERFLOW`; re-entrancy.
* Long calls: yield points + interrupt word (measure the cost); one-thread-per-store guard.
* Fuel and epochs (compiled in on demand).
* Cache and `Module.serialize`/`deserialize`: thin and fat files (DECISIONS.md D18).
* No embedded runtime: generated code uses the package's runtime (D18); the PoC's emitted `RUNTIME`
  goes away. Regeneration from the wasm on
  any mismatch.
* Exit criterion: the spec suite passes on CPython and PyPy.

## Phase 3 — runtime API, WASI, CLI
* wasmtime-py–compatible `Engine`, `Config`, `Store`, `Module`, `Linker`, `Instance`, externs, `Caller`,
  `Trap`/`TrapCode`, limits.
* Choose the WASI host (open question 2); WASI 0.1 and fused 0.2 through it (needs multi-memory from
  phase 2).
* CLI: `run`, `compile`, `wast`, `settings`/`targets`, `cache`.
* Differential tests against wasmtime on real programs.

## Phase 4 — `jit-x86_64` complete
* LIR and the shared JIT core extracted from `w2jit2`; **guard pages with a JIT-emitted signal
  handler** (`poc/w2jit_guard.py`: +20–25 % on CoreMark, the gap to Cranelift was mostly the bounds
  checks) per OS (Linux/BSD `sigaction` + `ucontext` offsets per architecture, macOS also SIGBUS,
  Windows a vectored exception handler), `sigaltstack` per thread for stack overflow; explicit checks
  as the fallback; then bounds-check elimination only where guard pages are not available.
* The full opcode set in stages (TARGETS.md), `memory.grow` on a reserved range,
  `call_indirect` and tables, nested entries, host exceptions across the boundary, trap frames,
  fuel/epoch checks, the interrupt word polled at back-edges (Ctrl+C via the waiting main thread),
  artifact serialization.
* The contract of ARTIFACT.md: format v1, context ABI v1 (re-entrant entry, `HOST_ERROR`, interrupt
  word), baseline-ISA sequences (no SSE4.1 at x86-64-v1), CPU feature detection, a C reference loader.
* System V first, then Win64 (a second entry trampoline in the same artifact).
* Executable-memory probe and per-module fallback to `pyc`.

## Phase 5 — `jit-aarch64`
* Linux, macOS (`MAP_JIT` + `pthread_jit_write_protect_np` via ctypes), Windows on ARM.
* Proves the shared JIT core; after it, decide on copy-and-patch for further architectures with
  numbers for both approaches.

## Phase 6 — `mpy` target
* `.mpy` ahead of time and on-device source; `@native` where it helps; avoid the `mpy-cross` large
  function bug (LESSONS.md 17).
* Research: lower HIR to `@micropython.viper` code (machine-word ints, `ptr8/16/32` memory) so that
  MicroPython's own emitters become native back ends on every MCU architecture its ports support
  (Thumb, Xtensa, RISC-V, …) without an encoder of ours; inline assemblers (`asm_thumb`, `asm_xtensa`)
  for the hottest functions (TARGETS.md, "MicroPython's own native emitters as JIT back ends").
* Port the translator to run on MicroPython if on-device compilation is wanted.

## Phase 7 — more architectures
* riscv64, armv7, mips32el (Ingenic X2000), ppc64le, loongarch64 — by hand or by copy-and-patch, CI
  under QEMU user-mode.

## Open questions
1. **Minimum Python.** Generated `pyc` code runs on 3.8 (3.4 with `--legacy`); the JIT needs only
   `ctypes` + `mmap` (measured on 3.8 and 3.13). The floor should follow the targets, not the WASI host.
2. **The WASI host.** fusedwasi's Python twin (candidate, not tried with w2py; declares Python 3.10+),
   an own implementation, or another; and what WASI means on MicroPython (open question 6).
3. **Parallelism of `pyc` guests**: yield points only, or also subinterpreters (3.12+/3.14),
   free-threaded builds, a process with shared memory (ARCHITECTURE.md, "Long calls and the GIL")?
4. **Wasmtime options with no meaning here** (`cranelift_*`, `profiler`, `signals_based_traps`, …):
   accept and ignore, or reject?
5. **NaN payloads in `pyc`**: Python floats keep the payload in most paths but not all (f32 rounding
   through `struct`); follow the spec's canonical/arithmetic NaN rules only, or carry bit patterns?
6. **WASI on MicroPython**: a slim twin, or fusedwasi compiled to wasm and translated by w2py itself
   (then each language/target needs only a thin layer of OS primitives)?
7. **Pyodide**: a target calling the browser's `WebAssembly` through `js`, or `pyc` only?
8. **Free-threaded CPython (no GIL)**: shared memories and atomics in `pyc` need real synchronisation.
9. **Name**: `w2py` grew beyond "wasm to Python" — keep it, or rename the runtime and keep `w2py` for
   the translator?
10. **Version skew** — settled by D18: no embedded runtime, exact match or regenerate; fat files carry
   the wasm. Left: the known-bad compiler list (where it lives, how it is updated).
11. **Components on the fly**: accept a component and fuse it at load time (then compile and cache the
   core module by the component's hash) by running **meld compiled to wasm on w2py itself**, shipped
   as a data file in the wheel (Apache-2.0): no native dependency and no second fuser to keep in sync.
   **Checked (meld 0.66.0, BENCHMARKS.md):** meld builds without patches for `wasm32-wasip1` (CLI,
   2.7 MB, 16 WASI p1 imports), `wasm32-wasip2` (component, 2.9 MB) and — best for this use —
   **meld-core alone for `wasm32-unknown-unknown`** behind a ~40-line wrapper (1.7 MB, **no imports at
   all**: `meld_alloc` / `meld_fuse` / `meld_out_ptr` / `meld_out_len`; attestation set to
   `reproducible`, there is no wall clock without WASI). It uses only MVP + sign-ext + `memory.copy` /
   `memory.fill` (no SIMD, no multi-value, no `trunc_sat`), and **the PoC's `pyc` already runs it**:
   meld fusing its own p2 build gives output byte-identical to the same run on wasmtime, in 44 s on
   PyPy and 253 s on CPython (wasmtime 1.05 s) — acceptable once per component with the cache, and the
   JIT will cut it. Still open: its coverage (resources, string transcoding, p3 async). Exports remain
   core functions; Python values need our own canonical-ABI lift at the boundary.
12. **Targets in one Store**: one shared representation of memories, tables and globals with
   per-module fallback, or one target per Store (ARCHITECTURE.md, "State shared across targets")?
   Measure `pyc` on a reserved mapping against a `bytearray` first.
13. **Lazy compilation**: compile each function on its first call (JIT: a stub that compiles and
   patches; `pyc`: translate and `exec` per function) so the first load costs what is used, not the
   whole module (QuickJS: ~1.5 s JIT / ~5.7 s `pyc` for everything; BENCHMARKS.md, "Compile time on
   large modules"). Costs: a patchable call path, compilation in the middle of a call (re-entrancy,
   the GIL, executable-memory growth). Compare with eager compilation + cache before deciding.
