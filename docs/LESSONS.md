---
title: Lessons
kind: log
status: current
updated: 2026-10-30
---

# Lessons

What went wrong or surprised us while building the PoC, so it is not found again the hard way.

## JIT (x86-64)

1. **`xor reg, reg` clobbers the flags.** Loading a zero with `xor` between a compare and the
   `select`/`br_if` that consumes its flags broke CoreMark's `printf("%f")` (fractions printed as
   integers) while every integer CRC was right. Rule: between a flags producer and its consumer only
   `mov`/`lea` may be emitted. Found by mixing v1 and v2 code generators per function and bisecting.
2. **Host calls clobber the pinned registers of every frame above.** Pinned locals live in registers
   that wasm functions treat as callee-saved, but a C-ABI callee (a ctypes callback) may destroy
   rsi/rdi/r8–r11. Saving only the current function's pinned registers is wrong: frames further up
   the stack keep locals there too. Save all C-caller-saved pool registers around every host call.
3. **Stack alignment alternates with call depth** unless every function keeps the ABI's invariant
   (16-byte aligned at call sites). Each function adjusts `rsp` in its prologue (pushes + padding).
4. **The entry trampoline keeps one saved `rsp`.** Nested entries (wasm → host → wasm) overwrite it;
   a trap in the inner entry would unwind to the wrong frame. The real entry must save and restore
   the previous value (a linked list in the context or on the stack).
5. **i32 values in 64-bit registers must stay zero-extended** if they can become addresses: use 32-bit
   operations and moves for i32 (`mov r32, …` zero-extends), and zero-extend values returned by C
   (`mov eax, eax`), which leaves the upper half undefined.
6. **Hand-computed jump offsets are a trap**: the first feasibility test crashed on a wrong `rel8`.
   Always assemble through labels and fixups.
7. **Memory cannot move under running code**: reserve the address range up front and commit on grow
   (ARCHITECTURE.md). The PoC uses a fixed-size memory and has no `memory.grow`.
8. **A ctypes `CFUNCTYPE` call releases the GIL but cannot be interrupted**: other threads ran during a
   19.6 s guest call; a SIGINT sent 3 s in did not stop it (it returned normally at 20.6 s). Interrupts
   must be polled by the generated code.
9. **ctypes calls cost ~0.3 µs** (CPython) and ~13 µs on PyPy: keep the Python↔machine-code boundary
   coarse; on PyPy use its built-in cffi.

## `pyc` translator (CPython, PyPy)

10. **Masks cannot simply be dropped**: clang code relies on 32-bit wrap-around (`(p - 1) & -4`, then
   `+ 6`). Track value bounds; when an expression can wrap at most once, compare-and-adjust is cheaper
   than `& 0xFFFFFFFF`.
11. **Keep ints small and exact**: `x + 0xFFFFFFFF` → `x - 1`; comparison results used as numbers →
    `1 if c else 0` (a `bool` operand de-specialises CPython's int fast paths).
12. **CPython allows only 20 statically nested blocks** (loops, `try`, `with`): emitting `while True`
    for every wasm `block` hit "too many statically nested blocks" on CoreMark. Only real loops become
    Python loops; block exits use flags / nested `if`. Deep expressions can also hit the parser's
    nesting limit: materialise into temporaries past a depth.
13. **`:=` needs Python 3.8**; `--legacy` replaces it with masks for older interpreters.
14. **A live `memoryview` blocks `bytearray.extend` on CPython** (`BufferError`); temporary views
    (as a buffer-taking WASI host such as fusedwasi would make them) are fine. PyPy does not lock at all.
15. **pywasm on PyPy 3.10 needed patches** (`typing.Self`, `int.from_bytes` without byteorder): it
    targets newer CPython only.

## MicroPython 1.29.0

16. **Missing APIs**: `struct.Struct`, `int.bit_length`, `int.bit_count`, `memoryview.cast`,
    `str.isidentifier`, `marshal`. `micropython.native` exists only as a compile-time decorator,
    not as an attribute.
17. **`mpy-cross -march=x64` miscompiles a very large function** (CoreMark's printf, ~1 300 lines of
    Python, emitted native): a call inside it fails with `TypeError: () takes 0 positional arguments
    but 2 were given`. The same source compiled natively at run time by the unix port works. Workaround:
    keep very large functions as bytecode. Worth reporting upstream with a reduced case.
18. **Small ints are 63-bit on 64-bit ports but 31-bit on 32-bit MCUs**: i32 values ≥ 2³⁰ become heap
    integers there.
19. **The unix port needs `-X heapsize=…`** for large modules (1.1 MB of linear memory + compilation).

## Lua (experiment)

20. **LuaJIT only traces real loops**: wasm loops as backward `goto` ran at 67 it/s; as
    `while true do … end` with a forward `goto` to a continue label, 1 873 it/s.
21. Lua has a limit of ~200 locals per function: spill the rest into a table.

22. **CPython's parser allows at most 100 levels of indentation** (on top of the 20 statically nested
    loop/`try`/`with` blocks of LESSONS 2). Wasm nests blocks without limit: in CPython built for WASI,
    `_PyEval_EvalFrameDefault` (a 155 KB function body) nests blocks **742 deep** — its big `switch`
    becomes one block per case. The `pyc` translator turns each block into an indented `if`, so the
    96.5 MB of generated source fails in `compile()` with `IndentationError: too many levels of
    indentation`. Possible answers: for functions past a depth, a flat state machine (`while True:` +
    a label variable + one `if`/`elif` level — the "label variable" fallback of the Relooper); or the
    switch lowering of BENCHMARKS.md, "br_table", which puts case bodies at the leaves instead of
    nesting them; or splitting deep regions into nested functions. The JIT has no such limit.

