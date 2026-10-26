# Roadmap

Phases in order; each ends with tests green for what it claims. Numbers are measured, never estimated
(BENCHMARKS.md).

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
* Cache and `Module.serialize`/`deserialize`.
* The generated-module ABI (ARCHITECTURE.md, "Versioned contracts"); regeneration from the wasm on
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
* LIR and the shared JIT core extracted from `w2jit2`; bounds-check elimination (measure against the
  ~20 % gap to Cranelift).
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
10. **Version skew**: self-contained modules, versioned helper namespaces or a declared module ABI;
   fat artifacts with the wasm embedded or not; how many older format versions a loader accepts
   (RISKS.md, R4).
