---
title: JIT context ABI and entry (phase 3, x86-64)
kind: spec
id: S0003
status: approved
superseded_by:
related: [D10, D14, D15, D18, D23, D24, R2, R3, R6, S0002]
owner: maintainer
updated: 2026-10-30
---

# S0003 JIT context ABI and entry (phase 3, x86-64)

## Summary
What `jit-x86_64` code expects from the runtime in phase 3, and what the runtime may rely on: the
context words in use, the entry trampolines, register roles, the two stacks, how traps, host calls
and host exceptions return, interrupt polling, re-entrancy, and the ISA-level rule. It refines
[ARTIFACT.md](../ARTIFACT.md) part 3 (same word numbering, same version 1, which phase 5 fixes);
it is not a new format. The runtime side is [S0002](S0002-target-interface.md).

## Goals / Non-goals
* Goals: every rule LESSONS.md 1–9 and RISKS.md R2/R3 ask for, stated once and testable; an ABI a
  second architecture can follow by filling in its own register table (D23).
* Non-goals, deferred: the artifact file (ARTIFACT.md part 2: header, sections, `TRAPMAP`,
  `RELOCS`, thin/fat files) and serialization — phase 5; v128 slots, multi-memory — stage 2 (tables, funcref and `call_indirect` are in step 3d, S0002); `FUEL`, `EPOCH_DEADLINE`; guard pages and the signal handler
  (R6) — phase 5; `Trap.frames`; CPU feature detection (phase 3 emits the baseline only); the C
  reference loader (optional, D18).

## Interface

### Context (`uint64_t ctx[]`, one per instance, stable address, 8-byte aligned)
| word | name | phase 3 |
|---|---|---|
| 0 | `SAVED_SP` | **unused, zero**: traps return through frames; kept for the phase-5 fault handler |
| 1 | `TRAP_CODE` | written by the trampoline when it returns a non-zero status |
| 2 | `INTERRUPT` | non-zero → the next poll returns status `INTERRUPT` (12); any thread may set it |
| 3, 4 | `FUEL`, `EPOCH_DEADLINE` | deferred, zero |
| 5 | `VSTACK_LIMIT` | value-stack address a frame must not cross → `STACK_OVERFLOW` |
| 6 | `NSTACK_LIMIT` | lowest native `rsp` allowed → `STACK_OVERFLOW`; written by the runtime before each entry |
| 7 | `HOST_ERROR` | **unused**: replaced by the host call's status (below; proposed amendment) |
| 8 | `HOST_BRIDGE` | **new (proposed)**: address of the host-call bridge for the host's convention |
| 9–15 | reserved | zero |
| 16 … | memory *i*: `base`, `size_bytes` | phase 3: at most one memory |
| … | table *i*: `base`, `size` | layout kept; a module that uses a table is `Unsupported` (S0002) |
| … | global *i*: 1 word | defined: the value; imported: a pointer to the owner's word (same target, S0002) |
| … | function import *i*: 1 word | address of a host function (signature below) |
| … | libcall *j*: 1 word | **new (proposed)**: same signature; the runtime services the module uses (`memory.grow`, `table.grow`, segment init — never computation, D25), in a fixed order |

### Calling conventions
```c
int32_t entry(uint64_t *ctx, uint64_t *frame, const void *code);   /* per host convention */
int32_t host (uint64_t *ctx, uint64_t *slots);                     /* imports and libcalls */
```
* `frame[0..n)` holds the arguments, `frame[0..m)` the results after a 0 return: one slot per value,
  i32 zero-extended, i64 as is, f32/f64 bit patterns (ARTIFACT.md).
* Return value (both): `0`, or a trap code of ARTIFACT.md part 3, or `13` `HOST_ERROR`.
* **`host` is uniform (proposed amendment)**: the arguments are in `slots[0..n)`, the function writes
  its results to `slots[0..m)`. One C signature for every import makes one bridge per convention
  possible, gives multi-value and a trap/exception status for free, and removes the C-ABI
  zero-extension hazard (LESSONS 5: results come back through slots the runtime masks). ARTIFACT.md
  part 3's typed `ret f(ctx, args…)` is replaced.

