"""Entry point executed inside a Nebius Serverless Job: run and grade one episode.

    python -m agentready.worker --spec /spec.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import Settings
from .episodes import execute_episode
from .jobs import RESULT_END, RESULT_START
from .llm import TokenFactoryLLM
from .repo import fetch_repo, new_temp_root, python_shim_dir
from .sandbox import LocalRunner
from .tasks import Task, test_files_of


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    args = ap.parse_args(argv)
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))

    settings = Settings.from_env()
    settings.models = spec["models"]
    settings.base_url = spec.get("base_url", settings.base_url)
    llm = TokenFactoryLLM(settings)

    work = new_temp_root("agentready-job-")
    pristine = fetch_repo(spec["source"], work)
    shim = python_shim_dir(work)
    t = spec["task"]
    task = Task(id=t["id"], kind=t["kind"], file=t["file"], symbol=t["symbol"], description=t["description"],
                mutated_source=t["mutated_source"], failing_output=t.get("failing_output", ""), issue=t["issue"])
    result = execute_episode(
        llm, task, pristine, work / "ws", LocalRunner(shim), shim, spec["test_info"], test_files_of(pristine),
        spec["condition"], spec["repeat"], spec.get("agents_md"), spec["max_steps"],
    )
    print(RESULT_START)
    print(json.dumps(result))
    print(RESULT_END)
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
