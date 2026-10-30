"""Spec-suite runner (docs/specs/S0001): drives a SpecEngine over the converted wast commands."""

import argparse
import json
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any, NamedTuple

from tests.spec import fetch
from tests.spec.engine import (
    ExternRef,
    FuncRef,
    Instance,
    LinkError,
    LoadError,
    Module,
    SpecEngine,
    Trap,
    TrapCode,
    Value,
)

CACHE_DIR = fetch.CACHE_DIR
ROOT = Path(__file__).resolve().parents[2]
Json = dict[str, Any]


def _wasmtime() -> SpecEngine:
    from tests.spec.wasmtime_engine import WasmtimeEngine

    return WasmtimeEngine()


ENGINES: dict[str, Callable[[], SpecEngine]] = {"wasmtime": _wasmtime}

TRAP_CODES = {
    "unreachable": TrapCode.UNREACHABLE,
    "out of bounds memory access": TrapCode.MEMORY_OUT_OF_BOUNDS,
    "integer divide by zero": TrapCode.INTEGER_DIVISION_BY_ZERO,
    "integer overflow": TrapCode.INTEGER_OVERFLOW,
    "invalid conversion to integer": TrapCode.BAD_CONVERSION_TO_INTEGER,
    "call stack exhausted": TrapCode.STACK_OVERFLOW,
    "undefined element": TrapCode.TABLE_OUT_OF_BOUNDS,
    "out of bounds table access": TrapCode.TABLE_OUT_OF_BOUNDS,
    "uninitialized element": TrapCode.INDIRECT_CALL_TO_NULL,
    "indirect call type mismatch": TrapCode.BAD_SIGNATURE,
    "null function reference": TrapCode.NULL_REFERENCE,
    "null reference": TrapCode.NULL_REFERENCE,
}
NULL_TYPES = ("refnull", "nullref", "nullfuncref", "nullexternref", "nullexnref")
LANE_BITS = {"i8": 8, "i16": 16, "i32": 32, "i64": 64, "f32": 32, "f64": 64}
# (mask, canonical, arithmetic) per float width: S0001 "Comparison"
NANS = {
    32: (0x7FFFFFFF, 0x7FC00000, 0x7FC00000),
    64: (0x7FFF_FFFF_FFFF_FFFF, 0x7FF8_0000_0000_0000, 0x7FF8_0000_0000_0000),
}


class Failure(Exception):
    """A command that did not meet its rule; the message is the reason."""


# --- values ---------------------------------------------------------------------------------


def _scalar(t: str, s: str) -> int:
    n = int(s)
    bits = LANE_BITS[t]
    signed = t[0] == "i"
    if not (-(1 << (bits - 1)) <= n < (1 << (bits - 1)) if signed else 0 <= n < (1 << bits)):
        raise Failure(f"value {s} out of range for {t}")
    return n


def parse_value(v: Json) -> Value:
    """A concrete value of the JSON encoding (arguments, and expected results but NaN patterns)."""
    t = v["type"]
    s: Any = v.get("value")
    if t in ("i32", "i64", "f32", "f64"):
        return _scalar(t, s)
    if t == "v128":
        lt = v["lane_type"]
        w = LANE_BITS[lt]
        lanes = [_scalar(lt, x) & ((1 << w) - 1) for x in s]
        return sum(x << (i * w) for i, x in enumerate(lanes))
    if t in NULL_TYPES and s is None or t in ("funcref", "anyref", "exnref") and s == "null":
        return None
    if t == "externref" and s is not None:
        return None if s == "null" else ExternRef(int(s))
    raise Failure("unsupported value type")


def _nan_ok(width: int, pattern: str, bits: int) -> bool:
    mask, canonical, arithmetic = NANS[width]
    if pattern == "nan:canonical":
        return bits & mask == canonical
    return bits & arithmetic == arithmetic


def matches(exp: Json, got: Value) -> bool:
    t = exp["type"]
    s: Any = exp.get("value")
    if t == "either":
        return any(matches(x, got) for x in exp["values"])
    if t in ("f32", "f64") and s.startswith("nan:"):
        w = LANE_BITS[t]
        return isinstance(got, int) and 0 <= got < (1 << w) and _nan_ok(w, s, got)
    if t == "v128" and any(x.startswith("nan:") for x in s):
        lt = exp["lane_type"]
        w = LANE_BITS[lt]
        if not isinstance(got, int) or not 0 <= got < (1 << 128):
            return False
        for i, x in enumerate(s):
            lane = got >> (i * w) & ((1 << w) - 1)
            if x.startswith("nan:"):
                if not _nan_ok(w, x, lane):
                    return False
            elif _scalar(lt, x) & ((1 << w) - 1) != lane:
                return False
        return True
    if t == "funcref" and s is None:
        return isinstance(got, FuncRef)
    want = parse_value(exp)
    if isinstance(want, int):
        return type(got) is int and got == want
    return want == got


# --- one file -------------------------------------------------------------------------------


class Result(NamedTuple):
    id: str
    status: str  # pass, fail, skip
    reason: str


