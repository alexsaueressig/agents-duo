# Suggestions

Code review from a duo session on 2026-09-25. The reviewer was assistant `claudinho` (Claude Code). The orchestrator spot-checked it: A1 and A2 were confirmed against the code. Nothing here has been applied yet.

Line numbers refer to `skills/duo-orchestrator/scripts/agents_bus.py` as of commit `5056da2`. The assistant copy is identical.

## A. Bugs in `agents_bus.py`

| # | Sev | Where | Problem | Suggested fix |
|---|-----|-------|---------|---------------|
| A1 | high | 750-752, 877 | `pulse` and `check` call `poll()`, which prints `NO_NEW_MESSAGES` and "NEXT: run the same wait again". This happens mid-task, and weak agents then drop the task and go wait. The reviewer reproduced it. | Add `hints=True` to `poll()`. `pulse` passes `False`, or prints "NEXT: continue task NNNN, then pulse again". |
| A2 | high | 808 vs 824-825 | Assistant `init --feedback-interval N` runs `resubscribe()` first, which closes its open tasks and skips unread messages. Only then does it exit with an error. The reviewer reproduced it. | Validate `--feedback-interval` (and `--keep`) for assistants at the top of `cmd_init`, before any writes. |
| A3 | med | 474-480, 535 | Usage takes the newest `*.jsonl`. With two Claude Code agents in the same project, each one reports whichever transcript was written last, and the cache is re-parsed from 0 every time that flips. Codex has the same problem. | Pin the transcript in `usage.json` on first measure and keep it while the file still grows, or use a session-id env var. |
| A4 | med | 812-813, 623 | A brand-new assistant starts with cursor `{}` and receives the whole backlog, including old broadcast tasks that are already done. | On first join, set the cursors to the current length but keep open `to: all` items. |
| A5 | med | 617, 302-307 | `heartbeat()` after `do_send` is neither locked nor caught. On Windows, `os.utime` can raise `PermissionError` during a parallel `os.replace`. The message was already sent, so the sender may resend it. | Wrap it in `try/except OSError`, since it is best effort. |
| A6 | low | 208-212 | Stale-lock removal is check-then-unlink (TOCTOU). Two waiters can end up both holding the lock. | `os.replace` the stale lock to a unique name first, and only the rename winner proceeds. |
| A7 | low | 842, 844 | `--body-file` crashes on UTF-16 files (PowerShell 5.1 `>`), and a BOM leaks `﻿` into the body. Stdin uses the console codepage. | Detect a BOM (utf-8-sig/utf-16) and fall back to utf-8 `errors=replace`. Read stdin from `sys.stdin.buffer`. |
| A8 | low | 506-510 | The usage cache tmp name `usage.tmp` is shared by parallel processes. A clash is swallowed, so the message goes out with no usage. | Use a unique tmp name, as `write_cursors` does (289). |
| A9 | low | 363, 437 | Plain-file entries have id 0. Their questions never show as open, and `--reply-to` can't target them. | Give them a synthetic id, or list plain questions separately. |
| A10 | low | 792 | The takeover check uses `3*DEFAULT_INTERVAL`, not the session interval or `BUSY_GRACE`. A busy assistant's name can be taken without `--takeover`. | Use the same limit as `active_others`. |

## B. Docs that don't match the code

| # | Sev | Doc | Problem | Suggested fix |
|---|-----|-----|---------|---------------|
| B1 | high | duo-assistant/SKILL.md:30,34 | The docs tell agents to pulse mid-task, but `pulse` says "wait again" (A1). | Fix A1. Until then, the SKILL should say to ignore that line after `pulse`. |
| B2 | med | protocol.md:75, README.md:98 | The docs say the warning fires after 3x the interval. The code uses 10x while busy, plus the 30 min `BUSY_GRACE`. | "3x the interval (10x while it has an open task)." |
| B3 | med | duo-orchestrator/SKILL.md:19, README.md:35,116 | "Cleared if no assistant is active" doesn't mention that a crashed busy assistant keeps the session for 30 min. | Add that note, and point to `reset --force`. |
| B4 | med | duo-orchestrator/SKILL.md:21, protocol.md:44 | Plain-file replies have no `[NNNN]` id and can't be used with `--reply-to`. | Document "reply with `--to <name>`", or fix A9. |
| B5 | med | duo-orchestrator/SKILL.md:67 | `done --reply-to <id>` looks like a command, but `done` is a message type and needs `--title`. | `BUS send --role orchestrator --type done --reply-to <id> --title "accepted"`. |
| B6 | low | protocol.md:80 | The init synopsis is missing `--keep`. `--agent` is shown as required, but it defaults to "unknown", which silently disables usage. | Add `[--keep]`, and note that `--agent` drives usage detection. |
| B7 | low | protocol.md:81-83 | The synopsis doesn't list `send --feedback-interval`, `wait --interval`/`--max-chars`, or the truncation hint. | Add them. |
| B8 | low | duo-assistant/SKILL.md:43 | The bye command is partial (no python/path). | Give the full command. |
| B9 | low | protocol.md:43 vs `plain_header` | The protocol lists `## status`/`## result` for outbox.md, but the inbox instructions don't mention them. | Align the two. |
| B10 | low | protocol.md:36, 62-63 | `reset` takes no `--role`, so any agent can run it. The `CLAUDE_CONFIG_DIR`/`CODEX_HOME` overrides are undocumented. The UTF-8 requirement for `--body-file` isn't stated. | Document all three. |

## C. Test coverage gaps (`tests/test_bus.py`, 23 tests passing)

| # | Sev | Untested | Test idea |
|---|-----|----------|-----------|
| C1 | high | `claude_usage` / `codex_usage` | Fake `.jsonl` under a temp `CLAUDE_CONFIG_DIR`/`CODEX_HOME`. Check the totals, dedupe, partial line and incremental growth. |
| C2 | high | `pulse` actually sending a status | `--feedback-interval 1`, sleep, `pulse --note x --reply-to 5`. Assert `STATUS_SENT`, and that there's no "wait again" line (A1). |
| C3 | high | Orchestrator `bye` to a bus assistant | Assistant `check` shows `PEER_SAID_BYE` and no "wait again" line. |
| C4 | med | Per-assistant `--feedback-interval` | Set it for `a` only and check it doesn't change `b`; then a broadcast changes both. |
| C5 | med | A2 regression | Assistant `init --feedback-interval` fails and leaves its task open. |
| C6 | med | `silence_note` warnings | Backdate mtimes. Check 3x idle vs 10x busy, the assistant side, and that plain assistants never warn. |
| C7 | med | Plain-file variants | `## question`, `## status`, `## bye`, `[0005]`/`#5` ids, incremental appends, broadcast to 2 inboxes, ping not rendered, invite of a name already on the bus. |
| C8 | low | `--body-file`, stdin, UTF-8 round trip | A `"ação ✓"` body comes back exactly. |
| C9 | low | Truncation and `read --id` | `--max-chars 10` shows the hint, `read --id` returns the full body, and an unknown id fails. |
| C10 | low | `ensure_gitignore`, `status` details | Only one `.agents-duo/` line added; an open question is listed. |

## Suggested order

1. A1 + B1 + C2/C3 (one change fixes the worst failure for weak agents).
2. A2 + C5.
3. B2-B5 (doc accuracy after the busy-grace change).
4. A5, A3, A4, then the low items.
