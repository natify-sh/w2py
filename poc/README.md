# w2py — WebAssembly → Python (.py/.pyc) AOT translator (PoC)

Translation, not interpretation: every wasm function becomes a plain Python function, the wasm
operand stack is resolved at translation time, `loop` → `while`, `block`/`br_if` → `if`.

## Usage

    python3 w2py.py core.wasm out/core.py      # writes out/core.py and out/core.pyc
    python3 run_pyc.py out/core.pyc            # CPython: run CoreMark from the .pyc
    pypy3   run_py.py  out/core.py             # PyPy: run the same generated source

    # mypyc (native extension): typed codegen, memory on librt.strings.BytesWriter
    python3 w2py.py core.wasm mypyc/wcore.py --mypyc
    cd mypyc && python3 -m mypyc wcore.py      # needs mypy (mypyc), mypy_extensions, librt
    python3 ../run_mod.py . wcore

Flags: `--inline N` inlines straight-line leaf functions up to N instructions (opt-in: neutral on
CPython 3.13, ~+5% on PyPy within noise).

`--trust-align` uses memoryview for accesses marked aligned; it gave no gain on CPython and is
much slower on PyPy, so leave it off.

## Python versions and MicroPython

- Generated code: CPython 3.8+ (uses `:=` for cheap single-wrap normalisation); tested on
  3.8, 3.9, 3.10, 3.13 and PyPy 3.10. `--legacy` replaces `:=` with masks: syntax-clean down to
  3.4 (checked statically; < 3.8 not run here).
- MicroPython 1.29.0 (unix port): `--micropython` (no struct.Struct / int.bit_length /
  memoryview.cast — the runtime feature-detects, struct call sites become unpack_from('<I', …)).

      python3 w2py.py core.wasm out/mp/core.py --micropython
      micropython -X heapsize=64M run_mp.py out/mp core           # 66 it/s (compile on import 59 ms)
      mpy-cross -o out/mp/core.mpy out/mp/core.py                  # 69 it/s, 37 KB, import 2 ms
      # @micropython.native on translated functions: 94 it/s (faster than CPython)

  mpy-cross 1.29.0 bug: native-emitted .mpy miscompiles one large function (CoreMark's printf,
  ~1300 lines) → `TypeError: () takes 0 positional arguments but 2 were given`; the same code
  compiled natively at runtime works. Workaround: keep very large functions as bytecode.

## Lua backends

    python3 w2lua.py    core.wasm out/core.lua       # Lua 5.4 / 5.5
    python3 w2luajit.py core.wasm out/core_jit.lua   # LuaJIT 2.1
    python3 run_lua.py out/core.lua lua54            # runs inside lupa (pip install lupa)
    python3 run_lua.py out/core_jit.lua luajit21

- control flow: `block`/`if` exits are forward `goto`s; a wasm `loop` is `while true do … end`
  and `br` to it is a forward `goto` to a continue-label before `end` (backward gotos are not
  seen as loops by LuaJIT: 67 → 1873 it/s after this change). All locals declared at entry;
  beyond Lua's 200-locals limit they spill into a table.
- Lua 5.4: i32 = unsigned value in a native int64 with lazy `& 0xFFFFFFFF`; i64 = native int64;
  memory = table of bytes with inlined byte composition.
