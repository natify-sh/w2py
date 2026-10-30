---
title: Engine interface of the spec-suite runner
kind: spec
id: S0001
status: draft
related: [D1, D5, D6, D10, D11, D19, R3]
owner: maintainer
updated: 2026-10-30
---

# S0001 Engine interface of the spec-suite runner

## Summary
The metric is the number of `WebAssembly/spec` `test/core` commands that pass, per engine. This spec
fixes the four-operation interface the runner drives (values, errors, `spectest`), the runner's
output and expected failures, and the pin. Wasmtime plugs in first, to test the runner; then `pyc`,
`jit-x86_64`. A test seam, not the public API (phase 6, [DECISIONS.md](../DECISIONS.md) D6).

## Goals / Non-goals
* Goals: run every binary-module command of the in-scope files on any engine; an exact, typed
  boundary (pyright strict, Python 3.11, D19); exit non-zero on any unexpected failure or pass.
* Non-goals: a wasmtime-like API; WAT parsing; `simd/` and proposal directories (later, see
  Semantics); differential fuzzing ([TESTING.md](../TESTING.md)); memory/table inspection.

## Interface

### Files (all dev-only; nothing under `src/w2py` changes, D1, D11)
Under `tests/spec/`: `engine.py` (the protocol, values, errors below; stdlib only),
`wasmtime_engine.py` (the adapter; the only file that imports `wasmtime`), `run.py` (the runner CLI,
value parsing and comparison), `fetch.py` (pin, download, conversion), `spectest.wat`,
`xfail/<engine>.txt`, `test_runner.py` (unit tests).
Empty `tests/__init__.py` and `tests/spec/__init__.py` make `python -m tests.spec.run` work. Later
engines add `tests/spec/<name>_engine.py`, an adapter over package internals that translates the
package's own errors into the ones below. The interface is a test seam, so it lives in `tests/`: the
package's trap and error types belong to the runtime and the Target spec, not to the runner.

### The protocol (`tests/spec/engine.py`)
```python
Module: TypeAlias = object     # opaque; made by load() of the same engine object
Instance: TypeAlias = object   # opaque; made by instantiate() of the same engine object

@dataclass(frozen=True)
class ExternRef:               # the host reference written (ref.extern N) in wast
    n: int

@dataclass(frozen=True, eq=False)
class FuncRef:                 # a non-null funcref returned by an engine; compared by presence only
    handle: object

Value: TypeAlias = int | ExternRef | FuncRef | None    # None: a null reference of any type

class TrapCode(Enum):          # Wasmtime's names (D6); a member is added when a test needs it
    UNREACHABLE = auto(); MEMORY_OUT_OF_BOUNDS = auto(); INTEGER_DIVISION_BY_ZERO = auto()
    INTEGER_OVERFLOW = auto(); BAD_CONVERSION_TO_INTEGER = auto(); STACK_OVERFLOW = auto()
    INDIRECT_CALL_TO_NULL = auto(); BAD_SIGNATURE = auto(); TABLE_OUT_OF_BOUNDS = auto()
    NULL_REFERENCE = auto()

class EngineError(Exception): ...    # base class; not raised itself
class LoadError(EngineError):        # load(): malformed or invalid module
    def __init__(self, message: str = "",
                 kind: Literal["malformed", "invalid"] | None = None) -> None:
        super().__init__(message)
        self.kind = kind             # None: the engine cannot tell (Wasmtime)
class LinkError(EngineError): ...    # instantiate(): unknown import, incompatible import type
class Trap(EngineError):
    def __init__(self, code: TrapCode, message: str = "") -> None:
        super().__init__(message or code.name)
        self.code = code

class SpecEngine(Protocol):
    def load(self, wasm: bytes) -> Module: ...
    def instantiate(self, module: Module, imports: Mapping[str, Instance]) -> Instance: ...
    def call(self, instance: Instance, name: str, args: Sequence[Value]) -> list[Value]: ...
    def get_global(self, instance: Instance, name: str) -> Value: ...
```
`typing.Protocol`, not an ABC: adapters satisfy it structurally and pyright strict checks that where
the runner's table `ENGINES: dict[str, Callable[[], SpecEngine]]` names them; an ABC would only add a
runtime base class for a check pyright already does. One engine object per `.wast` file is one store:
the runner calls the factory at the start of each file; an engine is used from one thread.

`instantiate` resolves each import `(module, field)`, in order, to the export `field` of
`imports[module]` (missing or mismatched → `LinkError`), then runs segments and start (a trap →
`Trap`). `call` raises `Trap`, or `LookupError` for a name that is not an exported function. Engines
know parameter and result types from the export's signature, so values carry no type tag.

