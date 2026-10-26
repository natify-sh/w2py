# Testing

Every target passes the same tests; a target becomes selectable only when it passes them for the
features it claims.

## Development dependencies (never runtime)

| tool | why |
|---|---|
| **wasmtime** (pip, pinned) | **the test oracle**: differential runs compare every w2py target with Wasmtime on the same module and inputs (results, traps, memory contents); also the reference for API behaviour and feature defaults |
| wabt (`wast2json`, `wasm2wat`) | turns the spec test suite `.wast` files into `.json` + `.wasm` (natify-sh/wabt-package, or built from source as in the PoC) |
| wasm-tools (`wasm-smith`, `validate`) | random valid modules for differential fuzzing; a second validator |
| pywasm (pip) | a second oracle and a source of ideas: its spec runner (`test/spec.py`) was adapted in the PoC session; it passes the core spec suite (54 006 commands, 0 failures at its pinned spec commit `fffc6e12f`) but treats `assert_invalid` / `assert_malformed` as no-ops |
| meld | fusing component test programs into core modules for WASI 0.2 |
| MicroPython unix port, `mpy-cross` | the `mpy` target |
| QEMU user-mode | JIT targets for other architectures in CI |

The runtime package itself declares **no** dependencies; these live in a development group.

## The spec test suite

* Source: `WebAssembly/spec` `test/core` (+ `simd/`, and the proposal repositories for features not
  merged yet), converted with `wast2json`. Pin the commit; move it in small steps.
* One harness, many targets: the JSON commands (`module`, `register`, `assert_return`,
  `assert_trap`, `assert_exhaustion`, `assert_invalid`, `assert_malformed`, `assert_unlinkable`,
  `assert_uninstantiable`, `action`) are executed through the **public w2py API**, parametrised over
  targets (`pytest -k target`). Unlike pywasm, `assert_invalid` / `assert_malformed` are real tests
  of our validator.
* Expected failures are listed per target in a file in the repo (feature not implemented yet), so
  progress is visible and regressions fail.

## Differential testing

* For each module (spec modules, `wasm-smith` output, real programs): run every target and Wasmtime;
  compare return values, trap codes, final memory hash, globals.
* NaN results compare by the spec's rules (canonical / arithmetic NaN), not bit-for-bit.
* A mismatch is minimized (`wasm-tools shrink`) and kept as a regression test.

## Programs and benchmarks

* CoreMark (`poc/in/core.wasm`, `zig cc -O3`): correctness by its own CRC self-check, speed in it/s.
  Record new numbers in BENCHMARKS.md with the conditions.
* WASI: the WASI testsuite (p1), the chosen host's conformance tests (fusedwasi has them for p2),
  small programs in C, Rust, Zig.
* Long calls: a guest loop interrupted from another thread and by SIGINT (CLI) on every target; another
  Python thread's progress during a long call.
* A few larger real modules (e.g. a compression library, a Python-in-wasm build, the ballistics core
  compiled to wasm) for compile time, cache behaviour and speed.

## CI matrix

| axis | values |
|---|---|
| Python | CPython 3.8 (or the chosen floor), 3.10, 3.12, 3.13, latest; PyPy 3.10+; free-threaded build later |
| OS | Linux x86-64, Linux aarch64, macOS arm64 (MAP_JIT), Windows x86-64 |
| target | `pyc` everywhere; `jit-*` where the machine matches; `mpy` with the MicroPython unix port |
| emulated | QEMU user-mode for riscv64, armv7, mips32el as JIT targets appear |

JIT artifacts: run baseline artifacts on an emulated baseline CPU (e.g. QEMU `-cpu` without SSE4.1 /
with plain ARMv8.0) and check that a higher-level artifact is refused there; load artifacts with the C
reference loader as well as from Python; refuse artifacts with an unknown format or context ABI version.

Run with executable memory forbidden too (e.g. a seccomp/SELinux profile or a forced probe failure) to
prove the fallback to `pyc`.
