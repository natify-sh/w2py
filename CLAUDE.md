# CLAUDE.md

Context and rules for working on w2py with an agent.

**Start with [`BRIEF.md`](BRIEF.md)**: what the project is, what it is not, and the order of work.

## What this is
A WebAssembly **compiler in pure Python** that emits machine code at run time (`jit-<arch>`, through
`mmap` + `ctypes`), with translation to Python (`pyc`) and MicroPython (`mpy`) where machine code is not
possible, and a thin runtime around it. No native dependency. Wasmtime is the test oracle and, later,
the shape of the public API — not the starting point. Read `BRIEF.md`, then `docs/BENCHMARKS.md` and
`docs/ARCHITECTURE.md`; the order of work is in `BRIEF.md`, "Where to start".

The maintainer communicates in Ukrainian; code, comments, commit messages and docs are in English.

## Hard rules
* **No runtime dependencies.** Only the standard library may be imported by the package. Development
  tools (wasmtime as the test oracle, wabt, wasm-tools, pywasm, meld, MicroPython, QEMU) stay in the
  development group or CI.
* **Wasmtime is the oracle, not the goal.** Where the spec leaves room (trap codes, NaN, limits) do
  what Wasmtime does, and test against it. The public API takes the shape of wasmtime-py in phase 6;
  API parity is never worked on before the compiler and the spec tests need it. Record deliberate
  deviations in `docs/DECISIONS.md`.
* **One interface for all targets**, one test suite for all targets. A target never special-cases
  another; the runtime never special-cases a target beyond `Target` methods.
* **Never crash the interpreter.** Deep recursion, bad memory accesses and host exceptions become
  traps or exceptions, on every target.
* **Long calls stay interruptible and do not starve other threads.** JIT code polls the interrupt
  word; `pyc` code has yield points; one thread per store (`docs/ARCHITECTURE.md`, "Long calls and
  the GIL").
* **The JIT artifact is a contract** (`docs/ARTIFACT.md`): any change to the format, the context ABI
  or the ISA policy bumps its version, updates the document and `docs/DECISIONS.md`, and adds a test
  that an old artifact is refused or still runs. Portable artifacts never use instructions above the
  architecture's baseline without a declared level.
* **The IR stays thin** (`docs/ARCHITECTURE.md`, "The IR"): HIR shared by all targets, LIR shared by
  all JIT architectures, structured, no SSA. An optimisation is added only with a measurement showing
  it pays. The shared layers stay language-neutral: the integer model is a target parameter, nothing
  Python-specific in HIR/LIR (DECISIONS.md D17).
* **Measure, do not estimate.** Speed claims come with a CoreMark run (or another benchmark) and go
  into `docs/BENCHMARKS.md` with the conditions; correctness claims come with tests.
* **Semantics first.** A faster code generator that fails a spec test is not faster.

## Working on the code
* The PoC in `poc/` is the reference for how things were done and measured; it is not the package.
  Port from it deliberately (phase 0), keep it runnable for comparisons.
* JIT work: read `docs/RISKS.md` (R2, R3) and `docs/LESSONS.md` items 1–9 first (flags, callee-saved registers across host calls,
  alignment, nested entries, zero-extension). Debug by mixing a known-good and a new code generator
  per function and bisecting — it is how the `printf` bug was found.
* Before implementing something hard (an operator's exact semantics, an ABI, an encoding, a new
  architecture), look at how the projects in `docs/REFERENCES.md` do it. Read and understand, write
  our own — never translate their code line by line; name the source of an idea in a comment.
* Every new opcode or feature: spec tests for it pass on every target that claims it, plus a
  differential run against wasmtime.
* `pyc` codegen: keep generated code valid for the minimum Python (see `docs/ROADMAP.md`, open
  question 1) and for the MicroPython flavour.

## Commands (PoC)
```
cd poc
python3 run_jit2.py in/core.wasm           # JIT, CoreMark
python3 w2py.py in/core.wasm out/core.py   # translate (+ out/core.pyc)
python3 run_pyc.py out/core.pyc
python3 ref_run.py in/core.wasm wasmtime   # the reference (wasmtime-py installed)
python3 ref_run.py in/core.wasm wasm3
```