### Values (the JSON's own encoding: the runner never converts floats)
| wasm type | wast2json `value` | at the interface |
|---|---|---|
| i32, i64 | unsigned decimal string | `int` in `[0, 2**32)` / `[0, 2**64)`: unsigned, as in the JSON |
| f32, f64 | bit pattern as unsigned decimal, or `nan:canonical` / `nan:arithmetic` (expected only) | `int` bit pattern, same ranges |
| v128 | `lane_type` + list of lane strings (lanes may be NaN patterns) | `int` in `[0, 2**128)`, lane `i` in bits `[i*w, (i+1)*w)` |
| funcref | `"null"`, or any other string = any non-null funcref (wabt writes `(ref.func)` as `"0"`) | `None` / `FuncRef` |
| externref | `"null"` or `"N"` | `None` / `ExternRef(N)` |

Other reference types (anyref, exnref, nullref, …) occur only in files not converted or not in
scope (Semantics); the runner fails such a command with reason `unsupported value type`.

### Commands and calls
`registry: dict[str, Instance]` starts as `{"spectest": <instance of spectest.wasm>}` per file and is
passed whole to every `instantiate`. `named: dict[str, Instance]`, `defs: dict[str, Module]`,
`current: Instance | None`. An action's target is `named[action.module]` if given, else `current`.

| command | calls | passes when |
|---|---|---|
| `module` | `load`; then `instantiate(m, registry)` → `current`, and `named[name]` if named | no exception |
| `module` with `"definition"` | `load` → `defs[name]` | no exception |
| `instance` | `instantiate(defs[definition], registry)` → `named[instance]` | no exception |
| `register` | `registry[as] = named[name]` or `current` | the instance exists |
| `action` (invoke / get) | `call` / `get_global` | no exception; results ignored |
| `assert_return` | `call` / `get_global` | results match `expected`, or any list in `either` |
| `assert_trap` | `call` | `Trap` whose code is the text's code (table below) |
| `assert_exhaustion` | `call` | `Trap(STACK_OVERFLOW)` |
| `assert_malformed`, `assert_invalid` (binary) | `load` | `LoadError` |
| the same, `module_type: text` | none | status `skip` (no WAT parser in scope) |
| `assert_unlinkable` | `load`, `instantiate` | `LinkError` from `instantiate` |
| `assert_uninstantiable` | `load`, `instantiate` | `Trap` whose code is the text's code |
| any other type (`assert_exception`, …) | none | fails: `unsupported command` |

A command passes only by the rule in its row; any other outcome, including an unlisted exception
such as `RecursionError`, is a failure whose reason is the exception type and first message line.

### The spectest module
Built by `fetch.py` with `wat2wasm`, instantiated through the engine under test (no host objects
needed), contents as `interpreter/host/spectest.ml` at the pin; printing is never observed:
```wat
(module
  (func (export "print")) (func (export "print_i32") (param i32))
  (func (export "print_i64") (param i64)) (func (export "print_f32") (param f32))
  (func (export "print_f64") (param f64)) (func (export "print_i32_f32") (param i32 f32))
  (func (export "print_f64_f64") (param f64 f64)) (table (export "table") 10 20 funcref)
  (global (export "global_i32") i32 (i32.const 666)) (memory (export "memory") 1 2)
  (global (export "global_i64") i64 (i64.const 666))
  (global (export "global_f32") f32 (f32.const 666.6))
  (global (export "global_f64") f64 (f64.const 666.6)))
```
`spectest.ml` also has `table64` (i64 index, 10..20); no top-level file imports it, so it is added
together with `memory64/`.

### Runner CLI and output
```
uv run python tests/spec/fetch.py                       # once per pin; idempotent
uv run python -m tests.spec.run --engine NAME [--json PATH] [--xfail PATH] [--write-xfail]
                                [--in-process] [--timeout 600] [FILE.wast ...]
```
* Input: `.cache/spec/<commit>/json/` (all files, or the named ones) and its `manifest.json`.
* Isolation: each file runs in a child (`sys.executable -m tests.spec.run --child FILE`) that prints
  one JSON line per command. If the child dies (abort, segfault) or exceeds `--timeout`, its remaining
  commands fail with `crash: <exit status>` / `timeout`. `--in-process` is for debugging.
* Command id: `<stem>.wast:<line>`; where several commands share a line (45 cases, e.g.
  `left-to-right.wast`), every one of them gets `.<k>`, the 0-based order on that line.
* Status per command: `pass`, `fail`, `xfail` (failed, listed), `xpass` (passed, listed), `skip`.
  Every command counts, including `module`, `register` and `instance`.
* Console: one line per file with the five counts, then a total line and the unconverted files.
* `--json PATH`: `{"engine", "spec_commit", "wabt", "flags", "totals": {status: n, "stale": n},
  "files": {name: {status: n}}, "unconverted": {name: error}, "results": [{"id", "type", "status",
  "reason"}]}`, where `results` holds every non-`pass` command.