### Register roles (x86-64, all conventions)
| register | role | across a wasm call | across a host call (bridge) |
|---|---|---|---|
| `r15` | context | preserved | preserved |
| `r12` | value-stack frame base (slot k = `[r12 + 8k]`) | caller moves it for frame overlap, restores | preserved |
| `r13`, `r14` | memory 0 base, size | preserved | **reloaded** from the context after the bridge |
| `rax` | status on return; top-of-stack cache inside a function | clobbered | clobbered |
| `rcx`, `rdx` | scratch (shift count, `idiv`, bridge arguments) | clobbered | clobbered |
| `rbx rbp rsi rdi r8–r11` | pool: pinned locals (`w2jit2`) | callee-saved (a function pushes the pins it uses) | preserved by the bridge |
| `xmm0–xmm5` | float scratch; floats live in slots | clobbered | clobbered |
| `xmm6–xmm15` | never used in phase 3 (Win64 callee-saved: no save needed) | — | — |

### Trampolines and the host bridge (one of each per convention, in the code area)
* **Entry, SysV** (`rdi`, `rsi`, `rdx`): `endbr64`; push `rbx rbp r12–r15` (+ pad to 16); `r15 ← ctx`,
  `r12 ← frame`, `r13/r14 ←` memory 0 words; `call rdx`; if `eax ≠ 0` store it in `ctx[1]`; pop;
  `ret`. **Win64 shape** (`rcx`, `rdx`, `r8`): also push `rsi rdi` (callee-saved there); the same
  body. The code itself is identical under both (D23).
* **Bridge**: wasm code sets `eax` = context word index of the import or libcall, `rdx` = slot
  pointer of the arguments, then `call [r15 + 8*HOST_BRIDGE]`. SysV bridge: `endbr64`; push the pool
  registers SysV lets a callee clobber (`rsi rdi r8–r11`; all of them, not only the caller's pins:
  frames above keep pins there too, LESSONS 2) + pad; `rdi ← r15`, `rsi ← rdx`; `call [r15+8*rax]`;
  pop; `ret` with the status in `eax`. Win64 bridge: saves `r8–r11` only, passes `rcx`/`rdx`, and
  reserves the 32-byte shadow space. The wasm call site then reloads `r13`/`r14` and does
  `test eax, eax; jnz unwind`.

## Semantics

**Stack alignment** (LESSONS 3). `rsp ≡ 0 (mod 16)` at every `call` executed in the code area;
each function's prologue pushes its pins plus a pad to keep it. A test build option makes every call
site check it (`test spl, 15; jnz` → trap) and the encoder tests run with it on.

