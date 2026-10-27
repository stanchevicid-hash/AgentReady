"""Command line: `python -m agentready <command>`."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time

from .config import ROLE_DESCRIPTIONS, Settings
from .llm import TokenFactoryLLM
from .pipeline import Pipeline, RunOptions


def cmd_serve(a) -> int:
    from .server import serve
    serve(a.host, a.port, a.open)
    return 0


def cmd_models(a) -> int:
    s = Settings.from_env()
    llm = TokenFactoryLLM(s)
    ids = llm.list_models()
    print("Nemotron models served by Token Factory:")
    for m in ids:
        if "nemotron" in m.lower():
            print("  ", m)
    resolved = llm.resolve_models()
    print("\nAgentReady role assignment:")
    for role, model in resolved.items():
        print(f"  {role:<6} {model:<45} {ROLE_DESCRIPTIONS[role]}")
    for n in llm.resolution_notes:
        print("  note:", n)
    return 0


def cmd_doctor(a) -> int:
    s = Settings.from_env()
    ok = True
    print(f"NEBIUS_API_KEY   {'set' if s.api_key else 'MISSING'}")
    print(f"TAVILY_API_KEY   {'set' if s.tavily_key else 'not set (web research will be skipped)'}")
    print(f"executor         {s.executor}")
    if not s.api_key:
        return 1
    try:
        llm = TokenFactoryLLM(s)
        models = llm.resolve_models()
        reply = llm.chat("nano", [{"role": "user", "content": "Reply with the single word: ready"}], max_tokens=50)
        print(f"Token Factory    OK ({models['nano']} says: {reply.strip()[:40]!r})")
    except Exception as exc:
        ok = False
        print(f"Token Factory    FAILED: {exc}")
    return 0 if ok else 1


def cmd_run(a) -> int:
    s = Settings.from_env()
    source = a.source
    if source.startswith("example:"):
        from .server import resolve_source
        source = resolve_source(source, public=False)
    opts = RunOptions(source=source, tasks=a.tasks, repeats=a.repeats, parallel=a.parallel,
                      max_steps=a.max_steps, executor=a.executor or s.executor, test_cmd=a.test_cmd, seed=a.seed,
                      label=a.source)
    pipe = Pipeline(s, TokenFactoryLLM(s), opts)
    print(f"Run {pipe.state.id}  ->  {pipe.dir}")
    seen = 0
    done = threading.Event()

    def tail():
        nonlocal seen
        while not done.is_set():
            evs = pipe.state.events[seen:]
            for e in evs:
                print(f"[{e['t']:>6}s] {e['kind']:<8} {e['msg']}")
            seen += len(evs)
            time.sleep(0.5)

    t = threading.Thread(target=tail, daemon=True)
    t.start()
    st = pipe.run()
    done.set()
    t.join()
    for e in pipe.state.events[seen:]:
        print(f"[{e['t']:>6}s] {e['kind']:<8} {e['msg']}")
    if st.status != "done":
        print("FAILED:", st.error)
        return 1
    m = st.metrics
    print("\n" + json.dumps({k: m[k] for k in ("score_before", "score_after")}, indent=2))
    print(f"Improved AGENTS.md: {pipe.dir / 'AGENTS.md'}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="agentready", description="Measure and improve how well AI coding agents work in a repo.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("serve", help="start the web dashboard")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--open", action="store_true", help="open the dashboard in your browser")
    p.set_defaults(fn=cmd_serve)
    p = sub.add_parser("run", help="run the full pipeline on a repo (path, git URL or example:parcelpost)")
    p.add_argument("source")
    p.add_argument("--tasks", type=int, default=5)
    p.add_argument("--repeats", type=int, default=2)
    p.add_argument("--parallel", type=int, default=4)
    p.add_argument("--max-steps", type=int, default=24)
    p.add_argument("--executor", choices=["local", "docker", "nebius"])
    p.add_argument("--test-cmd")
    p.add_argument("--seed", type=int, default=7)
    p.set_defaults(fn=cmd_run)
    sub.add_parser("models", help="list Nemotron models on Token Factory and the role mapping").set_defaults(fn=cmd_models)
    sub.add_parser("doctor", help="check keys and connectivity").set_defaults(fn=cmd_doctor)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
