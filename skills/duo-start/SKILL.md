---
name: duo-start
description: Reset a multi-agent (one orchestrator + any number of assistants) session between Claude Code, Codex, GPT or any AI coding agents by clearing the old shared Markdown files (.agents-chat/ and .agents-transfer-data/) and setting up a fresh, empty session, so /duo-orchestrator and $duo-assistant start clean. Use it whenever the user types /duo-start or $duo-start, says "reset the duo", "start a new duo session", "start over", "clear the agents chat" or wants to wipe .agents-chat or .agents-transfer-data, even if they don't name this skill.
---

# Start a fresh duo session

You prepare the project for a new orchestrator/assistant session. You clear the old conversation and create an empty session. You do **not** take a role yourself: the user starts `/duo-orchestrator` and one or more `$duo-assistant`s afterwards, and they all join the session you created.

## The bus

Everything goes through `scripts/agents_bus.py`, which sits next to this SKILL.md. Resolve its absolute path from this skill's directory (for example `~/.claude/skills/duo-start/scripts/agents_bus.py` or `~/.codex/skills/duo-start/scripts/agents_bus.py`). Run it from the **project root** with `python`. Below, `BUS` means `python "<that path>"`.

Don't delete the folders by hand. `reset` only removes `.agents-chat/` and `.agents-transfer-data/` and checks first that no agent is still running.

## Steps

1. If `.agents-chat/` exists, run `BUS status` and tell the user in one line what will be cleared: how many messages each participant sent and any open tasks. Old data is deleted permanently, so if there are open tasks, mention them explicitly.
2. Run `BUS reset`.
   - If it prints `WARNING: <role> looks active` (exit code 3), an orchestrator or assistant touched the bus recently. Tell the user. Re-run with `BUS reset --force` only after they confirm those agents are stopped, since a running agent would keep writing into the new session.
3. On `OK reset: ...`, tell the user in one or two lines that the session is fresh and what comes next: `/duo-orchestrator` on one platform and `$duo-assistant` on each assistant platform (any order; each assistant picks its own name).
