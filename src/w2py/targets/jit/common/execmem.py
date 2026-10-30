"""Executable memory, W^X (R2, S0003): map read-write, write the code, flip to read-execute; a page
is never writable and executable at once. POSIX: mmap/mprotect/munmap through ctypes. Windows:
VirtualAlloc/VirtualProtect/VirtualFree (the D23 shape; not tested yet)."""

import ctypes
import mmap
import os
import sys


class ExecMem:
    """A code area of `size` bytes (rounded up to pages): `write` while read-write, then `seal`."""

    def __init__(self, size: int) -> None:
        page = mmap.PAGESIZE
        self.size = max(page, (size + page - 1) // page * page)
        self.addr = _alloc(self.size)
        self.sealed = False

    def write(self, code: bytes) -> None:
        if self.sealed or not self.addr:
            raise ValueError("code area is sealed or freed")
        if len(code) > self.size:
            raise ValueError("code does not fit")
        ctypes.memmove(self.addr, code, len(code))

    def seal(self) -> None:
        """Read-write -> read-execute."""
        _protect(self.addr, self.size)
        self.sealed = True

    def free(self) -> None:
        if self.addr:
            addr, self.addr = self.addr, 0
            _free(addr, self.size)


def load(code: bytes) -> ExecMem:
    """Map, write and seal `code`."""
    mem = ExecMem(len(code))
    try:
        mem.write(code)
        mem.seal()
    except BaseException:
        mem.free()
        raise
    return mem


if sys.platform == "win32":
    # UNTESTED: the D23 shape for Windows; no Windows runner exercises it yet.
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.VirtualAlloc.restype = ctypes.c_void_p
    _k32.VirtualAlloc.argtypes = [
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_uint32,
        ctypes.c_uint32,
    ]
    _k32.VirtualProtect.argtypes = [
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    _k32.VirtualFree.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32]

    def _alloc(size: int) -> int:
        addr = _k32.VirtualAlloc(None, size, 0x3000, 0x04)  # MEM_COMMIT|MEM_RESERVE, PAGE_READWRITE
        if not addr:
            raise ctypes.WinError(ctypes.get_last_error())
        return addr

    def _protect(addr: int, size: int) -> None:
        old = ctypes.c_uint32()
        if not _k32.VirtualProtect(addr, size, 0x20, ctypes.byref(old)):  # PAGE_EXECUTE_READ
            raise ctypes.WinError(ctypes.get_last_error())

    def _free(addr: int, size: int) -> None:
        if not _k32.VirtualFree(addr, 0, 0x8000):  # MEM_RELEASE
            raise ctypes.WinError(ctypes.get_last_error())

else:
    _libc = ctypes.CDLL(None, use_errno=True)
    _libc.mmap.restype = ctypes.c_void_p
    _libc.mmap.argtypes = [
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_long,  # off_t on LP64 Linux/macOS (the probe refuses 32-bit processes)
    ]
    _libc.mprotect.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
    _libc.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]

    def _oserror() -> OSError:
        err = ctypes.get_errno()
        return OSError(err, f"executable memory: {os.strerror(err)}")

    def _alloc(size: int) -> int:
        flags = mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS
        addr = _libc.mmap(None, size, mmap.PROT_READ | mmap.PROT_WRITE, flags, -1, 0)
        if addr in (None, ctypes.c_void_p(-1).value):
            raise _oserror()
        return addr

    def _protect(addr: int, size: int) -> None:
        if _libc.mprotect(addr, size, mmap.PROT_READ | mmap.PROT_EXEC) != 0:
            raise _oserror()

    def _free(addr: int, size: int) -> None:
        if _libc.munmap(addr, size) != 0:
            raise _oserror()
