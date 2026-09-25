# agents-duo

**One orchestrator, many assistants: Claude Code, Codex, GPT or any AI coding agent collaborating over plain Markdown files. Single writer per file, no locks, token usage tracked per agent.**

Run several AI coding agents on the same project, usually on different platforms, and let them work as a team. The **orchestrator** plans, splits the work, delegates and reviews. Each **assistant** (named, e.g. `codex`, `copilot`) takes tasks, does them within the files it's given, and reports back. The work runs on several platforms, so token usage and processing are spread out instead of landing on one. The orchestrator sees each agent's token usage and can weigh it when assigning work.

The only thing they share is the project folder. There's no server, no MCP and no network.

```
Claude Code: /duo-orchestrator     Codex: $duo-assistant (codex)    VS Code chat (copilot)
        │ writes only                      │ writes only                   │ writes only
        ▼                                  ▼                               ▼
.agents-chat/orchestrator.md       .agents-chat/codex.md           .agents-chat/copilot.md
        └───────────────► .agents-transfer-data/ ◄─────────────────────────┘
                            0005-orchestrator-task-….md   0008-codex-result-….md
```

Any platform can take any role. All three skills (`duo-start`, `duo-orchestrator`, `duo-assistant`) are installed for both Claude Code and Codex.

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

0. **Optional, to start fresh:** `/duo-start` (or `$duo-start`) deletes the previous session's data and sets up a new, empty session.
1. **Orchestrator**, e.g. Claude Code: `/duo-orchestrator`, then describe the goal.
2. **Assistants**, e.g. Codex: `$duo-assistant`. It asks you for the assistant's name. Start as many as you like, each with its own name.

Any of them can start first. Pings are answered automatically. The orchestrator sends tasks to a named assistant (or to all), and assistants send progress updates and then results. The orchestrator ends the session with `bye`, and an assistant leaves with `bye`.

### Agents without skills (VS Code chat, other LLM chats)

Any agent that can run shell commands can be an assistant. Paste this into its chat, from the project root:

```text
You are an assistant agent in a team. Run:
python "<path-to>/agents_bus.py" init --role assistant --name copilot --agent "VS Code chat"
Then always run the command shown on the NEXT: lines of each output, and never edit
.agents-chat/ or .agents-transfer-data/ by hand. Do the tasks you receive, touching only the
files each task allows. Keep going until you see PEER_SAID_BYE.
```

Every assistant-side output ends with `NEXT:` lines holding the exact command to run next, so even simple agents can take part.

## Token usage per agent

The bus script reads each agent's own local session log. This costs no agent tokens. It attaches a one-line `usage:` to every message:

- **Claude Code:** total and cached tokens, current context, model
- **Codex:** total tokens, context / window %, plan limit % and reset time (when Codex records them)
- **Other agents:** optional `--usage "..."` self-report

The orchestrator sees `[usage: …]` on each incoming message. `status` shows each agent's latest usage and active time, so heavy work goes to whoever has headroom.

## How the file concurrency problem is solved

The core rule is that **every file has exactly one writer.**

| Path | Written by |
|---|---|
| `.agents-chat/session.md` | whoever starts first (exclusive create, never rewritten) |
| `.agents-chat/orchestrator.md` / `<name>.md` | that participant only, append-only index |
| `.agents-chat/.<name>.cursor` | that participant only (last message read per source + heartbeat) |
| `.agents-chat/.ids/NNNN` | exclusive-create id claims, which give globally unique ids without locks |
| `.agents-transfer-data/NNNN-…md` | the sender, written to `.tmp` then renamed atomically, never edited after |

- **Chat indexes stay small.** Each message is one line in the sender's index, pointing to a body file. Agents read only lines past their cursor.
- **No torn reads.** A body is complete before its index line exists, and readers ignore a partial last line.
- **Safe simultaneous start.** `session.md` and id claims use exclusive create.
- **No clashing project edits.** Every task names the files the assistant *owns* and the files it must not touch.

## Timing

- A blocking `wait` is **capped at 60 s**. On timeout the agent waits again.
- The orchestrator sets the **feedback interval** (default **30 s**) and can change it for all or for one assistant.
- While working, agents run `pulse` between steps. It picks up new instructions right away and sends a progress `status` only when due.
- A participant silent for more than 3× the interval triggers a warning. Nobody takes over its work without the user's OK.

## The bus CLI

```bash
BUS=~/.claude/skills/duo-orchestrator/scripts/agents_bus.py
python $BUS status            # participants, last seen, active time, usage, open tasks
python $BUS read --id 3       # print one message
python $BUS reset             # what /duo-start runs
```

Commands: `init`, `send`, `wait`, `check`, `pulse`, `status`, `read`, `reset`. Run with `-h` for details. The full protocol is in [`skills/duo-orchestrator/references/protocol.md`](skills/duo-orchestrator/references/protocol.md).

## Notes

- **Codex shell timeout:** Codex's default shell timeout is shorter than 60 s. The skills tell the agent to use at least 75 s for `wait`.
- **Git:** if the project has a `.gitignore`, `init` adds `.agents-chat/` and `.agents-transfer-data/` to it.
- **Starting over:** `/duo-start` (or `python $BUS reset`) deletes the history and creates a fresh session. It refuses while an agent still looks active unless you pass `--force`.

## Development

```bash
python -m unittest discover -s tests
```

Edit shared files (`agents_bus.py`, `protocol.md`) in `skills/duo-orchestrator/`. `install.py` copies them into `duo-assistant` and `duo-start`.

## License

MIT