def command_ids(stem: str, commands: Sequence[Json]) -> list[str]:
    per_line = Counter(c["line"] for c in commands)
    seen: Counter[int] = Counter()
    ids: list[str] = []
    for c in commands:
        line = c["line"]
        suffix = f".{seen[line]}" if per_line[line] > 1 else ""
        seen[line] += 1
        ids.append(f"{stem}.wast:{line}{suffix}")
    return ids


class _FileRun:
    def __init__(self, engine: SpecEngine, directory: Path, spectest: bytes) -> None:
        self.engine = engine
        self.dir = directory
        self.registry: dict[str, Instance] = {
            "spectest": engine.instantiate(engine.load(spectest), {})
        }
        self.named: dict[str, Instance] = {}
        self.defs: dict[str, Module] = {}
        self.current: Instance | None = None

    def load(self, c: Json) -> Module:
        return self.engine.load((self.dir / c.get("binary_filename", c["filename"])).read_bytes())

    def target(self, action: Json) -> Instance:
        inst = self.named[action["module"]] if "module" in action else self.current
        if inst is None:
            raise Failure("no current module")
        return inst

    def act(self, action: Json) -> list[Value]:
        inst = self.target(action)
        if action["type"] == "get":
            return [self.engine.get_global(inst, action["field"])]
        return self.engine.call(inst, action["field"], [parse_value(a) for a in action["args"]])

    def expect_trap(self, c: Json, thunk: Callable[[], object], code: TrapCode | None) -> None:
        if code is None:
            raise Failure(f"unknown trap text: {c['text']}")
        try:
            thunk()
        except Trap as e:
            if e.code is not code:
                raise Failure(f"trap {e.code.name}, expected {code.name}") from None
            return
        raise Failure("no trap")

    def execute(self, c: Json) -> str:
        t = c["type"]
        if c.get("module_type") == "text" and "binary_filename" not in c:
            return "skip"
        if t == "module":
            self.current = self.engine.instantiate(self.load(c), self.registry)
            if "name" in c:
                self.named[c["name"]] = self.current
        elif t == "module_definition":
            self.defs[c.get("name", "")] = self.load(c)
        elif t == "module_instance":
            self.named[c["instance"]] = self.engine.instantiate(
                self.defs[c["module"]], self.registry
            )
        elif t == "register":
            self.registry[c["as"]] = self.named[c["name"]] if "name" in c else self.target({})
        elif t == "action":
            self.act(c["action"])
        elif t == "assert_return":
            got = self.act(c["action"])
            exp = c["expected"]
            if len(got) != len(exp) or not all(
                matches(e, g) for e, g in zip(exp, got, strict=True)
            ):
                raise Failure(f"got {got}")
        elif t == "assert_trap":
            self.expect_trap(c, lambda: self.act(c["action"]), TRAP_CODES.get(c["text"]))
        elif t == "assert_exhaustion":
            self.expect_trap(c, lambda: self.act(c["action"]), TrapCode.STACK_OVERFLOW)
        elif t in ("assert_malformed", "assert_invalid"):
            try:
                self.load(c)
            except LoadError as e:
                kind = t.removeprefix("assert_")
                if e.kind is not None and e.kind != kind:
                    raise Failure(f"wrong kind: {e.kind}") from None
                return "pass"
            raise Failure("module accepted")
        elif t == "assert_unlinkable":
            module = self.load(c)
            try:
                self.engine.instantiate(module, self.registry)
            except LinkError:
                return "pass"
            raise Failure("instantiated")
        elif t == "assert_uninstantiable":
            module = self.load(c)
            self.expect_trap(
                c, lambda: self.engine.instantiate(module, self.registry), TRAP_CODES.get(c["text"])
            )
        else:
            raise Failure("unsupported command")
        return "pass"


def run_file(engine: SpecEngine, directory: Path, stem: str, spectest: bytes) -> Iterator[Result]:
    commands: list[Json] = json.loads((directory / f"{stem}.json").read_text())["commands"]
    ids = command_ids(stem, commands)
    try:
        state = _FileRun(engine, directory / stem, spectest)
    except Exception as e:
        reason = f"spectest: {_describe(e)}"
        yield from (Result(i, "fail", reason) for i in ids)
        return
    for cid, c in zip(ids, commands, strict=True):
        try:
            yield Result(cid, state.execute(c), "")
        except Failure as e:
            yield Result(cid, "fail", str(e))
        except Exception as e:  # RecursionError etc. are failures of the engine, not the runner
            yield Result(cid, "fail", _describe(e))


def _describe(e: Exception) -> str:
    return f"{type(e).__name__}: {(str(e).splitlines() or [''])[0]}"


# --- xfail ----------------------------------------------------------------------------------


