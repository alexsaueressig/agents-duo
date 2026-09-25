#!/usr/bin/env python3
"""agents_bus.py - file-based message bus for two AI agents (orchestrator + assistant).

Single-writer design: every file has exactly one writer, so no locks are needed.

  .agents-chat/session.md             created once (exclusive create) by whoever starts first
  .agents-chat/<role>.md              append-only chat index, written ONLY by <role>
  .agents-chat/.<role>.cursor         last message id <role> has read from its peer (+ heartbeat mtime)
  .agents-transfer-data/NNNN-<role>-<type>-<slug>.md   immutable message bodies (atomic rename)

Ids: orchestrator uses odd numbers, assistant even numbers -> unique without a shared counter.

Commands: init, send, wait, check, pulse, status, read.  Run with -h for details.
Stdlib only. Works on Windows, macOS, Linux.
"""
import argparse
import datetime as dt
import os
import re
import sys
import time
from pathlib import Path

PROTOCOL = 1
ROLES = ("orchestrator", "assistant")
PEER = {"orchestrator": "assistant", "assistant": "orchestrator"}
PARITY = {"orchestrator": 1, "assistant": 0}
TYPES = ("ping", "pong", "task", "result", "question", "answer", "status", "done", "bye")
MAX_WAIT = 60            # hard cap for a single blocking wait, seconds
DEFAULT_INTERVAL = 30    # default feedback interval, seconds
CHAT_DIR = ".agents-chat"
DATA_DIR = ".agents-transfer-data"
LINE_RE = re.compile(
    r"^- \[(?P<id>\d+)\] (?P<ts>\S+) (?P<type>\w+)(?: re:(?P<re>\d+))? -> (?P<to>\w+): (?P<title>.*) \| (?P<path>\S+)$"
)

try:  # Windows consoles default to cp1252; message bodies are UTF-8
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# ---------------------------------------------------------------- helpers

def now_iso():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def paths(root):
    root = Path(root).resolve()
    return root, root / CHAT_DIR, root / DATA_DIR


def index_file(chat, role):
    return chat / f"{role}.md"


def cursor_file(chat, role):
    return chat / f".{role}.cursor"


def slugify(text, n=40):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return (s[:n].rstrip("-")) or "msg"


def parse_index(chat, role):
    """Return complete entries of <role>'s index (ignores a trailing partial line)."""
    f = index_file(chat, role)
    if not f.exists():
        return []
    raw = f.read_text(encoding="utf-8", errors="replace")
    if not raw.endswith("\n"):
        raw = raw[: raw.rfind("\n") + 1]  # last line still being written
    out = []
    for line in raw.splitlines():
        m = LINE_RE.match(line.strip())
        if m:
            d = m.groupdict()
            d["id"] = int(d["id"])
            d["re"] = int(d["re"]) if d["re"] else None
            d["from"] = role
            out.append(d)
    return out


def read_cursor(chat, role):
    f = cursor_file(chat, role)
    try:
        return int(f.read_text(encoding="utf-8").strip() or 0)
    except (FileNotFoundError, ValueError):
        return 0


def write_cursor(chat, role, value):
    f = cursor_file(chat, role)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(str(value), encoding="utf-8")
    os.replace(tmp, f)


def heartbeat(chat, role):
    f = cursor_file(chat, role)
    if f.exists():
        os.utime(f, None)
    else:
        write_cursor(chat, role, 0)


def last_seen(chat, role):
    """Seconds since <role> last touched the bus, or None if never."""
    stamps = [p.stat().st_mtime for p in (cursor_file(chat, role), index_file(chat, role)) if p.exists()]
    return None if not stamps else max(0, time.time() - max(stamps))


def fmt_age(sec):
    if sec is None:
        return "never"
    sec = int(sec)
    return f"{sec}s ago" if sec < 120 else f"{sec // 60}m ago"


def front_matter(text):
    fm = {}
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    fm[k.strip()] = v.strip()
    return fm


def body_path(chat, entry):
    return (chat / entry["path"]).resolve()


def feedback_interval(chat):
    """Latest feedback_interval set by the orchestrator (ping/status front matter), else default."""
    for e in reversed(parse_index(chat, "orchestrator")):
        if e["type"] in ("ping", "status"):
            try:
                fm = front_matter(body_path(chat, e).read_text(encoding="utf-8", errors="replace"))
            except FileNotFoundError:
                continue
            if fm.get("feedback_interval", "").rstrip("s").isdigit():
                return int(fm["feedback_interval"].rstrip("s"))
    return DEFAULT_INTERVAL


