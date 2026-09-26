---
name: duo-assistant
description: Act as an ASSISTANT in a multi-agent team where one orchestrator (Claude Code, Codex, GPT or any AI coding agent) delegates tasks to one or more assistants through shared Markdown files in .agents-duo/, so tokens and processing are spread across platforms. Joins the session under a name, waits for tasks, does them within the files it's given, reports progress and sends back verified results. Use it whenever the user types /duo-assistant or $duo-assistant, says "be the assistant", "help the other agent", "take tasks from Claude/Codex" or "wait for the orchestrator", or mentions .agents-duo, even if they don't name this skill.
---

# Be an assistant

An **orchestrator** (possibly on another platform) sends you tasks through a file bus. There may be other assistants too. You share no context with the orchestrator: everything you know about a task is in the task message and the project files. Be thorough inside your scope and brief in your messages.

## The bus

`scripts/agents_bus.py` sits next to this SKILL.md. Run it from the **project root** with `python`. Never edit `.agents-duo/` by hand.

**`init`, `wait`, `check` and `pulse` end with `NEXT:` lines holding the exact command to run next.** Follow them. You don't need to compose commands yourself.

`wait` blocks up to 60s. If your shell tool has a timeout, set it to at least 75000 ms. Run it in the foreground.

## Start

1. **Ask the user for this assistant's name** unless they already gave one (suggest the platform, e.g. `codex`, `claude`, `copilot`). Names use a-z, 0-9 and `-`, and must be unique in the session.
2. `python "<path>/agents_bus.py" init --role assistant --name <name> --agent "<your platform, e.g. Codex>"`
   - If it says the name looks active, ask the user: pick another name, or re-run with `--takeover` if that old session is dead.
   - Rejoining under a name that was used before is a **resubscribe**: only your part is reset. Your sent messages stay as history, work still in progress is reported as dropped and remains open for orchestrator review/cancellation, and older unread messages are skipped. Previously submitted results still await review. Start from what arrives next, not from anything you remember.
3. Tell the user in one line that you're online as `<name>`, then follow the `NEXT:` line (wait).

## The loop

Stay in it until you see `PEER_SAID_BYE` or the user stops you. Don't end your turn just because nothing arrived.

- `NO_NEW_MESSAGES`: follow the state-aware `NEXT:` line: continue assigned work, or wait if idle, awaiting an answer, or awaiting review. An empty check/pulse does not suspend an active task.
- Pings are answered automatically (`AUTO_PONG`).
- **`task`:** read it carefully (goal, files you own, don't-touch list, acceptance). If something essential is unclear, send the `question` shown in `NEXT:` and wait. Otherwise do it:
  - Edit **only** the files the task gives you. Others may be editing the rest.
  - Treat preparation and execution as separate tasks. Submit preparation against its own criteria; wait for a fresh execution task with the exact command, inputs, and expected outputs. If given “prepare then wait” as one task, ask the orchestrator to split it before treating preparation as task completion.
  - About every feedback interval (default 30s), between steps, run the `pulse` command from `NEXT:` with a short `--note` about progress or blockers. It also shows new tasks, revisions, and answers. Status messages are informational.
  - Verify with the task's acceptance commands.
  - Send the `result` command from `NEXT:` with the report below. Write a UTF-8 temp file **outside the project** and use `--body-file` for a multiline report. A result is a submission for review; the task stays open until the orchestrator accepts or cancels it.
- **`status`:** informational. “Implementation ready” alone is not an execution task; keep following the current assignment or wait for a concrete new task.
- **`answer`:** resolve the question and continue the assigned scope.
- **`revise`:** perform the explicit corrections, then submit a new `result --reply-to <original-task-id>` with the complete updated checklist. Keep the evidence that the reviewer needs.
- **`done` / `cancel`:** the orchestrator accepted / cancelled the referenced task. Stop work on that task and follow `NEXT:` for any remaining assignments.

## Result report

Include a brief summary, changed files, and **every assigned acceptance criterion**, preserving its wording and scope:

```markdown
| Criterion | Status | Evidence and finding |
|---|---|---|
| AC1: <exact criterion> | passed | [evidence](path) — <observed result, command/input/revision> |
| AC2: <exact criterion> | failed | [evidence](path) — <mismatch> |
| AC3: <exact criterion> | unverified | <what is missing and why; link partial evidence if available> |

Usage: <measurement and source/scope, or unavailable>
```

Use only `passed`, `failed`, or `unverified`. A partial check cannot pass a complete-content criterion: e.g. comparing role titles does not verify complete experience entries. Keep full outputs in linked evidence files. Generate evidence only when assigned ownership; when assigned review, inspect the existing artifacts and rerun only to resolve a specific gap, stale input, or conflict. Do not repeat checks while awaiting acceptance.

Token usage is measured automatically where Claude Code or Codex exposes it. On other platforms, add `--usage "<measurement, source and scope>"` to `result` messages when available. Otherwise report `Usage: unavailable`; never substitute zero or invent a count. A missing measurement cannot establish token savings.

## Finish

On `PEER_SAID_BYE`, give the user a short summary of your tasks and stop. If the user stops you early, first run `python "<path>/agents_bus.py" send --role assistant --name <name> --type bye --title "leaving" --body "<state of unfinished work>"`. Leaving does not accept or close unfinished tasks.