def read_xfail(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    if not path.exists():
        return entries
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, _, reason = line.strip().partition("  ")
        if not reason.strip():
            raise ValueError(f"{path}: entry without a reason: {line!r}")
        entries[key] = reason.strip()
    return entries


# --- the run --------------------------------------------------------------------------------


def _child_results(engine_name: str, stem: str, ids: list[str], timeout: float) -> Iterator[Result]:
    cmd = [sys.executable, "-m", "tests.spec.run", "--engine", engine_name, "--child", stem]
    why = ""
    try:
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, timeout=timeout)
        out, why = proc.stdout, f"crash: {proc.returncode}"
    except subprocess.TimeoutExpired as e:
        out, why = e.stdout or b"", "timeout"
    done: dict[str, Result] = {}
    for line in out.decode(errors="replace").splitlines():
        if line.startswith("{"):
            r = Result(**json.loads(line))
            done[r.id] = r
    for cid in ids:
        yield done.get(cid) or Result(cid, "fail", why)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tests.spec.run")
    ap.add_argument("--engine", required=True)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--xfail", type=Path)
    ap.add_argument("--write-xfail", action="store_true")
    ap.add_argument("--in-process", action="store_true")
    ap.add_argument("--timeout", type=float, default=600)
    ap.add_argument("--child")
    ap.add_argument("files", nargs="*")
    args = ap.parse_args(argv)

    if args.engine not in ENGINES:
        print(f"unknown engine {args.engine!r}; known: {', '.join(ENGINES)}", file=sys.stderr)
        return 2
    json_dir = CACHE_DIR / "json"
    try:
        manifest: Json = json.loads((CACHE_DIR / "manifest.json").read_text())
        spectest = (CACHE_DIR / "spectest.wasm").read_bytes()
    except OSError:
        print("no spec cache: run `uv run python tests/spec/fetch.py` first", file=sys.stderr)
        return 2

    if args.child:
        for r in run_file(ENGINES[args.engine](), json_dir, args.child, spectest):
            print(json.dumps(r._asdict()), flush=True)
        return 0

    stems: list[str] = [Path(f).stem for f in args.files] or manifest["converted"]
    unknown = [s for s in stems if s not in manifest["converted"]]
    if unknown:
        print(f"not converted: {', '.join(unknown)}", file=sys.stderr)
        return 2
    xfail_path: Path = args.xfail or fetch.HERE / "xfail" / f"{args.engine}.txt"
    xfail = read_xfail(xfail_path)

    types: dict[str, str] = {}
    results: list[tuple[str, Result, str]] = []  # (stem, result, final status)
    used: set[str] = set()
    per_file: dict[str, Counter[str]] = {}
    for stem in stems:
        commands: list[Json] = json.loads((json_dir / f"{stem}.json").read_text())["commands"]
        ids = command_ids(stem, commands)
        types.update(zip(ids, (c["type"] for c in commands), strict=True))
        whole = f"{stem}.wast:*"
        if whole in xfail:
            used.add(whole)
            raw = (Result(i, "xfail", xfail[whole]) for i in ids)
        elif args.in_process:
            raw = run_file(ENGINES[args.engine](), json_dir, stem, spectest)
        else:
            raw = _child_results(args.engine, stem, ids, args.timeout)
        counts = per_file[stem] = Counter()
        for r in raw:
            status = r.status
            if r.id in xfail:
                used.add(r.id)
                status = {"fail": "xfail", "pass": "xpass"}.get(status, status)
            counts[status] += 1
            results.append((stem, r, status))
        print(f"{stem}.wast: " + " ".join(f"{k} {counts[k]}" for k in STATUSES))

    stale = sorted(
        k for k in xfail if k not in used and k.split(".wast:")[0] in stems
    )  # entries of files not in this run are not judged
    totals: Counter[str] = Counter()
    for counts in per_file.values():
        totals.update(counts)
    totals["stale"] = len(stale)
    print("total: " + " ".join(f"{k} {totals[k]}" for k in (*STATUSES, "stale")))
    unconverted: dict[str, str] = manifest["failed"]
    for name, err in unconverted.items():
        print(f"unconverted {name}: {err}")
    for k in stale:
        print(f"stale xfail entry: {k}")

    if args.json:
        report = {
            "engine": args.engine,
            "spec_commit": fetch.SPEC_COMMIT,
            "wasm_tools": manifest["wasm_tools"],
            "totals": {k: totals[k] for k in (*STATUSES, "stale")},
            "files": {f"{s}.wast": dict(c) for s, c in per_file.items()},
            "unconverted": unconverted,
            "results": [
                {"id": r.id, "type": types[r.id], "status": st, "reason": r.reason}
                for _, r, st in results
                if st != "pass"
            ],
        }
        args.json.write_text(json.dumps(report, indent=1) + "\n")

    if args.write_xfail:
        lines = {f"{k}  {v}" for k, v in xfail.items() if k.endswith(".wast:*")}
        lines |= {
            f"{r.id}  {' '.join(r.reason.split()) or 'failed'}"
            for _, r, st in results
            if st in ("fail", "xfail") and f"{r.id.split('.wast:')[0]}.wast:*" not in xfail
        }
        xfail_path.write_text("".join(f"{line}\n" for line in sorted(lines)), encoding="utf-8")
    return 0 if totals["fail"] == 0 and totals["xpass"] == 0 and not stale else 1


STATUSES = ("pass", "fail", "xfail", "xpass", "skip")

if __name__ == "__main__":
    sys.exit(main())
