"""The runtime side of the JIT ABI (docs/specs/S0003), shared by every architecture: the context,
the value stack, both stack limits, the entry call with status -> `Trap`, host callbacks that never
raise, nested entries, interrupt, linear memories (reserved, committed on grow, never moved).

Step 3a: an `Instance` is built from code assembled elsewhere (tests; the compiler in 3b)."""

import ctypes
import mmap
import os
import sys
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from w2py.runtime.store import StoreState
from w2py.targets.base import Extern, HostError, HostFunc, Raw, Trap, TrapCode
from w2py.targets.jit.common.context import (
    HOST_BRIDGE,
    INTERRUPT,
    NSTACK_LIMIT,
    STATUS_HOST_ERROR,
    STATUS_INTERRUPT,
    VSTACK_LIMIT,
    Layout,
)
from w2py.targets.jit.common.execmem import ExecMem, Reservation, load

ENTRY = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
HOST = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p)
VSTACK_BYTES = 1 << 20  # per store
MEMORY_RESERVATION = (
    1 << 32
)  # Wasmtime's default `memory_reservation` (crates/environ/src/tunables.rs)
MAX_PAGES = 1 << 16
DEFAULT_BUDGET = 512 << 10  # Wasmtime's default `max_wasm_stack` (D6)
UNKNOWN_STACK_BUDGET = 128 << 10  # when the thread's stack bounds cannot be read or are implausible
PLAUSIBLE_STACK = 1 << 30  # more stack than this below the entry: the bounds are not trusted


@dataclass(frozen=True)
class Trampolines:
    """Addresses of an architecture's trampolines for the host's convention."""

    entry: int
    bridge: int
    sp_stub: int
    keep: object = None  # the code area


class _StoreStacks:
    """Per store: the value stack, the JIT entries active on it, and the native limit set by the
    first of them."""

    def __init__(self) -> None:
        self.vstack = Reservation(VSTACK_BYTES, VSTACK_BYTES)
        self.vtop = self.vstack.addr + VSTACK_BYTES
        self.vsp = self.vstack.addr  # next free slot: above the innermost host call's slots
        self.nlimit = 0
        self.active = 0  # JIT entries in progress (wasm -> host -> wasm nests them)


def _stacks(store: StoreState) -> _StoreStacks:
    if not isinstance(store.target_state, _StoreStacks):
        store.target_state = _StoreStacks()
    return store.target_state


_thread = threading.local()


def _stack_low() -> int | None:
    """Lowest address of the current thread's native stack (cached per thread), None if unknown."""
    if not hasattr(_thread, "low"):
        _thread.low = _read_stack()[0]
    return _thread.low


def linux_main_low(high: int, soft: int | None) -> int | None:
    """Lowest usable address of the Linux main thread's stack, which the kernel grows on demand up
    to RLIMIT_STACK below its top `high` (glibc's rule for pthread_getattr_np; musl reports only
    the part mapped so far). `soft` None = unlimited: unknown. One page above the guard is kept
    clear, as on macOS."""
    if soft is None or soft <= 0:
        return None
    return high - min(soft, PLAUSIBLE_STACK) + mmap.PAGESIZE


def _linux_stack_vma_top() -> int | None:
    """End of the `[stack]` mapping: RLIMIT_STACK counts from there, above the args and
    environment, so it is higher than the top pthread_getattr_np reports."""
    try:
        with open("/proc/self/maps") as f:
            for line in f:
                if line.rstrip().endswith("[stack]"):
                    return int(line.split("-", 1)[1].split()[0], 16)
    except (OSError, ValueError, IndexError):
        pass
    return None


