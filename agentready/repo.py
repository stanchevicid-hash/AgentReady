"""Repository intake: fetch, copy into isolated workspaces, detect how tests run."""

from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

IGNORE_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".tox", ".nox", ".venv", "venv", "env", "node_modules", "dist", "build", ".idea", ".vscode",
    ".agentready",
}
INSTRUCTION_FILES = ["AGENTS.md", "CLAUDE.md", ".github/copilot-instructions.md", ".cursorrules"]
DOC_FILES = ["README.md", "README.rst", "README.txt", "README", "CONTRIBUTING.md"]


def _ignore(dirpath, names):
    return [n for n in names if n in IGNORE_DIRS or n.endswith(".pyc")]


def fetch_repo(source: str, dest_root: Path) -> Path:
    """Clone a git URL or copy a local folder into dest_root/source."""
    dest = dest_root / "source"
    if dest.exists():
        shutil.rmtree(dest)
    if re.match(r"^(https?://|git@)", source):
        proc = subprocess.run(
            ["git", "clone", "--depth", "1", source, str(dest)],
            capture_output=True, text=True, timeout=300,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"git clone failed: {proc.stderr.strip()[:400]}")
        shutil.rmtree(dest / ".git", ignore_errors=True)
    else:
        src = Path(source).expanduser().resolve()
        if not src.is_dir():
            raise RuntimeError(f"not a folder or git URL: {source}")
        shutil.copytree(src, dest, ignore=_ignore)
    return dest


def make_workspace(source: Path, parent: Path, name: str) -> Path:
    ws = parent / name
    if ws.exists():
        shutil.rmtree(ws, ignore_errors=True)
    shutil.copytree(source, ws, ignore=_ignore)
    return ws


def iter_files(root: Path, suffix: str | None = None):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORE_DIRS)
        for fn in sorted(filenames):
            if suffix and not fn.endswith(suffix):
                continue
            yield Path(dirpath) / fn


def is_test_file(rel: str) -> bool:
    parts = Path(rel).parts
    name = parts[-1]
    return (
        any(p in ("tests", "test", "testing") for p in parts[:-1])
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
    )


def file_tree(root: Path, max_entries: int = 200) -> list[str]:
    out = []
    for p in iter_files(root):
        out.append(p.relative_to(root).as_posix())
        if len(out) >= max_entries:
            out.append("...")
            break
    return out


def read_instruction_files(root: Path) -> dict:
    found = {}
    for rel in INSTRUCTION_FILES:
        p = root / rel
        if p.is_file():
            found[rel] = p.read_text(encoding="utf-8", errors="replace")[:12000]
    return found


def read_docs(root: Path) -> dict:
    found = {}
    for rel in DOC_FILES:
        p = root / rel
        if p.is_file():
            found[rel] = p.read_text(encoding="utf-8", errors="replace")[:8000]
    return found


# ------------------------------------------------------------------ tooling
def python_shim_dir(parent: Path) -> Path:
    """A folder whose `python` resolves to the interpreter running AgentReady."""
    shim = parent / ".shim"
    shim.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        (shim / "python.cmd").write_text(f'@"{sys.executable}" %*\r\n', encoding="utf-8")
    else:
        target = shim / "python"
        if not target.exists():
            target.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
            target.chmod(0o755)
    return shim


def base_env(shim: Path, extra: dict | None = None) -> dict:
    """Environment for commands run inside a workspace. Secrets are never passed through."""
    env = {
        k: v for k, v in os.environ.items()
        if not any(s in k.upper() for s in ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "NEBIUS", "TAVILY"))
    }
    env["PATH"] = str(shim) + os.pathsep + env.get("PATH", "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if extra:
        env.update(extra)
    return env


def run_cmd(cmd: str, cwd: Path, env: dict, timeout: int = 120) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd), env=env, shell=True, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        return 124, out + f"\n[timed out after {timeout}s]"


def _tests_ran(output: str) -> bool:
    m = re.search(r"Ran (\d+) tests?", output)
    if m and int(m.group(1)) > 0:
        return True
    return bool(re.search(r"\b\d+ passed\b", output))


def _has_pytest() -> bool:
    try:
        import pytest  # noqa: F401
        return True
    except ImportError:
        return False


def test_command_candidates(root: Path) -> list[dict]:
    has_src = (root / "src").is_dir()
    test_dir = next((d for d in ("tests", "test") if (root / d).is_dir()), None)
    cands = []
    if _has_pytest():
        cands.append({"cmd": "python -m pytest -q -p no:cacheprovider", "env": {}})
        if has_src:
            cands.append({"cmd": "python -m pytest -q -p no:cacheprovider", "env": {"PYTHONPATH": "src"}})
    if test_dir:
        for unittest in (f"python -m unittest discover -s {test_dir}", f"python -m unittest discover -s {test_dir} -t ."):
            cands.append({"cmd": unittest, "env": {}})
            if has_src:
                cands.append({"cmd": unittest, "env": {"PYTHONPATH": "src"}})
    cands.append({"cmd": "python -m unittest discover", "env": {}})
    return cands


def detect_test_command(root: Path, shim: Path, override: str | None = None) -> dict:
    if override:
        rc, out = run_cmd(override, root, base_env(shim))
        return {"cmd": override, "env": {}, "baseline_ok": rc == 0, "output": out[-2000:]}
    for cand in test_command_candidates(root):
        rc, out = run_cmd(cand["cmd"], root, base_env(shim, cand["env"]))
        if rc == 0 and _tests_ran(out):
            return {**cand, "baseline_ok": True, "output": out[-2000:]}
    raise RuntimeError(
        "Could not find a test command that passes on the unmodified repository. "
        "AgentReady needs a green test suite to create verifiable tasks; pass --test-cmd."
    )


def detect_tooling(root: Path, test_info: dict) -> dict:
    deps: list[str] = []
    req = root / "requirements.txt"
    if req.is_file():
        for line in req.read_text(encoding="utf-8", errors="replace").splitlines():
            name = re.split(r"[<>=!~\[; ]", line.strip(), maxsplit=1)[0]
            if name and not name.startswith(("#", "-")):
                deps.append(name)
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        text = pyproject.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"dependencies\s*=\s*\[(.*?)\]", text, re.DOTALL)
        if m:
            for item in re.findall(r"['\"]([^'\"]+)['\"]", m.group(1)):
                deps.append(re.split(r"[<>=!~\[; ]", item, maxsplit=1)[0])
    if not deps:
        deps = sorted(third_party_imports(root))[:6]
    runner = "pytest" if "pytest" in test_info["cmd"] else "unittest"
    return {"dependencies": sorted(set(deps)), "test_runner": runner}


def third_party_imports(root: Path) -> set:
    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    local = {p.stem for p in root.glob("*.py")} | {p.name for p in root.iterdir() if p.is_dir()}
    if (root / "src").is_dir():
        local |= {p.name for p in (root / "src").iterdir()}
    found = set()
    for path in iter_files(root, ".py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            for n in names:
                top = n.split(".")[0]
                if top and top not in stdlib and top not in local and top != "__future__":
                    found.add(top)
    return found


def new_temp_root(prefix: str = "agentready-") -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))
