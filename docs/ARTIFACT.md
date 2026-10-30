# JIT artifact, ISA levels and the context ABI

A JIT target's output is plain machine code with a small, fixed boundary. Nothing in it is
Python-specific (measured: the same module compiled under CPython 3.8 and 3.13 gives byte-identical
code), so the artifact is **a language-neutral format**: any host that can map executable memory, call
a C function pointer and pass C callbacks can load and run it (C/C++, Dart FFI, LuaJIT, Ruby Fiddle,
Java FFM, .NET, Deno/Bun, MicroPython's `ffi`). This document is the contract such hosts — and our own
Python loader — rely on. It has three parts, each versioned.

> **Scope (DECISIONS.md D18).** First of all this is the format of the w2py runtime's own cache entries
> and serialized files (`Module.serialize`, `w2py compile`): w2py follows the Wasmtime model, the
> runtime lives in the package and nothing is embedded in the artifact. Loading from other languages
> stays possible because the boundary is neutral, but it is optional.

> The PoC (`poc/w2jit.py`, `w2jit2.py`) predates this contract: its context layout and entry signature
> are a subset of version 1 below and must be brought in line in phase 4.

## 1. ISA levels

An artifact declares the **instruction-set level** it was compiled for; a loader refuses an artifact
the CPU cannot run.

| architecture | baseline (default for portable artifacts) | optional levels / features the compiler may use |
|---|---|---|
| x86-64 | **x86-64-v1**: SSE, SSE2 (every x86-64 CPU) | `x86-64-v2` (SSE3, SSSE3, SSE4.1, SSE4.2, POPCNT), `x86-64-v3` (AVX, AVX2, BMI1/2, LZCNT, FMA, MOVBE), single features by name |
| aarch64 | **ARMv8.0-A** with FP and Advanced SIMD (NEON) | `lse` (v8.1 atomics), `fp16`, `dotprod`, `rcpc`, … by name |
| later | each new architecture defines its baseline here before its first release | |

Rules:

* **Portable artifacts** (`w2py compile`, shipped files) use the baseline unless asked otherwise.
  An operation whose natural instruction is above the baseline gets a baseline sequence. Example found
  in the PoC: `f64.trunc` was emitted as `roundsd` (SSE4.1, x86-64-v2); at v1 it needs an SSE2
  sequence (convert-truncate for |x| < 2⁵², otherwise x itself, preserving sign and NaN). The same
  applies to `floor`, `ceil`, `nearest`, `popcnt`, `clz`, `ctz`.
* **Local cache artifacts** may use `isa="native"`: the features of the running CPU, recorded in the
  artifact and in the cache key, so a cache moved to another machine is recompiled, not executed.
* `Config.isa` (w2py addition): `"baseline"` (default), `"native"`, a level name, or a level plus
  feature names.
* **Detection at load time, stdlib only**: on x86-64 a small `cpuid` stub run through the same
  executable-memory + ctypes path (works on every OS); on aarch64 `getauxval(AT_HWCAP/AT_HWCAP2)` via
  ctypes on Linux/Android, `sysctlbyname("hw.optional.*")` on macOS, `IsProcessorFeaturePresent` on
  Windows. Hosts in other languages do the equivalent.

## 2. Artifact format (version 1)

A little-endian binary container. Self-contained: a host needs **no wasm parser** to instantiate it.

**Header** (fixed size):

| field | type | meaning |
|---|---|---|
| `magic` | 8 bytes | `W2PYJIT\0` |
| `format_version` | u16 | this document's part 2 version (1) |
| `ctx_abi_version` | u16 | part 3 version (1) |
| `arch` | u16 | 1 = x86-64, 2 = aarch64, 3 = riscv64, … (registry in this file) |
| `isa_level` | u16 | per architecture: 1 = baseline, 2 = v2, 3 = v3, … |
| `isa_features` | u64 | bitset of extra features (registry per architecture) |
| `flags` | u32 | code-affecting options: fuel, epoch/interrupt polling, bounds-check mode, … |
| `compiler` | str | `w2py <version> <target> <target version>` |
| `module_sha256` | 32 bytes | hash of the wasm bytes |
| `config_sha256` | 32 bytes | hash of every code-affecting option |
| `section_count` | u32 | |

**Sections** (`id: u16`, `length: u32`, payload; unknown ids are skipped):

| id | section | content |
|---|---|---|
| 1 | `CODE` | the machine code, position-independent: internal branches relative, everything absolute through the context register |
| 2 | `ENTRIES` | entry trampolines, one per host calling convention: `{sysv: offset, win64: offset}` on x86-64; `{aapcs64: offset, win-arm64: offset}` on aarch64. The host picks its own |
| 3 | `FUNCS` | per defined function: code offset, type index, frame size (slots), name (if any) |
| 4 | `TYPES` | function types (param/result value types) |
| 5 | `IMPORTS` | function imports: module, name, type index, context slot; memory/table/global imports with their types |
| 6 | `EXPORTS` | name → kind + index |
| 7 | `MEMORIES` | per memory: min, max, page size, 64-bit, shared |
| 8 | `TABLES` | per table: element type, min, max |
| 9 | `GLOBALS` | per global: type, mutability, initial value (or the import it comes from) |
| 10 | `SEGMENTS` | data and element segments: active (target, offset) or passive, bytes / function indices |
| 11 | `START` | start function index, if any |
| 12 | `TRAPMAP` | code offset ranges → wasm function index + wasm byte offset (for `Trap.frames`) |
| 13 | `RELOCS` | empty in version 1 (code is position-independent); reserved for copy-and-patch |
| 14 | `WASM` | optional: the original wasm bytes. Its presence makes a **fat** file (below) |
| 0xFFFF | `CHECKSUM` | sha256 of everything before it |

Strings are UTF-8 with a u32 length prefix. A loader refuses an artifact whose `magic`,
`format_version` major, `ctx_abi_version`, `arch` or ISA it does not support — it never "tries".
`Module.serialize()` writes this format; the cache stores it.

**Thin and fat files (D18).** A thin file (no `WASM` section) is refused on any mismatch. A fat file is
never refused: when the loader cannot use the code — another w2py or target version, another ISA
level, a compiler version on the known-bad list, a format it does not know — it checks that the
embedded wasm matches `module_sha256` and regenerates from it. For this the loader must be able to
read the header and find section 14 in every format version, so the header prefix (`magic`,
`format_version`) and the section framing are frozen across versions.

**Other targets.** Serialized `pyc` and `mpy` modules use the same container with their own
`arch`/`isa` meaning (the bytecode magic / `.mpy` version) and the marshalled code object or `.mpy`
as `CODE`; to settle in phase 2. File names: `<name>.<target>.<ext>`, e.g. `core.jit-x86_64-v1.w2a`,
`core.pyc-cp313.w2a` (`.w2a` is a working extension).

## 3. Context ABI (version 1)

What compiled code expects from its host at run time. Any change to a word's meaning or position is a
new `ctx_abi_version`; new words are only ever appended in reserved space.

**Entry.** Each trampoline in `ENTRIES` is a C function in the host's calling convention:

```c
int32_t entry(uint64_t *ctx, uint64_t *frame, const void *code_of_function);
```

* `frame` points into the host-allocated **value stack**; the arguments are written to `frame[0..n)`,
  the results come back in `frame[0..m)`, one 64-bit slot per value (i32 zero-extended in the low half;
  i64 as is; f32/f64 as bit patterns; v128 takes two slots; references as pointer-sized values).
* The return value is `0` or a trap code (below). On a trap the machine stack is unwound to the
  trampoline, which returns normally: the host never sees a signal or a non-local jump.
* **Re-entrant**: the trampoline saves the previous `ctx[SAVED_SP]` on its own stack and restores it on
  return or trap, so wasm → host → wasm nesting works (the PoC lacked this; LESSONS.md).

**Context words** (`uint64_t ctx[]`, 8-byte aligned, at a stable address for the instance's lifetime):

| index | name | direction | meaning |
|---|---|---|---|
| 0 | `SAVED_SP` | internal | native stack pointer of the innermost entry (unwind target) |
| 1 | `TRAP_CODE` | code → host | set together with the return value |
| 2 | `INTERRUPT` | host → code | non-zero: the code traps with `INTERRUPT` at its next poll (any thread may set it) |
| 3 | `FUEL` | both | remaining fuel (signed), when compiled with fuel |
| 4 | `EPOCH_DEADLINE` | host → code | when compiled with epochs |
| 5 | `VSTACK_LIMIT` | host → code | address the value stack must not cross → `STACK_OVERFLOW` |
| 6 | `NSTACK_LIMIT` | host → code | lowest native stack address allowed → `STACK_OVERFLOW` |
| 7 | `HOST_ERROR` | host → code | set by a host function that failed; checked after every import call, unwinds like a trap with code `HOST_ERROR`; the host keeps the actual exception |
| 8–15 | reserved | | zero |
| 16 … | per memory *i*: `base`, `size_bytes` (2 words) | host ↔ code | the host updates them on growth (the code reloads after `memory.grow` and after host calls) |
| … | per table *i*: `base`, `size` (2 words) | | table elements: `{code pointer, type id, context pointer}` |
| … | per global *i*: 1 word (2 for v128) | both | imported globals: a pointer to the owner's word instead |
| … | per function import *i*: 1 word | host → code | a C function pointer (signature below) |

Offsets of the variable part follow from the counts in the artifact, in exactly this order.

**Host functions** (imports) are C functions in the host's calling convention:
`ret f(uint64_t *ctx, args…)` — the context first (so a host can reach memory, implement `Caller`, or
set `HOST_ERROR`), then the wasm parameters mapped as i32 → `uint32_t`, i64 → `uint64_t`,
f32 → `float`, f64 → `double`, references → pointer-sized; a single result returned the same way;
more than one result written through an extra trailing `uint64_t *results` parameter.
Pool registers the compiled code keeps across such calls are its own responsibility (it saves every
register the host convention lets the callee clobber).

**Trap codes** (keep the PoC's first six numbers; names are Wasmtime's `TrapCode`):

| code | trap | code | trap |
|---|---|---|---|
| 0 | none | 8 | `BAD_SIGNATURE` |
| 1 | `UNREACHABLE` | 9 | `TABLE_OUT_OF_BOUNDS` |
| 2 | `MEMORY_OUT_OF_BOUNDS` | 10 | `NULL_REFERENCE` |
| 3 | `INTEGER_DIVISION_BY_ZERO` | 11 | `OUT_OF_FUEL` |
| 4 | `INTEGER_OVERFLOW` | 12 | `INTERRUPT` |
| 5 | `STACK_OVERFLOW` | 13 | `HOST_ERROR` (w2py) |
| 6 | `BAD_CONVERSION_TO_INTEGER` | 14 | `UNCAUGHT_EXCEPTION` |
| 7 | `INDIRECT_CALL_TO_NULL` | 15 | `HEAP_MISALIGNED` |

**Versioning rules**

* `format_version` and `ctx_abi_version` are independent. Loaders accept exactly the versions they
  know; the compiler can emit older versions while hosts migrate.
* Any change here goes with: the version bump, this document, `DECISIONS.md`, and a test that loads an
  artifact of the old version and checks it is refused (or still runs, if supported).
* A small reference loader in C is part of the test suite, to keep the contract honest outside
  Python.
