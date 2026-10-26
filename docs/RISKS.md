# Risks and possible solutions

Not decisions: what can go wrong, what we know about it, and the options to choose from when the time
comes. Decided matters live in DECISIONS.md.

## R1. Compiled artifacts moved between machines

**What is possible.** A JIT artifact (ARTIFACT.md) is position-independent, does not depend on the
Python version (byte-identical output under CPython 3.8 and 3.13) or on the OS when it carries both
entry trampolines. It runs on another machine with the same architecture, a CPU at or above its
declared ISA level, and a loader that knows its format and context-ABI versions.

**What to cache, what to ship — options.**

| form | load time | portable across | maintenance |
|---|---|---|---|
| wasm | compile on first use (CoreMark: ~25 ms JIT, ~111 ms `pyc`) | everything | none: it is the input |
| serialized HIR/LIR | still needs code generation (most of the ~25 ms) | architectures | one more format to version, little gain — the wasm already is a compact portable IR (parsing CoreMark takes 0.2 ms) |
| native artifact | ~instant | one architecture + ISA level + format/ABI versions | invalidated by compiler fixes (versions in the cache key) |

Likely shape: local cache = native artifacts (may use `isa="native"`); distribution = wasm, optionally
plus prebuilt baseline artifacts for x86-64 and aarch64 to skip the first compilation.

**Risk: trust.** The wasm sandbox exists only because the compiler inserted the checks (bounds, types,
stack). A native artifact from elsewhere is arbitrary machine code: a tampered or buggy one can do
anything the process can. (Wasmtime has the same property; its Rust `Module::deserialize` is marked
`unsafe`, as far as we know.) Options:

* load foreign artifacts only when explicitly allowed, never implicitly from a shared cache;
* a signature or a trusted hash list (the publisher's key, or the user's own build);
* otherwise always recompile from the wasm, which is safe to accept from anyone;
* the cache directory is per user and written atomically; refuse artifacts whose checksum, versions or
  ISA do not match before mapping anything executable.

**Risk: invalidation.** A compiler bug fixed in version N must not keep running from artifacts made by
N-1: the compiler version and the target version are part of the key and of the header; a list of
known-bad versions can force recompilation.

## R2. Platforms restricting run-time code generation

The direction is clear: more systems forbid writable-and-executable memory, and CPUs add control-flow
integrity that hand-made code must respect.

| platform | restriction | effect on us | possible solution |
|---|---|---|---|
| iOS / iPadOS (Pythonista) | no JIT for third-party apps | JIT impossible | `pyc`; maybe a system engine (R3, JavaScriptCore) |
| macOS on Apple Silicon | executable memory needs `MAP_JIT` and per-thread write/execute switching (`pthread_jit_write_protect_np`); hardened-runtime apps need a JIT entitlement | works if the interpreter binary allows it; depends on how it is signed | ctypes calls for `MAP_JIT` + toggling; probe; fall back to `pyc` |
| Linux, hardened | SELinux `deny_execmem`, PaX/grsecurity `MPROTECT`, systemd `MemoryDenyWriteExecute=yes` | `mmap`/`mprotect` to executable fails | probe; fall back; document for service authors |
| Windows | Arbitrary Code Guard (process mitigation, opt-in) blocks dynamic code | same | probe; fall back |
| Pyodide / browsers | no native code at all | JIT impossible | `pyc`, or the browser's `WebAssembly` (ROADMAP open question 7) |
| x86-64 with CET **IBT** | indirect branch targets must start with `endbr64` | the entry trampoline and anything called through a pointer would fault | emit `endbr64` at every indirectly reachable entry (a no-op on older CPUs) |
| x86-64 with CET **shadow stack** (Linux user space, Windows 11 hardware stack protection) | returns are checked against a hardware stack | the PoC's trap unwinding (reset `rsp`, then `ret`) would fault | unwind by returning through frames (each frame checks a trap flag), or a shadow-stack-aware unwind; never "jump" the stack |
| aarch64 **BTI** / **PAC** | landing pads (`bti c`) for indirect targets; signed return addresses in some processes (arm64e) | indirect entries fault without pads | emit `bti c`; keep return addresses unsigned unless the host process requires PAC |

General answers: W^X always (map writable, write, flip to executable; never both), the probe described
in TARGETS.md, CFI-compatible code from the first version, and `pyc` as the guaranteed fallback.

## R3. A hand-written JIT: correctness and safety

**Risk.** Encoding mistakes, register-allocation mistakes and missed checks are not exceptions but
wrong results or sandbox escapes. The PoC already met three silent bugs (LESSONS.md 1, 2, 5).

**Alternatives and their trade-offs.**

| option | idea | for | against |
|---|---|---|---|
| copy-and-patch | operation stencils in C compiled by clang on our CI, shipped as bytes; the run time only stitches and patches | clang generates the instructions; new architectures and SIMD are cheaper | build infrastructure; relocation formats follow LLVM versions; `musttail`/`preserve_none` not everywhere |
| an assembler library in Python (e.g. PeachPy for x86-64) | we select instructions, the library encodes | fewer encoding bugs | x86-64 only; project activity to check; vendoring |
| generate C at run time + the system compiler | wasm2c on the fly | best code | a compiler is often absent: not universal |
| a JIT library through ctypes (libLLVM, libgccjit) when installed | someone else's back end | code quality | not present everywhere, versions differ: a dependency in disguise |
| a system wasm engine | the browser's `WebAssembly` in Pyodide; JavaScriptCore on Apple platforms through ctypes / objc | no code generation of ours | platform-specific; whether JavaScriptCore runs wasm on iOS without JIT, and how fast, is unverified — worth checking first for Pythonista |
| our JIT with a small trusted core | see below | control, no dependencies | the work stays ours |

**Ways to shrink the risk of our own JIT** (applicable whatever is chosen):

* a table-driven instruction encoder, checked in CI against an external assembler/disassembler
  (`llvm-mc`, `objdump`) for every instruction form it can emit;
* memory safety concentrated in one small, reviewed piece: the bounds check, or an alternative design
  where addresses are masked/zero-extended into a reserved region surrounded by guard pages, so that a
  missed check crashes the process instead of escaping (a crash cannot be turned into a trap from
  Python — a mode to consider for speed, with that trade-off stated);
* differential fuzzing against wasmtime (`wasm-smith`), the spec suite, and mixing a known-good and a
  new code generator per function to bisect failures (how the `printf` bug was found);
* decide on copy-and-patch after `jit-aarch64` exists by hand, with numbers for both.

## R4. Other known risks (detailed elsewhere)

| risk | where |
|---|---|
| scope: feature parity with Wasmtime (SIMD, GC, exceptions, threads, memory64) is large | FEATURES.md, ROADMAP.md |
| `pyc` speed ceiling on CPython (~1.5–2× the PoC at best) | DECISIONS.md D4, BENCHMARKS.md |
| long calls: GIL in `pyc`, uninterruptible machine code | ARCHITECTURE.md, "Long calls and the GIL" |
| deep recursion in `pyc` | ARCHITECTURE.md, "Calls, stack depth and recursion" |
| the WASI host is undecided | ROADMAP.md, open question 2 |
| `mpy-cross` 1.29.0 miscompiles very large native functions | LESSONS.md 17 |
| maintaining several architectures | TARGETS.md, copy-and-patch |
