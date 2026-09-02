# AutoKnit

[English](README.md) | [简体中文](README.zh-CN.md)

**Throw in a PRD — it decomposes tasks, dispatches agents, verifies acceptance, and splits recursively on its own. Cheap to build, cheaper to maintain, zero babysitting.**

AutoKnit is an open-source divide-and-conquer execution framework that sits between "chatting with your agent step by step gets exhausting" and "enterprise-grade heavy frameworks are too expensive". It never burns context playing "general coordinator": **the program does all scheduling (0 tokens), LLMs only contribute intelligence**. You review the plan once, and merge the code once at the end.

> **Benchmarked**: on a ~7,000-line task, AutoKnit cold start was the **cheapest (749K tokens), fastest (37 minutes), and had the highest test density (26.4/1k lines)** — 19% cheaper than a single interactive agent and 41% cheaper than lh-harness. Full comparison in [Section 2](#2-benchmarked-083031-same-machine-same-accounting).

> The execution substrate is [dsh](docs/quickstart.md) (the **DeepSeek harness**) — it owns sessions and model calls; AutoKnit owns decomposition, dispatch and acceptance. Comparison baseline [lh-harness](https://github.com/) (**Long Horizon Harness**, an excellent enterprise-grade development framework) also runs on codex as executor; some of the numbers in this README come from fair head-to-head runs against it.

---

## 1. What problem does it solve

**Pain point: Vibe Coding developers spend endless time and energy and still don't know how to arrange and architect things.**

| What you do today | Why it hurts |
|---|---|
| Chat with your own agent step by step, fixing step by step | You repeat yourself over and over while the context balloon keeps growing |
| Throw a big task at an interactive coding tool | Past ~1,000 lines, single-session orchestration costs spiral out of control and delivery gets thin |
| Adopt a heavy open-source framework for quality | You keep re-injecting context to maintain state — enormous consumption |
| Changing requirements / fixing bugs after launch | Touch one point and several modules ripple; rework + regression burn tokens over and over — **5 bucks of development, 100 bucks of wrangling and maintenance** |

AutoKnit fills exactly this gap: **engineering and contracts replace "repeated conversation", so LLMs only contribute intelligence. Target scale: 1,000–10,000 lines — you save not only on code production itself, but on the hidden costs of back-and-forth wrangling during development and repeated rework during maintenance.**

---

## 2. Benchmarked (2026-08-30/31, same machine, same accounting)

> Detailed accounting notes at the end of this section; per-case full reports in `docs/benchmark.md`. These are **initial reference directions** (single-run samples). You're welcome to share your own measurements in Issues — the benchmark will keep updating with community feedback.
> Subjects: AutoKnit (deepseek-v4-flash @official API, thinking low, explicitly reproducible), lh-harness (same model and tier, thinking low), single interactive agent (GLM-5.3-Flash with thinking, single-session continuous mode; its billing excludes independent audit).
> Billing = uncached input + output (each tool counts tokens differently; normalized). **Every token number in this document is billed accounting and comparable across tables — the single exception is the "modification experiment" table (raw, incl. cache), which is for relative comparison between the four rows only.**

### Module-level tasks (500–1,000 lines, three independent implementations, same PRD)

**m01 DSH task-state data bridge** (~1,000 lines expected, 5 acceptance items)

| | Total input | Uncached input | Output | Cache read | Hit rate | Billed tokens | Delivery (lines) | Tests |
|---|---|---|---|---|---|---|---|---|
| AutoKnit | 3,838,733 | 151,181 | 68,352 | 3,687,552 | 96.1% | 219,533 | 1,591 (1,400 code) | **51** |
| lh-harness | 2,074,086 | 232,934 | 99,701 | 1,841,152 | 88.8% | 332,635 | 1,695 (1,534 code) | 29 |
| Single interactive agent | 382,495 | 32,863 | 12,989 | 349,632 | 91.4% | **45,852** | 785 | 15 |

**m02 session/usage data bridge** (5 acceptance items; delivery lines = business source lines, same accounting)

| | Total input | Uncached input | Output | Cache read | Hit rate | Billed tokens | Delivery (lines) | Tests |
|---|---|---|---|---|---|---|---|---|
| AutoKnit | 5,677,336 | 167,960 | 136,013 | 5,509,376 | 97.0% | **303,973** | 2,322 (1,120 code) | 82 |
| lh-harness | 8,309,223 | 197,735 | 165,796 | 8,111,488 | 97.6% | 363,531 | 1,867 | 88 |
| Single interactive agent | 4,682,422 | 352,310 | 154,453 | 4,330,112 | 92.5% | 506,763 | 1,463 | 47 |

> m02 is the final data from the 09-01 framework (same PRD, same model deepseek-v4-flash + thinking low). **AutoKnit 303,973 — 16% cheaper than lh (363,531), 40% cheaper than the interactive agent (506,763)**; delivery of 2,322 lines is 24% thicker than lh; 82 tests all green (auditor verifies item by item).

**m03 human-reply service** (smallest module, ~600 lines, 5 acceptance items; same PRD, three independent implementations, all independently re-verified)

| | Total input | Uncached input | Output | Cache read | Hit rate | Billed tokens | Delivery (lines) | Tests |
|---|---|---|---|---|---|---|---|---|
| AutoKnit | 1,143,461 | 72,101 | 35,685 | 1,071,360 | 93.7% | 107,786 | 785 (369 code) | 27 |
| lh-harness | 750,286 | 43,982 | 29,537 | 706,304 | 94.1% | **73,519** | 527 | 16 |
| Single interactive agent | 302,799 | 25,423 | 10,865 | 277,376 | 91.6% | **36,288** | 397 | 20 |

> Same PRD for all three (sha256 identical); the difference is the execution model itself: AutoKnit's auditor collects evidence item by item plus programmatic checks, lh's auditor "quits when acceptance passes", the interactive agent has no independent audit. m03 is the smallest module (~600 lines): AutoKnit 107,786 — 1.5× lh, 3.0× interactive. **At the 300–500 line scale orchestration overhead can't amortize; the interactive agent is the best tool** (consistent with the sweet spot below).

**m04 create-agent/create-session workspace binding** (~750 lines, 5 acceptance items; AutoKnit as a single module — different decomposition granularities of the same task differ 4.6× in cost: granularity itself is the biggest cost lever)

| | Billed tokens | Delivery (lines) | Tests |
|---|---|---|---|
| AutoKnit (single module) | **62,685** | 549 | 21 |
| lh-harness | 139,258 | 601 | 25 |
| Single interactive agent | **51,513** | 573 | 24 |

> m04 is 09-01 framework data (single module, done in 1 round, 139s). **AutoKnit 62,685 — 55% cheaper than lh**, only 22% more expensive than the interactive agent (51,513) — whose billing excludes independent audit.

### Large task (~7,000 lines: plan-only mode + programmatic code merge + DSH panel plugin; all three passed acceptance)

| | Billed tokens | Delivery (lines) | Tests | Time |
|---|---|---|---|---|
| AutoKnit (cold start, packaged artifact) | **748,802** | 3,866 | **102** | **~37 min** |
| AutoKnit (v4 rerun: fixed engine + UCD upstream digest, 2026-09-03) | **701,977** | 2,696* | 95 | **~33.6 min** |
| lh-harness | 1,267,832 | 4,727 | ~44 | 130.5 min |
| Single interactive agent | 926,171 | 1,784 | 48 | ~4.5h |

### Modification experiment: four deliverables given the same change request (add `--dry-run` to merge)

Real scenario: you ask an agent to change a feature; it first figures out "which files to touch", then starts. **If its estimate is far off, it misses files — at best multiple rounds of rework, at worst it ships with hidden problems**. So we tested: gave four independently generated deliverables (two AutoKnit builds / lh / interactive) the same change request, recorded the "predicted blast radius" with codegraph first, then measured the actual changes.

| | AutoKnit (dogfooding) | AutoKnit (cold start) | lh-harness | Single interactive agent |
|---|---|---|---|---|
| Consumption (raw, incl. cache) * | 770,150 | 628,552 | 802,329 | **381,670** |
| Predicted vs actual deviation | **-1** (predicted 5, actual 4) | **+1** (predicted 4, actual 5) | +3 (predicted 3, actual 6) | +2 (predicted 3, actual 5) |
| Tests surviving | **58/58** | **29/29** | 39/39 | 19/19 |
| Out-of-bound reads | 0 | 0 | 0 | 0 |

> ⚠️ **Accounting note**: this table is the only raw (incl. cache) accounting in the document — during the modification experiment that interactive tool only recorded cache-inclusive totals and couldn't split them. **The four rows share one accounting, so relative comparison is valid; absolute values cannot be converted against the other tables (billed accounting).**

### Four takeaways from the data

**One-line summary: AutoKnit buys more maintainable, more reliable code for fewer tokens — the savings don't come from making the LLM work less, they come from turning orchestration and acceptance into 0-token programs so every token is spent on real output.**

1. **Small tasks (≤1,000 lines): interactive tools are still cheapest** (36–52K vs AutoKnit's 62–304K) — that's why our sweet spot starts at 1,000 lines: single-session, zero orchestration overhead, it's simply optimal. **But once a task has a cross-module dependency chain (like m02's three modules), AutoKnit overtakes (303K vs 507K interactive, 40% cheaper)**.
2. **Large tasks flip the order**: at 7,000 lines AutoKnit cold start was **cheapest (749K, -19% vs interactive, -41% vs lh), fastest (37min vs 4.5h), highest test density (26.4/1k lines)**. The interactive single-session model inflates context at scale (861K of uncached input, all spent re-reading new content).
3. **The interactive agent's cheapness excludes audit**: its billing has no independent acceptance. Auditing three interactive deliverables at equivalent strength cost us 559K afterwards (~190K each) — **the interactive agent's true cost = generation + audit**. AutoKnit's auditor is built in; the quote includes verification.
4. **Modification blast radius is predictable and quotable**: an agent's inherent flow when changing code is "read the structure → predict which files to touch → change them → run tests". Because AutoKnit's deliverables are **decomposed + modularized + contract-bounded**, what the agent reads is a clean module topology — measured prediction deviation ≤±1, maximally avoiding the three rework accidents: incomplete changes, one-touch-ripples-to-many, and shipping problems from half-finished edits. **Test density of 26.4/1k lines is 3× the baselines — the same lines of code, with over 3× the test escort.**

#### Divide and conquer without reinventing wheels: UCD upstream digest (0 tokens)

The classic cost of module-level divide-and-conquer: each module runs in its own clean session and doesn't know what the others already built — wheels get reinvented. AutoKnit solves this with **UCD (upstream capability digest)**: once an upstream module passes acceptance, the **program** (AST extraction, 0 tokens) generates an `UPSTREAM.md` — public interfaces, function signatures, one-line purposes — injected into the downstream executor's startup context. Downstream modules `import` and reuse directly, **without reading upstream source** (contracts + digest suffice; coupling stays low) — freeing capacity for robust code, tests, and documentation.

v4 rerun (2026-09-03, fixed engine + UCD): same PRD, same baseline — 701,977 billed (-6.3% vs baseline), 33.6 min, 95 test cases, 26.5% doc density (docstrings counted). **The isolation dividend of divide-and-conquer and the reuse dividend of a monolith, at the same time.**

## Sweet spot (honest boundaries)

| Task size | Recommendation |
|---|---|
| ≤500 lines | Just use an interactive tool — instant to write, instant to change; don't use a framework |
| 500–1,000 lines | Either works; interactive is faster, AutoKnit is thicker |
| **1,000–10,000 lines** | **AutoKnit's bullseye** — decomposition, contracts, acceptance start compounding |
| >10,000 lines | Theoretically fine, not yet systematically validated |

---

## 3. How it works (architecture)

```
                 PRD
                  │
        ┌────────▼────────┐
        │     planner      │  Splits by coupling: tightly-coupled code goes together
        └────────┬────────┘   + first-task detailed checklist + inter-module contracts
                 ▼
        ┌────────▼────────┐
        │     executor     │  Independent session, responsible for its module only
        └────────┬────────┘
                 ▼
        ┌────────▼────────┐
        │     auditor      │  Verifies the acceptance list item by item + programmatic evidence
        └────────┬────────┘
                  │ Remaining modules still large?
                  ▼ large (threshold tunable, ≈1000 lines) → split recursively
                    small → the executor finishes the remainder itself
                  ▼
             All done ✅
```

1. **planner splits by coupling**: tightly-coupled code lands in the same module (if changing A means changing B, they belong together); modules communicate only through **data contracts + interface contracts** and never read each other's source.
2. **executor runs + warm continuation**: independent session per module; when the remaining volume drops below the threshold it **doesn't spawn a new block — the current executor finishes the remainder** — its context is still warm, saving tokens and keeping quality coherent.
3. **auditor acceptance**: independent role, verifies the acceptance list item by item + programmatic evidence collection (pytest / semgrep / boundary checks); failures get sent back.
4. **split recursion**: only splits further when the remainder exceeds the threshold; each block is "swallowed in one bite".
5. **Granularity is the biggest cost lever and the quality/overhead balance point**: different granularities of the same task differ 4.6× in cost (63K–336K). Our balance threshold is **~1,000 lines per executor task** — above it, split recursively (smaller context per block); below it, let the current executor finish (warm context saves tokens).

### Rejection and escalation (the boundary of "fully automatic" — when you actually show up)

An auditor rejection ≠ starting over. Repairs are always **in-place**: module artifacts stay, the executor's next round continues with its 【previous-round feedback】 (done / to-do); even when the executor is swapped, the replacement **takes over existing progress** (progress snapshot + handover bundle) — never from scratch.

The escalation chain after a rejection (default config, fully automatic — you only show up if everything fails):

```
auditor rejection
  → same executor fixes in place (up to 2 rounds, REVIEW pinpoints the to-dos)
  → a different executor takes over (once)
  → recursive split: break the module into smaller pieces
  → model upgrade fallback (flash → pro)
  → only then needs_human, and a human takes over
```

Two exceptions escalate immediately without burning rounds: failures rooted in **upstream/contract** (retrying is pointless — fix the dependency first), or **environment problems** (rate limits / network — bounded backoff, 3 retries, since swapping executors is useless).

The dashboard's "pending decisions" contains only two kinds — neither is a code-level micro-decision (code-level items are already verified by the auditor's item-by-item evidence):
- **Human acceptance items**: GUI appearance / real-world scenarios / experience — dimensions a framework cannot verify. Code is all green; these are listed for you and an external AI to review.
- **Modules needing a decision**: modules where automation is exhausted, with root cause attached (delivery / environment / contract / stall) and options A abandon / B change approach / C pause / D custom.

### What a contract looks like

Modules share no code — only shapes. Each module's task book carries contract files (auto-generated; you can also declare them explicitly in the PRD and the planner will respect that):

```yaml
# contracts/m01-task-state.yaml (example)
interface:
  dsh.task.list:
    direction: F→R
    returns:
      tasks: "List[TaskSummary]"   # sorted by urgency desc
      task:
        id: str
        stage: planning|executor|auditor|rejected|reassigned|needs_human|done
        modules: "List[{id: str, stage: str, rejected_count: int}]"
data:
  snapshot.json:        # upstream file this module reads read-only
    run_id: str
    phase: str
  dispatch.jsonl:       # event stream, append-only
    events: "List[{seq: int, type: str, payload: dict}]"
boundary:
  may_read: [contracts/, shared/]
  may_write: [modules/m01/]
```

The executor's entire world is this contract plus its own module directory — it doesn't need, and is not allowed, to know the global picture.

### Why it stays high-quality while saving tokens

**AutoKnit's secret to saving tokens isn't "making the LLM work less" — it's "letting the LLM do only what it's best at": writing code. All orchestration, verification, dependency and reuse guidance is done by the program in the 0-token scheduling layer, so a modest amount of LLM intelligence produces higher-quality work.**

- **0-token programmatic scheduling**: planner/split/runner are programs, not a "general coordinator" LLM. In the large-task run AutoKnit's orchestration cost was literally 0 — every token went to real output.
- **Cache friendly**: frozen-prefix discipline + independent sessions per module; cache hit rates of 94–98%.
- **A deterministic workstation**: contracts, interfaces and boundaries are pre-processed by the program into the task book — 100% of executor tool calls go to writing code, zero to exploration.
- **Upstream capability digests (UCD)**: when a module finishes, the program AST-extracts its public interfaces and injects them 0-token into the downstream executor's context — downstream reuses upstream capabilities directly (m03 reusing m02's summary), no rewriting, no exploration (measured 32% cheaper for that module).
- **Programmatic auditor evidence**: pytest/semgrep/boundary checks are pre-run by the program into an evidence layer; the auditor only spot-checks, never reads everything — audit cost share dropped from 34% to 22–28%, still item-by-item.
- **Dependencies installed once**: python_packages declared in the task are aggregated and installed by the bootstrap (120s timeout fallback) — executors never install on the fly or reinvent wheels.
- **Cheap models + low thinking tier work**: deepseek-v4-flash (flash tier, thinking low) produced thick deliveries end to end — the architecture takes "arranging and architecting" off the LLM's shoulders.