def _read_stack() -> tuple[int | None, str]:
    """(low, the rule that gave it)."""
    vp, size_t = ctypes.c_void_p, ctypes.c_size_t
    try:
        if sys.platform == "win32":
            lo, hi = vp(), vp()
            k32 = ctypes.WinDLL("kernel32")
            k32.GetCurrentThreadStackLimits(ctypes.byref(lo), ctypes.byref(hi))
            return lo.value, "GetCurrentThreadStackLimits"
        libc = ctypes.CDLL(None)
        libc.pthread_self.restype = vp
        me = libc.pthread_self()
        if sys.platform == "darwin":
            # stackaddr is the top; the main thread's reported size may exceed its rlimit, so
            # take the smaller; leave one guard's worth (a page) above the guard page as unusable
            libc.pthread_get_stackaddr_np.restype = vp
            libc.pthread_get_stacksize_np.restype = size_t
            libc.pthread_get_stackaddr_np.argtypes = libc.pthread_get_stacksize_np.argtypes = [vp]
            top, size = libc.pthread_get_stackaddr_np(me), libc.pthread_get_stacksize_np(me)
            if libc.pthread_main_np():
                import resource

                soft = resource.getrlimit(resource.RLIMIT_STACK)[0]
                size = min(size, soft) if soft > 0 else size
            return (top - size + mmap.PAGESIZE if top else None), "pthread_get_stack*_np"
        attr = ctypes.create_string_buffer(256)  # >= sizeof(pthread_attr_t) on glibc and musl
        libc.pthread_getattr_np.argtypes = [vp, ctypes.c_char_p]
        if libc.pthread_getattr_np(me, attr) != 0:
            return None, "pthread_getattr_np failed"
        addr, size = vp(), size_t()
        libc.pthread_attr_getstack(attr, ctypes.byref(addr), ctypes.byref(size))
        libc.pthread_attr_destroy(attr)
        if sys.platform.startswith("linux") and threading.get_native_id() == os.getpid():
            import resource

            soft = resource.getrlimit(resource.RLIMIT_STACK)[0]
            finite = None if soft == resource.RLIM_INFINITY else soft
            high = _linux_stack_vma_top() or (addr.value or 0) + size.value
            return linux_main_low(high, finite), "linux main thread: top - RLIMIT_STACK"
        return addr.value, "pthread_getattr_np"
    except (OSError, AttributeError):
        return None, "unavailable"


