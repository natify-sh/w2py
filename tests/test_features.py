import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run_check(root: Path) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(root / "tools/features.py"), "--check"]
    return subprocess.run(cmd, capture_output=True, text=True)


def copy_repo(dst: Path) -> Path:
    for rel in ("tools/features.py", "docs", "tests/spec/xfail"):
        src = ROOT / rel
        if src.is_dir():
            shutil.copytree(src, dst / rel)
        else:
            (dst / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, dst / rel)
    return dst


def test_repo_is_current() -> None:
    assert run_check(ROOT).returncode == 0


def test_unknown_key_fails(tmp_path: Path) -> None:
    root = copy_repo(tmp_path)
    path = root / "docs/features.json"
    data = json.loads(path.read_text())
    data["runtimes"]["w2py pyc"]["features"]["noSuchFeature"] = True
    path.write_text(json.dumps(data))
    r = run_check(root)
    assert r.returncode == 1 and "noSuchFeature" in r.stderr


def test_stale_table_fails(tmp_path: Path) -> None:
    root = copy_repo(tmp_path)
    path = root / "docs/FEATURES.md"
    path.write_text(path.read_text().replace("| 5 |", "| 6 |", 1))
    assert run_check(root).returncode == 1