---

## 4. How to use

### Flow

Today's agents can already "ask clarifying questions and turn a vague requirement into a complete PRD" — that upfront conversation usually takes **3 rounds**. Hand it to AutoKnit from there; what you save is hours of back-and-forth and the information loss of a bloating context:

```
1. Tell your agent the requirement        ← you only bring the idea
2. Your agent asks clarifications, produces a complete PRD  ← usually 3 rounds
3. autoknit plan-only <task dir>   ← review the plan: how many modules, how many lines each, contract list (no execution, no cost)
4. autoknit run                    ← split / dispatch / write / verify / continue / recurse — fully automatic
5. Get modules/ module code + merge notes
6. Your agent merges against the notes, one round  ← you review once
```

### Dependencies (from zero)

1. Python 3.11+, git, npm
2. `pip install autoknit` — installs the `autoknit` command in one shot (framework bundled; incl. fw-protocol / fw-scaffold / the data bridge). Source route: `git clone https://github.com/Renjie-hub-byte/DSH-AutoKnit.git && cd DSH-AutoKnit && bash install.sh`
3. Install [dsh](docs/quickstart.md appendix A) (the DeepSeek harness, `npm install -g @deepseek-ai/dsh`) and log in, or point `~/.autoknit/config.yml` at your dsh path and credentials
4. `autoknit doctor` — one-shot health check: dsh binary / credentials / model routing / panel connectivity, with human-readable fix instructions for anything missing