* Expected failures, `tests/spec/xfail/<engine>.txt`: UTF-8, sorted, one entry per line,
  `<id>  <reason>` (reason required); `<stem>.wast:*  <reason>` lists a whole file, which is then not
  run and all its commands count as `xfail`; `#` starts a comment. An entry matching no command is
  `stale`. `--write-xfail` rewrites the file from the run (reason = the failure reason).
* Exit code: 0 iff `fail == 0`, `xpass == 0` and `stale == 0`; else 1; 2 on setup errors (no
  cache, unknown engine).

### Pinning and fetching (`tests/spec/fetch.py`)
Constants at the top: `SPEC_COMMIT = "6057acdb1a213f85369c64454c078a15f11ac4b1"` (main, 2026-10-28),
`WABT_VERSION = "1.0.42"`, `WAST2JSON_FLAGS = ("--enable-function-references",)`. It checks
`wast2json --version` (exit 2 on mismatch), downloads
`https://github.com/WebAssembly/spec/archive/<commit>.tar.gz` with `urllib`, extracts only
`test/core/*.wast` (top level) with `tarfile` `filter="data"`, converts each with `wast2json` into
`.cache/spec/<commit>/json/`, builds `spectest.wasm` there with `wat2wasm`, and writes
`manifest.json` (`commit`, `wabt`, `flags`, `converted`, `failed: {file: first error line}`). It
skips work when the manifest matches. `.cache/` goes into `.gitignore`; nothing converted is
committed; CI caches `.cache/spec/<commit>`. Moving the pin is a one-line change plus xfail updates.

## Semantics

**Scope at the pin** (measured 2026-10-30, wabt 1.0.42, `for f in test/core/*.wast; do wast2json
--enable-function-references $f ...; done`): 97 top-level files, **92 convert, 5 fail** —
`annotations`, `ref_null`, `type-canon`, `type-equivalence`, `type-rec` (GC type syntax and
annotations wabt does not parse); they are reported as `unconverted`. The 92 files hold **21 091
commands**: assert_return 16 593, assert_invalid 1 308, assert_malformed 1 306, module 1 080,
assert_trap 539, assert_unlinkable 136, action 49, assert_uninstantiable 34, register 28,
assert_exhaustion 15, instance 3; 602 of them (598 malformed, 4 invalid) are text modules → `skip`.
No v128 value and no `either` occurs at top level. Default flags convert only 74/97; `--enable-all`
converts 92 but writes the compact-imports encoding (leading byte 0x7F), which Wasmtime rejects.
Later, with the same flags: `simd/` 59/59, `multi-memory/` 41/41, `relaxed-simd/` 7/7 (uses
`either`), `memory64/` 24/25, `bulk-memory/` 7/8, `exceptions/` 2/5, `gc/` 1/17.

**Comparison.** Result count must equal the expected count. Integers and float bit patterns compare
exactly and must be in range (a signed `-1` for i32 fails). `nan:canonical`: f32
`v & 0x7FFFFFFF == 0x7FC00000`, f64 `v & 0x7FFF_FFFF_FFFF_FFFF == 0x7FF8_0000_0000_0000`;
`nan:arithmetic`: f32 `v & 0x7FC00000 == 0x7FC00000`, f64 `v & 0x7FF8_0000_0000_0000 ==
0x7FF8_0000_0000_0000`. v128 compares per lane of `lane_type`, NaN patterns per lane. funcref: null
vs any `FuncRef`. externref: `ExternRef` equality. `expected` without `value` (assert_trap) is ignored.

**Errors.** `assert_malformed` and `assert_invalid` both accept `LoadError`: the oracle cannot tell
them apart (measured: of 1 304 binary `assert_invalid` modules Wasmtime reports 151 as parse errors;
of 708 binary `assert_malformed`, 20 as compile errors). Our engines set `LoadError.kind`; when it is
not `None` it must equal the command (`assert_malformed` → `"malformed"`, `assert_invalid` →
`"invalid"`), otherwise the command fails with reason `wrong kind`. Link texts (`unknown import`, `incompatible
import type`) are not matched. Trap texts map to codes exactly (an unknown text fails the command, so
a moved pin shows new texts); verified on Wasmtime for all 539 + 15 + 34 trap commands:

| spec text | TrapCode | | spec text | TrapCode |
|---|---|---|---|---|
| unreachable | UNREACHABLE | | undefined element | TABLE_OUT_OF_BOUNDS |
| out of bounds memory access | MEMORY_OUT_OF_BOUNDS | | out of bounds table access | TABLE_OUT_OF_BOUNDS |
| integer divide by zero | INTEGER_DIVISION_BY_ZERO | | uninitialized element | INDIRECT_CALL_TO_NULL |
| integer overflow | INTEGER_OVERFLOW | | indirect call type mismatch | BAD_SIGNATURE |
| invalid conversion to integer | BAD_CONVERSION_TO_INTEGER | | null function reference | NULL_REFERENCE |
| call stack exhausted | STACK_OVERFLOW | | null reference | NULL_REFERENCE |

**Never crash the interpreter** (D10). Exhaustion is `Trap(STACK_OVERFLOW)`; an engine that lets
`RecursionError` out fails the command (the runner does not convert it, so the bug is visible). A
process crash costs one file, not the run (per-file child; the JIT's crash risk, R3).

**Wasmtime adapter.** Default `wasmtime.Config` (Tier 1 features), one `Store` per adapter object.
`load`: bytes not starting with `b"\0asm"` → `LoadError` without calling Wasmtime (wasmtime-py would
parse them as WAT: 14 malformed binaries); `WasmtimeError` → `LoadError`. `instantiate`: imports
looked up in `module.imports` order, `wasmtime.Instance`; `WasmtimeError` → `LinkError`;
`wasmtime.Trap` → `Trap(TrapCode[code.name])` (a code with no member propagates as is). `call` and
`get_global` use the raw C path (`wasmtime._ffi.wasmtime_func_call` / `wasmtime_global_get` under
`wasmtime._func.enter_wasm`), numbers through the integer fields of the value union: the `float`
path quiets f32 signalling NaNs (measured `0x7FA00000` → `0x7FE00000`; 988 commands in 8 files).
References go through `wasmtime.Val`; an `ExternRef` object comes back as itself. The file disables
pyright's `reportPrivateUsage` (pinned version). Known: wasmtime-py 50.0.1 aborts the process on a
`(ref extern)` in a signature ("support for non-externref and non-funcref references is not
implemented") → `local_init.wast:*` in its xfail file.

## Test plan
A throwaway prototype of this design over Wasmtime (not committed) gave 19 370 assertion passes,
0 failures, 602 skips, plus the `local_init` abort. Acceptance for the implementation step:
```
uv run python tests/spec/fetch.py          # manifest: 92 converted, the 5 failures named above
uv run python -m tests.spec.run --engine wasmtime --json /tmp/wasmtime.json
    # exit 0; totals about pass 20 479, xfail 10 (local_init.wast:*), skip 602, fail 0, xpass 0
uv run pytest tests/spec                   # unit tests below pass
uv run pyright && uv run ruff check        # clean (strict)
python3 tools/check_docs.py                # exit 0
```
`test_runner.py` covers: f32 `0x7FC00000`, `0xFFC00000` canonical; `0x7FC00001` arithmetic only;
`0x7FA00000` neither, but equal to `"2141192192"`; the same for f64; i32 `"4294967295"` vs
`0xFFFFFFFF` (pass) and `-1` (fail); a v128 NaN lane; funcref `"0"`; externref; `either`; ids on a
shared line; `LoadError(kind="invalid")` on `assert_malformed` fails, `kind=None` passes;
xfail parsing, `stale`; exit codes with a fake engine (wrong result → 1, xpass → 1).

## Alternatives considered
* **A wasmtime-py-shaped API now** (Store, Linker, Func objects): that is phase 6; four operations
  express every in-scope command, and a wide surface would delay the first `pyc` number.
* **Strings or Python floats as values**: strings make every engine parse NaN payloads; floats lose
  f32 signalling-NaN bits (988 commands). Bit-pattern ints are the JSON's own encoding.
* **Shelling out to `wasmtime wast`**: tests neither the runner nor our engines; no wasmtime binary
  in the dev group.
* **Extern handles (module → field → extern)**: every import source in the command set is an
  instance (`register`, `spectest`); handles would add a type with no user.
* **spectest from host objects in each engine**: needs host functions, memory, table and globals in
  `pyc` before its first number; a 13-export wasm module gives the same imports.
* **Separate `Malformed`/`Invalid` errors**, **`--enable-all`**: see Semantics (measured).
* **Interface in `src/w2py/_spec_engine.py`**: ships a test seam and pulls the runtime's trap type
  towards it; an adapter per engine costs one `try/except`.
* **In-process only**: one abort (already real for wasmtime-py) loses the whole run.

## Open questions
* The 5 unconverted files: wait for a wabt release, or convert just those with `wasm-tools`?
* [TESTING.md](../TESTING.md) says the suite runs "through the public w2py API … (`pytest -k
  target`)"; on approval it should say: through this interface, as a CLI per engine.

## Changes
* 2026-10-30: created (draft).
* 2026-10-30: `LoadError.kind` added (maintainer); text modules stay `skip`, outside the metric.