**Two stacks** (ARCHITECTURE.md, "Calls, stack depth and recursion"). Values and locals live on the
**value stack** the runtime maps per store (frame overlap as in `w2jit`); every prologue checks
`r12 + 8·frame_slots ≤ ctx[VSTACK_LIMIT]`. The **native stack** holds only return addresses, pushed
pins and pads (≤ 80 bytes per frame, never more than one page at once: no stack probes on Windows,
D23; the SysV red zone is never used). Every prologue checks `rsp ≥ ctx[NSTACK_LIMIT]`. The runtime
sets that limit at the first JIT entry of the store on the stack, whatever the store's depth:
`limit = sp − min(max_wasm_stack, (sp − low) / 2)`, `max_wasm_stack` default 512 KiB (Wasmtime's,
D6), `low` the thread's lowest stack address (`pthread_getattr_np`; macOS `pthread_get_stackaddr_np
− stacksize`; Windows `GetCurrentThreadStackLimits`); if `low` cannot be read the budget is
`min(max_wasm_stack, 128 KiB)`. The wasm part never takes more than half of the stack below the
entry, so a host function called from the deepest frame has at least as much (a host that needs more
than its share can still overflow the C stack on CPython < 3.14, which has no C-stack check — as it
would on so small a thread without wasm; RISKS.md R5). `sp` comes from a stub run through the same
entry path, `endbr64; mov [r12], rsp; xor eax, eax; ret`: it writes rsp as a wasm function sees it
to frame[0] (a 32-bit status cannot carry it). The value stack is 1 MiB per store, mapped once,
limit in word 5; a nested entry starts above the host call's slots. Both overflows trap with
`STACK_OVERFLOW` (D10).

**Traps return through frames** (R2: CET shadow stacks; no `rsp` reset). A trap point jumps to a
cold per-function stub `mov eax, code; jmp .unwind`; `.unwind` is the function's epilogue (pops,
`ret`) with `eax` intact. After every wasm → wasm call: `test eax, eax; jnz .unwind`. The trampoline
returns the status to the runtime, which raises `Trap(TrapCode(status))` (S0002). Every `ret` matches
its `call`. Checks the hardware does not do the wasm way are explicit: `idiv` with divisor 0 →
`INTEGER_DIVISION_BY_ZERO`, `INT_MIN / -1` → `INTEGER_OVERFLOW` (`div_s`) or result 0 (`rem_s`),
because the CPU faults there and a fault would end the process (D10).

**Flags** (LESSONS 1). Between a flags producer and its consumer only `mov`/`lea` are emitted; the
backend interface exposes compare-and-branch / compare-and-set / select-on-compare as single
operations (S0002, proposed decision), so the shared core never places code between them.

**Zero-extension** (LESSONS 5). i32 values are kept zero-extended in 64-bit registers and slots:
32-bit operations and moves for i32; an i32 used as an address is already a valid 64-bit offset.

**Host calls and host exceptions.** The runtime makes one ctypes callback per import of type
`CFUNCTYPE(c_int32, c_void_p, c_void_p)`; its whole body is a `try` (a ctypes callback that
raises only prints the error and returns 0, i.e. "success"): read arguments from the slots, call
the `HostFunc`, write results masked to their width, return 0; on `Trap` return its code; on any other `BaseException` store it in
`StoreState` and return 13. The code unwinds as for a trap; the runtime re-raises the stored
exception at the outermost entry. The GIL is released by the entry (`CFUNCTYPE`) and taken back by
callbacks (ARCHITECTURE.md, "Long calls and the GIL").

**Libcalls.** An operation a backend does not implement calls a Python helper through the bridge,
with its own word after the imports; the helper set is shared by all architectures
(`jit/common/libcalls.py`); `memory.grow` is one (the runtime commits pages and rewrites the memory
words; the reload after every bridge call picks them up). Memory never moves under running code
(LESSONS 7): the runtime reserves the maximum up front.

**Interrupt polling** (D14, LESSONS 8; on by default, maintainer 2026-10-30 — timeouts are a runtime
timer setting this word; fuel is a separate opt-in, later). At every function entry and loop header:
`cmp qword [r15 + 16], 0; jne` → status 12. `StoreState.interrupt()` sets word 2 in every live
context of the store; the runtime clears it when it raises the `INTERRUPT` trap.

**Re-entrancy** (LESSONS 4). The trampoline keeps nothing in the context except `TRAP_CODE`, so
wasm → host → wasm nests: the inner entry saves and restores the callee-saved registers, and a trap
inside it returns to its own trampoline. The runtime starts a nested frame above the host call's
slots (`slots + 8·max(n, m)` of the import — everything above the arguments is free under frame
overlap) and checks it against the value-stack limit.

**Executable memory** (R2). W^X: map RW (`mmap`), write, flip to RX (`mprotect` via ctypes), never
both; the S0002 probe does exactly this with a `ret` stub and calls it once. `endbr64` (a hint NOP on
CPUs without CET) at every indirectly reached entry: trampolines, bridges, the stack-pointer stub.

**ISA level** (D24). Phase-3 code is x86-64-v1. A declared variant (`roundsd` for `f64.trunc`,
x86-64-v2) is emitted only when the configured level declares it; its baseline twin (SSE2 sequence)
is mandatory. Jump tables are placed in a data area after the code and recorded, so only code is
disassembled. Check: `objdump -D -b binary -m i386:x86-64` of every code range; every mnemonic must
be in the x86-64-v1 allowlist (`endbr64` allowed). Measured on `w2jit2`'s CoreMark code today:
`roundsd` (v2) and jump-table bytes decoded as `(bad)`/`outsb` both appear — the check fails it, as
it should.

**What a second architecture defines** (phase 4), in its own table like the one above: context,
frame, memory base/size, status, scratch and pool registers; registers no supported OS reserves
(AArch64: never `x18`; RISC-V: never `gp`/`tp`); entry and bridge per convention (`aapcs64`,
`win-arm64`; RISC-V psABI) with the callee-saved sets; landing pads (`bti c`) where `endbr64` is
used here; stack alignment (16 on both); compares without flags (RISC-V); division checks (AArch64
and RISC-V do not trap on 0: explicit checks give the wasm traps); shift-count masking; immediate
and offset ranges; its baseline (ARTIFACT.md part 1) and the disassembler allowlist; encoder tests
against `llvm-mc`.

## Test plan
In addition to S0002 steps 2–6, `tests/jit/` (each test runs on every JIT target the machine has):
```
uv run pytest tests/jit -k "entry or trap or reentry or host or interrupt or stack"
```
* entry: results of i32/i64 functions with 0–6 arguments; i32 results zero-extended;
* traps: each code in the phase-3 set returns its status and leaves the instance usable; a trap
  three calls deep returns through every frame (count frames by a host call before the trap);
* re-entry: wasm → host → wasm → trap: the inner `Trap` reaches the host function, which catches it
  and returns normally; the outer call completes; nesting 50 deep;
* host: a host function raising `ValueError` / `KeyboardInterrupt` reaches the caller as itself;
  pinned locals of every frame above survive a host call that clobbers `rsi rdi r8–r11`
  (a host written as machine code that zeroes them);
* interrupt: a `loop` without exit stops with `INTERRUPT` within 50 ms of `interrupt()` from another
  thread; stack: unbounded recursion → `STACK_OVERFLOW` on both stacks (small budgets), process alive;
* ISA: the objdump check on all spec modules and CoreMark; a planted `roundsd` fails it.

## Alternatives considered
* **Typed host signatures** (ARTIFACT.md part 3 as written): one ctypes type per signature, C-ABI
  zero-extension of results, a separate `HOST_ERROR` check after each call, no multi-value path; the
  bridge would have to marshal per signature. Rejected for phase 3, pending approval.
* **Unwind by resetting `rsp` to `SAVED_SP`** (the PoC): faults under CET shadow stacks (R2), needs a
  linked list for nesting. Rejected.
* **Saving only the caller's own pins around host calls**: wrong (LESSONS 2).
* **The runtime passing the native stack limit as an entry argument**: changes the entry signature
  for a value that changes only per outermost entry. Rejected.

## Open questions
* None at approval. Resolved: the ARTIFACT.md part 3 v1 amendments are accepted without a version bump
  (v1 is fixed in phase 5); ARTIFACT.md and DECISIONS.md are updated in the implementation step.
  The native budget is not a fixed 512 KiB everywhere: it is capped by the real stack size of the
  entering thread (macOS secondary threads have 512 KiB in total), and CI checks deep recursion on the
  main and a secondary thread on every OS runner where the JIT runs (Windows x86-64 in phase 3).

## Changes
* 2026-10-30: created (draft).
* 2026-10-30: approved by the maintainer (amendments accepted; budget capped by the thread's stack).
* 2026-10-30: linear memory reserves 4 GiB of address space per 32-bit memory and commits on grow (never moves; Wasmtime `memory_reservation` = 4 GiB, `memory_guard_size` = 32 MiB in crates/environ/src/tunables.rs — the guard comes in phase 5); explicit bounds checks in phase 3; libcalls are runtime services only (maintainer).
* 2026-10-30 (step 3a, after review): native limit rule above (first JIT entry, half of the stack
  below the entry, 128 KiB fallback); the rsp stub writes frame[0]; value stack 1 MiB per store.
  Interrupt: `interrupt()` (any thread, under the store's lock) sets word 2 in every context of the
  store; a nested entry returning INTERRUPT leaves it set so the frames below stop at their next poll;
  the outermost JIT entry returning INTERRUPT clears word 2 in every context under the same lock
  before raising (consuming every request made until then); a request made later, or while no code
  runs, stays set and stops the next call at its first poll. `TRAP_CODE` (word 1) is written on a
  non-zero status only. A host callback returns status 0 only after its results are written; an
  asynchronous exception arriving in the callback's last handler can still escape to ctypes (which
  then returns 0).
