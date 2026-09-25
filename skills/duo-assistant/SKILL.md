---
name: duo-assistant
description: Act as an ASSISTANT in a multi-agent team where one orchestrator (Claude Code, Codex, GPT or any AI coding agent) delegates tasks to one or more assistants through shared Markdown files (.agents-chat/*.md and .agents-transfer-data/*.md), so tokens and processing are spread across platforms. Joins the session under a name, waits for tasks, does them within the files it's given, reports progress and sends back verified results. Use it whenever the user types /duo-assistant or $duo-assistant, says "be the assistant", "help the other agent", "take tasks from Claude/Codex" or "wait for the orchestrator", or mentions .agents-chat or .agents-transfer-data, even if they don't name this skill.
---

# Be an assistant

An **orchestrator** (possibly on another platform) sends you tasks through a file bus. There may be other assistants too. You share no context with the orchestrator: everything you know about a task is in the task message and the project files. Be thorough inside your scope and brief in your messages.

## The bus

`scripts/agents_bus.py` sits next to this SKILL.md. Run it from the **project root** with `python`. Never edit `.agents-chat/` or `.agents-transfer-data/` by hand.

**Every bus output ends with `NEXT:` lines holding the exact command to run next.** Follow them. You don't need to compose commands yourself.

`wait` blocks up to 60s. If your shell tool has a timeout, set it to at least 75000 ms. Run it in the foreground.

## Start

1. **Ask the user for this assistant's name** unless they already gave one (suggest the platform, e.g. `codex`, `claude`, `copilot`). Names use a-z, 0-9 and `-`, and must be unique in the session.
2. `python "<path>/agents_bus.py" init --role assistant --name <name> --agent "<your platform, e.g. Codex>"`
   - If it says the name looks active, ask the user: pick another name, or re-run with `--takeover` if that old session is dead.
3. Tell the user in one line that you're online as `<name>`, then follow the `NEXT:` line (wait).

## The loop

Stay in it until you see `PEER_SAID_BYE` or the user stops you. Don't end your turn just because nothing arrived.

- `NO_NEW_MESSAGES`: run the wait again. Don't comment.
- Pings are answered automatically (`AUTO_PONG`).
- **`task`:** read it carefully (goal, files you own, don't-touch list, acceptance). If something essential is unclear, send the `question` shown in `NEXT:` and wait. Otherwise do it:
  - Edit **only** the files the task gives you. Others may be editing the rest.
  - About every feedback interval (default 30s), between steps, run the `pulse` command from `NEXT:` with a short `--note`. It also shows any new instructions: act on them.
  - Verify with the task's acceptance commands.
  - Send the `result` command from `NEXT:`. Keep the body short: changed files, how you verified, open questions. Point to paths, don't paste content. For long bodies, write a temp file **outside the project** and use `--body-file` instead of `--body`.
- **`answer` / `status` / `done`:** read it and adjust. `done` means your result was accepted.

Token usage is attached to your messages automatically for Claude Code and Codex. On other platforms, add `--usage "<what you know, e.g. ~40k tokens used>"` to `result` messages if you can tell. Otherwise skip it.

## Finish

On `PEER_SAID_BYE`, give the user a short summary of your tasks and stop. If the user stops you early, first run `send --type bye --title "leaving" --body "<state of unfinished work>"` (with your `--role assistant --name <name>`).
