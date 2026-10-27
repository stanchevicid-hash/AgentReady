"""Verifiable task generation.

AgentReady does not ask a model to invent tasks it cannot grade. It takes a repo
whose test suite passes, injects a real, realistic defect into code the tests
cover (an off-by-one, a flipped comparison, a wrong operator, a removed
implementation), and keeps only defects that make the suite fail. Nemotron Super
then writes the bug report a user would file, without revealing the location.

A task is solved when the hidden, untouched test suite passes again. Agents
cannot game it by editing tests: test files are restored before grading.
"""

from __future__ import annotations

import ast
import copy
import random
import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .repo import base_env, is_test_file, iter_files, run_cmd


@dataclass
class Task:
    id: str
    kind: str              # "bug" | "reimplement"
    file: str              # repo-relative path of the mutated file (hidden from the agent)
    symbol: str            # function the defect lives in (hidden from the agent)
    description: str       # short internal description of the injected change
    mutated_source: str    # full text of the mutated file
    failing_output: str    # tail of the failing test run (used to write the issue)
    issue: str = ""        # the bug report the agent receives
    meta: dict = field(default_factory=dict)

    def public(self) -> dict:
        d = asdict(self)
        d.pop("mutated_source")
        return d


# ------------------------------------------------------------------ mutation operators
_CMP_SWAP = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt, ast.Eq: ast.NotEq, ast.NotEq: ast.Eq}
_BIN_SWAP = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.FloorDiv, ast.Div: ast.Mult}
_BOOL_SWAP = {ast.And: ast.Or, ast.Or: ast.And}


def _mutations_for(node: ast.AST):
    """Yield (mutated_node, description) single-line mutations of an expression node."""
    if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in _CMP_SWAP:
        m = copy.deepcopy(node)
        m.ops = [_CMP_SWAP[type(node.ops[0])]()]
        yield m, "flipped a comparison boundary"
    elif isinstance(node, ast.BinOp) and type(node.op) in _BIN_SWAP:
        m = copy.deepcopy(node)
        m.op = _BIN_SWAP[type(node.op)]()
        yield m, "swapped an arithmetic operator"
    elif isinstance(node, ast.BoolOp) and type(node.op) in _BOOL_SWAP:
        m = copy.deepcopy(node)
        m.op = _BOOL_SWAP[type(node.op)]()
        yield m, "swapped a boolean operator"
    elif (
        isinstance(node, ast.Constant) and isinstance(node.value, int)
        and not isinstance(node.value, bool) and node.value not in (0,)
    ):
        m = copy.deepcopy(node)
        m.value = node.value + 1
        yield m, "introduced an off-by-one constant"


def _replace_segment(lines: list[str], node: ast.AST, new_text: str) -> list[str] | None:
    if node.lineno != node.end_lineno:
        return None
    i = node.lineno - 1
    line = lines[i]
    # ast offsets are utf-8 byte offsets
    b = line.encode("utf-8")
    new_line = (b[: node.col_offset] + new_text.encode("utf-8") + b[node.end_col_offset :]).decode("utf-8")
    out = list(lines)
    out[i] = new_line
    return out


def _function_candidates(tree: ast.AST, referenced: set):
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("__") or not node.body:
            continue
        body = node.body
        has_doc = isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
            and isinstance(body[0].value.value, str)
        stmts = body[1:] if has_doc else body
        if not stmts or stmts[0].lineno == node.lineno:
            continue
        if node.name not in referenced and node.name.lstrip("_") not in referenced:
            # Only mutate code the tests can see, directly or through a public caller.
            continue
        yield node, stmts


def _referenced_names(root: Path) -> tuple[set, set]:
    names, test_files = set(), set()
    for p in iter_files(root, ".py"):
        rel = p.relative_to(root).as_posix()
        if is_test_file(rel):
            test_files.add(rel)
            names |= set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", p.read_text(encoding="utf-8", errors="replace")))
    return names, test_files


def propose_mutations(root: Path, rng: random.Random) -> list[dict]:
    referenced, _ = _referenced_names(root)
    proposals = []
    for path in iter_files(root, ".py"):
        rel = path.relative_to(root).as_posix()
        if is_test_file(rel) or rel.startswith(("setup.py", "docs/")):
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        lines = source.splitlines(keepends=True)
        for func, stmts in _function_candidates(tree, referenced):
            # bug-style mutations inside the function body
            for stmt in stmts:
                for node in ast.walk(stmt):
                    for mutated, desc in _mutations_for(node):
                        try:
                            new_text = ast.unparse(mutated)
                        except Exception:
                            continue
                        new_lines = _replace_segment(lines, node, new_text)
                        if new_lines is None:
                            continue
                        proposals.append({
                            "kind": "bug", "file": rel, "symbol": func.name,
                            "description": f"{desc} in {func.name}() line {node.lineno}",
                            "source": "".join(new_lines),
                        })
            # feature-style: the implementation is gone, only signature + docstring remain
            if len(stmts) >= 2:
                first, last = stmts[0], stmts[-1]
                indent = " " * first.col_offset
                new_lines = lines[: first.lineno - 1] + [f"{indent}pass\n"] + lines[last.end_lineno :]
                proposals.append({
                    "kind": "reimplement", "file": rel, "symbol": func.name,
                    "description": f"removed the implementation of {func.name}()",
                    "source": "".join(new_lines),
                })
    rng.shuffle(proposals)
    return proposals


