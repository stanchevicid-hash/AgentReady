# Devpost submission kit

Everything below is in English, as the rules require. Replace the `<...>` placeholders.

## Track

Coding and Agentic Engineering

## Team

George Cunup, Daniel Stanchevici, Andrei Dediu. Add the other two as team members on the Devpost project page; one of you submits as the Representative.

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

Read at a calm pace: about 2:50. Numbers are from the real 5 tasks x 2 attempts run on parcelpost
(20261027-161147-3c0e). If the re-run gives different numbers, say the new ones: never edit them.

| Time | On screen | Voice-over |
|---|---|---|
| 0:00-0:15 | Sign-in screen, then the Tester tab | "AI coding agents now write a lot of our code. But in a real repository they waste most of their steps just figuring out how the project works. We built AgentReady to measure that, fix it, and prove the fix with numbers." |
| 0:15-0:35 | Paste the parcelpost demo, press Test this repo, stages light up | "You give it a repository. AgentReady plants small, real bugs that the project's own tests catch. Nemotron 3 Super, running on Nebius Token Factory, writes a bug report for each one, the way a user would, without saying where the bug is." |
| 0:35-1:00 | Attempts tab, lanes filling live | "Then a team of Nemotron Super agents tries to fix every bug in parallel, each in its own sandbox. Every mark is one step. Red marks are failed commands: the README says pytest, but this repo uses unittest, so the agents get lost." |
| 1:00-1:20 | Open one failed replay | "Here is a replay. The agent trusts the outdated README and burns its steps. Nemotron 3 Nano reviews every attempt like this and labels what went wrong." |
| 1:20-1:45 | Causes tab, then AGENTS.md tab | "Nemotron 3 Ultra reads all those labels, finds the root causes, and writes an AGENTS.md. It is grounded in the test command AgentReady actually ran, and in official docs that Tavily found." |
| 1:45-2:10 | Overview tab: rings and bars | "Then the exact same bugs, the same model, only with the new file. Solved tasks went from 7 out of 10 to 10 out of 10, and the score from 62 to 93. Every number comes with a 95% interval, so you can tell a real change from luck." |
| 2:10-2:35 | Verifier: open bad-change.patch, result | "AgentReady also checks code an AI assistant wrote. This change imports a package that doesn't exist, uses eval, and breaks the tests. AgentReady applies it in a sandbox, runs the real test suite, and Nemotron reviews the diff. Verdict: do not merge." |
| 2:35-2:50 | Phone: scan the QR, the app opens, a notice from the PC appears | "It works on your computer and on your phone. Scan a QR code and the phone is signed in. Start a check on one device, and the other one knows." |
| 2:50-3:00 | GitHub page | "AgentReady is open source and runs on Nebius. Make your repo work for the agents that work on it." |

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
