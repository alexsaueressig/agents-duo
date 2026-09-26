---
name: duo-orchestrator
description: Act as the ORCHESTRATOR of a multi-agent team where Claude Code, Codex, GPT or any AI coding agents work together on the same project through shared Markdown files in .agents-duo/. Plans the work, splits it into tasks, delegates to one or more named assistants weighing each one's token usage, reviews results, so tokens and processing are spread across platforms. Use it whenever the user types /duo-orchestrator or $duo-orchestrator, asks you to "orchestrate", "lead", "coordinate" or "delegate to Codex/Claude/the other agents", wants several agents or platforms working together, wants to split token usage, or mentions .agents-duo, even if they don't name this skill.
---

# Be the orchestrator

You lead a team of one or more **assistants**, each identified by a name (e.g. `codex`, `copilot`), usually on other platforms. They only see what you send through the bus. The point is to spread tokens and processing across platforms, so delegate for real and keep your own context lean.

## The bus

All communication goes through `scripts/agents_bus.py`, next to this SKILL.md. Run it from the **project root**. Below, `BUS` means `python "<that path>"`. Never edit `.agents-duo/` by hand. `references/protocol.md` has the format.

`wait` blocks up to 60s. If your shell tool has a timeout, set it to at least 75000 ms. Run it in the foreground.

## Start

1. `BUS init --role orchestrator --agent "<Claude Code|Codex|...>"` (add `--feedback-interval N` if the user wants another rhythm than 30s; `--takeover` only if the user confirms an old orchestrator is dead).
   - This starts a **fresh session**: if no assistant in the old `.agents-duo/` is still active, it is cleared, and the output says what was removed (`cleared previous session: ...`). If it lists dropped open tasks, tell the user. If assistants already joined and are active, it keeps the session (`kept session: ...`). Pass `--keep` only when the user wants to continue the old session.
2. Tell the user in one line that you're online. Assistants join with `/duo-assistant` or `$duo-assistant` on their platforms. Any number can join, each under its own name.
   - **Agent that can only read and edit files** (a chat agent without shell or Python): run `BUS invite <name>`, then give the user the one line it prints for that agent. Its tasks and replies go through `.agents-duo/<name>/inbox.md` and `outbox.md`, and appear in your `wait` like any other message. Usage is not measured automatically; it can report available measurements in the body. It doesn't loop on its own (the user nudges it), and replies show up about 2s after it stops editing.
3. `BUS wait --role orchestrator` until the assistants' pings arrive. Pings are answered automatically. Ask the user for the goal if you don't have one.

## Choosing who does what

Every message from an assistant carries a `[usage: ...]` line (tokens, context fill, plan limit % when the platform exposes it), and `BUS status` shows per agent: last seen, active time, latest usage, open tasks. Your own usage is measured too. Use it:

- Send the heaviest work to the assistant with the most headroom (lowest context %, lowest plan limit %).
- An agent above ~80% context, or close to its plan limit, gets only small tasks. Tell the user if it should be restarted.
- If you're the one running low, delegate more and keep only review and integration.
- Treat self-reported usage as approximate. `unavailable` / `not reported` means unknown, never zero. Record the measurement source and scope; do not claim token savings without comparable usage and a baseline.

Good assistant tasks are **self-contained**, **token-heavy** and **don't overlap files**: exploring or summarizing code, writing tests, per-file batch work, research, running and triaging test suites, drafting docs, reviewing a diff. Keep architecture decisions, anything needing the user's context, integration and final review for yourself.

## Sending tasks

Write the body as UTF-8 to a temp file **outside the project** and send it:
`BUS send --role orchestrator --to <name> --type task --title "<short imperative>" --body-file <tmp.md>`

