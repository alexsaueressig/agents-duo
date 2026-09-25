# agents-duo

**One orchestrator, many assistants: Claude Code, Codex, GPT, Copilot or any AI agent collaborating over plain Markdown files. Single writer per file, token usage tracked per agent.**

Run several AI agents on the same project, usually on different platforms, and let them work as a team. The **orchestrator** plans, splits the work, delegates and reviews. Each **assistant** (named, e.g. `codex`, `copilot`) takes tasks, does them within the files it's given, and reports back. The work runs on several platforms, so token usage and processing are spread out instead of landing on one. The orchestrator sees each agent's token usage and can weigh it when assigning work.

The only thing they share is the project folder. There's no server, no MCP and no network.

```
Claude Code: /duo-orchestrator      Codex: $duo-assistant             VS Code chat (no Python)
        │ writes only                       │ writes only                    │ writes only
        ▼                                   ▼                                ▼
.agents-duo/orchestrator/index.md   .agents-duo/codex/index.md       .agents-duo/copilot/outbox.md
        │                                   │                                ▲ reads
        └──────────► .agents-duo/messages/ ◄┘      .agents-duo/copilot/inbox.md (orchestrator writes)
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

0. **Optional, to start fresh:** `/duo-start` (or `$duo-start`) deletes the previous session (`.agents-duo/`) and sets up a new, empty one.
1. **Orchestrator**, e.g. Claude Code: `/duo-orchestrator`, then describe the goal.
2. **Assistants**, e.g. Codex: `$duo-assistant`. It asks you for the assistant's name. Start as many as you like, each with its own name.

Any of them can start first. Pings are answered automatically. The orchestrator sends tasks to a named assistant (or to all), and assistants send progress updates and then results. The orchestrator ends the session with `bye`, and an assistant leaves with `bye`.

### Agents without the skills

**Can run shell commands** (e.g. VS Code chat with a terminal): paste this into its chat, from the project root:

```text
You are an assistant agent in a team. Run:
python "<path-to>/agents_bus.py" init --role assistant --name copilot --agent "VS Code chat"
Then always run the command shown on the NEXT: lines of each output, and never edit
.agents-duo/ by hand. Do the tasks you receive, touching only the files each task allows.
Keep going until you see PEER_SAID_BYE.
```

Every assistant-side output ends with `NEXT:` lines holding the exact command to run next.

**Can only read and edit files:** ask the orchestrator to invite it (`invite copilot`), then tell the agent one line:

```text
Read .agents-duo/copilot/inbox.md and follow its instructions.
```

The inbox explains the rules, and each task is appended there in full. The agent replies by adding `## reply 0005` sections to `.agents-duo/copilot/outbox.md`. Chat agents usually stop between turns, so say "check" when you want it to look for new messages.

## Token usage per agent

The bus script reads each agent's own local session log. This costs no agent tokens. It attaches a one-line `usage:` to every message:

- **Claude Code:** total and cached tokens, current context, model
- **Codex:** total tokens, context / window %, plan limit % and reset time (when Codex records them)
- **Other agents:** optional `--usage "..."` self-report

The orchestrator sees `[usage: …]` on each incoming message. `status` shows each agent's latest usage and active time, so heavy work goes to whoever has headroom.

## How the file concurrency problem is solved

The core rule is that **every file has exactly one writer.**

| Path (under `.agents-duo/`) | Written by |
|---|---|
| `session.md` | whoever starts first (exclusive create, never rewritten) |
| `messages/NNNN-…md` | the sender, written to `.tmp` then renamed atomically, never edited after |
| `.ids/NNNN` | exclusive-create id claims, which give globally unique ids |
| `<name>/index.md` | that participant only, append-only index |
| `<name>/cursor`, `<name>/usage.json` | that participant only (lines read per source + heartbeat; usage cache) |
| `<name>/inbox.md` | the orchestrator only (plain-file assistants) |
| `<name>/outbox.md` | that assistant only, by hand (plain-file assistants) |

- **Indexes stay small.** Each message is one line in the sender's index, pointing to a body file. Agents read only lines past their cursor.
- **No torn reads.** A body is complete before its index line exists, readers ignore a partial last line, and an outbox is read only after it has been unchanged for 2 s.
- **Parallel tool calls are safe.** When one agent runs several bus commands at once, a short lock file serializes its own appends.
- **No clashing project edits.** Every task names the files the assistant *owns* and the files it must not touch.

Stress-tested with 6 assistants and 120 concurrent tasks: no messages lost, duplicated or misrouted.

## Timing

- A blocking `wait` is **capped at 60 s**. On timeout the agent waits again.
- The orchestrator sets the **feedback interval** (default **30 s**) and can change it for all or for one assistant.
- While working, agents run `pulse` between steps. It picks up new instructions right away and sends a progress `status` only when due.
- A bus participant silent for more than 3× the interval triggers a warning. Nobody takes over its work without the user's OK.

## The bus CLI

```bash
BUS=~/.claude/skills/duo-orchestrator/scripts/agents_bus.py
python $BUS status            # participants, last seen, active time, usage, open tasks
python $BUS invite copilot    # add a plain-file assistant
python $BUS read --id 3       # print one message
python $BUS reset             # what /duo-start runs
```

Commands: `init`, `send`, `wait`, `check`, `pulse`, `status`, `invite`, `read`, `reset`. Run with `-h` for details. The full protocol is in [`skills/duo-orchestrator/references/protocol.md`](skills/duo-orchestrator/references/protocol.md).

## Notes

- **Codex shell timeout:** Codex's default shell timeout is shorter than 60 s. The skills tell the agent to use at least 75 s for `wait`.
- **Git:** if the project has a `.gitignore`, `init` adds `.agents-duo/` to it.
- **Starting over:** `/duo-start` (or `python $BUS reset`) deletes `.agents-duo/` and creates a fresh session. It refuses while an agent still looks active unless you pass `--force`.
- **Same platform twice:** if two agents on the same platform work in the same project, their usage may be measured from the same (newest) session log.

## Development

```bash
python -m unittest discover -s tests
```

Edit shared files (`agents_bus.py`, `protocol.md`) in `skills/duo-orchestrator/`. `install.py` copies them into `duo-assistant` and `duo-start`, and refuses to install into the repo's own `skills/` folder.

## License

MIT
