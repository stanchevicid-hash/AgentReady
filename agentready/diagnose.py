"""Turning raw agent trajectories into root causes and a fix.

1. Triage (Nemotron Nano, one cheap call per run): why did this run fail or struggle?
2. Synthesis (Nemotron Ultra): what is wrong with the repository, across all runs?
3. Fix (Nemotron Ultra + Tavily docs): an AGENTS.md grounded in verified facts.
"""

from __future__ import annotations

import json
import os
from collections import Counter

CATEGORIES = {
    "navigation": "Could not find the relevant code quickly",
    "test_discovery": "Did not know how to run the tests",
    "environment": "Setup/import/path problems (e.g. PYTHONPATH, missing package layout knowledge)",
    "misleading_docs": "Followed documentation that is wrong or outdated",
    "conventions": "Broke a project convention or architecture rule",
    "verification": "Finished without verifying the fix",
    "reasoning": "Found the code but produced a wrong fix",
    "budget": "Ran out of steps",
    "none": "Solved efficiently, no problem",
}

TRIAGE_PROMPT = """You review one run of an AI coding agent inside a repository and classify what got in its way.

Categories:
{categories}

Hidden ground truth (the agent did NOT know this): the defect was in `{file}`, function `{symbol}`.
The correct way to run the test suite is: `{test_cmd}`{test_env}.

Issue given to the agent: {issue_title}
Result: {result}. Steps used: {n_steps}/{max_steps}. First step that opened the defective file: {first_touch}.

Trajectory (step: action args -> observation excerpt):
{trajectory}

Return ONLY JSON:
{{"category": "<one category key>", "secondary": "<category key or none>",
  "explanation": "<max 2 sentences, concrete>",
  "evidence_steps": [<step numbers>],
  "missing_knowledge": "<the ONE fact about this repo that, written in AGENTS.md, would have helped most; or empty>"}}"""

SYNTHESIS_PROMPT = """You are a staff engineer diagnosing why AI coding agents struggle in a repository.
You get per-run triage notes from {n_runs} agent runs, plus verified facts about the repo.

Verified facts (measured by running the repo, trust these):
- Test command that passes: `{test_cmd}`{test_env}
- Test runner: {runner}; dependencies: {deps}
- Agent instruction files present: {instr_files}

Repository file tree:
{tree}

README/CONTRIBUTING excerpt:
{docs}

Per-run triage notes:
{triage}

Find the 3-6 root causes IN THE REPOSITORY (documentation gaps, misleading docs, confusing layout,
undocumented setup) that explain the failures and wasted steps. Do not blame the model unless the
notes show the repo was not the problem. Each finding needs evidence from the notes.

Return ONLY JSON:
{{"summary": "<2 sentences>",
  "findings": [{{"title": "...", "category": "<category key>", "severity": "high|medium|low",
                 "affected_runs": <int>, "evidence": "<concrete, cite runs/steps>",
                 "fix": "<what to change in the repo or its agent docs>"}}]}}"""

AGENTS_MD_PROMPT = """Write an AGENTS.md file for this repository: the instructions an AI coding agent reads
before working here. It must fix the root causes below. Keep it short, specific and scannable
(40-90 lines), using headings and bullet points. Every command and path you write MUST come from
the verified facts or the file tree; never invent commands, tools or files.

Verified facts:
- Run the full test suite with: `{test_cmd}`{test_env}
- Shell the agent uses: {shell}
- Test runner: {runner}; dependencies: {deps}

Root causes found by observing agents fail:
{findings}

File tree:
{tree}

Existing agent instructions (improve or replace):
{existing}

Relevant official documentation (from web research; cite a URL only if it is listed here):
{research}

Required sections: "Project layout" (where code and tests live, what maps to what),
"Running tests" (exact command, including how to set environment variables on Windows cmd and on
POSIX shells if needed, and how to run one test), "Working rules" (conventions agents must follow),
"Known pitfalls" (things in the docs that are wrong or surprising).
Reply with the Markdown only, no code fences around the whole document."""


def _compact_trajectory(ep: dict, limit: int = 40) -> str:
    lines = []
    for s in ep["steps"][:limit]:
        args = {k: (v[:80] + "..." if isinstance(v, str) and len(v) > 80 else v) for k, v in s["args"].items()}
        obs = s["observation"].replace("\n", " ")[:220]
        lines.append(f"{s['n']}: {s['action']} {json.dumps(args)[:200]} -> {obs}")
    return "\n".join(lines) or "(no steps)"


def _env_str(test_info: dict, shell: str = "") -> str:
    env = test_info.get("env") or {}
    if not env:
        return ""
    pairs = ", ".join(f"{k}={v}" for k, v in env.items())
    return f" with environment variable(s) {pairs} set"


