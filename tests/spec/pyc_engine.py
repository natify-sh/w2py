# pyright: reportPrivateUsage=false, reportUnknownMemberType=false, reportUnknownVariableType=false
"""The `pyc` target as a SpecEngine (docs/specs/S0001): an adapter over package internals.

Values: i32/i64 are signed at the interface and unsigned inside `pyc`; f32/f64 are bit patterns at
the interface and Python floats inside (NaN payloads can be lost). References are unsupported.
"""

import struct
from collections.abc import Callable, Mapping, Sequence
from typing import Any, cast

from tests.spec.engine import Instance, LinkError, LoadError, Module, Trap, TrapCode, Value
from w2py.frontend.binary import ImplLimit, Malformed, parse
from w2py.targets.pyc import Trap as PycTrap
from w2py.targets.pyc import load, translate
from w2py.targets.pyc import runtime as rt

I32, I64, F32, F64 = 0x7F, 0x7E, 0x7D, 0x7C
_f32_i32 = cast(Callable[[int], float], rt._f32_i32)
_f64_i64 = cast(Callable[[int], float], rt._f64_i64)
_i32_f32 = cast(Callable[[float], int], rt._i32_f32)
_i64_f64 = cast(Callable[[float], int], rt._i64_f64)
_PARSE_ERRORS = (AssertionError, IndexError, ValueError, struct.error, UnicodeDecodeError)


class _Module:
    def __init__(self, wasm: bytes, parsed: dict[str, Any]) -> None:
        self.wasm = wasm
        self.imports: list[tuple[Any, ...]] = parsed["imports"]
        self.types: list[tuple[list[int], list[int]]] = parsed["types"]
        nimp = sum(1 for im in self.imports if im[0] == "func")
        gl = [im[3] for im in self.imports if im[0] == "global"] + [
            (g[0], g[1]) for g in parsed["globals"]
        ]
        self.kinds: dict[str, int] = {}
        self.functypes: dict[str, tuple[list[int], list[int]]] = {}
        self.globaltypes: dict[str, tuple[int, int]] = {}
        for name, kind, idx in parsed["exports"]:
            self.kinds[name] = kind
            if kind == 0:
                tidx = self.imports[idx][3] if idx < nimp else parsed["funcs"][idx - nimp]
                self.functypes[name] = self.types[tidx]
            elif kind == 3:
                self.globaltypes[name] = gl[idx]


class _Instance:
    def __init__(self, module: _Module, exports: dict[str, Any]) -> None:
        self.module = module
        self.exports = exports


def _to_internal(ty: int, v: Value) -> Any:
    if not isinstance(v, int):
        raise NotImplementedError("reference values unsupported")
    if ty == I32:
        return v & 0xFFFFFFFF
    if ty == I64:
        return v & 0xFFFFFFFFFFFFFFFF
    if ty == F32:
        return _f32_i32(v)
    if ty == F64:
        return _f64_i64(v)
    raise NotImplementedError(f"value type {ty:#x} unsupported")


def _from_internal(ty: int, v: Any) -> Value:
    if ty == I32:
        return cast(int, v) - ((v & 0x80000000) << 1)
    if ty == I64:
        return cast(int, v) - ((v & 0x8000000000000000) << 1)
    if ty == F32:
        return _i32_f32(v)
    if ty == F64:
        return _i64_f64(v)
    raise NotImplementedError(f"value type {ty:#x} unsupported")


def _trap(e: PycTrap) -> Exception:
    code = getattr(e, "code", None)
    if code is None:
        return e
    return Trap(TrapCode[code], str(e))


class PycEngine:
    def load(self, wasm: bytes) -> Module:
        try:
            try:
                parsed = parse(wasm)
            except (ImplLimit, NotImplementedError) as e:  # not a verdict on the module
                raise LoadError(f"{type(e).__name__}: {e}") from None
            translate(wasm)  # decode errors surface here; NotImplementedError is a target gap
        except Malformed as e:
            raise LoadError(str(e), "malformed") from None
        except _PARSE_ERRORS as e:
            raise LoadError(f"{type(e).__name__}: {e}") from None
        return _Module(wasm, parsed)

    def instantiate(self, module: Module, imports: Mapping[str, Instance]) -> Instance:
        m = cast(_Module, module)
        wired: dict[str, dict[str, Any]] = {}
        kinds = {"func": 0, "table": 1, "mem": 2, "global": 3}
        for kind, mod, name, desc in m.imports:
            src = imports.get(mod)
            if src is None or cast(_Instance, src).module.kinds.get(name) != kinds[kind]:
                raise LinkError(f"unknown import {mod}.{name}")
            inst = cast(_Instance, src)
            ex = inst.exports[name]
            if kind == "func":
                ok = inst.module.functypes[name] == m.types[desc]
            elif kind == "global":
                ok = inst.module.globaltypes[name] == desc
            else:
                size = len(ex) >> 16 if kind == "mem" else len(ex.t)
                ok = size >= desc[0] and (
                    desc[1] is None or (ex.max is not None and ex.max <= desc[1])
                )
            if not ok:
                raise LinkError(f"incompatible import type {mod}.{name}")
            wired.setdefault(mod, {})[name] = ex
        try:
            return _Instance(m, load(m.wasm, wired))
        except PycTrap as e:
            raise _trap(e) from None

    def call(self, instance: Instance, name: str, args: Sequence[Value]) -> list[Value]:
        inst = cast(_Instance, instance)
        ps, rs = inst.module.functypes.get(name, (None, None))
        if ps is None or rs is None:
            raise LookupError(f"no exported function {name}")
        raw = [_to_internal(t, a) for t, a in zip(ps, args, strict=True)]
        try:
            res = inst.exports[name](*raw)
        except PycTrap as e:
            raise _trap(e) from None
        if len(rs) == 0:
            return []
        vals = [res] if len(rs) == 1 else list(res)
        return [_from_internal(t, v) for t, v in zip(rs, vals, strict=True)]

    def get_global(self, instance: Instance, name: str) -> Value:
        inst = cast(_Instance, instance)
        gt = inst.module.globaltypes.get(name)
        if gt is None:
            raise LookupError(f"no exported global {name}")
        return _from_internal(gt[0], inst.exports[name].get())