## Tooling

23. The sandbox could not reach PyPI-hosted binaries for wabt or PyPy; wabt (`wast2json`) was built
    from source in a minute with CMake + Ninja; CPython 2.7 for RPython was built from the CPython
    `2.7` branch; old CPythons came from `uv python install` (3.8+ only).

## Found later

24. **Guard pages are reachable from Python after all.** The usual statement "a SIGSEGV cannot be
    turned into a trap from Python" is true of a Python handler; a handler emitted as machine code by
    the JIT and installed with `sigaction` through ctypes works (`poc/w2jit_guard.py`: 119 bytes; Linux
    x86-64; traps at every out-of-bounds address, the module keeps working after a trap, a real
    segfault elsewhere still ends the process with 139). Watch: `ctypes.memmove` does not take a
    `bytearray` (pass `bytes`); 0xFFFC + 4 bytes is the last valid i32 of a 64 KiB memory.
25. **`wasm-opt` on code that LLVM already optimised gains nothing** and its inlining slows the
    interpreters (wasm3, iwasm: −7…8 %); Lua gained 17 % (fewer calls). The PoC JIT had to learn
    `if`/`else`: clang/zig never emitted it for CoreMark, binaryen does.
26. **Precompiled Lua (`string.dump`) only saves load time** (130 KB source 2–3 ms → 76 KB bytecode
    0.1 ms); the speed is the same VM's.
27. **An expected-failures file can hide a contract violation.** The strict reader rejected 13
    well-formed but invalid modules as *malformed* (range limits read as encoding errors; `ref.eq`/
    `ref.as_non_null`/`br_on_*` opcodes mis-numbered), and `--write-xfail` filed them as `wrong kind`
    so the run stayed green. Symptom: `wrong kind` reasons in `xfail/pyc.txt`. Rule: a `wrong kind`
    is never an acceptable xfail; verify every step with a run against an empty xfail file and
    `grep -c "wrong kind"` = 0. Range limits (memarg offset ≥ 2³², memory/table sizes, alignment) are
    decoded as u64 and rejected by the validator, not the reader. Found: validator step 1, 2026-10-30.
28. **A lazy operand stack can drop a trap.** `pyc` builds expressions lazily; `drop` discarded an
    unevaluated `i32.load` whose address was out of bounds, so the trap never happened (60 commands in
    `address.wast`). Wasm evaluates every operand: anything that can trap (loads, `div`/`rem`,
    `trunc`, `call`) must be evaluated even when its value is discarded. Fixed with an `x` (may trap)
    flag on div/rem/trunc next to `m` (reads memory): `drop` emits such operands as statements and
    `select` evaluates both operands when either has the flag. Audited later: values discarded below
    the arity of `br`/`br_if`/`return` and before `unreachable` were also dropped unevaluated (`br_table`
    already flushed); a branch now materialises whatever has the `m`/`x` flag first
    (`flush_trapping`; `test_discarded_values_that_may_trap_still_run`). Found: pyc loop, 2026-10-30.
29. **Never write a float into generated source with `repr`.** `repr(inf)`/`repr(nan)` give `inf`/
    `nan`, undefined names in the generated module, so a module with such a global initialiser failed
    to load (`select.wast`, found while fixing NaN constants). Emit special values through an exact
    constructor from their bits (`_f64_i64(0x…)`). Found: pyc loop, 2026-10-30.
