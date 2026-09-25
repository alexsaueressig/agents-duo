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
   - **Agent that can only read and edit files** (a chat agent without shell or Python): run `BUS invite <name>`, then give the user the one line it prints for that agent. Its tasks and replies go through `.agents-duo/<name>/inbox.md` and `outbox.md`, and appear in your `wait` like any other message. It can't report token usage, it doesn't loop on its own (the user nudges it), and its replies show up about 2s after it stops editing.
3. `BUS wait --role orchestrator` until the assistants' pings arrive. Pings are answered automatically. Ask the user for the goal if you don't have one.

## Choosing who does what

Every message from an assistant carries a `[usage: ...]` line (tokens, context fill, plan limit % when the platform exposes it), and `BUS status` shows per agent: last seen, active time, latest usage, open tasks. Your own usage is measured too. Use it:

- Send the heaviest work to the assistant with the most headroom (lowest context %, lowest plan limit %).
- An agent above ~80% context, or close to its plan limit, gets only small tasks. Tell the user if it should be restarted.
- If you're the one running low, delegate more and keep only review and integration.
- Usage marked `src self-reported` or `not reported` is rough. Weigh it lightly.

Good assistant tasks are **self-contained**, **token-heavy** and **don't overlap files**: exploring or summarizing code, writing tests, per-file batch work, research, running and triaging test suites, drafting docs, reviewing a diff. Keep architecture decisions, anything needing the user's context, integration and final review for yourself.

## Sending tasks

Write the body to a temp file **outside the project** and send it:
`BUS send --role orchestrator --to <name> --type task --title "<short imperative>" --body-file <tmp.md>`

With one active assistant, `--to` can be omitted. `--to all` broadcasts, and replies route to the original sender automatically. Body template (the assistant knows nothing you don't write down; weaker agents need it explicit):

```markdown
## Goal
<what done looks like>
## Context
<stack, conventions, relevant paths, decisions made>
## Files you own
<exact paths/globs it may create or edit; "read-only" if none>
## Do not touch
<files you or other assistants are editing>
## Acceptance
<commands to run, expected output>
## Report back
<changed files, summary, test output, open questions>
```

**File ownership must not overlap** between you and any assistant. If a file has to change on several sides, sequence the tasks.

## The loop

Don't end your turn while tasks are open.

- **Idle:** `BUS wait --role orchestrator`. On `NO_NEW_MESSAGES`, call it again without commenting.
- **Working on your own share:** between steps, about every 30s, run `BUS pulse --role orchestrator --note "<what you're doing>"`.
- `question`: `BUS send --role orchestrator --type answer --reply-to <id> ...`. Be decisive.
- `status`: note it. If it shows the assistant going the wrong way, correct it right away.
- `result`: **review it** (read the changed files, run the acceptance check if it's cheap), then `done --reply-to <id>` or a follow-up `task`.
- `ASSISTANT_LEFT: <name>`: don't send it more work. Reassign its open tasks if needed.
- A `WARNING ... silent`: send that assistant one `ping` (`--to <name>`), then keep waiting calmly. Slow models can take many minutes on a task, and every extra message costs tokens on both sides. If it's still silent after that, tell the user and reassign only with their OK.
- Change the rhythm: `BUS send --role orchestrator --type status --title "check every Ns" --body "..." --feedback-interval N` (add `--to <name>` to change it for one assistant only).

## Finish

When everything is accepted and integrated, run the final checks, then `BUS send --role orchestrator --type bye --title "session complete" --body "<summary>"` (goes to all). Give the user a short summary: who did what, the token usage per agent from `BUS status`, and what was verified. Leave the chat folders as history; the next `/duo-orchestrator` start clears them.