### Commands & flags

| Command / flag | Meaning |
|---|---|
| `autoknit plan-only <dir>` | Runs only the planner: produces task.yaml (module decomposition + contracts), no execution, no execution tokens; review it, then continue with `run` |
| `autoknit run [--resume]` | Full pipeline; `--resume` continues from a checkpoint (no re-planning after crashes/interruptions). Continuation threshold: `split_exit_threshold` (default 1000 lines) |
| `--executor-model <model>` | Swap the executor model (measured: the flash tier already produces high-quality deliveries) |
| `autoknit dashboard` | Visual panel: module progress chain, per-role timing, token consumption (input/output/cache), pending decisions (human acceptance items / escalated modules with root causes — see "Rejection and escalation"). Installed by install.sh; a dsh-side dashboard plugin ships along, plug and play; all APIs exposed, build your own UI if you like |
| `autoknit doctor` | Health check: what's missing, how to install it, human-readable errors |

### About merging

`autoknit merge` is **pure program, zero LLM** mechanical merging: it produces ① the directory skeleton positioned by dependency topology, ② each module's target interface files, ③ cross-module import wiring, ④ four conflict lists (name clashes / naming mismatches / interface signature drift / needs-semantic-merge — each marked "needs human decision"). Measured: hand the merge notes to an agent and **one session finishes the merge**; the semantic-level fusions are clearly listed.

