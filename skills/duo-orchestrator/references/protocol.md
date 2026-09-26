# Agents duo protocol (v3)

How one orchestrator and any number of assistants (Claude Code, Codex, GPT, Copilot, …) work together through files in the project root. The SKILL.md files hold the everyday workflow.

## Layout and who writes what

Everything lives under `.agents-duo/`, with one folder per participant:

```
.agents-duo/
  session.md                       whoever starts first (exclusive create), never rewritten
  messages/NNNN-<from>-<type>-<slug>.md   the sender; immutable body (.tmp + atomic rename)
  .ids/NNNN                        whoever claims the id (exclusive create -> globally unique ids)
  orchestrator/index.md            orchestrator only: append-only index, one line per message
  orchestrator/cursor              orchestrator only: lines read per source (mtime = heartbeat)
  orchestrator/usage.json          orchestrator only: token-usage measurement cache
  <name>/index.md, cursor, usage.json    bus assistant <name> only (same meaning)
  <name>/inbox.md                  plain-file assistant: orchestrator only (rules + its messages in full)
  <name>/outbox.md                 plain-file assistant: <name> only, edited by hand
```

**Single writer:** every file has one owner, so there are no merge conflicts and no torn reads. Readers ignore a half-written last index line, and a body is in place before its index line is appended. An owner that runs several processes at once (parallel tool calls) is serialized by a short `<file>.lock`, taken with an exclusive create. The lock holds an owner token, so a process only ever releases its own lock. A lock older than 5s is treated as stale and removed. Each participant's cursor read-and-write runs under its own lock, so parallel `check`/`pulse`/`wait` calls deliver every message once.

**Topology:** a star. The orchestrator sends to one assistant (`-> codex`) or to everyone (`-> all`). Assistants send only to the orchestrator.

**Index line** (the path is relative to the sender's folder):
```
- [0005] 2026-09-25T14:02:05Z task -> codex: refactor header | ../messages/0005-orchestrator-task-refactor-header.md
- [0008] 2026-09-25T14:05:40Z result re:0005 -> orchestrator: header refactored | ../messages/0008-codex-result-header-refactored.md
```

**Body:** front matter (`id`, `from`, `to`, `type`, `reply_to`, `created`, `title`, and optionally `feedback_interval`, `agent`, `name`, `usage`), then free Markdown. Broadcast tasks record `assignees` at send time. Review decisions record `reviewed_result` (a message ID, or `plain-<name>-<section-number>` for an outbox result).

**Cursors** count index lines read per source, not ids. Concurrent sends can append ids out of order, and counting lines means nothing is skipped or delivered twice.

**Resubscribe:** `init` under an assistant name whose `index.md` exists resets only that assistant's part. Its index and bodies stay (the orchestrator's cursor counts those lines), each task still assigned or awaiting revision gets an incomplete `result` titled `dropped: assistant resubscribed`, its cursor jumps to the end (the unread backlog is skipped), `usage.json` is deleted, and it pings the orchestrator again. These results remain open for review/cancellation; previously submitted results are preserved. The orchestrator cancels obsolete tasks before assigning replacements. The whole session is reset only by the orchestrator: its `init` clears an old session when no assistant is active (`--keep` to join it instead), and `reset` does it by hand.

## Two ways to be an assistant

1. **Bus assistant** (can run Python): `init --role assistant --name <name>`, then `wait` / `pulse` / `send`. The `init`, `wait`, `check` and `pulse` outputs end with `NEXT:` lines holding the exact command to run next (`send` just prints `SENT`). Pings are answered automatically.
2. **Plain-file assistant** (can only read and edit files): the orchestrator runs `invite <name>`, and the user tells the agent: *Read `.agents-duo/<name>/inbox.md` and follow its instructions.*
   - `inbox.md` explains the rules, and every message for `<name>` (or for all) is appended there in full.
   - The agent appends sections to `outbox.md`: `## hello`, `## reply 0005`, `## question 0005`, `## status`, `## bye`, each followed by free text.
   - The orchestrator's bus reads `outbox.md` only when it has been unchanged for 2s, since the agent may be mid-edit, and shows the sections as normal messages (`reply` = `result`).

## Message types

| Type | Sent by | Meaning |
|---|---|---|
| `ping` / `pong` | all | Presence. The script answers every ping with a pong automatically. |
| `task` | orchestrator | One phase with exact inputs, commands, expected outputs and acceptance criteria. |
| `result` | assigned assistant | Submission for review, `--reply-to <task id>`; keeps the task open. |
| `question` / `answer` | all | Clarification. The answer uses `--reply-to`. |
| `status` | all | Informational progress heartbeat, never an execution handoff. The orchestrator may add `feedback_interval`. |
| `done` | orchestrator | Accepts the latest submitted result and closes that assignee's task. |
| `revise` | orchestrator | Requests explicit corrections to a submitted result; keeps the original task open. |
| `cancel` | orchestrator | Closes obsolete or dropped work without acceptance; explain why. |
| `bye` | all | Orchestrator → all ends the session (`PEER_SAID_BYE`). Assistant → orchestrator means that assistant left (`ASSISTANT_LEFT: <name>`). |