def triage_episode(llm, ep: dict, task: dict, test_info: dict, max_steps: int) -> dict:
    title = (task.get("issue") or "").splitlines()[0][:160] if task.get("issue") else task["symbol"]
    result = "SOLVED (hidden tests pass)" if ep["success"] else f"FAILED ({ep.get('error') or 'tests still fail'})"
    prompt = TRIAGE_PROMPT.format(
        categories="\n".join(f"- {k}: {v}" for k, v in CATEGORIES.items()),
        file=task["file"], symbol=task["symbol"], test_cmd=test_info["cmd"], test_env=_env_str(test_info),
        issue_title=title, result=result, n_steps=ep["n_steps"], max_steps=max_steps,
        first_touch=ep.get("first_touch_step") or "never", trajectory=_compact_trajectory(ep),
    )
    try:
        out = llm.chat_json("nano", [{"role": "user", "content": prompt}], max_tokens=700)
        if not isinstance(out, dict):
            raise ValueError("not an object")
    except Exception as exc:
        out = {"category": "none" if ep["success"] else "reasoning", "secondary": "none",
               "explanation": f"triage unavailable ({str(exc)[:80]})", "evidence_steps": [], "missing_knowledge": ""}
    if out.get("category") not in CATEGORIES:
        out["category"] = "reasoning" if not ep["success"] else "none"
    out["key"] = ep["key"]
    out["task_id"] = ep["task_id"]
    out["success"] = ep["success"]
    return out


def synthesize(llm, triage: list, facts: dict) -> dict:
    notes = "\n".join(
        f"- run {t['key']} ({'solved' if t['success'] else 'failed'}): [{t['category']}/{t.get('secondary','none')}] "
        f"{t.get('explanation','')} | missing: {t.get('missing_knowledge','')}"
        for t in triage
    )
    prompt = SYNTHESIS_PROMPT.format(
        n_runs=len(triage), test_cmd=facts["test_cmd"], test_env=facts["test_env"], runner=facts["runner"],
        deps=", ".join(facts["deps"]) or "none", instr_files=", ".join(facts["instr_files"]) or "none",
        tree="\n".join(facts["tree"][:150]), docs=facts["docs"][:3000] or "(none)", triage=notes,
    )
    try:
        out = llm.chat_json("ultra", [{"role": "user", "content": prompt}], max_tokens=3000)
        if isinstance(out, dict) and isinstance(out.get("findings"), list):
            return out
    except Exception as exc:
        facts.setdefault("notes", []).append(f"synthesis failed: {exc}")
    # Deterministic fallback: aggregate the triage categories.
    counts = Counter(t["category"] for t in triage if t["category"] != "none")
    return {
        "summary": "Automatic aggregation of per-run triage (deep synthesis unavailable).",
        "findings": [
            {"title": CATEGORIES[c], "category": c, "severity": "high" if n >= 2 else "medium",
             "affected_runs": n, "evidence": "; ".join(t["explanation"] for t in triage if t["category"] == c)[:500],
             "fix": "; ".join(sorted({t.get("missing_knowledge", "") for t in triage if t["category"] == c} - {""}))[:500]}
            for c, n in counts.most_common(6)
        ],
    }


def fallback_agents_md(facts: dict, findings: list) -> str:
    env = facts.get("env") or {}
    lines = ["# AGENTS.md", "", "Instructions for AI coding agents working in this repository.", "",
             "## Running tests", ""]
    if env:
        posix = " ".join(f"{k}={v}" for k, v in env.items())
        win = "&& ".join(f"set {k}={v}" for k, v in env.items())
        lines += [f"- POSIX shells: `{posix} {facts['test_cmd']}`", f"- Windows cmd: `{win}&& {facts['test_cmd']}`"]
    else:
        lines += [f"- `{facts['test_cmd']}`"]
    lines += ["", "## Known pitfalls", ""]
    for f in findings:
        lines.append(f"- **{f.get('title','')}**: {f.get('fix','')}")
    return "\n".join(lines) + "\n"


def write_agents_md(llm, facts: dict, findings: list, research: list, existing: dict, shell: str) -> str:
    research_txt = "\n".join(f"- {r['title']} ({r['url']}): {r['content'][:300]}" for r in research if r.get("url")) or "(none)"
    findings_txt = "\n".join(
        f"- [{f.get('severity','')}] {f.get('title','')}: {f.get('evidence','')} -> FIX: {f.get('fix','')}" for f in findings
    )
    prompt = AGENTS_MD_PROMPT.format(
        test_cmd=facts["test_cmd"], test_env=facts["test_env"], shell=shell, runner=facts["runner"],
        deps=", ".join(facts["deps"]) or "none", findings=findings_txt or "(none)",
        tree="\n".join(facts["tree"][:150]),
        existing="\n\n".join(f"--- {k} ---\n{v}" for k, v in existing.items()) or "(none)",
        research=research_txt,
    )
    try:
        md = llm.chat("ultra", [{"role": "user", "content": prompt}], max_tokens=3500, temperature=0.2)
        md = md.strip()
        if md.startswith("```"):
            md = md.strip("`")
            md = md.split("\n", 1)[1] if "\n" in md else md
        if len(md) < 120:
            raise ValueError("too short")
    except Exception:
        md = fallback_agents_md(facts, findings)
    # Guarantee the one fact we measured ourselves is present and exact.
    if facts["test_cmd"] not in md:
        md += "\n\n## Verified by AgentReady\n\n"
        md += f"- This exact command passes on a clean checkout: `{facts['test_cmd']}`"
        if facts.get("env"):
            md += f" (with {', '.join(f'{k}={v}' for k, v in facts['env'].items())})"
        md += "\n"
    return md.rstrip() + "\n"


def category_breakdown(triage: list) -> dict:
    counts = Counter(t["category"] for t in triage)
    return {k: counts.get(k, 0) for k in CATEGORIES if counts.get(k, 0)}


def shell_for_docs() -> str:
    return "Windows cmd.exe" if os.name == "nt" else "POSIX sh"