---

## 5. Failure modes and protections (all bitten and fixed — so you don't have to)

| What we stepped on | Status & protection |
|---|---|
| Prompt "if present" wording created uncertainty → executor lost its boundary and explored (measured +80K/round) | ✅ Fixed: the runner decides programmatically — always provide what should be given, never mention what doesn't exist |
| Wrong decomposition granularity → cost inflated 4.6× | ✅ Protected: plan-only review + recursive split + warm continuation |
| final_block remainder silently swallowed | ✅ Fixed: split single-block semantics rebuilt (BUG-20260829, traceable) |
| Each module's tests green, breaks when assembled (contract drift / CORS / wrong endpoints) | ✅ Protected: contract alignment + merge conflict lists + human integration acceptance (still recommended as a final human pass) |
| venv state drift / dead interpreter symlink → silent startup crash | ✅ Fixed: bootstrap trial-run validation + human-readable preflight errors |
| Long tasks frozen by system sleep (macOS lid close) | ✅ Protected: runtime anti-sleep wrapping + QUICKSTART note |
| Task-declared dependencies not installed (zstandard etc.) → executor forced to reinvent | ✅ Fixed: bootstrap aggregates python_packages from task.yaml (120s timeout fallback; on failure writes "known risks" instead of blocking) |
| "Prefer stdlib on missing deps" ambiguity → executor reinvented a codec (pure-Python zstd, 884 lines, once 293K/30min) | ✅ Fixed: tool red lines (no web_search / curl downloads / pip install / reimplementing codecs; zstd via `zstd -d -c`) |
| Upstream info cut off → downstream re-implements (m03 rewrote m02's 138-line summary) | ✅ Fixed: UCD upstream capability digests (AST-extracted interfaces injected 0-token into downstream context) |
| Told to "reuse" without an import path → downstream sys.path hacks and source-diving (m03 once +16%) | ✅ Fixed: UCD reuse guidance (direct import) + PYTHONPATH injection + anti-association discipline |
| Auditor reading all source + re-running tests → 34% of cost | ✅ Fixed: programmatic pre-evidence (pytest/semgrep/boundary) + spot-check-not-full-read (share down to 22–28%, still item-by-item) |

**Why we dare promise "robust"**: we found AI fault-tolerance is "checklist-driven" — scenarios the PRD names explicitly (atomic writes, malformed input, deterministic error codes) all survived adversarial injection testing; what the PRD doesn't name (race conditions, symlink escapes) is every AI tool's shared blind spot. So AutoKnit's answer isn't praying for model luck — it's hard-coding the standard robustness checklist **into every task book programmatically**, so the auditor has something to verify against.

---

## 6. What AutoKnit is NOT for

- A few-hundred-line script or one-off tool — overkill; the sweet spot starts at 1,000 lines
- Pixel-level UI polish that needs unified aesthetic judgment — the divide-and-conquer output is "usable and robust"; pixel-level tuning belongs to interactive tools
- Projects whose requirements you haven't thought through — contract-driven assumes you can say what you want (that's exactly why steps 1–2 exist)

