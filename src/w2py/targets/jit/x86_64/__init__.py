"""The `jit-x86_64` target (S0002, S0003). Step 2: the probe only; compilation comes in step 3."""

import ctypes
import platform
import sys
from collections.abc import Sequence
from typing import TYPE_CHECKING

from w2py.targets.base import Extern, TargetInstance, Unsupported, ValidModule

if TYPE_CHECKING:
    from w2py.runtime.store import StoreState

_NOT_YET = "not implemented yet"


class JitX86_64Target:
    name = "jit-x86_64"
    version = 0
    # S0002 step 2 guard: `target_order("auto")` skips a target with `ready = False`. Step 3 deletes
    # this attribute together with the check in base._probe.
    ready = False

    def probe(self) -> str | None:
        """None if x86-64 code can be mapped W^X and called here; else the reason."""
        if platform.machine().lower() not in ("x86_64", "amd64") or sys.maxsize < 1 << 32:
            return (
                f"not an x86-64 process ({platform.machine()}, {sys.maxsize.bit_length() + 1} bit)"
            )
        try:
            from w2py.targets.jit.common.execmem import load
            from w2py.targets.jit.x86_64.encoder import RAX, Asm

            a = Asm()
            a.endbr64()
            a.mov_imm(RAX, 42)
            a.ret()
            mem = load(a.finish())
            try:
                got = ctypes.CFUNCTYPE(ctypes.c_int)(mem.addr)()
            finally:
                mem.free()
        except Exception as e:
            return f"executable memory unavailable: {e!r}"
        return None if got == 42 else f"probe function returned {got}"

    def supports(self, module: ValidModule) -> str | None:
        return _NOT_YET

    def compile(self, module: ValidModule) -> object:
        raise Unsupported(self.name, _NOT_YET)

    def instantiate(
        self, compiled: object, imports: Sequence[Extern], store: "StoreState"
    ) -> TargetInstance:
        raise Unsupported(self.name, _NOT_YET)
