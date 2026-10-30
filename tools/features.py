"""Feature checklist in the WebAssembly website's features.json format (docs/FEATURES.md).

`python3 tools/features.py` regenerates docs/features.json and the table in docs/FEATURES.md;
`--check` fails if either is stale or a feature key is not in the pinned upstream copy
(docs/upstream/features.json). Evidence rule: a feature is true on pyc only if the spec files that
exercise it have no entry in tests/spec/xfail/pyc.txt; partial lists what fails; null otherwise.
"""

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
UPSTREAM_URL = "https://raw.githubusercontent.com/WebAssembly/website/refs/heads/main/features.json"
BEGIN, END = "<!-- features:begin -->", "<!-- features:end -->"
TARGETS = ("pyc", "jit-x86_64")  # mpy (tier 3, D22) is left out until it exists

# feature key -> spec stems (top-level test/core of the pinned commit) that exercise it. [] means
# the spec files are not run yet (bulk-memory, SIMD, exceptions, ... live elsewhere): null.
SPEC = {
    "mutableGlobals": ["global", "imports", "exports"],
    "signExtensions": ["i32", "i64"],
    "saturatedFloatToInt": ["conversions"],
    "multiValue": ["block", "loop", "if", "br", "br_if", "br_table", "call", "func"],
    "referenceTypes": [
        *("ref_func", "ref_is_null", "ref_null", "ref", "table_get", "table_set"),
        *("table_grow", "table_size", "table", "select"),
    ],
    "tailCall": ["return_call", "return_call_indirect", "return_call_ref"],
    "extendedConst": ["global", "data", "elem"],
    "typedFunctionReferences": [
        *("call_ref", "br_on_null", "br_on_non_null", "ref_as_non_null", "local_init"),
        "return_call_ref",
    ],
    **{
        k: []
        for k in (
            *("bulkMemory", "gc", "simd", "relaxedSimd", "memory64", "multiMemory"),
            *("exceptions", "exceptionsFinal", "threads", "customPageSizes", "wideArithmetic"),
        )
    },
}


def xfails() -> dict[str, list[str]]:
    """Spec stem -> reasons, from tests/spec/xfail/pyc.txt (`file.wast:line  reason`)."""
    out: dict[str, list[str]] = {}
    for line in (ROOT / "tests/spec/xfail/pyc.txt").read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            head, _, reason = line.partition("  ")
            out.setdefault(head.split(".wast")[0], []).append(reason.strip())
    return out


def pyc_value(key: str, bad: dict[str, list[str]]) -> Any:
    stems = SPEC.get(key)
    if not stems:  # unmapped, or spec files not run yet: upstream's null has no note
        return None
    hit = {s: bad[s] for s in stems if s in bad}
    if not hit:
        return True
    top = Counter(r for rs in hit.values() for r in rs).most_common(1)[0][0]
    counts = ", ".join(f"{s} {len(r)}" for s, r in hit.items())
    return ["partial", f"xfail {sum(map(len, hit.values()))} ({counts}); mostly: {top}"]


def build(upstream: dict[str, Any]) -> dict[str, Any]:
    feats = list(upstream["features"])
    bad = xfails()
    runtimes = {}
    for t in TARGETS:
        values = {k: (pyc_value(k, bad) if t == "pyc" else None) for k in feats}
        runtimes[f"w2py {t}"] = {
            "url": "https://github.com/natify-sh/w2py",
            "category": "Standalone Runtimes",
            "features": values,
        }
    return {"$source": UPSTREAM_URL, "runtimes": runtimes}


def cell(v: Any) -> str:
    if v is True:
        return "yes"
    if v is None:
        return "-"
    if isinstance(v, str):
        return f"since {v}"
    assert isinstance(v, list)
    return "partial" if v[0] == "partial" else "flag" if v[0] == "flag" else f"since {v[0]}"


def table(upstream: dict[str, Any], ours: dict[str, Any]) -> str:
    wt = upstream["browsers"]["Wasmtime"]["features"]
    rt = ours["runtimes"]
    rows = ["| feature | phase | Wasmtime | pyc | jit-x86_64 |", "|---|---|---|---|---|"]
    for k, f in upstream["features"].items():
        mine = [cell(rt[f"w2py {t}"]["features"][k]) for t in TARGETS]
        row = [f"{f['description']} (`{k}`)", f["phase"], cell(wt.get(k)), *mine]
        rows.append("| " + " | ".join(map(str, row)) + " |")
    return "\n".join(rows)


def main(argv: list[str]) -> int:
    check = "--check" in argv
    upstream = json.loads((ROOT / "docs/upstream/features.json").read_text())
    path = ROOT / "docs/features.json"
    ours = build(upstream)
    problems: list[str] = []
    if path.exists():
        have = json.loads(path.read_text())
        for name, rt in have.get("runtimes", {}).items():
            problems += [
                f"{name}: unknown key {k}" for k in rt["features"] if k not in upstream["features"]
            ]
    problems += [f"SPEC: unknown key {k}" for k in SPEC if k not in upstream["features"]]
    text = json.dumps(ours, indent=2) + "\n"
    md_path = ROOT / "docs/FEATURES.md"
    md = md_path.read_text()
    head, sep, rest = md.partition(BEGIN)
    if not sep or END not in rest:
        problems.append(f"FEATURES.md: markers {BEGIN} / {END} missing")
        return report(problems)
    tail = rest.partition(END)[2]
    new_md = f"{head}{BEGIN}\n{table(upstream, ours)}\n{END}{tail}"
    if check:
        if not path.exists() or path.read_text() != text:
            problems.append("docs/features.json is stale: run python3 tools/features.py")
        if md != new_md:
            problems.append("docs/FEATURES.md table is stale: run python3 tools/features.py")
        return report(problems)
    if problems:
        return report(problems)
    path.write_text(text)
    md_path.write_text(new_md)
    for name, rt in ours["runtimes"].items():
        c = Counter(
            "partial" if isinstance(v, list) else "true" if v else "null"
            for v in rt["features"].values()
        )
        print(f"{name}: true {c['true']}, partial {c['partial']}, null {c['null']}")
    return 0


def report(problems: list[str]) -> int:
    for p in problems:
        print(p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
