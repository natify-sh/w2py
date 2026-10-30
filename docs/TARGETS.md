---
title: Targets
kind: reference
status: current
updated: 2026-10-30
---

# Targets

A target turns the IR of a module into something the host can run, and runs it. Targets are added one
at a time; each one passes the same tests before it is selectable.

## The interface

A sketch (names to settle during phase 1); everything a target needs comes in, nothing global:

```python
class Target(Protocol):
    name: str                      # "pyc", "mpy", "jit-x86_64-sysv", "jit-aarch64", ...
    version: int                   # bumps invalidate cached artifacts

    def probe(self, config: Config) -> Probe:
        """Can this target run here? (interpreter, architecture, OS, executable memory, CPU
        features). Returns ok/why-not and the environment string that goes into cache keys."""

    def supports(self, module: ModuleIR, config: Config) -> Support:
        """Can it compile *this* module? (features it uses: SIMD, memory64, threads, exceptions,
        GC, ...). A target may support a subset; selection falls back per module."""

    def compile(self, module: ModuleIR, config: Config) -> Artifact: ...
    def serialize(self, artifact: Artifact) -> bytes: ...
    def deserialize(self, data: bytes) -> Artifact: ...

    def instantiate(self, artifact: Artifact, store: Store, imports: ResolvedImports) -> TargetInstance:
        """Create memories/tables/globals (through the runtime's facades), apply segments,
        bind imports, run start; return the callable exports."""
```

Contracts every target keeps:

* **Semantics are the spec's**, checked by the spec test suite (TESTING.md), including NaN and trap
  behaviour; traps are reported with Wasmtime's `TrapCode`.