def native_budget(sp: int, low: int | None, budget: int) -> int:
    """Native stack wasm code may use below `sp` (S0003, D10): never more than half of the
    thread's stack left below `sp`, so a host function called from the deepest wasm frame still
    has at least as much; if the bounds are unknown or implausible (`sp` not within 1 GiB above
    `low`: e.g. an unlimited RLIMIT_STACK on the main thread), at most UNKNOWN_STACK_BUDGET."""
    if low is None or not 0 < sp - low <= PLAUSIBLE_STACK:
        return min(budget, UNKNOWN_STACK_BUDGET)
    return min(budget, (sp - low) // 2)


class Memory:
    """A 32-bit linear memory: 4 GiB of address space reserved, pages committed on grow; the base
    never changes. `grow` rewrites the size word of every context bound to it."""

    def __init__(self, pages: int, maximum: int | None) -> None:
        self.res = Reservation(MEMORY_RESERVATION, pages << 16)
        self.pages = pages
        self.max = MAX_PAGES if maximum is None else min(maximum, MAX_PAGES)
        self.users: list[tuple[ctypes.Array[ctypes.c_uint64], int]] = []

    def bind(self, ctx: "ctypes.Array[ctypes.c_uint64]", word: int) -> None:
        ctx[word], ctx[word + 1] = self.res.addr, self.pages << 16
        self.users.append((ctx, word))

    def grow(self, delta: int) -> int:
        """memory.grow: the old size in pages, or -1."""
        old = self.pages
        if delta > self.max - old:
            return -1
        try:
            self.res.commit((old + delta) << 16)
        except OSError:
            return -1
        self.pages = old + delta
        for ctx, word in self.users:
            ctx[word + 1] = self.pages << 16
        return old


class Instance:
    """`funcs`: (code offset, result count) of each defined function; function indices count the
    imports first. `imports`: (host function, parameter count, result masks)."""

    def __init__(
        self,
        store: StoreState,
        tramp: Trampolines,
        code: bytes,
        funcs: Sequence[tuple[int, int]],
        imports: Sequence[tuple[HostFunc, int, Sequence[int]]] = (),
        memory: Memory | None = None,
    ) -> None:
        self.store, self.tramp, self.funcs = store, tramp, funcs
        self.nimports = len(imports)
        self.layout = Layout(memories=1 if memory else 0, imports=len(imports))
        self.ctx = (ctypes.c_uint64 * self.layout.words)()
        self.ctx[HOST_BRIDGE] = tramp.bridge
        self.code: ExecMem = load(code)
        self.entry = ENTRY(tramp.entry)
        self.callbacks = [HOST(self._host(*im)) for im in imports]  # kept alive here
        for i, cb in enumerate(self.callbacks):
            self.ctx[self.layout.import_(i)] = ctypes.cast(cb, ctypes.c_void_p).value or 0
        if memory:
            memory.bind(self.ctx, self.layout.memory(0))
        self.memory = memory
        store.instances.append(self)

    def call(self, func_index: int, args: Sequence[Raw]) -> list[Raw]:
        offset, nres = self.funcs[func_index - self.nimports]
        store, st, ctx = self.store, _stacks(self.store), self.ctx
        store.enter()
        st.active += 1
        try:
            if st.active == 1:  # no JIT frame below, whatever the store's depth
                st.vsp = st.vstack.addr
                st.nlimit = self._nstack_limit(st.vsp)
            frame, n = st.vsp, max(len(args), nres)
            if frame + 8 * n > st.vtop:
                raise Trap(TrapCode.STACK_OVERFLOW, "call stack exhausted")
            slots = (ctypes.c_uint64 * n).from_address(frame)
            slots[: len(args)] = list(args)
            ctx[NSTACK_LIMIT], ctx[VSTACK_LIMIT] = st.nlimit, st.vtop
            status = self.entry(ctypes.addressof(ctx), frame, self.code.addr + offset)
            if status:
                raise self._error(status)
            return list(slots[:nres])
        finally:
            st.active -= 1
            store.leave()

    def _nstack_limit(self, frame: int) -> int:
        """At the first JIT entry on the stack: rsp as wasm code sees it (the stub), then
        `native_budget`."""
        if self.entry(ctypes.addressof(self.ctx), frame, self.tramp.sp_stub):
            raise RuntimeError("stack-pointer stub failed")
        sp = ctypes.c_uint64.from_address(frame).value
        return sp - native_budget(sp, _stack_low(), self.store.max_wasm_stack or DEFAULT_BUDGET)

    def _error(self, status: int) -> BaseException:
        if status == STATUS_HOST_ERROR:
            exc, self.store.pending = self.store.pending, None
            if exc is None:
                return RuntimeError("HOST_ERROR status without a pending exception")
            return exc if self.store.depth == 1 else HostError(exc)
        if status == STATUS_INTERRUPT and _stacks(self.store).active == 1:
            # The outermost JIT entry consumes every interrupt() made so far, in every context of
            # the store (under the store's lock); one made later stays set. A nested entry leaves
            # the word set, so frames below stop at their next poll even if a host catches this.
            with self.store.interrupt_lock:
                for inst in self.store.instances:
                    if isinstance(inst, Instance):
                        inst.ctx[INTERRUPT] = 0
        try:
            return Trap(TrapCode(status))
        except ValueError:
            return RuntimeError(f"JIT code returned status {status}")

    def _host(self, f: HostFunc, nparams: int, masks: Sequence[int]) -> Callable[[int, int], int]:
        """A ctypes callback must not raise: ctypes would print the error and return 0, i.e.
        success with unwritten results. Every outcome becomes a status; a host exception waits in
        the store. 0 is returned only after the results are written; an asynchronous exception
        (KeyboardInterrupt) raised inside the handlers of `serve` is caught by the outer handler.
        What remains: one arriving inside the outer handler or the final `finally` escapes."""
        store, n = self.store, max(nparams, len(masks))

        def serve(slots: int) -> int:
            try:
                vals = (ctypes.c_uint64 * n).from_address(slots)
                res = f(vals[:nparams])
                for i, (r, m) in enumerate(zip(res, masks, strict=True)):
                    vals[i] = r & m
                return 0
            except Trap as e:
                return int(e.code)
            except BaseException as e:
                store.pending = e.exc if isinstance(e, HostError) else e
                return STATUS_HOST_ERROR

        def callback(ctx: int, slots: int) -> int:
            st = _stacks(store)
            saved, st.vsp = st.vsp, slots + 8 * n  # a nested entry starts above these slots
            try:
                return serve(slots)
            except BaseException as e:
                store.pending = store.pending or e
                return STATUS_HOST_ERROR
            finally:
                st.vsp = saved

        return callback

    def interrupt(self) -> None:
        """Any thread: the next poll of this instance's code returns INTERRUPT."""
        self.ctx[INTERRUPT] = 1

    def export(self, name: str) -> Extern:
        raise LookupError(f"no export {name}")  # exports come with the compiler (3b)

    def limits(self, name: str) -> tuple[int, int | None]:
        raise LookupError(f"no export {name}")

    def global_get(self, global_index: int) -> Raw:
        raise LookupError(f"no global {global_index}")