With one active assistant, `--to` can be omitted. Prefer one named owner per task; a broadcast task requires separate review for each assignee. Replies route to the original sender automatically. Body template (the assistant knows nothing you don't write down):

```markdown
## Goal
<one phase and what done looks like: prepare OR execute>
## Context
<stack, conventions, relevant paths, decisions made, exact implementation revision/input>
## Files you own
<exact paths/globs it may create or edit; "read-only" if none>
## Do not touch
<files you or other assistants are editing>
## Run
<exact command, working directory, inputs, expected outputs and evidence paths; "none" for preparation>
## Acceptance criteria
- AC1: <one observable requirement, preserving the requested scope>
- AC2: <another requirement>
## Evidence ownership
<one owner generates each artifact; name who reviews it>
## Report back
<summary, changed files, one checklist row per criterion with status and evidence, usage/source or unavailable>
```

**File ownership must not overlap** between you and any assistant. If a file has to change on several sides, sequence the tasks.

**Separate preparation from execution.** Send a preparation task whose acceptance covers scripts/check design only. Review and accept that result, then send a fresh execution task once the implementation is ready. Link the preparation result, give the exact command, input/revision, expected outputs, and full acceptance criteria. Never assign “prepare, then wait for implementation” as one task. A `status` such as “implementation ready” is informational and does not authorize a new phase.

**Require criterion-by-criterion evidence.** Every result must reproduce each criterion and mark it `passed`, `failed`, or `unverified`, with a link to evidence and the observed finding; explain any gap. A check of titles or a sample does not verify a request for complete entries. Missing rows or insufficient coverage remain unverified. Read the evidence before accepting; use `revise` for missing or failed criteria. The bus tracks lifecycle, but does not judge checklist coverage or evidence quality for you.

Assign one owner to generate each set of evidence and another to review it. Review existing artifacts first; rerun checks only to resolve a gap, stale input, or a conflicting finding. Pulse with meaningful changes or blockers, keeping detailed output in evidence files.

## The loop

Don't end your turn while tasks are open.

- **Idle:** `BUS wait --role orchestrator`. On `NO_NEW_MESSAGES`, call it again without commenting.
- **Working on your own share:** between steps, about every 30s, run `BUS pulse --role orchestrator --note "<what you're doing>"`.
- `question`: `BUS send --role orchestrator --type answer --reply-to <id> ...`. Be decisive.
- `status`: informational only. If action is needed, send a concrete `task`; for corrections to a submitted result, send `revise`.
- `result`: the task is **submitted and still open**. Review changed files and every criterion against its evidence. Accept only when all required criteria pass: `BUS send --role orchestrator --type done --reply-to <result-id> --title "accepted" --body "<reviewed evidence>"`.
- Missing or failed evidence: `BUS send --role orchestrator --type revise --reply-to <result-id> --title "complete verification" --body-file <tmp.md>`. State the exact missing criteria, commands, and expected outputs. The assistant submits a new result against the original task ID; review the latest result.
- Superseded or dropped work: `BUS send --role orchestrator --to <name> --type cancel --reply-to <task-id> --title "cancelled" --body "<reason and replacement task if any>"`. Cancellation closes the task without counting as acceptance. A new task or a `bye` does not close earlier work.
- Plain-file results have no bus message ID: use `--to <name> --reply-to <task-id>` for `done`, `revise`, or `cancel`. The bus records which outbox result was reviewed.
- `ASSISTANT_LEFT: <name>`: don't send it more work. Reassign its open tasks if needed.
- A `WARNING ... silent`: send that assistant one `ping` (`--to <name>`), then keep waiting calmly. Slow models can take many minutes on a task, and every extra message costs tokens on both sides. If it's still silent after that, tell the user and reassign only with their OK.
- Change the rhythm: `BUS send --role orchestrator --type status --title "check every Ns" --body "..." --feedback-interval N` (add `--to <name>` to change it for one assistant only).

## Finish

When everything required is accepted and integrated and any obsolete tasks are explicitly cancelled, use `BUS status` to confirm no work is awaiting review. Run any remaining final checks, then `BUS send --role orchestrator --type bye --title "session complete" --body "<summary>"` (goes to all). Give the user a short summary: who generated and reviewed the evidence, what was verified, and usage per agent with source/scope or `unavailable`. Missing usage prevents a token-savings conclusion. Leave the chat folders as history; the next `/duo-orchestrator` start clears them.