def generate_tasks(
    root: Path, scratch: Path, shim: Path, test_info: dict, count: int, seed: int = 7,
    reimplement_share: float = 0.34, on_progress=None,
) -> list[Task]:
    """Validate proposed mutations against the real test suite and pick a diverse set."""
    rng = random.Random(seed)
    proposals = propose_mutations(root, rng)
    want_reimpl = max(1, round(count * reimplement_share)) if count >= 3 else 0
    picked: list[Task] = []
    used_symbols: set = set()
    files_count: dict = {}
    env = base_env(shim, test_info.get("env"))
    tries = 0
    # Prefer spreading tasks over files and symbols.
    proposals.sort(key=lambda p: rng.random())
    for prop in proposals:
        if len(picked) >= count or tries >= count * 8:
            break
        n_reimpl = sum(1 for t in picked if t.kind == "reimplement")
        if prop["kind"] == "reimplement" and n_reimpl >= want_reimpl:
            continue
        if prop["kind"] == "bug" and (len(picked) - n_reimpl) >= count - want_reimpl:
            continue
        if prop["symbol"] in used_symbols or files_count.get(prop["file"], 0) >= max(2, count // 2):
            continue
        tries += 1
        target = scratch / prop["file"]
        original = target.read_text(encoding="utf-8")
        try:
            target.write_text(prop["source"], encoding="utf-8")
            rc, out = run_cmd(test_info["cmd"], scratch, env, timeout=180)
        finally:
            target.write_text(original, encoding="utf-8")
        if rc == 0:
            continue  # tests don't notice this change -> not a gradeable task
        task = Task(
            id=f"T{len(picked) + 1}", kind=prop["kind"], file=prop["file"], symbol=prop["symbol"],
            description=prop["description"], mutated_source=prop["source"], failing_output=out[-3000:],
        )
        picked.append(task)
        used_symbols.add(prop["symbol"])
        files_count[prop["file"]] = files_count.get(prop["file"], 0) + 1
        if on_progress:
            on_progress(task)
    return picked


ISSUE_PROMPT = """You are writing a GitHub issue as a USER of this project who hit a problem.
Write a realistic, concise bug report (or feature request if the behaviour is entirely missing).

Rules:
- Describe the observed symptom and the expected behaviour, like a real user would.
- You may mention the public function or feature name a user would call.
- NEVER mention file paths, line numbers, the word "mutation", or how the code was changed.
- Do not paste the full test output; at most one short line of error message.
- 60-140 words. Markdown is fine. Start with a one-line title prefixed by '# '.

Kind: {kind}
Function involved: {symbol}
Failing test output (tail):
{failing}
"""


def write_issue(llm, task: Task) -> str:
    failing = task.failing_output[-1800:]
    try:
        text = llm.chat(
            "super",
            [{"role": "user", "content": ISSUE_PROMPT.format(kind=task.kind, symbol=task.symbol, failing=failing)}],
            max_tokens=600,
            temperature=0.4,
        )
        text = re.sub(r"[\w./\\-]+\.py(:\d+)?", "the module", text)  # belt and braces: no paths
        if len(text) > 40:
            return text.strip()
    except Exception:
        pass
    verb = "is missing its behaviour" if task.kind == "reimplement" else "returns wrong results"
    return f"# `{task.symbol}` {verb}\n\nCalling `{task.symbol}` no longer behaves as documented and the test suite fails. Please fix it."


def apply_task(workspace: Path, task: Task) -> None:
    (workspace / task.file).write_text(task.mutated_source, encoding="utf-8")


def restore_tests(workspace: Path, pristine: Path, test_files: set) -> list[str]:
    """Undo any edits the agent made to test files. Returns the files it had touched."""
    touched = []
    for rel in test_files:
        src, dst = pristine / rel, workspace / rel
        if not dst.exists() or dst.read_bytes() != src.read_bytes():
            touched.append(rel)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    return touched


def test_files_of(root: Path) -> set:
    return _referenced_names(root)[1]