- LuaJIT: i32 = unsigned double with lazy `% 2^32`, `bit.*` for bit ops, exact mul via 16-bit
  split; i64 = FFI int64_t; memory = calloc'ed FFI buffer reserving the 4 GiB address space
  (accesses past memory.size don't trap — PoC limitation).

## CoreMark results (same machine, iterations/sec)

| Engine                                   | it/s          |
|------------------------------------------|---------------|
| wasmtime (JIT)                           | 16 589        |
| **w2jit2 — stdlib JIT with registers, CPython 3.13 / 3.8** | **15 088 – 15 437** |
| w2jit — stdlib JIT, all values in memory, CPython 3.13 / 3.8 | 3 874 – 3 898 |
| w2py → .py under PyPy 7.3.19             | 2 300 – 2 900 (noisy) |
| pywasm3 (C interpreter)                  | 1 850 – 2 035 |
| **w2luajit → LuaJIT 2.1**                | **1 873**     |
| **w2py --mypyc → native extension**      | **1 734 – 1 814** |
| w2lua → Lua 5.4 / 5.5 (interpreter)      | 271 / 246     |
| w2py → .pyc under CPython 3.13           | 86 (was 78)   |
| MicroPython 1.29 @native (unix)          | 94            |
| pywasm 2.2.3 under PyPy / CPython        | 6.1 / 0.58    |

CPython codegen optimisations (on top of the first PoC):
- large constants become small negative ones (`x + 0xFFFFFFFF` → `x - 1`), signed compares
  against constants become range tests — values stay on CPython's compact-int fast paths;
- comparison results entering arithmetic become exact ints (bool breaks int specialisation);
- `a ± b` of canonical operands can wrap at most once: normalised with a compare+adjust instead
  of `& 0xFFFFFFFF` (masks in CoreMark: 675 → 158). clang code really relies on wrap-around
  (e.g. `(p-1)&-4` then `+6`), so masks cannot simply be dropped.

mypyc mode: wasm i32 lives in native `i64` (mypyc i64 wraps silently, so + - * << keep the low
32 bits), linear memory is a `librt.strings.BytesWriter` (native byte get/set: ~12 ns per
32-bit access vs ~70–130 ns via struct), wasm globals are fields of a native class, calls between
translated functions are direct C calls. wasm i64 values use Python `int` (u64 doesn't fit i64).

All runs pass CoreMark's self-check (crclist 0xe714, crcmatrix 0x1fd7, crcstate 0x8e3a).

## w2jit: stdlib-only JIT (x86-64 System V)

    python3 run_jit.py core.wasm        # any CPython 3.8+, no compiler, no C extension

Machine code is assembled in Python, placed in an executable mmap and called via ctypes
(compile of CoreMark: ~30 ms, 71 KB of code). Baseline single-pass design: every wasm value lives
in a value-stack slot (wasm3-style frame overlap for calls), constants and local reads are folded
into operands, compare+br_if is fused into cmp+jcc, memory accesses are bounds-checked explicitly,
traps unwind to the entry trampoline, imports are ctypes callbacks. Covers the opcodes CoreMark
uses (no if/else, call_indirect, memory.grow, f32 yet). Not yet: keeping values in registers.

### w2jit2: registers (same session: wasmtime 19 044, wasm3 2 433, w2jit2 15 337 it/s)

    python3 run_jit2.py core.wasm

- the hottest integer locals (uses weighted by loop depth) are pinned to rbx rbp rsi rdi r8-r11
  for the whole function — no reconciliation at control-flow merges; between wasm functions these
  registers are callee-saved (a function pushes those it pins); around a host (C ABI) call all
  six C-caller-saved pool registers are saved, since they may hold locals of frames further up;
- top-of-stack cache in rax; `x op= y` on a pinned local is one instruction; a load whose next
  instruction is `local.set` of a pinned local loads straight into its register;
- bug found on the way: `xor reg, reg` for a zero constant clobbers the flags of a pending
  compare feeding `select` — zeros are loaded with `mov` where flags must survive.

## Files

- `w2jit2.py`, `run_jit2.py` — the JIT with registers (builds on w2jit.py)
- `w2jit.py`, `run_jit.py` — stdlib JIT for x86-64 SysV and its CoreMark runner
- `w2py.py` — translator (parser, codegen, runtime helpers), no dependencies
- `w2lua.py`, `w2luajit.py`, `run_lua.py` — Lua 5.4 and LuaJIT backends, runner (lupa)
- `out/core.lua`, `out/core_jit.lua` — generated Lua
- `run_mp.py`, `out/mp/core.py` — MicroPython runner and translated module
- `run_mod.py` — run a module importable by name (the mypyc build)
- `run_pyc.py` / `run_py.py` — load translated module, provide `wiring.millis/print`, run `setup`
- `ref_run.py` — reference runs: `python3 ref_run.py core.wasm wasmtime|wasm3`
- `run_pywasm.py` — pure-Python interpreter baseline
- `prof.py`, `mb.py` — cProfile of translated code; memory-access microbenchmark
- `in/core.wasm` — input (CoreMark, zig cc wasm32 -O3)
- `out/core.py`, `out/core.pyc` — generated output (.pyc is for CPython 3.13 only)
- `mypyc/wcore.py` — generated typed module for mypyc (build it locally, the .so is platform-specific)

## PoC limitations

No signature check in `call_indirect`; no block params (multi-value blocks); NaN payloads not
preserved; no SIMD; .pyc is tied to the CPython version that produced it.