> Note: "strong global state" and "blurry module boundaries" are commonly misread as poor fits — the opposite is true. Strong global state = high coupling, which should be gathered into one module when splitting by coupling (the framework handles oversized modules, splitting recursively past the threshold); blurry boundaries are split's day job. So they're not "poor fits" — they're AutoKnit's use cases.

---

## 7. Quick start

```bash
pip install autoknit                 # one-shot: autoknit command + data bridge (source route: clone + bash install.sh)
autoknit doctor                  # health check, with fix instructions
# Prepare a PRD (have your agent interview you and produce one)
autoknit plan-only <task dir>    # review the plan
autoknit run                     # fully automatic execution
autoknit dashboard               # optional: live progress / consumption / pending decisions
```

---

## Roadmap

**v1.0 shipped**

- [x] Divide-and-conquer execution framework (planner / executor / auditor / split)
- [x] Contract-driven + change isolation + recursive splitting + warm continuation
- [x] Human-in-the-loop + visual panel (incl. dsh dashboard plugin, all APIs exposed) + token/cache observability
- [x] Programmatic code merging (merge, zero LLM) + plan-only review mode
- [x] Packaging & installation (one-shot install.sh + doctor health check + anti-sleep)

**Planned (community-feedback driven, non-blocking for release)**

- [ ] Delivery health system: plan-level topology checks + blast-radius forecasting + static structure evidence
- [ ] More models / more substrate presets
- [ ] More third-party benchmark data (Issues welcome)

---

*AutoKnit — let the LLM do the thinking, let the program do all the legwork.*

---

### Documentation map

| Document | Content |
|---|---|
| docs/quickstart.md | Zero to running (dependencies, dsh install & login, environment pitfalls) |
| docs/cli.md | Commands & flags in detail (threshold semantics, resume mechanics) |
| docs/architecture.md | The four roles + contract system (for contributors) |
| docs/benchmark.md | All benchmark data, per-case reports, full accounting notes |
| docs/faq.md | Glossary (contract / needs_human / rejection / continuation) + FAQ |
