---
title: Features
kind: reference
status: current
updated: 2026-10-30
---

# Features

The goal is everything Wasmtime supports for core WebAssembly. The component model is the
exception: components are supported **fused** into one core module (`meld fuse`), with WASI 0.2
provided by the WASI host (to decide; fusedwasi is the candidate); nothing of `wasmtime.component` is planned.

The checklist below is wasmtime-py 50.0.1's own list of `Config.wasm_*` switches (introspected), plus
the runtime features around them. Which of them Wasmtime enables by default changes between versions;
w2py follows the defaults of the wasmtime version pinned as the test oracle (TESTING.md).

## Proposals (`Config.wasm_*`)

"In Core 3.0" marks what the WebAssembly Core Specification 3.0 contains (ROADMAP.md, "Scope and order of
the wasm features"): all of it is in scope and is tested against the pinned spec suite; the rows marked
"no" are separate proposals tracked after it.

| switch | proposal | in Core 3.0 | `pyc` PoC | `jit-x86_64` PoC | notes for the implementation |
|---|---|---|---|---|---|
| — | MVP + mutable globals | yes | CoreMark subset (no spec run yet) | CoreMark subset | PoC gaps: validator, `call_indirect` signature check, block parameters; the JIT lacks `memory.grow` and full i64 |
| — | sign-extension ops | yes | yes | i32 forms | |
| — | non-trapping float-to-int | yes | yes | f64→i32 forms | |
| `wasm_multi_value` | multi-value | yes | results yes, block params no | no | |
| `wasm_bulk_memory` | bulk memory | yes | `memory.copy`/`fill` | no | passive segments, `memory.init`, `data.drop`, `table.*` missing |
| `wasm_reference_types` | reference types | yes | no | no | `funcref`/`externref`, multiple tables |
| `wasm_simd` | 128-bit SIMD | yes | no | no | `pyc`: v128 as 16 bytes / int, correct first; JIT: SSE2/NEON |
| `wasm_relaxed_simd` (+ `_deterministic`) | relaxed SIMD | yes | no | no | follow Wasmtime's deterministic choice when asked |
| `wasm_tail_call` | tail calls | yes | no | no | `pyc`: no TCO in Python → trampoline for `return_call*` |
| `wasm_multi_memory` | multi-memory | yes | no | no | **needed for fused components** |
| `wasm_memory64` | 64-bit memories/tables | yes | no | no | addresses are Python ints already in `pyc` |
| `wasm_custom_page_sizes` | custom page sizes | no | no | no | a property of the `Memory` facade |
| `wasm_threads` (+ `Config.shared_memory`) | threads and atomics | no | no | no | `SharedMemory`; `pyc` atomics under the GIL, care for free-threaded builds |
| `wasm_exceptions` | exception handling | yes | no | no | `Tag`, `try_table`, `throw`, `exnref` |
| `wasm_function_references` | typed function references | yes | no | no | `call_ref`, non-null types |
| `wasm_gc` (+ `Config.gc_support`) | garbage collection | yes | no | no | structs/arrays: Python objects in `pyc` are natural |
| `wasm_wide_arithmetic` | 128-bit arithmetic ops | no | no | no | easy in `pyc` (big ints) |
| `wasm_stack_switching` | stack switching | no | no | no | after everything else |
| `wasm_component_model*` | component model | no (out of scope) | — | — | out of scope (fused modules only) |
| extended constant expressions | `i32.add` etc. in const exprs | yes | no | — | frontend only |

## Runtime features

| feature | Wasmtime API | plan |
|---|---|---|
| interruption of long calls | (epochs; Ctrl+C in the CLI) | JIT: interrupt word polled by the code; `pyc`: yield points (ARCHITECTURE.md, "Long calls and the GIL") |
| fuel | `Config.consume_fuel`, `Store.set_fuel/get_fuel` | counters at entries and back-edges, compiled in on demand |
| epochs | `Config.epoch_interruption`, `Engine.increment_epoch`, `Store.set_epoch_deadline` | as fuel; checked against a context slot |
| limits | `Store.set_limits` | memory/table/instance limits in the runtime |
| stack limit | `Config.max_wasm_stack` | ARCHITECTURE.md, "Calls, stack depth and recursion" |
| serialization | `Module.serialize`, `Module.deserialize(_file)` | the cache artifact format |
| cache | `Config.cache` | ARCHITECTURE.md, "Cache" |
| WASI 0.1 | `Linker.define_wasi`, `WasiConfig` | the WASI host (candidate: fusedwasi `p1` twin) |
| WASI 0.2 (fused) | `Store.set_wasi`, `set_wasi_http` | the WASI host (candidate: fusedwasi `p2` twin); needs multi-memory |
| traps with frames | `Trap.frames`, `Frame` | `pyc`: from Python tracebacks; JIT: code-address map |
| NaN semantics | canonicalization option | `pyc` loses NaN payload bits through Python floats today; decide per spec tests |

## Per-target notes

* **`mpy` (MicroPython)** supports what its interpreter allows: no `SharedMemory`/threads in general,
  big-int i64 (slow on 32-bit MCUs), floats only on ports built with them. Unsupported features are
  reported by `Target.supports`, never mis-executed.
* **JIT targets** may lag the `pyc` target feature by feature; per-module fallback keeps every module
  runnable (TARGETS.md).
