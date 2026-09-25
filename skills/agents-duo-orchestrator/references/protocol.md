# Agents duo protocol (v1)

How two AI agents (for example Claude Code and Codex) work together through files in the project root. Read this if something about the bus seems odd. The SKILL.md files hold the everyday workflow.

## Files and who writes them

| Path | Writer | Notes |
|---|---|---|
| `.agents-chat/session.md` | whoever starts first | Exclusive create. Never rewritten. |
| `.agents-chat/orchestrator.md` | orchestrator only | Append-only chat index, one line per message. |
| `.agents-chat/assistant.md` | assistant only | Append-only chat index, one line per message. |
| `.agents-chat/.<role>.cursor` | that role only | Last peer id read. Its mtime is the heartbeat. |
| `.agents-transfer-data/NNNN-<role>-<type>-<slug>.md` | the sender | Immutable body, written to `.tmp` then renamed atomically. |

**Single writer:** no file ever has two writers, so there are no locks, no merge conflicts and no torn reads. The reader ignores a half-written last index line. The body is renamed into place before its index line is appended, so an index line never points to a missing body.

**Ids:** the orchestrator uses odd numbers (1, 3, 5, …) and the assistant uses even numbers (2, 4, 6, …). Each side takes its next id from its own index, so there is no shared counter.

**Index line:**
```
- [0003] 2026-09-25T14:02:05Z task -> assistant: refactor header | ../.agents-transfer-data/0003-orchestrator-task-refactor-header.md
- [0004] 2026-09-25T14:05:40Z result re:0003 -> orchestrator: header refactored | ../.agents-transfer-data/0004-assistant-result-header-refactored.md
```

**Body:** YAML-ish front matter (`id`, `from`, `to`, `type`, `reply_to`, `created`, `title`, and optionally `feedback_interval` and `agent`), then free Markdown.

## Message types

| Type | Sent by | Meaning |
|---|---|---|
| `ping` / `pong` | both | Presence. Every ping gets a pong. |
| `task` | orchestrator | A unit of work (see the task template in agents-duo-orchestrator). |
| `result` | assistant | The finished task. `--reply-to <task id>`. |
| `question` / `answer` | both | Clarification. The answer uses `--reply-to`. |
| `status` | both | Progress heartbeat. The orchestrator may add `feedback_interval`. |
| `done` | orchestrator | Accepts a result (optional, with `--reply-to`). |
| `bye` | both | Ends the session. The peer stops its loop after `PEER_SAID_BYE`. |

## Timing

- `wait` blocks for at most **60s** (a hard cap in the script), then prints `NO_NEW_MESSAGES`, and the agent calls it again.
- The orchestrator sets `feedback_interval` (default **30s**) in its ping, and can change it with `send --type status --feedback-interval N`.
- While working, each agent runs `pulse --note "..."` between steps. `pulse` returns any new messages right away and sends a `status` only when the interval has passed since that agent's last message.
- If the peer hasn't been seen for more than 3× the interval, `wait` prints a `WARNING`. Tell the user. Don't take over the peer's work unless the user says so.

## Script commands

```
python agents_bus.py init   --role R --agent NAME [--feedback-interval 30] [--takeover]
python agents_bus.py send   --role R --type T --title "one line" [--reply-to N] (--body "..." | --body-file F | --body -)
python agents_bus.py wait   --role R [--timeout 60]
python agents_bus.py check  --role R
python agents_bus.py pulse  --role R [--note "progress"] [--reply-to N]
python agents_bus.py status
python agents_bus.py read   --id N
```
Every command accepts `--root DIR` (default: the current directory = the project root).

## Starting over

To start a fresh session, move or delete `.agents-chat/` and `.agents-transfer-data/`, but only when the user asks. They are the conversation history.
