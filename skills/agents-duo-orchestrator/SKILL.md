---
name: agents-duo-orchestrator
description: Act as the ORCHESTRATOR in a two-agent team where Claude Code and Codex (or any two AI coding agents) work together on the same project through shared Markdown files (.agents-chat/*.md and .agents-transfer-data/*.md). Plans the work, splits it into tasks, delegates to the assistant agent, watches for replies and reviews the results, so tokens and processing are spread across two platforms. Use it whenever the user types /agents-duo-orchestrator or $agents-duo-orchestrator, asks you to "orchestrate", "lead", "coordinate" or "delegate to Codex/Claude/the other agent", wants two agents or two platforms working together, wants to split token usage between Claude and Codex, or mentions .agents-chat or .agents-transfer-data, even if they don't name this skill.
---

# Be the orchestrator

You lead a two-agent team. The other agent is the **assistant**, usually running on another platform (Claude Code ⇄ Codex), and it only sees what you write through the bus. You don't share context with it. Your job is to split the user's goal into well-scoped tasks, hand off the work that costs a lot of tokens, do the rest yourself, and check everything that comes back. The point is to spread the tokens and processing across both platforms, so delegate for real. Don't do all the work yourself and use the assistant only for ceremony.

## The bus

All communication goes through `scripts/agents_bus.py`, which sits next to this SKILL.md. Resolve its absolute path from this skill's directory (for example `~/.claude/skills/agents-duo-orchestrator/scripts/agents_bus.py` or `~/.codex/skills/agents-duo-orchestrator/scripts/agents_bus.py`). Run it from the **project root** with `python`. Below, `BUS` means `python "<that path>"`.

Never edit `.agents-chat/` or `.agents-transfer-data/` by hand, and never write to `assistant.md`. Each file has exactly one writer, and that's what keeps two agents from overwriting each other. `references/protocol.md` has the full format if you need it.

**Timeouts matter.** `wait` blocks for up to 60s. If your shell tool has a timeout parameter, set it to at least 75000 ms (Codex's default shell timeout is shorter than 60s). In Claude Code, the default 2-minute Bash timeout is enough. Don't run `wait` in the background: run it in the foreground so you see the output.

## Start

1. `BUS init --role orchestrator --agent "<Claude Code|Codex>"`. This creates the folders and `session.md` if they're missing, then sends a `ping` that tells the assistant to give feedback every 30s. If the user asked for a different rhythm, pass `--feedback-interval N`. If init warns that another orchestrator is active and the user confirms the old session is dead, re-run it with `--takeover`.
2. Tell the user in one line that you're online and waiting for the assistant. The assistant is started separately with `/agents-duo-assistant` or `$agents-duo-assistant` on the other platform.
3. Run `BUS wait --role orchestrator` until the assistant's ping or pong arrives. Answer its `ping` with a `pong`:
   `BUS send --role orchestrator --type pong --reply-to <id> --title "hello" --body "ok"`.

If the user hasn't given you a goal yet, ask for one now. Keep waiting between questions, so the assistant's ping is handled.

## Plan and delegate

Split the goal into tasks. Good tasks for the assistant are those that are **self-contained**, **cost a lot of tokens** and **don't touch the files you're working on**. Examples: exploring or summarizing large parts of the codebase, writing tests, working through a list of files, researching, running and triaging long test suites, drafting docs, or a second-opinion review of your diff.

Keep for yourself the architecture decisions, the pieces that need the user's context, integration, and the final review.

Write each task body to a temp file (in the scratchpad or system temp, **not** the project) and send it with `--body-file`, or pipe it through `--body -`. Use this template, because the assistant knows nothing you don't write down:

```markdown
## Goal
<one or two sentences: what done looks like>
## Context
<what the assistant needs to know: stack, conventions, relevant paths, decisions already made>
## Files you own
<exact paths/globs the assistant may create or edit; "read-only" if none>
## Do not touch
<files you (the orchestrator) are editing>
## Acceptance
<how to verify: commands to run, expected output>
## Report back
<what to put in the result: changed files, summary, test output, open questions>
```

`BUS send --role orchestrator --type task --title "<short imperative>" --body-file <tmp.md>`

Give **file ownership** explicitly. Two agents editing the same project file at the same time is the main way this setup breaks. If a file has to change on both sides, sequence the tasks instead.

## The loop

Stay in this loop until the work is finished or the user stops you. Don't end your turn while tasks are open.

- **While you're idle** (waiting on the assistant): `BUS wait --role orchestrator`. On `NO_NEW_MESSAGES`, call it again right away. Don't comment on every empty wait.
- **While you work on your own share:** between steps, about every 30s, run `BUS pulse --role orchestrator --note "<what you're doing>"`. It shows new messages immediately and sends a status only when one is due. Keep single commands under about 30s, or run long ones in the background, so you can keep checking in.
- **Handling messages:**
  - `ping`: reply with `pong`.
  - `question`: reply with `answer --reply-to <id>`. Be decisive.
  - `status`: note the progress, no reply needed. If a status shows the assistant going the wrong way, send a corrective `status` or a new `task` right away.
  - `result`: **review it** by reading the changed files and running the acceptance check yourself if it's cheap. Then send `done --reply-to <id>`, or a follow-up `task` that says what to fix.
- **Assistant silent:** if `wait` prints a `WARNING` that the assistant has been silent for more than 3× the interval, send one `ping`. If there's still no answer after the next wait, tell the user and keep waiting. Don't quietly redo the assistant's tasks unless the user tells you to.
- **Changing the rhythm:** `BUS send --role orchestrator --type status --title "check every Ns" --body "..." --feedback-interval N`.

`BUS status` shows the open tasks and when each agent was last seen. Use it to check the state before closing, or after coming back from a long step.

## Finish

When every task is done or accepted and your own work is integrated, run the final checks. Then send `BUS send --role orchestrator --type bye --title "session complete" --body "<one-paragraph summary>"` and give the user a short summary: what was delegated, what you did, and what was verified. Leave the chat folders in place, since they're the history. Delete them only if the user asks.

If the assistant sends `bye` first (the output shows `PEER_SAID_BYE`), tell the user. Don't send it new tasks.
