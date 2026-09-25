# agents-duo

**Orchestrator/assistant collaboration between Claude Code and Codex over plain Markdown files. Single writer per file, no locks.**

Run two AI coding agents on the same project, usually on two different platforms, and let them work as a team. One is the **orchestrator**: it plans, splits the work, delegates and reviews. The other is the **assistant**: it takes tasks, does them within the files it's given, and reports back. Because the work runs on two platforms, token usage and processing are split between them instead of all landing on one.

The only thing they share is the project folder. There's no server, no MCP and no network.

```
you ──► Claude Code (/be-orchestrator) ─┐                     ┌─ Codex ($be-assistant)
                                        ▼                     ▼
                        .agents-chat/orchestrator.md   .agents-chat/assistant.md
                                        │     .agents-transfer-data/     │
                                        └──► 0003-orchestrator-task-… ◄──┘
                                             0004-assistant-result-…
```

Either platform can take either role. Both skills are installed on both.

## Install

Requires Python 3.8+ (standard library only).

```bash
git clone https://github.com/alexsaueressig/agents-duo.git
cd agents-duo
python install.py            # installs into ~/.claude/skills and ~/.codex/skills
# python install.py --claude | --codex | --dest <skills-dir>
```

## Use

In the same project folder:

1. **Claude Code:** `/be-orchestrator`, then describe the goal.
2. **Codex:** `$be-assistant`

Either can start first. Whoever starts first creates the session and sends a `ping`, and the other answers with a `pong`. From then on both keep watching the folder. The orchestrator sends tasks, and the assistant sends progress updates and then results. Either side can end the session with `bye`.

To swap roles, run `$be-orchestrator` in Codex and `/be-assistant` in Claude.

## How the file concurrency problem is solved

The core rule is that **every file has exactly one writer.**

| Path | Written by |
|---|---|
| `.agents-chat/session.md` | whoever starts first (exclusive create, never rewritten) |
| `.agents-chat/orchestrator.md` | orchestrator only, append-only index |
| `.agents-chat/assistant.md` | assistant only, append-only index |
| `.agents-chat/.<role>.cursor` | that role only (last message read + heartbeat) |
| `.agents-transfer-data/NNNN-…md` | the sender, written to `.tmp` then renamed atomically, never edited after |

- **Chat indexes stay small.** Each message is one short line in the sender's own index, pointing to a body file. Agents read only the lines past their cursor, never the whole history.
- **No torn reads.** A body is complete before its index line exists, and the reader ignores a partial last line.
- **Unique ids without a shared counter.** The orchestrator uses odd ids and the assistant even ids.
- **Safe simultaneous start.** `session.md` is created with an exclusive create, so one agent creates the session and the other joins it.
- **No clashing project edits.** Every task names the files the assistant *owns* and the files it must not touch.

## Timing

- A blocking `wait` is **capped at 60 s**. On timeout the agent simply waits again, which keeps it responsive to you and cheap on tokens.
- The orchestrator sets the **feedback interval** (default **30 s**) in its first ping, and can change it at any time.
- While working, agents run `pulse` between steps. It picks up new instructions right away and sends a progress `status` only when the interval has passed.
- If the other agent is silent for more than 3× the interval, the waiting agent warns you. It doesn't take over the silent agent's work.

## The bus CLI

The skills drive this for you, but you can also inspect a session by hand:

```bash
BUS=~/.claude/skills/be-orchestrator/scripts/agents_bus.py
python $BUS status            # roles, last seen, open tasks
python $BUS read --id 3       # print one message
```

Commands: `init`, `send`, `wait`, `check`, `pulse`, `status`, `read`. Run with `-h` for details. The full protocol is in [`skills/be-orchestrator/references/protocol.md`](skills/be-orchestrator/references/protocol.md).

## Notes

- **Codex shell timeout:** Codex's default shell timeout is shorter than 60 s. The skills tell the agent to set at least 75 s for `wait`. If Codex cuts it off, remind it.
- **Git:** if the project has a `.gitignore`, `init` adds `.agents-chat/` and `.agents-transfer-data/` to it.
- **Starting over:** to start fresh, delete or move those two folders. They are the conversation history.

## Development

```bash
python -m unittest discover -s tests
```

Edit shared files (`agents_bus.py`, `protocol.md`) in `skills/be-orchestrator/`. `install.py` copies them into `be-assistant`.

## License

MIT