def require_session(chat):
    if not chat.exists():
        sys.exit(f"ERROR: no {CHAT_DIR}/ here. Run `init --role <role>` first (cwd={Path.cwd()}).")


# ---------------------------------------------------------------- core ops

def do_send(root, role, mtype, title, body, reply_to=None, extra=None):
    root, chat, data = paths(root)
    require_session(chat)
    data.mkdir(parents=True, exist_ok=True)
    own = parse_index(chat, role)
    last = max([e["id"] for e in own], default=0)
    mid = last + 1 if last else (1 if PARITY[role] else 2)
    if mid % 2 != PARITY[role]:
        mid += 1
    title = " ".join(title.replace("|", "/").split())[:120] or mtype
    name = f"{mid:04d}-{role}-{mtype}-{slugify(title)}.md"
    while (data / name).exists():  # extremely defensive; should never happen
        mid += 2
        name = f"{mid:04d}-{role}-{mtype}-{slugify(title)}.md"
    fm = {
        "id": mid, "from": role, "to": PEER[role], "type": mtype,
        "reply_to": reply_to or "", "created": now_iso(), "title": title,
    }
    fm.update(extra or {})
    text = "---\n" + "".join(f"{k}: {v}\n" for k, v in fm.items()) + "---\n\n" + (body or "").rstrip() + "\n"
    tmp = data / (name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, data / name)  # atomic: peer never sees a partial body
    re_part = f" re:{int(reply_to):04d}" if reply_to else ""
    line = f"- [{mid:04d}] {fm['created']} {mtype}{re_part} -> {PEER[role]}: {title} | ../{DATA_DIR}/{name}\n"
    with open(index_file(chat, role), "a", encoding="utf-8", newline="\n") as fh:  # single writer
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())
    heartbeat(chat, role)
    return mid


def new_messages(chat, role):
    cur = read_cursor(chat, role)
    return [e for e in parse_index(chat, PEER[role]) if e["id"] > cur]


def print_messages(chat, role, msgs, max_chars):
    for e in msgs:
        re_part = f" (re:{e['re']:04d})" if e["re"] else ""
        print(f"=== [{e['id']:04d}] {e['type'].upper()} from {e['from']}{re_part}: {e['title']} ===")
        p = body_path(chat, e)
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            print(f"(body missing: {p})")
            continue
        end = text.find("\n---", 3) if text.startswith("---") else -1
        fm = front_matter(text)
        body = text[end + 4:].strip() if end != -1 else text.strip()
        if fm.get("feedback_interval"):
            print(f"[feedback_interval: {fm['feedback_interval']}]")
        if len(body) > max_chars:
            body = body[:max_chars] + f"\n...[truncated; full text: read --id {e['id']}]"
        print(body or "(empty body)")
        print()
    write_cursor(chat, role, max(e["id"] for e in msgs))
    if any(e["type"] == "bye" for e in msgs):
        print("PEER_SAID_BYE")


def poll(root, role, timeout, interval, max_chars):
    root, chat, _ = paths(root)
    require_session(chat)
    timeout = max(0.0, min(float(timeout), MAX_WAIT))
    deadline = time.time() + timeout
    while True:
        heartbeat(chat, role)
        msgs = new_messages(chat, role)
        if msgs:
            print_messages(chat, role, msgs, max_chars)
            return True
        if time.time() >= deadline:
            break
        time.sleep(min(interval, max(0.05, deadline - time.time())))
    peer = PEER[role]
    age = last_seen(chat, peer)
    iv = feedback_interval(chat)
    note = ""
    if age is None:
        note = f" | {peer} has not joined yet"
    elif age > 3 * iv:
        note = f" | WARNING: {peer} silent for {int(age)}s (> 3x feedback interval {iv}s)"
    print(f"NO_NEW_MESSAGES (waited {int(timeout)}s) | {peer} last seen {fmt_age(age)}{note}")
    return False


# ---------------------------------------------------------------- commands

