"""The AgentReady pipeline: measure -> diagnose -> fix -> prove."""

from __future__ import annotations

import json
import shutil
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from . import diagnose, metrics
from .config import ROLE_DESCRIPTIONS, Settings
from .episodes import execute_episode
from .repo import (detect_test_command, detect_tooling, fetch_repo, file_tree, make_workspace,
                   new_temp_root, python_shim_dir, read_docs, read_instruction_files)
from .research import TavilyClient, research_tooling
from .sandbox import DockerRunner, LocalRunner
from .tasks import generate_tasks, test_files_of, write_issue

class Cancelled(Exception):
    """Raised inside the pipeline once the user stops a run."""


STAGES = [
    ("fetch", "Fetch repository"),
    ("probe", "Find a green test command"),
    ("tasks", "Generate verifiable tasks"),
    ("baseline", "Agents attempt tasks (current docs)"),
    ("triage", "Nano triages every run"),
    ("research", "Tavily researches official docs"),
    ("synthesize", "Ultra finds root causes & writes AGENTS.md"),
    ("improved", "Agents retry with improved AGENTS.md"),
    ("report", "Compare & report"),
]


@dataclass
class RunOptions:
    source: str
    tasks: int = 6
    repeats: int = 2
    parallel: int = 4
    max_steps: int = 24
    executor: str = "local"
    test_cmd: str | None = None
    seed: int = 7
    label: str = ""


@dataclass
class RunState:
    id: str
    options: dict
    created: float = field(default_factory=time.time)
    status: str = "queued"
    stage: str = ""
    stages_done: list = field(default_factory=list)
    events: list = field(default_factory=list)
    repo: dict = field(default_factory=dict)
    tasks: list = field(default_factory=list)
    episodes: dict = field(default_factory=dict)
    live: dict = field(default_factory=dict)
    triage: list = field(default_factory=list)
    research: list = field(default_factory=list)
    synthesis: dict = field(default_factory=dict)
    agents_md: str = ""
    agents_md_before: str = ""
    metrics: dict = field(default_factory=dict)
    models: dict = field(default_factory=dict)
    usage: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    error: str = ""
    finished: float | None = None

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["stages"] = [{"key": k, "label": label} for k, label in STAGES]
        d["roles"] = ROLE_DESCRIPTIONS
        return d