* **Memory goes through the runtime facade** (`Memory`), so hosts and WASI are target-independent.
* **Calls from Python** take and return Python values as wasmtime-py does (`int` for i32/i64 with
  Wasmtime's signedness conventions, `float`, `None`/objects for references).
* **Host functions** are plain Python callables (receiving a `Caller` when asked for one); their
  exceptions propagate to the caller of the wasm export.
* **Stack depth** is bounded as ARCHITECTURE.md describes; no target may crash the interpreter.
* **Artifacts are self-describing** (target name, version, environment) so a wrong one is refused,
  never executed. JIT artifacts follow [ARTIFACT.md](ARTIFACT.md): declared ISA level, format
  version 1, context ABI version 1.

## Selection

`Config.target` is `"auto"` (default) or a name or a list of names in order of preference.

For `"auto"`, the engine probes in this order and takes the first that is ok:

1. the JIT for this architecture and ABI (`platform.machine()`, `sys.platform`, pointer size), if
   executable memory can be obtained (probe: map RW, write `ret`, flip to RX — `mprotect`, `MAP_JIT`
   + `pthread_jit_write_protect_np` on macOS arm64, `VirtualAlloc`/`VirtualProtect` on Windows — and
   call it once through ctypes), and if the CPU reaches the ISA level the code will be compiled for
   (ARTIFACT.md, part 1);
2. `mpy` on MicroPython;
3. `pyc` (always available).

Platforms that restrict run-time code (and CPU control-flow integrity: CET, BTI) are listed with
possible answers in RISKS.md, R2.

Per module: if the chosen target does not support a feature the module uses, the next target that
does is taken (and the choice is logged), so adding a JIT never makes a module stop working.
`W2PY_TARGET` (environment) overrides for testing.

## Adding an architecture step by step

The JIT is split into a shared core and per-architecture parts, so an architecture can start small and
grow:

| shared (`jit/common`) | per architecture (`jit/<arch>`) |
|---|---|
| executable memory per OS | instruction encoder |
| ctypes entry trampoline protocol, host callbacks | ABI: argument registers, callee-saved set, stack alignment |
| value-stack frame layout (frame overlap) | register pools (pinned locals, scratch) |
| lowering of HIR to LIR: a machine-neutral sequence of operations with operand kinds (imm / reg / mem) (ARCHITECTURE.md, "The IR") | instruction selection for LIR operations |
| pinned-local choice (uses weighted by loop depth), top-of-stack caching | lowering of memory access (bounds check + addressing modes) |
| labels, fixups, trap stubs, function table, artifact format | relocation/fixup kinds of the ISA |

How others solved the same problems on each architecture (Winch, V8 Liftoff, TinyCC, asmjit, DynASM,
MicroPython's emitters): REFERENCES.md — read to understand, then write our own.

An architecture is added in stages, each one usable because of per-module fallback:

1. **Integer core**: i32/i64, locals, globals, control flow, calls, memory loads/stores with bounds
   checks, traps. (What `w2jit2` covers on x86-64, minus i64 arithmetic.)
2. **Floats** (f32/f64 incl. NaN rules and conversions), `call_indirect` and tables, `memory.grow`,
   bulk memory, reference types, multi-value, multi-memory, memory64.
3. **SIMD** (and relaxed SIMD), then threads/atomics, exceptions, tail calls, GC/function references.

A module using a stage the architecture does not have yet runs on `pyc` meanwhile.

**An alternative to evaluate for later architectures: copy-and-patch.** Each wasm operation becomes a
small C "stencil" compiled by clang for every target triple on *our* CI and shipped as bytes in the
pure-Python wheel; at run time Python only stitches stencils and patches holes (relocations). Per
architecture, the Python side is then only the relocation patcher (~100–200 lines) and executable
memory; SIMD stencils written with clang's generic vector types become SSE / NEON / scalar code by
themselves. CPython's own JIT works this way. Costs: clang (pinned version) in our CI, relocation
formats tied to LLVM versions, `musttail`/`preserve_none` not available on every architecture (the
fallback is threaded code returning to a dispatcher). Decide after `jit-aarch64` exists by hand,
with real numbers for both.

## The targets planned

| target | runs on | state in the PoC | notes |
|---|---|---|---|
| `pyc` | CPython ≥ 3.11 (D19; PoC measured down to 3.8), PyPy | CoreMark | floor for everything; artifact = code object; the fastest pure-Python option on PyPy (2 300–2 900 it/s) |
| `mpy` | MicroPython (unix port tested, 1.29.0) | CoreMark | `.mpy` ahead of time or source on device; `@native` (94 it/s); next: viper code, i.e. MicroPython's own emitters as native back ends for every MCU architecture (below) |
| `jit-x86_64-sysv` | Linux, macOS (Intel), BSDs | CoreMark (`w2jit2`) | 15 337 it/s; needs the rest of the opcode set, `memory.grow`, nested entry |
| `jit-x86_64-win64` | Windows | — | other argument registers, shadow space, `VirtualAlloc` |
| `jit-aarch64` | Linux, macOS (Apple Silicon: `MAP_JIT`), Windows on ARM, Android (Termux) | — | the second architecture (or `jit-riscv64`, ROADMAP phase 4); its integer core proves the split before the opcode set is widened |
| `jit-riscv64`, `jit-armv7`, `jit-mips32` (Ingenic X2000, little-endian), `jit-ppc64le`, `jit-loongarch64` | as available | — | QEMU user-mode in CI; candidates for copy-and-patch |
| `lua` / `luajit` | Lua 5.4, LuaJIT | CoreMark | **not a w2py target**: an experiment showing the IR serves other languages (271 / 1 873 it/s); the shared layers stay language-neutral so such backends remain possible (DECISIONS.md D17) |
| `mypyc` | CPython + mypyc runtime | CoreMark | **dropped**: needs `librt` at run time (a dependency) and is 9× slower than wasm2c for a native build |

**MicroPython's own native emitters as JIT back ends (research).** Our JIT is the same idea as
MicroPython's `@micropython.viper` and `@micropython.asm_thumb` / `asm_xtensa`: machine code emitted
into RAM at run time — they from Python source, we from wasm. Wasm is statically typed with a flat
byte memory, which is almost exactly viper's model (machine-word integers, raw `ptr8/16/32` pointers).
So on MicroPython the `mpy` target can lower HIR to **viper-flavoured Python** and let MicroPython's
emitter produce the machine code: native back ends for the architectures its ports support (x86/x64,
ARM Thumb on Cortex-M, Xtensa on ESP32, RISC-V — per port and release, to check) without an encoder of
ours. Measured: a 32-bit load through viper `ptr32` costs 3.4 ns against ~150 ns through `struct`
(BENCHMARKS.md). The hottest functions could go further through the inline assemblers
(`asm_thumb`, `asm_xtensa`), with MicroPython doing the instruction encoding. Limits to work around:
viper's argument-count and calling-convention limits (to check per release), i64 and floats remaining
objects, `ptr` accesses without bounds checks (we emit the checks), and the `mpy-cross` native bug on
very large functions (LESSONS.md 17).

Where no JIT is allowed at all — iOS (Pythonista), Pyodide, hardened Linux without executable
anonymous memory — `pyc` is the target. In Pyodide the browser's own `WebAssembly` is faster than any
of ours; a thin target calling it through `js` is an option to consider.
