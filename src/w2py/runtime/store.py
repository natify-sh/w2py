"""The per-store state shared by the runtime and its target instances (docs/specs/S0002)."""

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from w2py.targets.base import TargetInstance


class StoreState:
    """One thread per store: the owner is recorded on the first entry; nesting is allowed."""

    def __init__(self, max_wasm_stack: int = 0) -> None:
        self.owner: int | None = None
        self.depth = 0  # entries into wasm code that are active (wasm -> host -> wasm nests)
        self.max_wasm_stack = max_wasm_stack  # 0: the target's default
        self.instances: list[TargetInstance] = []

    def enter(self) -> None:
        me = threading.get_ident()
        if self.owner is None:
            self.owner = me
        elif self.owner != me:
            raise RuntimeError("a store is used from one thread only")
        self.depth += 1

    def leave(self) -> None:
        self.depth -= 1
