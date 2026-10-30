---
title: References
kind: reference
status: current
updated: 2026-10-30
---

# References

Projects worth reading to see **how something is done correctly**. We read, understand and write our
own code; we do not translate other projects line by line, so their licences do not flow into ours.
When an idea comes from one of them, the code says so in a comment (e.g. "bounds check for memory64
as in Liftoff") — that keeps the reasoning traceable. If a snippet is ever copied, it is recorded in a
`THIRD_PARTY_NOTICES` file with its licence; the default is not to copy.

Correctness never rests on the source: it is shown by our own tests — the spec suite, differential runs
against wasmtime, and encodings checked against `objdump` / `llvm-mc` in CI.

| source | what to look at | useful for |
|---|---|---|
| **Winch** — wasmtime's baseline compiler (Rust) | the closest project to our JIT: single-pass wasm → x64 / aarch64; lowering of every operator with Wasmtime's trap semantics; the lazy value stack and register cache; bounds checks; fuel and epoch checks; trap codes | `jit-x86_64`, `jit-aarch64`, LIR design |
| **V8 Liftoff** — V8's baseline wasm compiler (C++) | the same job across **many architectures** (x64, ia32, arm, arm64, riscv, mips64, loong64, ppc64, s390x): how each wasm operator maps onto each ISA; how register state is reconciled at block merges (we avoid it by pinning locals — worth knowing what it costs); memory64 bounds checks | every new architecture |
| **wasm2c** (`wasm-rt` and its helpers) | exact semantics of the awkward operators as C: NaN rules of `min`/`max`, `trunc`, `trunc_sat`, `nearest`, signed division and remainder, rotations, conversions | `pyc` helpers, JIT instruction sequences |
| **The spec reference interpreter** (OCaml, `WebAssembly/spec`) | what an operator means when in doubt | everything |
| **TinyCC** | small per-architecture back ends (`x86_64-gen.c`, `arm64-gen.c`, `riscv64-gen.c`, `arm-gen.c`, `i386-gen.c`): calling conventions, instruction encodings; the `vtop` value stack with `VT_CMP` — the same idea as our lazy entries and fused compare + branch | ABIs and encodings for new architectures |
| **asmjit** | complete encoding tables for x86 and AArch64, including SIMD | encoders, SIMD |
| **DynASM** (LuaJIT) | encoders for x86/x64, ARM, ARM64, PPC and **MIPS** | MIPS (Ingenic X2000), PPC |
| **Cranelift's assemblers** | encodings for x64, aarch64, riscv64, s390x | encoders |
| **MicroPython's native emitters** (`py/emitnative.c`, `py/asmthumb.c`, `py/asmxtensa.c`, `py/asmrv32.c`, …) | code generation for MCU architectures (Thumb, Xtensa, RISC-V 32); how viper types and calls work | the `mpy` target, viper code, future Thumb/Xtensa back ends |
| **CPython's JIT** (`Tools/jit`) | how copy-and-patch is built: stencils, holes, relocations | if copy-and-patch is chosen (TARGETS.md) |
| **wasm-to-asm** (calebsander, TypeScript) | a small readable wasm → x86-64 compiler: the bottom of the operand stack kept in registers, a calling convention without caller-saved registers (it has no traps: not a sandbox) | register allocation ideas |
| **pywasm** | a complete pure-Python interpreter (Wasm 2.0, SIMD, WASI p1) and its spec runner | the test harness, a second oracle, WASI p1 |
