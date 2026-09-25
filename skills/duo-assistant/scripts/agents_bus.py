#!/usr/bin/env python3
"""agents_bus.py - file-based message bus for one orchestrator and any number of assistants.

Single-writer design: every file has exactly one writer, so no locks are needed.

  .agents-duo/session.md                 created once (exclusive create) by whoever starts first
  .agents-duo/messages/NNNN-<from>-<type>-<slug>.md   immutable message bodies (atomic rename)
  .agents-duo/.ids/NNNN                  id claims (exclusive create, one file per id, never rewritten)
  .agents-duo/<name>/                    one folder per participant ("orchestrator", "codex", ...):
      index.md       append-only chat index, written ONLY by <name>
      cursor         index lines <name> has read, per source (+ heartbeat mtime)
      usage.json     cache for token-usage measurement
      inbox.md       plain-file assistants only: written ONLY by the orchestrator
      outbox.md      plain-file assistants only: written ONLY by <name>, by hand

Topology is a star: the orchestrator talks to one assistant (--to NAME) or to all (--to all);
assistants talk only to the orchestrator.

Agents without shell/Python join as plain-file assistants (`invite NAME`): they read
.agents-duo/<name>/inbox.md and append '## reply NNNN' sections to .agents-duo/<name>/outbox.md.

Commands: init, send, wait, check, pulse, status, invite, read, reset.  Run with -h for details.
Stdlib only. Works on Windows, macOS, Linux.
"""
import argparse
import datetime as dt
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

