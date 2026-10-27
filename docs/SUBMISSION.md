# Devpost submission kit

Everything below is in English, as the rules require. Replace the `<...>` placeholders.

## Track

Coding and Agentic Engineering

## Project name and tagline

**AgentReady**: does your repo work for AI coding agents? Measure it, fix it, prove it.

## Description (paste into Devpost)

### What it does
AgentReady audits how well AI coding agents work inside a specific repository, and then makes them work better. It plants real defects in the code (off-by-one errors, flipped comparisons, removed implementations) and keeps only those the repo's own test suite catches. NVIDIA Nemotron 3 Super writes a realistic bug report for each one, then a fleet of Nemotron Super coding agents tries to fix them in isolated sandboxes. Every attempt is graded objectively by the hidden, untouched test suite.

Nemotron 3 Nano triages each attempt (did the agent get lost, fail to find the test command, trust outdated docs?), Tavily pulls the official docs of the repo's tools, and Nemotron 3 Ultra synthesizes the root causes and writes an AGENTS.md grounded in facts AgentReady verified itself. Then the same tasks run again with the new instructions. The dashboard shows the before/after score with a 95% confidence interval, steps and tokens per solved task, a replay of every agent step, and a downloadable AGENTS.md and badge. A second tool, the Verifier, checks a diff an AI assistant wrote: does it apply, parse, leave tests alone, avoid risky code and invented packages, and pass the repo's tests, plus a Nemotron review. It works as an installable app on PC and phone, in English and Russian, accessible (WCAG 2.1 AA) and private by design (self-hosted, no tracking).

### Why we built it
Most code is now written with AI agents, yet every repository is a different maze. Teams write agent instruction files by guesswork and never find out whether they help, while agents burn tokens rediscovering how to run tests or following a README that drifted from the code. Model benchmarks tell you which model is smart; nothing told you whether *your* repo lets agents succeed, or what to change. AgentReady closes that loop with evidence instead of opinions.

### How it works
- Defect injection uses Python's AST; each mutation is validated against the real test suite, so every task is gradeable and none is invented.
- The agent under test is a compact tool-using agent (list, read, search, edit, run, finish) driven by Nemotron 3 Super through Nebius Token Factory's OpenAI-compatible API.
- Grading restores the original test files first, so agents cannot pass by editing tests.
- Three Nemotron tiers, each where it fits: Nano for high-volume triage, Super for multi-step tool use, Ultra for the few calls that need deep reasoning.
- Attempts run in parallel locally, in network-less Docker containers, or as Nebius Serverless Jobs (one job per attempt); the dashboard is deployed as a Nebius Serverless Endpoint.
- Zero third-party Python dependencies: it runs anywhere Python 3.9+ runs.

### Challenges
Making tasks objectively gradeable (solved with test-validated mutations), keeping agents from gaming the grader (restoring tests), and making results trustworthy despite non-deterministic agents (repeats plus Wilson confidence intervals).

### What's next
JavaScript/TypeScript and Go defect injectors, a GitHub App that runs on every PR touching docs, and a public leaderboard of agent-readiness for popular open-source projects.

## Links
- Demo: `<https://your-endpoint-url>` (access token for starting runs: `<token>`; viewing runs needs no token)
- Video: `<YouTube URL>`
- Code: `<https://github.com/you/AgentReady>` (MIT license)

## Testing instructions for judges
1. Open the demo URL. Finished real runs are listed under **Recent** on the Tester page (and in **History**): open one to see the full report. `Ctrl+K` jumps to any page or run.
2. In a run, the tabs show: **Overview** (score before/after with 95% ranges), **Attempts** (every agent attempt; click a row to replay each step), **Causes** (Nemotron Ultra's root causes and Nano's triage), **AGENTS.md** (old vs new, copy or download), **Tasks & log**. "PDF / print" saves the whole report.
3. To start a new run: click "Try the **parcelpost** demo", then "Test this repo", and enter the access token. The page updates live; "Stop" ends it early, "Run again" repeats it with the same settings.
4. **Verifier**: paste a diff an AI wrote (try `docs/sample-patches/bad-change.patch` with the parcelpost demo) to get a 0-100 score, every check, and the diff with colours.
5. Locally: README "Quick start" (Python 3.9+, no pip installs). The app installs on a phone (PWA) and pairs with the PC by QR code.

## 3-minute video script

| Time | On screen | Voice-over |
|---|---|---|
| 0:00-0:15 | Dashboard home | "Most code is now written by AI agents. But does your repo actually let them succeed? AgentReady measures it." |
| 0:15-0:40 | Start the parcelpost demo; stages light up; tasks appear | "It plants real bugs your test suite catches. Nemotron Super writes the bug report a user would file, without saying where the bug is." |
| 0:40-1:10 | Agent lanes filling live, red failed steps | "Nemotron Super coding agents attempt every task in parallel sandboxes, on Nebius Token Factory. Each mark is a step. Watch them try pytest, which this repo doesn't use, and wander." |
| 1:10-1:35 | Open one failed replay | "Here's a replay: the README told it to run pytest, imports fail because the code lives under src. Nemotron Nano triages every attempt like this." |
| 1:35-2:00 | Findings + AGENTS.md | "Nemotron Ultra finds the root causes across all runs and writes an AGENTS.md, grounded in the test command we verified and docs Tavily found." |
| 2:00-2:30 | Improved lanes: shorter, greener; scoreboard | "Then the exact same tasks again. Shorter runs, more solved, fewer tokens per fix, with a confidence interval, not a vibe." |
| 2:30-2:50 | Models panel, Nebius jobs mention, badge | "Three Nemotron tiers, each where it fits. Attempts can fan out as Nebius Serverless Jobs; the dashboard runs as a Serverless Endpoint." |
| 2:50-3:00 | Download AGENTS.md | "AgentReady. Make your repo work for the agents that work on it." |

Record with real numbers from a real run; do not edit them.

## Feedback on Nebius and NVIDIA (fill in from your own experience)

Draft points to check against what you actually experienced:
- Token Factory: OpenAI-compatible API meant one client for all three Nemotron tiers; note latency/throughput you saw with many parallel agents, and any rate limits.
- Model IDs: note whether discovering the exact Nemotron model IDs was easy (AgentReady added `/v1/models` auto-discovery because of this).
- Nemotron 3 Super as a tool-using agent: how reliably it followed the JSON action protocol; how often reasoning text needed stripping.
- Nemotron 3 Nano: quality of short structured classifications.
- Nemotron 3 Ultra: quality of root-cause synthesis.
- Serverless Jobs: how easy `--inject-file` and logs made one-job-per-attempt; cold-start times.
- Serverless Endpoints: deployment experience for a small web app.
- Docs: what was missing or confusing.

## Was this project built during the submission period?
Yes. AgentReady was created from scratch during the submission period.
