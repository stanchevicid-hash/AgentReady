"""One episode = one agent attempt at one task, in a fresh copy of the repo, then graded."""

from __future__ import annotations

import shutil
from pathlib import Path

from .agent import run_episode
from .repo import base_env, make_workspace, read_instruction_files, run_cmd
from .tasks import apply_task, restore_tests


def execute_episode(llm, task, pristine: Path, work_root: Path, runner, shim: Path, test_info: dict,
                    test_files: set, condition: str, repeat: int, agents_md: str | None,
                    max_steps: int, on_step=None, keep_workspace: bool = False) -> dict:
    name = f"{condition}-{task.id}-{repeat}"
    ws = make_workspace(pristine, work_root, name)
    try:
        apply_task(ws, task)
        if agents_md is not None:
            (ws / "AGENTS.md").write_text(agents_md, encoding="utf-8")
        instructions = read_instruction_files(ws)
        ep = run_episode(llm, task, ws, runner, instructions, condition, repeat, max_steps=max_steps, on_step=on_step)
        # Grade with the pristine tests and the verified command.
        ep.tests_touched = restore_tests(ws, pristine, test_files)
        rc, out = run_cmd(test_info["cmd"], ws, base_env(shim, test_info.get("env")), timeout=180)
        ep.success = rc == 0
        ep.grade_output = out[-1500:]
        return ep.to_dict()
    finally:
        if not keep_workspace:
            shutil.rmtree(ws, ignore_errors=True)