PROTOCOL = 2
ORCH = "orchestrator"
ALL = "all"
ROLES = (ORCH, "assistant")
TYPES = ("ping", "pong", "task", "result", "question", "answer", "status", "done", "bye")
BROADCAST_TYPES = ("ping", "status", "bye")  # orchestrator sends these to all by default
MAX_WAIT = 60            # hard cap for a single blocking wait, seconds
DEFAULT_INTERVAL = 30    # default feedback interval, seconds
BASE_DIR = ".agents-duo"      # everything the bus writes lives under this one folder
MSG_DIR = "messages"          # message bodies, inside BASE_DIR
MSG_FROM_AGENT = f"../{MSG_DIR}"  # body path as written in index lines (relative to an agent folder)
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
RESERVED = (ORCH, ALL, "session", MSG_DIR)
LINE_RE = re.compile(
    r"^- \[(?P<id>\d+)\] (?P<ts>\S+) (?P<type>\w+)(?: re:(?P<re>\d+))? -> (?P<to>[\w-]+): (?P<title>.*) \| (?P<path>\S+)$"
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
    return root, root / BASE_DIR, root / BASE_DIR / MSG_DIR


def agent_dir(chat, name):
    return chat / name


def index_file(chat, name):
    return chat / name / "index.md"


def cursor_file(chat, name):
    return chat / name / "cursor"


def slugify(text, n=40):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return (s[:n].rstrip("-")) or "msg"


def identity(a):
    """The participant name for this invocation: 'orchestrator' or the assistant's --name."""
    if a.role == ORCH:
        if a.name and a.name != ORCH:
            sys.exit("ERROR: the orchestrator is always named 'orchestrator'; drop --name.")
        return ORCH
    return valid_name(a.name or "assistant")


def valid_name(name):
    name = name.lower()
    if not NAME_RE.match(name) or name in RESERVED:
        sys.exit(f"ERROR: invalid assistant name {name!r} (use a-z, 0-9, '-'; not {', '.join(RESERVED)}).")
    return name


def assistants(chat):
    """Names of every assistant: bus assistants (own index) and plain-file assistants (invited)."""
    if not chat.is_dir():
        return []
    return sorted(d.name for d in chat.iterdir()
                  if d.is_dir() and d.name not in RESERVED and not d.name.startswith(".")
                  and ((d / "index.md").exists() or (d / "inbox.md").exists()))


def is_plain(chat, name):
    return inbox_file(chat, name).exists() and not index_file(chat, name).exists()


# ---------------------------------------------------------------- plain-file assistants
# The fundamental channel for agents that can only read and edit files (no shell/Python):
#   .agents-duo/<name>/inbox.md   written only by the orchestrator: rules + every message for <name>, in full
#   .agents-duo/<name>/outbox.md  written only by <name>, by hand: "## reply 0005" sections

PLAIN_SETTLE = 2.0  # seconds an outbox must be unchanged before it is read (the agent may be mid-edit)
PLAIN_SEC = re.compile(r"^##[ \t]*(hello|reply|result|question|status|bye)\b[ \t]*\[?#?(\d+)?\]?[^\n]*$", re.I | re.M)
PLAIN_TYPES = {"hello": "ping", "reply": "result", "result": "result", "question": "question",
               "status": "status", "bye": "bye"}


def inbox_file(chat, name):
    return chat / name / "inbox.md"


def plain_file(chat, name):
    return chat / name / "outbox.md"


def parse_plain(chat, name):
    f = plain_file(chat, name)
    try:
        mtime = f.stat().st_mtime
    except FileNotFoundError:
        return []
    if time.time() - mtime < PLAIN_SETTLE:
        return []  # still being edited; read on a later poll (cursors do not move)
    text = f.read_text(encoding="utf-8", errors="replace")
    ts = dt.datetime.fromtimestamp(mtime, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    heads = list(PLAIN_SEC.finditer(text))
    out = []
    for i, m in enumerate(heads):
        body = text[m.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)].strip()
        mtype = PLAIN_TYPES[m.group(1).lower()]
        title = next((ln.strip() for ln in body.splitlines() if ln.strip()), mtype)[:80]
        out.append({"id": 0, "ts": ts, "type": mtype, "re": int(m.group(2)) if m.group(2) else None,
                    "to": ORCH, "title": title, "path": "", "from": name, "body": body, "plain": True})
    return out


def plain_header(name):
    return (
        f"# Messages for {name}\n\n"
        f"Written only by the orchestrator. Do not edit this file.\n\n"
        f"You are **{name}**, an assistant in a team led by an orchestrator agent. How to work:\n\n"
        f"1. First, create `{BASE_DIR}/{name}/outbox.md` with one line: `## hello`. That file is yours: "
        f"only you write it, and you only ever add to its end. Never edit anything else in `{BASE_DIR}/`.\n"
        f"2. New messages are added at the bottom of this file as `## [0005] task: <title>`.\n"
        f"3. Do each task. Edit only the files the task says you own.\n"
        f"4. When done, add to the end of `{BASE_DIR}/{name}/outbox.md`:\n\n"
        f"   ```\n   ## reply 0005\n   <what you did, which files changed, how you checked it>\n   ```\n\n"
        f"   If the task is unclear, add `## question 0005` with your question instead, and wait for the answer "
        f"here.\n"
        f"5. Then read this file again for the next message. If there is none yet, tell the user you are waiting "
        f"and ask them to say \"check\" later.\n"
        f"6. When a message says `bye`, stop. If you have to stop early, add `## bye` with the state of your work.\n\n"
        f"---\n"
    )


def render_plain(chat, to, mid, mtype, title, body, reply_to):
    """Append an orchestrator message, in full, to each plain recipient's inbox (single writer)."""
    if mtype in ("ping", "pong"):
        return
    names = [n for n in assistants(chat) if is_plain(chat, n)] if to == ALL else [to] if is_plain(chat, to) else []
    re_part = f" (re {int(reply_to):04d})" if reply_to else ""
    hint = {"task": f"\n\n→ When done, add `## reply {mid:04d}` to your outbox.md.",
            "bye": "\n\n→ Session ended. Stop now."}.get(mtype, "")
    for n in names:
        append_text(inbox_file(chat, n), f"\n## [{mid:04d}] {mtype}{re_part}: {title}\n\n{(body or '').strip()}{hint}\n")


def append_text(path, text, stale=5.0):
    """Append for a file's single owner. The owner may run several processes at once (parallel tool
    calls), and appends are not atomic across processes on Windows, so a short exclusive-create lock
    guards the write. A lock older than `stale` seconds is from a crashed process and is removed."""
    lock = path.with_name(path.name + ".lock")
    while True:
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > stale:
                    lock.unlink()
            except FileNotFoundError:
                pass
            time.sleep(0.01)
    try:
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
    finally:
        lock.unlink()


def participants(chat):
    return ([ORCH] if index_file(chat, ORCH).exists() else []) + assistants(chat)


def sources(chat, me):
    """Indexes <me> reads from."""
    return assistants(chat) if me == ORCH else [ORCH]


def addressed_to(entry, me):
    return me == ORCH or entry["to"] in (me, ALL)


def parse_index(chat, name):
    """Return complete entries of <name>'s index (ignores a trailing partial line)."""
    f = index_file(chat, name)
    if not f.exists():
        return parse_plain(chat, name) if is_plain(chat, name) else []
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
            d["from"] = name
            out.append(d)
    return out


def read_cursors(chat, name):
    """{source: number of that source's index lines already read}."""
    out = {}
    try:
        for line in cursor_file(chat, name).read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].isdigit():
                out[parts[0]] = int(parts[1])
    except FileNotFoundError:
        pass
    return out


