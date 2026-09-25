# Agents duo protocol (v2)

How one orchestrator and any number of assistants (Claude Code, Codex, GPT, …) work together through files in the project root. The SKILL.md files hold the everyday workflow.

## Files and who writes them

| Path | Writer | Notes |
|---|---|---|
| `.agents-chat/session.md` | whoever starts first (or `reset`) | Exclusive create. Never rewritten. |
| `.agents-chat/orchestrator.md` | orchestrator only | Append-only chat index, one line per message. |
| `.agents-chat/<name>.md` | assistant `<name>` only | Append-only chat index. |
| `.agents-chat/.<name>.cursor` | that participant only | Last id read per source (`<source> <id>` lines). Its mtime is the heartbeat. |
| `.agents-chat/.<name>.usage` | that participant only | Cache for incremental usage measurement. |
| `.agents-chat/.ids/NNNN` | whoever claims the id | Exclusive create, which makes ids globally unique with no locks. |
| `.agents-transfer-data/NNNN-<name>-<type>-<slug>.md` | the sender | Immutable body, written to `.tmp` then renamed atomically. |

**Single writer:** no file ever has two writers. The reader ignores a half-written last index line, and the body is in place before its index line is appended.

**Topology:** a star. The orchestrator sends to one assistant (`-> codex`) or to everyone (`-> all`). Assistants send only to the orchestrator.

**Index line:**
```
- [0005] 2026-09-25T14:02:05Z task -> codex: refactor header | ../.agents-transfer-data/0005-orchestrator-task-refactor-header.md
- [0008] 2026-09-25T14:05:40Z result re:0005 -> orchestrator: header refactored | ../.agents-transfer-data/0008-codex-result-header-refactored.md
```

**Body:** front matter (`id`, `from`, `to`, `type`, `reply_to`, `created`, `title`, and optionally `feedback_interval`, `agent`, `name`, `usage`), then free Markdown.

## Message types

| Type | Sent by | Meaning |
|---|---|---|
| `ping` / `pong` | all | Presence. The script answers every ping with a pong automatically. |
| `task` | orchestrator | A unit of work (template in duo-orchestrator). |
| `result` | assistant | The finished task, `--reply-to <task id>`. |
| `question` / `answer` | all | Clarification. The answer uses `--reply-to`. |
| `status` | all | Progress heartbeat. The orchestrator may add `feedback_interval`. |
| `done` | orchestrator | Accepts a result. |
| `bye` | all | Orchestrator → all ends the session (`PEER_SAID_BYE`). Assistant → orchestrator means that assistant left (`ASSISTANT_LEFT: <name>`). |

## Token usage

Every sent message gets a `usage:` line, measured by the script from the sender's local session log for this project, so no agent tokens are spent on it:

- **Claude Code:** the newest `~/.claude/projects/<project-slug>/*.jsonl`. Total tokens, cached tokens, current context and model. Parsed incrementally.
- **Codex:** the newest `~/.codex/sessions/**/rollout-*.jsonl` whose `cwd` is the project. Total tokens, context / window %, and plan limits (percent used, window, reset time) when Codex records them.
- **Others:** `--usage "<text>"` on `send`/`pulse` (marked `src self-reported`).

The platform is detected from `init --agent`. The orchestrator sees `[usage: …]` on each message it receives, and `status` shows each participant's latest usage and active time.

Limitation: if two participants run on the same platform in the same project, both may measure the newest log of that platform.

## Guidance for simple agents

Every assistant-side output ends with `NEXT:` lines holding the exact command to run next (wait, pulse, result, question), so an agent only has to follow them.

## Timing

- `wait` blocks for at most **60s**, then prints `NO_NEW_MESSAGES`, and the agent calls it again.
- The orchestrator sets `feedback_interval` (default **30s**) in its ping, and changes it with `send --type status --feedback-interval N` (to all, or `--to <name>`).
- `pulse --note "..."` returns new messages right away and sends a `status` only when the interval has passed.
- A participant silent for more than 3× the interval triggers a `WARNING` in the other side's `wait`.

## Script commands

```
python agents_bus.py init   --role R [--name N] --agent PLATFORM [--feedback-interval 30] [--takeover]
python agents_bus.py send   --role R [--name N] --type T --title "one line" [--to NAME|all] [--reply-to ID]
                            (--body "..." | --body-file F | --body -) [--usage "..."]
python agents_bus.py wait   --role R [--name N] [--timeout 60]
python agents_bus.py check  --role R [--name N]
python agents_bus.py pulse  --role R [--name N] [--note "progress"] [--reply-to ID] [--usage "..."]
python agents_bus.py status
python agents_bus.py read   --id ID
python agents_bus.py reset  [--force]   # delete both folders, create a fresh empty session.md
```
`--name` is the assistant's name (default `assistant`). The orchestrator has no name. Every command accepts `--root DIR` (default: the current directory, which should be the project root).
