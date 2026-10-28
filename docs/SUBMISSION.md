# Devpost submission kit

Everything below is in English, as the rules require. Replace the `<...>` placeholders.

## Track

Coding and Agentic Engineering

## Team

George Cunup, Daniel Stancheivici, Andrei Dediu. Add the other two as team members on the Devpost project page; one of you submits as the Representative.

## Project name and tagline

AgentReady: find out where AI coding agents get stuck in your repo, and check whether new instructions help.

## Description (paste into Devpost)

### What it does
AgentReady measures how well coding agents work in one particular repository. It plants small defects in a copy of the code (off-by-one errors, flipped comparisons, deleted function bodies) and keeps only the ones the repo's own tests catch. NVIDIA Nemotron 3 Super writes a bug report for each, describing the symptom without saying where the bug is. Then Nemotron Super agents try to fix them, each in its own sandbox, and the repo's tests decide whether they did.

Nemotron 3 Nano reads every attempt and labels why it failed: the agent got lost, couldn't find the test command, trusted an out-of-date README, and so on. Tavily looks up the official docs of the repo's tools, and Nemotron 3 Ultra turns all of that into an AGENTS.md. The same tasks then run again with the new file. The report shows the score before and after, the success rate with a 95% interval, steps and tokens per solved task, and a replay of every agent step.

On the demo repo, the README told agents to run pytest (the project uses unittest), never mentioned that the code lives under src/, and pointed to two files that were renamed long ago. The AGENTS.md that AgentReady wrote addressed all three. The score went from 53 to 93 and agents needed 13.7 steps per task instead of 18.3.

There is also a Verifier for code an assistant wrote. You paste code or a diff, with or without the repository, and it checks for packages that don't exist on PyPI, eval and shell=True, swallowed errors and keys in the code. With the repository it also applies the change and runs the tests. The app installs on a PC or a phone, each account sees only its own history, and it runs on your own server without tracking.

### Why we built it
Agents waste a lot of their steps working out how a repository is set up: which test command to run, which README still matches the code, where a module went. People write instruction files to help with that, but there is no easy way to see whether those files make a difference. Benchmarks such as SWE-bench compare models across many repositories. We wanted something that looks at a single repository and shows, with numbers, what an instruction file changes there.

### How it works
- Defects are injected with Python's ast module and each one is checked against the real test suite, so every task can be graded.
- The agent under test is a small tool-using agent (list, read, search, edit, run, finish) driven by Nemotron 3 Super through Token Factory's OpenAI-compatible API.
- Before grading, the original test files are restored, so editing a test never counts as a fix.
- Each Nemotron model does the job it suits: Nano labels hundreds of attempts, Super does the multi-step tool use, Ultra makes the one or two calls that need the most reasoning.
- Attempts run in parallel on the host, in Docker containers without network, or as Nebius Serverless Jobs, one job per attempt. The dashboard is a container that runs as a Nebius Serverless Endpoint.
- It uses only Python's standard library, so it runs wherever Python 3.9 does.

### Challenges
Every task had to be gradeable without a human, which is why only defects the tests catch are kept. Agents sometimes "fix" a bug by editing the test, so tests are restored before grading. And agents are random, so each task runs several times and the report shows Wilson confidence intervals; when the intervals overlap, the app says so.

### What's next
Defect injectors for JavaScript, TypeScript and Go, a GitHub App that runs AgentReady on pull requests that touch docs, and a public table of results for well-known open-source projects.

## Links
- Demo: `<https://your-endpoint-url>` (access token for starting runs: `<token>`; viewing runs needs no token)
- Video: `<YouTube URL>`
- Code: `<https://github.com/you/AgentReady>` (MIT license)

## Testing instructions for judges
1. Open the demo URL. The first page explains the project and replays a real run. Click "Open AgentReady"; finished runs are listed under Recent on the Tester page and in History. `Ctrl+K` jumps to any page or run.
2. A run has five tabs. Overview has the score before and after with 95% ranges. Attempts lists every agent attempt; click one to replay its steps. Causes has Nemotron Ultra's root causes and Nano's labels. AGENTS.md shows the old and new file. Tasks & log has the rest. "PDF / print" saves the report.
3. To start a run, click "Try the parcelpost demo", then "Test this repo", and enter the access token. The page updates live. "Stop" ends it early and "Run again" repeats it with the same settings.
4. Verifier: click "Try AI-written code", then Verify. No repository is needed. To see the tests run as well, paste `docs/sample-patches/bad-change.patch` and use `example:parcelpost` as the repository.
5. Locally: README "Quick start" (Python 3.9+, no pip installs). The app installs on a phone (PWA) and pairs with the PC by QR code.

## 3-minute video script

| Time | On screen | Voice-over |
|---|---|---|
| 0:00-0:15 | The intro screen with the replayed run | "Coding agents spend a lot of their steps just figuring out how a repo works. AgentReady measures how much, in your repo." |
| 0:15-0:40 | Start the parcelpost demo; stages light up; tasks appear | "It plants small bugs that the repo's own tests catch. Nemotron Super writes a bug report for each one, without saying where the bug is." |
| 0:40-1:10 | Agent lanes filling live, red failed steps | "Nemotron Super agents work on every task in parallel, each in its own sandbox, through Nebius Token Factory. Each mark is one step. The red ones are commands that failed, like pytest: the README says pytest, but this repo uses unittest." |
| 1:10-1:35 | Open one failed replay | "In this replay the agent follows the README, runs pytest, and the imports fail because the code lives under src. Nemotron Nano labels every attempt like this one." |
| 1:35-2:00 | Causes tab, then the AGENTS.md tab | "Nemotron Ultra reads all of those labels and writes an AGENTS.md. The test command in it is one AgentReady actually ran, and the doc links come from Tavily." |
| 2:00-2:30 | Improved lanes, then the Overview tab | "Then the same tasks again, with the new file. The runs are shorter and more of them pass. The score comes with a 95% interval, so you can tell a real change from luck." |
| 2:30-2:50 | Verifier with the sample snippet | "It also checks code an assistant wrote. This snippet imports a package that doesn't exist and has an API key in it." |
| 2:50-3:00 | Download AGENTS.md | "The AGENTS.md is a normal file you can commit. AgentReady is open source." |

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