def write_cursors(chat, name, cursors):
    f = cursor_file(chat, name)
    f.parent.mkdir(exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text("".join(f"{k} {v}\n" for k, v in sorted(cursors.items())), encoding="utf-8")
    os.replace(tmp, f)


def heartbeat(chat, name):
    f = cursor_file(chat, name)
    if f.exists():
        os.utime(f, None)
    else:
        write_cursors(chat, name, {})


def last_seen(chat, name):
    """Seconds since <name> last touched the bus, or None if never."""
    stamps = [p.stat().st_mtime for p in (cursor_file(chat, name), index_file(chat, name), plain_file(chat, name))
              if p.exists()]
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
    return (chat / entry["from"] / entry["path"]).resolve()


def entry_fm(chat, entry):
    if entry.get("plain"):
        return {}
    try:
        return front_matter(body_path(chat, entry).read_text(encoding="utf-8", errors="replace"))
    except FileNotFoundError:
        return {}


def feedback_interval(chat, me=ALL):
    """Latest feedback_interval the orchestrator sent to <me> or to all, else default."""
    for e in reversed(parse_index(chat, ORCH)):
        if e["type"] in ("ping", "status") and e["to"] in (me, ALL):
            fm = entry_fm(chat, e)
            if fm.get("feedback_interval", "").rstrip("s").isdigit():
                return int(fm["feedback_interval"].rstrip("s"))
    return DEFAULT_INTERVAL


def departed(chat, name):
    own = parse_index(chat, name)
    return bool(own) and own[-1]["type"] == "bye"


def active_assistants(chat):
    return [n for n in assistants(chat) if not departed(chat, n)]


def agent_label(chat, name):
    for e in parse_index(chat, name):
        if e["type"] == "ping":
            return entry_fm(chat, e).get("agent", "")
    return ""


def require_session(chat):
    if not chat.exists():
        sys.exit(f"ERROR: no {BASE_DIR}/ here. Run `init --role <role>` first (cwd={Path.cwd()}).")


def claim_id(chat):
    """Globally unique id: exclusive-create .ids/NNNN. No locks, any number of writers."""
    ids = chat / ".ids"
    ids.mkdir(exist_ok=True)
    n = max([int(p.name) for p in ids.iterdir() if p.name.isdigit()], default=0) + 1
    while True:
        try:
            with open(ids / f"{n:04d}", "x"):
                return n
        except FileExistsError:
            n += 1


def resolve_to(chat, me, mtype, to, reply_to):
    if me != ORCH:
        if to and to != ORCH:
            sys.exit("ERROR: assistants only send to the orchestrator.")
        return ORCH
    if to:
        if to != ALL and to not in assistants(chat):
            sys.exit(f"ERROR: unknown assistant {to!r}. Joined: {', '.join(assistants(chat)) or 'none'}.")
        return to
    if reply_to:
        for n in assistants(chat):
            if any(e["id"] == int(reply_to) for e in parse_index(chat, n)):
                return n
    if mtype in BROADCAST_TYPES:
        return ALL
    act = active_assistants(chat)
    if len(act) == 1:
        return act[0]
    sys.exit(f"ERROR: pass --to <name> or --to all. Active assistants: {', '.join(act) or 'none yet'}.")


# ---------------------------------------------------------------- token usage
# Measured from the platform's own local session log for this project, so the orchestrator can
# weigh each agent's load. Best effort: returns "" when nothing can be measured.

def fmt_tokens(n):
    n = int(n or 0)
    return f"{n / 1e6:.2f}M" if n >= 1e6 else f"{n / 1e3:.0f}k" if n >= 1e3 else str(n)


def _newest(files, limit=40):
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[:limit]


def _jsonl(path, tail_bytes=None):
    with open(path, "rb") as fh:
        if tail_bytes:
            fh.seek(max(0, path.stat().st_size - tail_bytes))
            fh.readline()  # drop a partial first line
        for raw in fh:
            try:
                yield json.loads(raw)
            except ValueError:
                continue


def claude_usage(root, cache_path):
    """Sum usage from the newest Claude Code transcript of this project, parsing only new bytes."""
    home = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
    slug = re.sub(r"[^a-zA-Z0-9]", "-", str(root)).lower()
    dirs = [d for d in home.glob("*") if d.is_dir() and d.name.lower() == slug] if home.is_dir() else []
    logs = _newest([f for d in dirs for f in d.glob("*.jsonl")], 1)
    if not logs:
        return ""
    log = logs[0]
    try:
        c = json.loads(cache_path.read_text(encoding="utf-8"))
        if c.get("file") != str(log) or c.get("offset", 0) > log.stat().st_size:
            raise ValueError
    except (OSError, ValueError):
        c = {"file": str(log), "offset": 0, "total": 0, "cached": 0, "ctx": 0, "turns": 0, "model": "", "last_id": ""}
    g = lambda u, k: int(u.get(k) or 0)
    with open(log, "rb") as fh:
        fh.seek(c["offset"])
        for raw in fh:
            if not raw.endswith(b"\n"):
                break  # line still being written; pick it up next time
            c["offset"] += len(raw)
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            msg = obj.get("message") if obj.get("type") == "assistant" else None
            if not (isinstance(msg, dict) and isinstance(msg.get("usage"), dict)):
                continue
            if msg.get("id") and msg["id"] == c["last_id"]:
                continue  # one API message is logged as several lines with the same usage
            u = msg["usage"]
            ctx = g(u, "input_tokens") + g(u, "cache_creation_input_tokens") + g(u, "cache_read_input_tokens")
            c.update(total=c["total"] + ctx + g(u, "output_tokens"),
                     cached=c["cached"] + g(u, "cache_read_input_tokens"),
                     ctx=ctx, turns=c["turns"] + 1, model=msg.get("model") or c["model"], last_id=msg.get("id") or "")
    tmp = cache_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(c), encoding="utf-8")
    os.replace(tmp, cache_path)
    if not c["turns"]:
        return ""
    return (f"tokens {fmt_tokens(c['total'])} ({fmt_tokens(c['cached'])} cached) | context {fmt_tokens(c['ctx'])} | "
            f"turns {c['turns']}{' | model ' + c['model'] if c['model'] else ''} | src claude-code")


