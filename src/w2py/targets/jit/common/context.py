"""The context words (docs/specs/S0003, ARTIFACT.md part 3): fixed words, then the variable part
in the order memories, tables, globals, function imports, libcalls. Shared by every JIT
architecture."""

from dataclasses import dataclass

SAVED_SP, TRAP_CODE, INTERRUPT, FUEL, EPOCH_DEADLINE = 0, 1, 2, 3, 4
VSTACK_LIMIT, NSTACK_LIMIT, HOST_ERROR, HOST_BRIDGE = 5, 6, 7, 8
FIRST_VAR = 16  # words 9-15 are reserved, zero

# Status values returned by the entry, the bridge and every function (0 = success).
STATUS_INTERRUPT = 12
STATUS_HOST_ERROR = 13

# Libcalls (S0003): runtime services, one context word each after the imports, in this order.
MEMORY_GROW = 0
LIBCALLS = 1


@dataclass(frozen=True)
class Layout:
    """Word indices of an instance's context, from the module's counts."""

    memories: int = 0
    tables: int = 0  # 2 words each
    globals: int = 0  # 1 word each
    imports: int = 0

    def memory(self, i: int) -> int:
        """`base`; `size_bytes` is the next word."""
        return FIRST_VAR + 2 * i

    def global_(self, i: int) -> int:
        """Global i (imports first): its value, or for an imported one the address of it."""
        return FIRST_VAR + 2 * self.memories + 2 * self.tables + i

    def import_(self, i: int) -> int:
        return FIRST_VAR + 2 * self.memories + 2 * self.tables + self.globals + i

    def libcall(self, j: int) -> int:
        return self.import_(self.imports) + j

    @property
    def words(self) -> int:
        # at least memory 0's two words: the entry loads them whether or not the module has a memory
        return max(self.libcall(LIBCALLS), FIRST_VAR + 2)
