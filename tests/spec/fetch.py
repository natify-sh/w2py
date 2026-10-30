"""Pin, download and conversion of the WebAssembly/spec core tests (docs/specs/S0001).

Run as `uv run python tests/spec/fetch.py`; idempotent: does nothing when the manifest matches.
"""

import io
import json
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

SPEC_COMMIT = "6057acdb1a213f85369c64454c078a15f11ac4b1"  # main, 2026-10-28
WASM_TOOLS_VERSION = "1.262.0"  # PyPI wasm-tools-package, dev group

HERE = Path(__file__).resolve().parent
CACHE_DIR = HERE.parents[1] / ".cache" / "spec" / SPEC_COMMIT


def _wasm_tools() -> str:
    beside = Path(sys.executable).parent / "wasm-tools"
    return str(beside) if beside.exists() else shutil.which("wasm-tools") or "wasm-tools"


def main() -> int:
    tool = _wasm_tools()
    try:
        version = subprocess.run([tool, "--version"], capture_output=True, text=True).stdout
    except OSError as e:
        print(f"cannot run wasm-tools: {e}", file=sys.stderr)
        return 2
    if version.split()[1:2] != [WASM_TOOLS_VERSION]:
        print(
            f"wasm-tools {WASM_TOOLS_VERSION} required, found: {version.strip()}", file=sys.stderr
        )
        return 2

    manifest_path = CACHE_DIR / "manifest.json"
    if manifest_path.exists():
        old = json.loads(manifest_path.read_text())
        if old["commit"] == SPEC_COMMIT and old["wasm_tools"] == WASM_TOOLS_VERSION:
            print(f"up to date: {len(old['converted'])} converted, {len(old['failed'])} failed")
            return 0

    shutil.rmtree(CACHE_DIR, ignore_errors=True)
    wast_dir, json_dir = CACHE_DIR / "wast", CACHE_DIR / "json"
    wast_dir.mkdir(parents=True)
    json_dir.mkdir()
    url = f"https://github.com/WebAssembly/spec/archive/{SPEC_COMMIT}.tar.gz"
    with urllib.request.urlopen(url) as resp:
        data = resp.read()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar:
            parts = member.name.split("/")
            if len(parts) == 4 and parts[1:3] == ["test", "core"] and parts[3].endswith(".wast"):
                member.name = parts[3]
                tar.extract(member, wast_dir, filter="data")

    converted: list[str] = []
    failed: dict[str, str] = {}
    for wast in sorted(wast_dir.glob("*.wast")):
        (json_dir / wast.stem).mkdir()
        cmd = [tool, "json-from-wast", str(wast), "-o", str(json_dir / f"{wast.stem}.json")]
        proc = subprocess.run(
            [*cmd, "--wasm-dir", str(json_dir / wast.stem)], capture_output=True, text=True
        )
        if proc.returncode == 0:
            converted.append(wast.stem)
        else:
            failed[wast.name] = (proc.stderr.strip().splitlines() or [""])[0]
    subprocess.run(
        [tool, "parse", str(HERE / "spectest.wat"), "-o", str(CACHE_DIR / "spectest.wasm")],
        check=True,
    )
    manifest = {
        "commit": SPEC_COMMIT,
        "wasm_tools": WASM_TOOLS_VERSION,
        "converted": converted,
        "failed": failed,
    }
    manifest_path.write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"{len(converted)}/{len(converted) + len(failed)} converted")
    for name, err in failed.items():
        print(f"  failed {name}: {err}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