def _limit(rl, label):
    if not isinstance(rl, dict) or rl.get("used_percent") is None:
        return ""
    win = rl.get("window_minutes")
    win_s = f"{win // 60}h" if win and win % 60 == 0 else f"{win}m" if win else ""
    reset = ""
    if rl.get("resets_at"):
        reset = " resets " + dt.datetime.fromtimestamp(int(rl["resets_at"]), dt.timezone.utc).strftime("%H:%MZ")
    elif rl.get("resets_in_seconds") is not None:
        reset = f" resets in {int(rl['resets_in_seconds']) // 60}m"
    return f"{label} {win_s} {float(rl['used_percent']):.0f}%{reset}".replace("  ", " ")


def codex_usage(root):
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "sessions"
    if not home.is_dir():
        return ""
    want = os.path.normcase(str(root))
    for log in _newest(home.rglob("rollout-*.jsonl")):
        first = next(_jsonl(log), {})
        cwd = (first.get("payload") or {}).get("cwd") if first.get("type") == "session_meta" else None
        if not cwd or os.path.normcase(str(Path(cwd).resolve())) != want:
            continue
        tc = None
        for obj in _jsonl(log, tail_bytes=512 * 1024):
            p = obj.get("payload") or {}
            if p.get("type") == "token_count" and p.get("info"):
                tc = p
        if not tc:
            return ""
        info, rl = tc["info"], tc.get("rate_limits") or {}
        tot, last = info.get("total_token_usage") or {}, info.get("last_token_usage") or {}
        ctx, win = int(last.get("input_tokens") or 0), info.get("model_context_window")
        ctx_s = f"context {fmt_tokens(ctx)}/{fmt_tokens(win)} ({100 * ctx // win}%)" if win else f"context {fmt_tokens(ctx)}"
        parts = [f"tokens {fmt_tokens(tot.get('total_tokens'))} ({fmt_tokens(tot.get('cached_input_tokens'))} cached)",
                 ctx_s, _limit(rl.get("primary"), "limit"), _limit(rl.get("secondary"), "limit2")]
        return " | ".join(x for x in parts if x) + " | src codex"
    return ""


def measure_usage(root, chat, me, agent):
    a = (agent or "").lower()
    try:
        if "claude" in a:
            return claude_usage(root, chat / me / "usage.json")
        if "codex" in a:
            return codex_usage(root)
    except Exception:  # never let measurement break messaging
        pass
    return ""


def latest_usage(chat, name):
    for e in reversed(parse_index(chat, name)):
        fm = entry_fm(chat, e)
        if fm.get("usage"):
            return fm["usage"], e["ts"]
    return "", ""