## Task lifecycle and handoffs

`assigned → submitted → accepted`, or `submitted → revision_requested → submitted`. The orchestrator can move any open task to `cancelled`. `status` lists each assignee's state, including accepted and cancelled work. `result`, `answer`, `status`, a new task, and `bye` never close a task. Only the orchestrator sends `done` or `cancel`. Questions close on an `answer`.

Prefer separate preparation and execution tasks: accept the scripts/check design first, then send a fresh task when implementation is ready. That execution task must link the prepared checks, identify the input/revision, supply the exact command and expected output paths, and enumerate the execution acceptance criteria. “Implementation ready” in a status message does not start work. A preparation result verifies only the preparation task.

Review commands (not standalone `done`/`revise` subcommands):

```text
python agents_bus.py send --role orchestrator --type done --reply-to RESULT_ID --title "accepted" --body "Reviewed evidence ..."
python agents_bus.py send --role orchestrator --type revise --reply-to RESULT_ID --title "complete checks" --body-file corrections.md
python agents_bus.py send --role orchestrator --type cancel --to NAME --reply-to TASK_ID --title "cancelled" --body "Reason ..."
```

`done` and `revise` can also reference a task ID; they target its latest submitted result. For plain-file results (which have no bus message ID), use `--to NAME --reply-to TASK_ID`. The stored section key identifies the reviewed outbox entry. For broadcast tasks, each assignee needs a separate decision; specify `--to NAME` when referencing the task. New assistants do not inherit v3 broadcast tasks. Revisions submit results against the original task ID. A stale result, wrong assignee, acceptance without a result, or assistant-side acceptance is rejected before writing a message.

Every result must include every acceptance criterion verbatim, marked `passed`, `failed`, or `unverified`, plus an evidence link and observed finding or an explanation of the gap. Full-content requirements need full-content evidence, not selected fields or samples. The orchestrator checks coverage and evidence before `done`; the bus validates transitions, not report semantics. One owner generates each artifact, another reviews it. Repeat checks only for a specific gap, stale input, or conflicting finding.

The v3 reader also understands v2 indexes and `done` references. Old results without orchestrator acceptance now appear as submitted/open; review or cancel them explicitly. Upgrade both skill copies together; older scripts still apply the old closure rules.

## Token usage

Every sent message gets a `usage:` line, measured by the script from the sender's local session log for this project. No agent tokens are spent on it:

- **Claude Code:** the newest `~/.claude/projects/<project-slug>/*.jsonl`. Total and cached tokens, current context, model. Parsed incrementally.
- **Codex:** the newest `~/.codex/sessions/**/rollout-*.jsonl` whose `cwd` is the project. Total tokens, context / window %, and plan limits (percent used, window, reset time) when Codex records them.
- **Others:** `--usage "<measurement, source and scope>"` on `send`/`pulse` (marked `src self-reported`). Plain-file assistants include available usage in the report body; it is not extracted into the usage summary.

The platform is detected from `init --agent`. The orchestrator sees `[usage: …]` on each message it receives, and `status` shows each participant's latest usage and active time. Unavailable measurements are explicitly `unavailable`, never zero. Token-savings claims require comparable usage scope and a baseline; missing measurements prevent that conclusion.

Limitation: if two participants run on the same platform in the same project, both may measure the newest log of that platform.

## Timing

- `wait` blocks for at most **60s**, then prints `NO_NEW_MESSAGES`, and the agent calls it again.
- The orchestrator sets `feedback_interval` (default **30s**) in its ping, and changes it with `send --type status --feedback-interval N` (to all, or `--to <name>`).
- `pulse --note "..."` returns new messages right away and sends a `status` only when the interval has passed. Its `NEXT:` follows task state: continue assigned work, or wait for answers/review when appropriate. No new messages does not mean stop working.
- A bus participant silent for more than 3× the interval (10× with open tasks) triggers a `WARNING` in the other side's `wait`. Plain-file assistants never trigger it. Open tasks, including submitted results, retain a 30-minute activity grace for reset/start checks.

## Script commands

```
python agents_bus.py init   --role R [--name N] --agent PLATFORM [--feedback-interval 30] [--takeover]
python agents_bus.py send   --role R [--name N] --type T --title "one line" [--to NAME|all] [--reply-to ID]
                            (--body "..." | --body-file F | --body -) [--usage "..."]
python agents_bus.py wait   --role R [--name N] [--timeout 60]
python agents_bus.py check  --role R [--name N]
python agents_bus.py pulse  --role R [--name N] [--note "progress"] [--reply-to ID] [--usage "..."]
python agents_bus.py invite NAME          # orchestrator: add a plain-file assistant
python agents_bus.py status
python agents_bus.py read   --id ID
python agents_bus.py reset  [--force]     # delete .agents-duo/ and create a fresh, empty session
```
`--name` is the assistant's name (default `assistant`). The orchestrator has no name. Every command accepts `--root DIR` (default: the current directory, which should be the project root).
