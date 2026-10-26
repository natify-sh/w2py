# Benchmarks

Everything measured during the PoC, with its conditions. Numbers from different sessions differ by up
to ~20 % (shared cloud machine); compare within a session.

**Machine**: cloud container, x86-64 Linux, Intel Xeon @ 2.10 GHz, 2 vCPU.
**Software**: CPython 3.13.17 (also 3.8.20, 3.9.25, 3.10.20 from uv), PyPy 7.3.19 (3.10), MicroPython
1.29.0 unix port (standard build), wasmtime-py 50.0.1, pywasm3 0.5.0, pywasm 2.2.3, lupa 2.8 (Lua 5.4,
5.5, LuaJIT 2.1), mypy/mypyc 2.4.0, warpy (kanaka, 2019) translated with RPython from PyPy `main` under
CPython 2.7.18.

**Workload**: CoreMark 1.0, `poc/in/core.wasm` (zig cc wasm32 -O3, 19 KB); it/s as CoreMark prints it.
Every run listed passed CoreMark's own self-check (crclist 0xe714, crcmatrix 0x1fd7, crcstate 0x8e3a;
crcfinal depends on the iteration count).

## CoreMark

| engine | it/s | notes |
|---|---|---|
| wasmtime (Cranelift) | 16 589 / **19 044** | first / last session |
| **w2jit2: stdlib JIT with registers, CPython 3.13 / 3.8** | 15 088, **15 337 / 15 437** | last session; ~80 % of wasmtime, 6.3× wasm3 |
| w2jit: stdlib JIT, every value in memory | 3 874–3 898 (3.13), 3 882 (3.8) | |
| w2py → PyPy 7.3.19 | 2 873; 2 300–2 900 later | noisy |
| wasm3 (pywasm3) | 1 850–2 035 / **2 433** | first / last session |
| w2luajit → LuaJIT 2.1 | 1 873 | was 67 with `goto`-based loops |
| w2py --mypyc → native extension | 1 734–1 814 | needs `librt` at run time (dropped) |
| w2lua → Lua 5.4 / 5.5 | 271 / 246 | was 244 with `goto`-based loops |
| warpy (RPython, meta-tracing JIT) | 186–193 | MVP-only; patched for 4 post-MVP ops |
| w2py → CPython 3.13 | 78 → 86 → ~100 | first PoC → codegen optimisations → last run |
| w2py → CPython 3.8 / 3.9 / 3.10 | 86.6 / 81.9 / 82.6 | `--legacy` (no `:=`) on 3.8: 89 |
| w2py → MicroPython 1.29 | .py 66 · .mpy 69 · `@native` 94 | native .mpy needs the big printf function left as bytecode |
| w2py `--trust-align` (memoryview casts) | CPython 74 · PyPy 137 | rejected |
| pywasm 2.2.3 | CPython 0.58 · PyPy 6.1 | PyPy run < 10 s (formally invalid for CoreMark) |

## Compile and load times (CoreMark)

| step | time |
|---|---|
| `pyc`: parse 0.2 ms · codegen 53 ms · `compile()` 58 ms · exec 0.4 ms | cold ≈ 111 ms (19 KB wasm → 146 KB / 3 871 lines of Python) |
| `pyc` from a cached code object (`marshal.loads` + exec) | 0.5 ms (123 KB) |
| `mpy`: import of source / of `.mpy` | 59 ms / 2 ms (`.mpy` 37 KB bytecode; native `.mpy` 285–312 KB) |
| JIT `w2jit` / `w2jit2` | ~28–31 ms (3.13), ~50 ms (3.8); 71 KB / 39 KB of x86-64 |

## Micro-benchmarks behind the decisions

**Memory access in CPython 3.13** (`poc/bench/mem_access_cpython.py`, ns per op): `M[a]` 35 ·
`Struct.unpack_from` 86 · `memoryview.cast('I')[i]` 69 · `int.from_bytes(slice)` 209 · 4 bytes
composed 253 · `Struct.pack_into` 84 · memoryview store 77 · `(a+v) & 0xFFFFFFFF` 67 · `a+v` 34.

**Masking**: `l = l + 2` 31 ns · `l = (l + 2) & 0xFFFFFFFF` 64 ns · compare-and-adjust forms 45–51 ns
(this is why "single wrap" normalisation replaced masks: 675 → 158 masks in CoreMark, +15 %).

**mypyc** (ns per op): `Struct.unpack_from` 68 · bytearray composed 159 · `librt` `BytesWriter`
load 11.5 / store 13.5 · `i64` arithmetic 1.8.

**CPython vs MicroPython** (`poc/bench/ops_cpython_vs_micropython.py`, ns per iteration):

| operation | CPython 3.13 | MP bytecode | MP `@native` |
|---|---|---|---|
| arithmetic, values < 256 | 32–36 | 44 | 26 |
| arithmetic, i32-sized values | 56–62 | 46 | 25 |
| `unpack_from` load | 123–131 | 262 | 149 |
| `pack_into` store | 129–143 | 128 | 105 |
| function call | 35–53 | 99 | 57 |
| empty loop | 14 | 24 | 13 |

MicroPython keeps small ints inside the pointer (63 bits on 64-bit unix), CPython allocates every
result above 256: that is why MicroPython wins on arithmetic.

**MicroPython 32-bit load** (`poc/bench/micropython_viper_load.py`): `unpack_from` (native) 158 ns ·
bytes composed (native) 140 ns · **viper `ptr32` 3.4 ns**.

**Machine code from Python** (`poc/bench/jit_loop_feasibility.py`, one loop iteration = i32 add +
i32 load): CPython 3.13 222 ns → mmap+ctypes code 0.66 ns (×338); CPython 3.8 176 → 1.04 ns; PyPy 5.6
→ 0.89 ns.

**Crossing into machine code** (`poc/bench/ctypes_call_cost.py`, ns per call): plain Python call 47 ·
`CFUNCTYPE` 327 · `PYFUNCTYPE` 303 (CPython 3.13); 64 / 277 / 206 (3.8). PyPy's ctypes ≈ 13 µs per
call (use its built-in cffi there).

**Long calls and the GIL** (`w2jit2`, CoreMark `setup`, CPython 3.13): during a 19.6 s guest call another
Python thread ran 17 175 iterations of a `sleep(0.001)` loop (~876/s): the GIL is released. A SIGINT
sent ~3 s into the call did not stop it; the call returned normally after 20.6 s.

**pywasm spec suite**: 54 006 commands, 0 failures, 40 s on CPython 3.13 (assert_invalid /
assert_malformed — 3 956 of them — are not checked by its runner).