def cmd_init(a):
    root, chat, data = paths(a.root)
    chat.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)
    age = last_seen(chat, a.role) if cursor_file(chat, a.role).exists() else None
    if age is not None and age < 3 * DEFAULT_INTERVAL and not a.takeover:
        print(f"WARNING: another {a.role} looks active (last seen {int(age)}s ago). "
              f"If that session is dead, re-run with --takeover.")
        sys.exit(3)
    created = False
    try:
        with open(chat / "session.md", "x", encoding="utf-8", newline="\n") as fh:
            fh.write(
                "# Agents session\n\n"
                f"- protocol: {PROTOCOL}\n- created: {now_iso()}\n- created_by: {a.role}\n"
                f"- project: {root}\n- default_feedback_interval: {DEFAULT_INTERVAL}s "
                "(the orchestrator's latest ping/status overrides this)\n\n"
                "Rules: each agent appends ONLY to its own index (`orchestrator.md` / `assistant.md`).\n"
                f"Message bodies live in `../{DATA_DIR}/` and are never edited after creation.\n"
                "Use the agents_bus.py script to send/receive; do not hand-edit these files.\n"
            )
        created = True
    except FileExistsError:
        pass
    idx = index_file(chat, a.role)
    if not idx.exists():
        idx.write_text(f"# {a.role} -> {PEER[a.role]} (append-only, written only by {a.role})\n\n", encoding="utf-8")
    if not cursor_file(chat, a.role).exists():
        write_cursor(chat, a.role, 0)
    gi = root / ".gitignore"
    if gi.exists():
        lines = gi.read_text(encoding="utf-8", errors="replace").splitlines()
        missing = [d + "/" for d in (CHAT_DIR, DATA_DIR) if d + "/" not in lines and d not in lines]
        if missing:
            with open(gi, "a", encoding="utf-8") as fh:
                fh.write(("\n" if lines and lines[-1].strip() else "") + "\n".join(missing) + "\n")
    iv = a.feedback_interval or DEFAULT_INTERVAL
    if a.role == "orchestrator":
        body = (f"Orchestrator online (agent: {a.agent}).\n\n"
                f"Instruction: while working on a task, get feedback every {iv}s - between work steps run "
                f"`pulse` (checks for new instructions and sends a progress status when due). "
                f"Blocking waits are max {MAX_WAIT}s. Reply with `pong`, then wait for tasks.")
        extra = {"feedback_interval": f"{iv}s", "agent": a.agent}
    else:
        body = (f"Assistant online (agent: {a.agent}). Ready for tasks. "
                f"Will follow the orchestrator's feedback_interval (default {DEFAULT_INTERVAL}s).")
        extra = {"agent": a.agent}
    mid = do_send(a.root, a.role, "ping", f"{a.role} online", body, extra=extra)
    peer_age = last_seen(chat, PEER[a.role])
    print(f"OK init role={a.role} session={'created' if created else 'joined'} ping=[{mid:04d}] "
          f"{PEER[a.role]} last seen {fmt_age(peer_age)}")
    print(f"chat: {chat}\ndata: {data}")


def read_body_arg(a):
    if a.body_file:
        return Path(a.body_file).read_text(encoding="utf-8")
    if a.body == "-":
        return sys.stdin.read()
    return a.body or ""


def cmd_send(a):
    extra = {}
    if a.feedback_interval:
        if a.role != "orchestrator":
            sys.exit("ERROR: only the orchestrator sets feedback_interval.")
        extra["feedback_interval"] = f"{a.feedback_interval}s"
    mid = do_send(a.root, a.role, a.type, a.title, read_body_arg(a), a.reply_to, extra)
    print(f"SENT [{mid:04d}] {a.type} -> {PEER[a.role]}")


def cmd_wait(a):
    poll(a.root, a.role, a.timeout, a.interval, a.max_chars)


def cmd_check(a):
    poll(a.root, a.role, 0, 0.1, a.max_chars)