def active_for(chat, name):
    own = parse_index(chat, name)
    if not own:
        return ""
    start = dt.datetime.strptime(own[0]["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    end = time.time() - (last_seen(chat, name) or 0)
    mins = max(0, int((end - start.timestamp()) // 60))
    return f"{mins // 60}h{mins % 60:02d}m" if mins >= 60 else f"{mins}m"


# ---------------------------------------------------------------- core ops

def do_send(root, me, mtype, title, body, to, reply_to=None, extra=None):
    root, chat, data = paths(root)
    require_session(chat)
    data.mkdir(parents=True, exist_ok=True)
    mid = claim_id(chat)
    title = " ".join(title.replace("|", "/").split())[:120] or mtype
    name = f"{mid:04d}-{me}-{mtype}-{slugify(title)}.md"
    fm = {
        "id": mid, "from": me, "to": to, "type": mtype,
        "reply_to": reply_to or "", "created": now_iso(), "title": title,
    }
    extra = dict(extra or {})
    if not extra.get("usage"):
        extra["usage"] = measure_usage(root, chat, me, extra.get("agent") or agent_label(chat, me))
    fm.update({k: " ".join(str(v).split()) for k, v in extra.items() if v})
    text = "---\n" + "".join(f"{k}: {v}\n" for k, v in fm.items()) + "---\n\n" + (body or "").rstrip() + "\n"
    tmp = data / (name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, data / name)  # atomic: readers never see a partial body
    re_part = f" re:{int(reply_to):04d}" if reply_to else ""
    line = f"- [{mid:04d}] {fm['created']} {mtype}{re_part} -> {to}: {title} | {MSG_FROM_AGENT}/{name}\n"
    agent_dir(chat, me).mkdir(exist_ok=True)
    append_text(index_file(chat, me), line)  # single writer (possibly several of its processes)
    if me == ORCH:
        render_plain(chat, to, mid, mtype, title, body, reply_to)
    heartbeat(chat, me)
    return mid


def unread(chat, me, src, cursors):
    """Cursors count complete index lines read (not ids: concurrent sends can append ids out of order)."""
    return parse_index(chat, src)[cursors.get(src, 0):]


def fetch_new(chat, me):
    """(messages addressed to me, updated cursors). Cursors advance past messages meant for others too."""
    cursors = read_cursors(chat, me)
    msgs = []
    for src in sources(chat, me):
        entries = unread(chat, me, src, cursors)
        if entries:
            cursors[src] = cursors.get(src, 0) + len(entries)
            msgs += [e for e in entries if addressed_to(e, me)]
    return sorted(msgs, key=lambda e: e["id"]), cursors


def print_messages(chat, me, msgs, max_chars):
    for e in msgs:
        re_part = f" (re:{e['re']:04d})" if e["re"] else ""
        to_part = " [to all]" if e["to"] == ALL else ""
        id_part = f"[{e['id']:04d}] " if e["id"] else ""
        plain = " (plain file)" if e.get("plain") else ""
        print(f"=== {id_part}{e['type'].upper()} from {e['from']}{plain}{to_part}{re_part}: {e['title']} ===")
        if e.get("plain"):
            text = e["body"]
        else:
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
        if me == ORCH and fm.get("usage"):
            print(f"[usage: {fm['usage']}]")
        if len(body) > max_chars:
            body = body[:max_chars] + f"\n...[truncated; full text: read --id {e['id']}]"
        print(body or "(empty body)")
        print()
    for e in msgs:
        if e["type"] == "bye":
            print("PEER_SAID_BYE" if me != ORCH else f"ASSISTANT_LEFT: {e['from']}")


def silence_note(chat, me):
    if me != ORCH:
        age, iv = last_seen(chat, ORCH), feedback_interval(chat, me)
        if age is None:
            return f"{ORCH} has not joined yet"
        warn = f" | WARNING: {ORCH} silent for {int(age)}s (> 3x feedback interval {iv}s)" if age > 3 * iv else ""
        return f"{ORCH} last seen {fmt_age(age)}{warn}"
    act = active_assistants(chat)
    if not act:
        return "no assistant has joined yet"
    parts, warns = [], []
    for n in act:
        age = last_seen(chat, n)
        plain = is_plain(chat, n)
        parts.append(f"{n}{' (plain)' if plain else ''} {fmt_age(age)}")
        if not plain and age is not None and age > 3 * feedback_interval(chat, n):
            warns.append(f"{n} silent for {int(age)}s")
    note = "assistants last seen: " + ", ".join(parts)
    if warns:
        note += " | WARNING: " + ", ".join(warns) + " (> 3x feedback interval)"
    return note


def bus_cmd(root_arg, me, cmd):
    """Exact command line an assistant should run next (so simple agents never have to compose one)."""
    root_part = "" if root_arg == "." else f' --root "{root_arg}"'
    return f'python "{Path(__file__).resolve()}" {cmd} --role assistant --name {me}{root_part}'


def next_hints(root_arg, me, msgs):
    """NEXT: lines for assistants. The orchestrator gets none (it follows its skill)."""
    if me == ORCH:
        return []
    out = []
    for e in msgs:
        if e["type"] == "bye":
            return ["NEXT: the orchestrator ended the session. Stop and summarize your work for the user."]
        if e["type"] == "task":
            out.append(f"NEXT: do task {e['id']:04d} (only touch the files it allows). About every "
                       f"{feedback_interval(paths(root_arg)[1], me)}s report progress with: "
                       f"{bus_cmd(root_arg, me, 'pulse')} --reply-to {e['id']} --note \"<progress>\"")
            out.append(f"NEXT: when finished: {bus_cmd(root_arg, me, 'send')} --type result --reply-to {e['id']} "
                       f"--title \"<one-line outcome>\" --body \"<changed files, how you verified, open questions>\"")
            out.append(f"NEXT: if the task is unclear, ask first: {bus_cmd(root_arg, me, 'send')} --type question "
                       f"--reply-to {e['id']} --title \"<question>\" --body \"<details>\"")
    return out + [f"NEXT: then wait again: {bus_cmd(root_arg, me, 'wait')}"]


def auto_pong(root_arg, chat, me, msgs):
    """Every ping gets a pong; the script does it so agents never have to."""
    for e in msgs:
        if e["type"] == "ping" and not e.get("plain"):
            mid = do_send(root_arg, me, "pong", "pong", "auto-reply", e["from"] if me == ORCH else ORCH, e["id"])
            print(f"AUTO_PONG [{mid:04d}] -> {e['from']}")


def poll(root_arg, me, timeout, interval, max_chars):
    root, chat, _ = paths(root_arg)
    require_session(chat)
    timeout = max(0.0, min(float(timeout), MAX_WAIT))
    deadline = time.time() + timeout
    while True:
        heartbeat(chat, me)
        msgs, cursors = fetch_new(chat, me)
        if cursors != read_cursors(chat, me):
            write_cursors(chat, me, cursors)
        if msgs:
            print_messages(chat, me, msgs, max_chars)
            auto_pong(root_arg, chat, me, msgs)
            for line in next_hints(root_arg, me, msgs):
                print(line)
            return True
        if time.time() >= deadline:
            break
        time.sleep(min(interval, max(0.05, deadline - time.time())))
    print(f"NO_NEW_MESSAGES (waited {int(timeout)}s) | {silence_note(chat, me)}")
    if me != ORCH:
        print(f"NEXT: run the same wait again: {bus_cmd(root_arg, me, 'wait')}")
    return False


# ---------------------------------------------------------------- commands

def create_session(root, chat, created_by):
    """Create session.md with an exclusive create. Returns False if it already exists."""
    try:
        with open(chat / "session.md", "x", encoding="utf-8", newline="\n") as fh:
            fh.write(
                "# Agents session\n\n"
                f"- protocol: {PROTOCOL}\n- created: {now_iso()}\n- created_by: {created_by}\n"
                f"- project: {root}\n- default_feedback_interval: {DEFAULT_INTERVAL}s "
                "(the orchestrator's latest ping/status overrides this)\n\n"
                "Rules: one orchestrator, any number of named assistants. Each participant appends ONLY\n"
                "to its own folder (`orchestrator/`, `<assistant-name>/`).\n"
                f"Message bodies live in `{MSG_DIR}/` and are never edited after creation.\n"
                "Use the agents_bus.py script to send/receive; do not hand-edit these files.\n"
            )
        return True
    except FileExistsError:
        return False


def ensure_gitignore(root):
    gi = root / ".gitignore"
    if gi.exists():
        lines = gi.read_text(encoding="utf-8", errors="replace").splitlines()
        if BASE_DIR + "/" not in lines and BASE_DIR not in lines:
            with open(gi, "a", encoding="utf-8") as fh:
                fh.write(("\n" if lines and lines[-1].strip() else "") + BASE_DIR + "/\n")


def cmd_init(a):
    me = identity(a)
    root, chat, data = paths(a.root)
    chat.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)
    age = last_seen(chat, me) if cursor_file(chat, me).exists() else None
    if age is not None and age < 3 * DEFAULT_INTERVAL and not a.takeover:
        hint = "" if me == ORCH else " Or join under another --name."
        print(f"WARNING: {me} looks active (last seen {int(age)}s ago). "
              f"If that session is dead, re-run with --takeover.{hint}")
        sys.exit(3)
    created = create_session(root, chat, me)
    agent_dir(chat, me).mkdir(exist_ok=True)
    idx = index_file(chat, me)
    if not idx.exists():
        header = f"# {ORCH} -> assistants" if me == ORCH else f"# {me} -> {ORCH}"
        idx.write_text(f"{header} (append-only, written only by {me})\n\n", encoding="utf-8")
    if not cursor_file(chat, me).exists():
        write_cursors(chat, me, {})
    ensure_gitignore(root)
    iv = a.feedback_interval or DEFAULT_INTERVAL
    if me == ORCH:
        body = (f"Orchestrator online (agent: {a.agent}).\n\n"
                f"Instruction: while working on a task, get feedback every {iv}s - between work steps run "
                f"`pulse` (checks for new instructions and sends a progress status when due). "
                f"Blocking waits are max {MAX_WAIT}s. Reply with `pong`, then wait for tasks.")
        extra = {"feedback_interval": f"{iv}s", "agent": a.agent}
        to = ALL
    else:
        if a.feedback_interval:
            sys.exit("ERROR: only the orchestrator sets feedback_interval.")
        body = (f"Assistant '{me}' online (agent: {a.agent}). Ready for tasks. "
                f"Will follow the orchestrator's feedback_interval (default {DEFAULT_INTERVAL}s).")
        extra = {"agent": a.agent, "name": me}
        to = ORCH
    mid = do_send(a.root, me, "ping", f"{me} online", body, to, extra=extra)
    print(f"OK init role={a.role} name={me} session={'created' if created else 'joined'} ping=[{mid:04d}] "
          f"| {silence_note(chat, me)}")
    print(f"chat: {chat}\ndata: {data}")
    if me != ORCH:
        print(f"NEXT: wait for tasks: {bus_cmd(a.root, me, 'wait')}")


def read_body_arg(a):
    if a.body_file:
        return Path(a.body_file).read_text(encoding="utf-8")
    if a.body == "-":
        return sys.stdin.read()
    return a.body or ""


def cmd_send(a):
    me = identity(a)
    _, chat, _ = paths(a.root)
    require_session(chat)
    extra = {}
    if a.feedback_interval:
        if me != ORCH:
            sys.exit("ERROR: only the orchestrator sets feedback_interval.")
        extra["feedback_interval"] = f"{a.feedback_interval}s"
    if a.usage:
        extra["usage"] = a.usage + " | src self-reported"
    to = resolve_to(chat, me, a.type, a.to, a.reply_to)
    mid = do_send(a.root, me, a.type, a.title, read_body_arg(a), to, a.reply_to, extra)
    print(f"SENT [{mid:04d}] {a.type} -> {to}")


def cmd_wait(a):
    poll(a.root, identity(a), a.timeout, a.interval, a.max_chars)


def cmd_check(a):
    poll(a.root, identity(a), 0, 0.1, a.max_chars)


def cmd_pulse(a):
    """check for messages; send a progress status only if the feedback interval has elapsed."""
    me = identity(a)
    root, chat, _ = paths(a.root)
    require_session(chat)
    got = poll(a.root, me, 0, 0.1, a.max_chars)
    own = parse_index(chat, me)
    iv = feedback_interval(chat, me)
    since = None
    if own:
        ts = dt.datetime.strptime(own[-1]["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        since = (dt.datetime.now(dt.timezone.utc) - ts).total_seconds()
    if a.note and (since is None or since >= iv):
        to = resolve_to(chat, me, "status", None, None)
        extra = {"usage": a.usage + " | src self-reported"} if a.usage else None
        mid = do_send(a.root, me, "status", a.note, a.note, to, a.reply_to, extra)
        print(f"STATUS_SENT [{mid:04d}] (interval {iv}s)")
    elif a.note:
        print(f"STATUS_NOT_DUE (last message {int(since)}s ago, interval {iv}s)")
    return got


def cmd_status(a):
    root, chat, data = paths(a.root)
    require_session(chat)
    print(f"session: {chat / 'session.md'}")
    print(f"feedback_interval: {feedback_interval(chat)}s | max wait: {MAX_WAIT}s")
    idx = {n: parse_index(chat, n) for n in participants(chat)}
    if ORCH not in idx:
        print(f"- {ORCH}: not joined")
    for n, own in idx.items():
        last = own[-1] if own else None
        plain = n != ORCH and is_plain(chat, n)
        cursors = read_cursors(chat, n)
        n_unread = "n/a" if plain else sum(
            1 for s in sources(chat, n) for e in unread(chat, n, s, cursors) if addressed_to(e, n))
        last_s = (f"last={'[%04d] ' % last['id'] if last['id'] else ''}{last['type']}" if last else "last=none")
        agent = agent_label(chat, n)
        role = "" if n == ORCH else " (assistant, plain files)" if plain else " (assistant)"
        left = ", LEFT" if n != ORCH and departed(chat, n) else ""
        print(f"- {n}{role}{f' [{agent}]' if agent else ''}: last seen {fmt_age(last_seen(chat, n))}, "
              f"active {active_for(chat, n) or '0m'}, sent {len(own)}, {last_s}, unread: {n_unread}{left}")
        usage, at = latest_usage(chat, n)
        print(f"    usage: {usage} (reported {at})" if usage else "    usage: not reported")
    if not assistants(chat):
        print("- assistants: none joined")
    everything = [e for own in idx.values() for e in own]
    replied = {e["re"] for e in everything if e["re"] and e["type"] in ("result", "answer", "done")}
    open_items = [e for e in everything if e["type"] in ("task", "question") and e["id"] and e["id"] not in replied]
    if open_items:
        print("open tasks/questions (no result/answer yet):")
        for e in sorted(open_items, key=lambda e: e["id"]):
            print(f"  [{e['id']:04d}] {e['type']} {e['from']} -> {e['to']}: {e['title']}")
    else:
        print("open tasks/questions: none")


def cmd_reset(a):
    """delete the whole .agents-duo folder and create a fresh, empty session."""
    root, chat, data = paths(a.root)
    if chat.is_dir() and not a.force:
        for n in participants(chat):
            age = last_seen(chat, n)
            if age is not None and age < 3 * feedback_interval(chat, n) and not departed(chat, n):
                print(f"WARNING: {n} looks active (last seen {int(age)}s ago). "
                      f"Stop it first or re-run with --force.")
                sys.exit(3)
    removed = len(list(data.glob("*.md"))) if data.is_dir() else 0
    if chat.is_dir():
        shutil.rmtree(chat)
    chat.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)
    create_session(root, chat, "duo-start")
    ensure_gitignore(root)
    print(f"OK reset: removed {removed} messages | fresh session at {chat / 'session.md'}")


def cmd_invite(a):
    """orchestrator: set up a plain-file assistant (no shell/Python needed on its side)."""
    root, chat, _ = paths(a.root)
    require_session(chat)
    name = valid_name(a.name)
    if index_file(chat, name).exists():
        sys.exit(f"ERROR: {name!r} already joined through the bus; pick another name.")
    f = inbox_file(chat, name)
    if not f.exists():
        f.parent.mkdir(exist_ok=True)
        f.write_text(plain_header(name), encoding="utf-8", newline="\n")
    d = f"{BASE_DIR}/{name}"
    print(f"OK invited {name} (plain files: {d}/inbox.md <- orchestrator, {d}/outbox.md <- {name})")
    print(f"TELL_USER: give {name} this one line: Read {d}/inbox.md and follow its instructions.")


def cmd_read(a):
    root, chat, data = paths(a.root)
    require_session(chat)
    hits = sorted(data.glob(f"{int(a.id):04d}-*.md"))
    if not hits:
        sys.exit(f"ERROR: no message {int(a.id):04d}")
    print(hits[0].read_text(encoding="utf-8", errors="replace"))


USAGE_HELP = ("self-reported usage, one line (only if your platform's usage is not measured automatically; "
              "Claude Code and Codex are measured from their local session logs)")


def main():
    p = argparse.ArgumentParser(description="File-based message bus: one orchestrator, many assistants.")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=".", help="project root (default: cwd)")
    sub = p.add_subparsers(dest="cmd", required=True)
    _add = sub.add_parser
    sub.add_parser = lambda *x, **k: _add(*x, parents=[common], **k)

    def role(sp):
        sp.add_argument("--role", required=True, choices=ROLES)
        sp.add_argument("--name", help="assistant name, unique per session, e.g. 'codex' (default: 'assistant')")

    sp = sub.add_parser("init", help="create folders/session (if missing) and send ping")
    role(sp)
    sp.add_argument("--agent", default="unknown", help="who you are, e.g. 'Claude Code', 'Codex' or 'GPT'")
    sp.add_argument("--feedback-interval", type=int, help="orchestrator only: seconds (default 30)")
    sp.add_argument("--takeover", action="store_true", help="claim the name even if it looks active")
    sp.set_defaults(fn=cmd_init)

    sp = sub.add_parser("send", help="send a message")
    role(sp)
    sp.add_argument("--type", required=True, choices=TYPES)
    sp.add_argument("--title", required=True, help="one short line shown in the chat index")
    sp.add_argument("--to", help="orchestrator only: assistant name or 'all' (default: sender of --reply-to, "
                                 "'all' for ping/status/bye, else the only active assistant)")
    sp.add_argument("--reply-to", type=int)
    sp.add_argument("--body", help="body text, or '-' for stdin")
    sp.add_argument("--body-file", help="read body from this file")
    sp.add_argument("--feedback-interval", type=int, help="orchestrator only: set feedback interval (s)")
    sp.add_argument("--usage", help=USAGE_HELP)
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
    sp.add_argument("--usage", help=USAGE_HELP)
    sp.add_argument("--max-chars", type=int, default=6000)
    sp.set_defaults(fn=cmd_pulse)

    sp = sub.add_parser("status", help="show session overview")
    sp.set_defaults(fn=cmd_status)

    sp = sub.add_parser("invite", help="orchestrator: add a plain-file assistant (for agents without shell/Python)")
    sp.add_argument("name", help="assistant name, e.g. copilot")
    sp.set_defaults(fn=cmd_invite)

    sp = sub.add_parser("reset", help="delete all session data and create a fresh, empty session")
    sp.add_argument("--force", action="store_true", help="reset even if an agent looks active")
    sp.set_defaults(fn=cmd_reset)

    sp = sub.add_parser("read", help="print one message body by id")
    sp.add_argument("--id", required=True, type=int)
    sp.set_defaults(fn=cmd_read)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