class Pipeline:
    def __init__(self, settings: Settings, llm, opts: RunOptions, run_id: str | None = None, tavily=None):
        self.settings = settings
        self.llm = llm
        self.opts = opts
        self.tavily = tavily or TavilyClient(settings.tavily_key)
        rid = run_id or time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        self.state = RunState(id=rid, options=dict(opts.__dict__))
        self.dir = Path(settings.runs_dir) / rid
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._last_save = 0.0
        self.cancelled = threading.Event()

    def cancel(self) -> None:
        # in-flight agent attempts stop at their next step; queued ones never start
        self.cancelled.set()
        self.event("info", "Stop requested")

    def _check_cancel(self) -> None:
        if self.cancelled.is_set():
            raise Cancelled("stopped by user")

    # ------------------------------------------------------------ state helpers
    def event(self, kind: str, msg: str, **data) -> None:
        with self._lock:
            self.state.events.append({"t": round(time.time() - self.state.created, 1), "kind": kind, "msg": msg, **data})
        self.save()

    def stage(self, key: str) -> None:
        self._check_cancel()
        with self._lock:
            if self.state.stage and self.state.stage not in self.state.stages_done:
                self.state.stages_done.append(self.state.stage)
            self.state.stage = key
        self.event("stage", dict(STAGES)[key], stage=key)

    def save(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_save < 0.5:
            return
        with self._lock:
            self._last_save = now
            self.state.usage = self.llm.usage.snapshot() if hasattr(self.llm, "usage") else {}
            data = json.dumps(self.state.to_dict(), default=str)
        tmp = self.dir / "run.json.tmp"
        tmp.write_text(data, encoding="utf-8")
        tmp.replace(self.dir / "run.json")

    # ------------------------------------------------------------ main
    def run(self) -> RunState:
        st = self.state
        st.status = "running"
        work = new_temp_root()
        try:
            self._run(work)
            st.status = "done"
        except Cancelled:
            st.status = "cancelled"
            self.event("info", "Run stopped by user")
        except Exception as exc:
            st.status = "error"
            st.error = f"{exc}"
            self.event("error", str(exc), trace=traceback.format_exc()[-2000:])
        finally:
            st.finished = time.time()
            if st.stage and st.stage not in st.stages_done and st.status == "done":
                st.stages_done.append(st.stage)
            self.save(force=True)
            shutil.rmtree(work, ignore_errors=True)
        return st

    def _run(self, work: Path) -> None:
        st, o = self.state, self.opts
        if hasattr(self.llm, "resolve_models"):
            st.models = self.llm.resolve_models()
            st.notes += getattr(self.llm, "resolution_notes", [])
        else:
            st.models = getattr(self.llm, "models", {})

        # 1. fetch
        self.stage("fetch")
        pristine = fetch_repo(o.source, work)
        shim = python_shim_dir(work)
        tree = file_tree(pristine)
        instr = read_instruction_files(pristine)
        docs = read_docs(pristine)
        st.agents_md_before = instr.get("AGENTS.md", "")
        st.repo = {"source": o.source, "files": len(tree), "tree": tree[:300],
                   "instruction_files": list(instr), "doc_files": list(docs)}
        self.event("info", f"Fetched {len(tree)} files; agent instructions: {', '.join(instr) or 'none'}")

        # 2. probe
        self.stage("probe")
        test_info = detect_test_command(pristine, shim, o.test_cmd)
        tooling = detect_tooling(pristine, test_info)
        test_files = test_files_of(pristine)
        st.repo.update({"test_cmd": test_info["cmd"], "test_env": test_info.get("env", {}),
                        "test_files": sorted(test_files), **tooling})
        env_txt = f" (env {test_info['env']})" if test_info.get("env") else ""
        self.event("info", f"Green test command: {test_info['cmd']}{env_txt}")

        # 3. tasks
        self.stage("tasks")
        scratch = make_workspace(pristine, work, "scratch")
        tasks = generate_tasks(
            pristine, scratch, shim, test_info, o.tasks, seed=o.seed,
            on_progress=lambda t: self.event("task", f"Verified defect {t.id}: {t.description}", task=t.id),
        )
        if not tasks:
            raise RuntimeError("Could not create any verifiable task: the tests did not catch any injected defect.")

        def _issue(t):
            t.issue = write_issue(self.llm, t)
            return t

        with ThreadPoolExecutor(max_workers=min(4, len(tasks))) as pool:
            list(pool.map(_issue, tasks))
        st.tasks = [t.public() for t in tasks]
        self.event("info", f"{len(tasks)} tasks ready; Nemotron Super wrote an issue report for each")

        # 4. baseline
        runner = self._runner(shim)
        self.stage("baseline")
        base_eps = self._run_condition("baseline", tasks, pristine, work, runner, shim, test_info, test_files, None)

        # 5. triage (Nano)
        self.stage("triage")
        task_by_id = {t["id"]: t for t in st.tasks}
        with ThreadPoolExecutor(max_workers=max(1, o.parallel)) as pool:
            futs = [pool.submit(diagnose.triage_episode, self.llm, e, task_by_id[e["task_id"]], test_info, o.max_steps)
                    for e in base_eps]
            for f in as_completed(futs):
                t = f.result()
                with self._lock:
                    st.triage.append(t)
                self.event("triage", f"{t['key']}: {t['category']} - {t.get('explanation','')[:120]}")

        # 6. research (Tavily)
        self.stage("research")
        st.research = research_tooling(self.tavily, tooling)
        self.event("info", f"Tavily: {len(st.research)} documentation sources"
                   if self.tavily.enabled else "Tavily key not set: skipping web research")

        # 7. synthesize (Ultra)
        self.stage("synthesize")
        facts = {
            "test_cmd": test_info["cmd"], "test_env": diagnose._env_str(test_info), "env": test_info.get("env", {}),
            "runner": tooling["test_runner"], "deps": tooling["dependencies"], "instr_files": list(instr),
            "tree": tree, "docs": "\n\n".join(f"--- {k} ---\n{v}" for k, v in docs.items()),
        }
        st.synthesis = diagnose.synthesize(self.llm, st.triage, facts)
        self.event("info", f"Ultra found {len(st.synthesis.get('findings', []))} root causes")
        st.agents_md = diagnose.write_agents_md(self.llm, facts, st.synthesis.get("findings", []),
                                                st.research, instr, runner.shell)
        (self.dir / "AGENTS.md").write_text(st.agents_md, encoding="utf-8")
        self.event("info", "Improved AGENTS.md written")

        # 8. improved
        self.stage("improved")
        self._run_condition("improved", tasks, pristine, work, runner, shim, test_info, test_files, st.agents_md)

        # 9. report
        self.stage("report")
        self._compute_metrics()
        b, a = st.metrics["baseline"], st.metrics["improved"]
        self.event("done", f"Agent-Ready score {st.metrics['score_before']} -> {st.metrics['score_after']} "
                           f"(success {b['success_rate']:.0%} -> {a['success_rate']:.0%})")

    def _runner(self, shim: Path):
        if self.opts.executor == "docker":
            return DockerRunner()
        if self.opts.executor == "nebius":
            from .jobs import NebiusJobsRunner
            return NebiusJobsRunner(self.settings)
        return LocalRunner(shim)

    def _run_condition(self, condition, tasks, pristine, work, runner, shim, test_info, test_files, agents_md):
        o, st = self.opts, self.state
        jobs = [(t, r) for r in range(1, o.repeats + 1) for t in tasks]
        results = []

        def on_step(ep, step):
            self._check_cancel()
            with self._lock:
                st.live[ep.key] = {"task_id": ep.task_id, "condition": ep.condition, "repeat": ep.repeat,
                                   "n": step.n, "action": step.action, "ok": step.ok,
                                   "detail": (step.args.get("path") or step.args.get("command")
                                              or step.args.get("pattern") or step.thought or "")[:90],
                                   "status": "running"}
            self.save()

        def one(task, rep):
            key = f"{condition}:{task.id}:{rep}"
            self._check_cancel()
            with self._lock:
                st.live[key] = {"task_id": task.id, "condition": condition, "repeat": rep, "n": 0,
                                "action": "starting", "ok": True, "detail": "", "status": "running"}
            if getattr(runner, "remote", False):
                return runner.run_episode(task=task, condition=condition, repeat=rep, agents_md=agents_md,
                                          test_info=test_info, source=o.source, max_steps=o.max_steps,
                                          models=st.models)
            return execute_episode(self.llm, task, pristine, work / "ws", runner, shim, test_info, test_files,
                                   condition, rep, agents_md, o.max_steps, on_step=on_step)

        with ThreadPoolExecutor(max_workers=max(1, o.parallel)) as pool:
            futs = {pool.submit(one, t, r): (t, r) for t, r in jobs}
            for f in as_completed(futs):
                t, r = futs[f]
                try:
                    ep = f.result()
                except Exception as exc:
                    ep = {"key": f"{condition}:{t.id}:{r}", "task_id": t.id, "condition": condition, "repeat": r,
                          "steps": [], "n_steps": 0, "finished": False, "summary": "", "error": str(exc)[:500],
                          "prompt_tokens": 0, "completion_tokens": 0, "seconds": 0, "success": False,
                          "tests_touched": [], "grade_output": "", "first_touch_step": None, "ran_tests": False}
                if self.cancelled.is_set():
                    continue  # attempts cut short by a stop are not results
                results.append(ep)
                with self._lock:
                    st.episodes[ep["key"]] = ep
                    live = st.live.get(ep["key"], {})
                    live.update({"status": "solved" if ep["success"] else "failed", "n": ep["n_steps"]})
                    st.live[ep["key"]] = live
                self.event("episode", f"{ep['key']} {'SOLVED' if ep['success'] else 'failed'} in {ep['n_steps']} steps",
                           key=ep["key"], success=ep["success"])
        return results

    def _compute_metrics(self) -> None:
        st = self.state
        eps = list(st.episodes.values())
        base = [e for e in eps if e["condition"] == "baseline"]
        imp = [e for e in eps if e["condition"] == "improved"]
        mb = metrics.condition_metrics(base, self.settings.prices)
        mi = metrics.condition_metrics(imp, self.settings.prices)
        st.metrics = {
            "baseline": mb, "improved": mi,
            "score_before": metrics.readiness_score(mb), "score_after": metrics.readiness_score(mi),
            "per_task": metrics.per_task(eps),
            "categories": diagnose.category_breakdown(st.triage),
            "tavily_calls": self.tavily.calls,
        }