def cmd_pulse(a):
    """check for messages; send a progress status only if the feedback interval has elapsed."""
    root, chat, _ = paths(a.root)
    require_session(chat)
    got = poll(a.root, a.role, 0, 0.1, a.max_chars)
    own = parse_index(chat, a.role)
    iv = feedback_interval(chat)
    since = None
    if own:
        ts = dt.datetime.strptime(own[-1]["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        since = (dt.datetime.now(dt.timezone.utc) - ts).total_seconds()
    if a.note and (since is None or since >= iv):
        mid = do_send(a.root, a.role, "status", a.note, a.note, a.reply_to)
        print(f"STATUS_SENT [{mid:04d}] (interval {iv}s)")
    elif a.note:
        print(f"STATUS_NOT_DUE (last message {int(since)}s ago, interval {iv}s)")
    return got


def cmd_status(a):
    root, chat, data = paths(a.root)
    require_session(chat)
    print(f"session: {chat / 'session.md'}")
    print(f"feedback_interval: {feedback_interval(chat)}s | max wait: {MAX_WAIT}s")
    idx = {r: parse_index(chat, r) for r in ROLES}
    for r in ROLES:
        last = idx[r][-1] if idx[r] else None
        unread = len([e for e in idx[PEER[r]] if e["id"] > read_cursor(chat, r)])
        last_s = f"last=[{last['id']:04d}] {last['type']}" if last else "last=none"
        print(f"- {r}: last seen {fmt_age(last_seen(chat, r))}, sent {len(idx[r])}, {last_s}, "
              f"unread from {PEER[r]}: {unread}")
    replied = {e["re"] for r in ROLES for e in idx[r] if e["re"] and e["type"] in ("result", "answer", "done")}
    open_items = [e for r in ROLES for e in idx[r] if e["type"] in ("task", "question") and e["id"] not in replied]
    if open_items:
        print("open tasks/questions (no result/answer yet):")
        for e in sorted(open_items, key=lambda e: e["id"]):
            print(f"  [{e['id']:04d}] {e['type']} {e['from']} -> {e['to']}: {e['title']}")
    else:
        print("open tasks/questions: none")


def cmd_read(a):
    root, chat, data = paths(a.root)
    require_session(chat)
    hits = sorted(data.glob(f"{int(a.id):04d}-*.md"))
    if not hits:
        sys.exit(f"ERROR: no message {int(a.id):04d}")
    print(hits[0].read_text(encoding="utf-8", errors="replace"))


def main():
    p = argparse.ArgumentParser(description="File-based message bus for orchestrator/assistant agents.")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=".", help="project root (default: cwd)")
    sub = p.add_subparsers(dest="cmd", required=True)
    _add = sub.add_parser
    sub.add_parser = lambda *x, **k: _add(*x, parents=[common], **k)

    def role(sp):
        sp.add_argument("--role", required=True, choices=ROLES)

    sp = sub.add_parser("init", help="create folders/session (if missing) and send ping")
    role(sp)
    sp.add_argument("--agent", default="unknown", help="who you are, e.g. 'Claude Code' or 'Codex'")
    sp.add_argument("--feedback-interval", type=int, help="orchestrator only: seconds (default 30)")
    sp.add_argument("--takeover", action="store_true", help="claim the role even if it looks active")
    sp.set_defaults(fn=cmd_init)

    sp = sub.add_parser("send", help="send a message")
    role(sp)
    sp.add_argument("--type", required=True, choices=TYPES)
    sp.add_argument("--title", required=True, help="one short line shown in the chat index")
    sp.add_argument("--reply-to", type=int)
    sp.add_argument("--body", help="body text, or '-' for stdin")
    sp.add_argument("--body-file", help="read body from this file")
    sp.add_argument("--feedback-interval", type=int, help="orchestrator only: set peer feedback interval (s)")
    sp.set_defaults(fn=cmd_send)

    for name, fn, hlp in (("wait", cmd_wait, f"block until new messages (max {MAX_WAIT}s)"),
                          ("check", cmd_check, "non-blocking check for new messages")):
        sp = sub.add_parser(name, help=hlp)
        role(sp)
        if name == "wait":
            sp.add_argument("--timeout", type=float, default=MAX_WAIT, help=f"seconds, capped at {MAX_WAIT}")
            sp.add_argument("--interval", type=float, default=1.0, help="poll interval seconds")
        sp.add_argument("--max-chars", type=int, default=6000, help="truncate each printed body")
        sp.set_defaults(fn=fn)

    sp = sub.add_parser("pulse", help="check + send progress status if feedback interval elapsed")
    role(sp)
    sp.add_argument("--note", help="one-line progress note (sent only when due)")
    sp.add_argument("--reply-to", type=int, help="task id this progress belongs to")
    sp.add_argument("--max-chars", type=int, default=6000)
    sp.set_defaults(fn=cmd_pulse)

    sp = sub.add_parser("status", help="show session overview")
    sp.set_defaults(fn=cmd_status)

    sp = sub.add_parser("read", help="print one message body by id")
    sp.add_argument("--id", required=True, type=int)
    sp.set_defaults(fn=cmd_read)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
