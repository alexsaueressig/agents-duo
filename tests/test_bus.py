"""Tests for agents_bus.py.  Run: python -m unittest discover -s tests"""
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

BUS = Path(__file__).resolve().parents[1] / "skills" / "duo-orchestrator" / "scripts" / "agents_bus.py"


def bus(root, *args, check=True):
    r = subprocess.run([sys.executable, str(BUS), *args, "--root", str(root)],
                       capture_output=True, text=True, encoding="utf-8")
    if check and r.returncode != 0:
        raise AssertionError(f"{args} failed ({r.returncode}): {r.stdout}{r.stderr}")
    return r


def orch(root, *args, **kw):
    return bus(root, args[0], "--role", "orchestrator", *args[1:], **kw)


def asst(root, name, *args, **kw):
    return bus(root, args[0], "--role", "assistant", "--name", name, *args[1:], **kw)


def sent_id(result):
    return result.stdout.split("[")[1].split("]")[0]


class BusTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_concurrent_init_creates_one_session(self):
        cmds = [["--role", "orchestrator"], ["--role", "assistant", "--name", "a"],
                ["--role", "assistant", "--name", "b"]]
        procs = [subprocess.Popen([sys.executable, str(BUS), "init", *c, "--root", str(self.root)],
                                  stdout=subprocess.PIPE, text=True) for c in cmds]
        outs = [p.communicate()[0] for p in procs]
        self.assertTrue(all(p.returncode == 0 for p in procs), outs)
        joined = "".join(outs)
        self.assertEqual(joined.count("session=created"), 1)
        self.assertEqual(joined.count("session=joined"), 2)

    def test_concurrent_sends_get_unique_ids(self):
        orch(self.root, "init")
        for n in "abcd":
            asst(self.root, n, "init")
        procs = [subprocess.Popen([sys.executable, str(BUS), "send", "--role", "assistant", "--name", n,
                                   "--type", "status", "--title", f"s{i}", "--body", "x", "--root", str(self.root)],
                                  stdout=subprocess.PIPE, text=True) for n in "abcd" for i in range(3)]
        for p in procs:
            p.communicate()
        names = [p.name for p in (self.root / ".agents-duo/messages").iterdir()]
        ids = [n[:4] for n in names]
        self.assertEqual(len(ids), len(set(ids)), names)
        self.assertEqual(len(ids), 5 + 12)

    def test_round_trip_auto_pong_and_hints(self):
        orch(self.root, "init")
        out = asst(self.root, "codex", "init").stdout
        self.assertIn("NEXT: wait for tasks:", out)
        out = asst(self.root, "codex", "check").stdout
        self.assertIn("[0001] PING", out)
        self.assertIn("AUTO_PONG", out)
        out = orch(self.root, "check").stdout
        self.assertIn("[0002] PING from codex", out)
        self.assertIn("PONG from codex", out)
        self.assertNotIn("NEXT:", out)  # orchestrator gets no hints
        asst(self.root, "codex", "check")  # consume the orchestrator's auto-pong
        for i in range(3):
            tid = int(orch(self.root, "send", "--type", "task", "--title", f"t{i} | pipe",
                           "--body", "x").stdout.split("[")[1][:4])
            out = asst(self.root, "codex", "wait", "--timeout", "2").stdout
            self.assertIn(f"[{tid:04d}] TASK", out)
            self.assertIn(f"--type result --reply-to {tid}", out)
            rid = sent_id(asst(self.root, "codex", "send", "--type", "result", "--reply-to", str(tid), "--title", "ok", "--body", "d"))
            self.assertIn("RESULT from codex", orch(self.root, "wait", "--timeout", "2").stdout)
            self.assertIn(f"[{tid:04d}] submitted -> codex", bus(self.root, "status").stdout)
            orch(self.root, "send", "--type", "done", "--reply-to", rid, "--title", "accepted")
            asst(self.root, "codex", "check")
        self.assertFalse([p for p in (self.root / ".agents-duo/messages").iterdir() if p.suffix == ".tmp"])
        self.assertIn("open tasks/questions: none", bus(self.root, "status").stdout)
        self.assertIn("NO_NEW_MESSAGES", asst(self.root, "codex", "check").stdout)

    def test_routing_between_assistants(self):
        orch(self.root, "init")
        asst(self.root, "a", "init")
        asst(self.root, "b", "init")
        for n in "ab":
            asst(self.root, n, "check")
        orch(self.root, "check")
        for n in "ab":
            asst(self.root, n, "check")  # consume auto-pongs
        r = orch(self.root, "send", "--type", "task", "--title", "x", "--body", "x", check=False)
        self.assertNotEqual(r.returncode, 0)  # two assistants: --to required
        orch(self.root, "send", "--type", "task", "--to", "a", "--title", "task-a", "--body", "x")
        orch(self.root, "send", "--type", "status", "--title", "broadcast", "--body", "x")
        out_a, out_b = asst(self.root, "a", "check").stdout, asst(self.root, "b", "check").stdout
        self.assertIn("task-a", out_a)
        self.assertNotIn("task-a", out_b)
        self.assertIn("broadcast", out_a)
        self.assertIn("broadcast", out_b)
        r = asst(self.root, "a", "send", "--type", "question", "--to", "b", "--title", "x", check=False)
        self.assertNotEqual(r.returncode, 0)  # assistants only talk to the orchestrator
        sent = asst(self.root, "b", "send", "--type", "question", "--title", "q", "--body", "?").stdout
        qid = sent.split("[")[1][:4]
        self.assertIn("-> b", orch(self.root, "send", "--type", "answer", "--reply-to", qid,
                                   "--title", "a", "--body", "!").stdout)
        status = bus(self.root, "status").stdout
        self.assertIn("- a (assistant)", status)
        self.assertIn("task-a", status)  # open task

    def test_bye_from_assistant(self):
        orch(self.root, "init")
        asst(self.root, "a", "init")
        asst(self.root, "a", "send", "--type", "bye", "--title", "leaving", "--body", "x")
        self.assertIn("ASSISTANT_LEFT: a", orch(self.root, "check").stdout)
        self.assertIn("LEFT", bus(self.root, "status").stdout)

    def test_self_reported_usage(self):
        orch(self.root, "init")
        asst(self.root, "gpt", "init", "--agent", "GPT")
        asst(self.root, "gpt", "send", "--type", "status", "--title", "s", "--body", "x", "--usage", "40k tokens")
        self.assertIn("[usage: 40k tokens | src self-reported]", orch(self.root, "check").stdout)
        self.assertIn("usage: 40k tokens", bus(self.root, "status").stdout)

    def test_plain_file_assistant(self):
        import os
        orch(self.root, "init")
        self.assertIn("Read .agents-duo/copilot/inbox.md", bus(self.root, "invite", "copilot").stdout)
        tid = orch(self.root, "send", "--type", "task", "--title", "review x", "--body", "do it").stdout.split("[")[1][:4]
        to = (self.root / ".agents-duo/copilot/inbox.md").read_text(encoding="utf-8")
        self.assertIn(f"## [{tid}] task: review x", to)
        self.assertIn("do it", to)
        frm = self.root / ".agents-duo/copilot/outbox.md"
        frm.write_text(f"## hello\n\n## reply {tid}\nreviewed, all good\n", encoding="utf-8")
        self.assertNotIn("RESULT", orch(self.root, "check").stdout)  # too fresh: may still be mid-edit
        old = time.time() - 5
        os.utime(frm, (old, old))
        out = orch(self.root, "check").stdout
        self.assertIn(f"RESULT from copilot (plain file) (re:{tid}): reviewed, all good", out)
        self.assertNotIn("AUTO_PONG", out)
        self.assertNotIn("RESULT", orch(self.root, "check").stdout)  # not delivered twice
        status = bus(self.root, "status").stdout
        self.assertIn("copilot (assistant, plain files)", status)
        self.assertIn(f"[{tid}] submitted -> copilot", status)
        orch(self.root, "send", "--to", "copilot", "--type", "done", "--reply-to", tid, "--title", "accepted")
        self.assertIn("open tasks/questions: none", bus(self.root, "status").stdout)
        orch(self.root, "send", "--type", "bye", "--title", "end", "--body", "thanks")
        self.assertIn("Session ended", (self.root / ".agents-duo/copilot/inbox.md").read_text(encoding="utf-8"))

    def test_pulse_throttles_status(self):
        orch(self.root, "init")
        asst(self.root, "a", "init")
        first = asst(self.root, "a", "pulse", "--note", "step 1").stdout
        second = asst(self.root, "a", "pulse", "--note", "step 2").stdout
        self.assertIn("STATUS_NOT_DUE", first)  # init ping was just sent
        self.assertIn("STATUS_NOT_DUE", second)

    def test_only_orchestrator_sets_interval(self):
        asst(self.root, "a", "init")
        r = asst(self.root, "a", "send", "--type", "status", "--title", "x", "--feedback-interval", "5", check=False)
        self.assertNotEqual(r.returncode, 0)
        orch(self.root, "init", "--feedback-interval", "15")
        self.assertIn("feedback_interval: 15s", bus(self.root, "status").stdout)

    def test_interval_must_be_positive(self):
        for bad in ("0", "-5"):
            self.assertNotEqual(orch(self.root, "init", "--feedback-interval", bad, check=False).returncode, 0, bad)
        orch(self.root, "init")
        for bad in ("0", "-5"):
            r = orch(self.root, "send", "--type", "status", "--title", "x", "--body", "x",
                     "--feedback-interval", bad, check=False)
            self.assertNotEqual(r.returncode, 0, bad)

    def test_parallel_checks_deliver_each_message_once(self):
        orch(self.root, "init")
        asst(self.root, "a", "init")
        titles = [f"task-{i}" for i in range(6)]
        for t in titles:
            orch(self.root, "send", "--type", "task", "--title", t, "--body", "x")
        procs = [subprocess.Popen([sys.executable, str(BUS), cmd, "--role", "assistant", "--name", "a",
                                   "--root", str(self.root)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, encoding="utf-8") for cmd in ["check", "pulse"] * 4]
        outs = [p.communicate() for p in procs]
        self.assertFalse([err for _, err in outs if err.strip()], "stderr output")
        joined = "".join(out for out, _ in outs)
        for t in titles:
            self.assertEqual(joined.count(f"TASK from orchestrator: {t} ==="), 1, t)
        adir = self.root / ".agents-duo/a"
        self.assertFalse([p.name for p in adir.iterdir() if p.suffix in (".tmp", ".lock")])
        self.assertIn("NO_NEW_MESSAGES", asst(self.root, "a", "check").stdout)

    def test_lock_recovers_stale_and_keeps_foreign_lock(self):
        import importlib.util
        import os
        spec = importlib.util.spec_from_file_location("agents_bus", BUS)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        target = self.root / "f.md"
        lock = self.root / "f.md.lock"
        lock.write_bytes(b"dead-writer")
        old = time.time() - 60
        os.utime(lock, (old, old))
        with mod.locked(target):  # stale lock from a crashed process is taken over
            self.assertNotEqual(lock.read_bytes(), b"dead-writer")
            lock.write_bytes(b"newer-owner")  # we stalled and someone else took the lock over
        self.assertEqual(lock.read_bytes(), b"newer-owner")  # release leaves the other owner's lock alone
        lock.unlink()
        with mod.locked(target):
            pass
        self.assertFalse(lock.exists())

    def test_assistant_resubscribes_on_init(self):
        orch(self.root, "init")
        asst(self.root, "a", "init")
        asst(self.root, "a", "check")
        orch(self.root, "check")
        asst(self.root, "a", "check")  # consume auto-pongs
        done_id = orch(self.root, "send", "--type", "task", "--title", "finished", "--body", "x").stdout.split("[")[1][:4]
        asst(self.root, "a", "check")
        asst(self.root, "a", "send", "--type", "result", "--reply-to", done_id, "--title", "ok", "--body", "x")
        open_id = orch(self.root, "send", "--type", "task", "--title", "unfinished", "--body", "x").stdout.split("[")[1][:4]
        orch(self.root, "send", "--type", "status", "--title", "backlog", "--body", "x")
        (self.root / ".agents-duo/a/usage.json").write_text("{}", encoding="utf-8")
        out = asst(self.root, "a", "init", "--takeover").stdout
        self.assertIn(f"resubscribed (skipped 2 unread, dropped tasks: {open_id})", out)
        self.assertFalse((self.root / ".agents-duo/a/usage.json").exists())
        self.assertIn("NO_NEW_MESSAGES", asst(self.root, "a", "check").stdout)  # backlog skipped
        out = orch(self.root, "check").stdout
        self.assertIn(f"RESULT from a (re:{open_id}): dropped: assistant resubscribed", out)
        self.assertIn("Fresh start: resubscribed", out)
        status = bus(self.root, "status").stdout
        self.assertIn(f"[{done_id}] submitted -> a", status)  # a previous result still needs review
        self.assertIn(f"[{open_id}] submitted -> a", status)  # dropped work does not vanish
        self.assertIn("sent 5", status)  # history kept: ping, pong, result, dropped result, new ping
        orch(self.root, "send", "--type", "done", "--reply-to", done_id, "--title", "accepted")
        orch(self.root, "send", "--type", "cancel", "--reply-to", open_id, "--title", "reassign dropped work")
        self.assertIn("open tasks/questions: none", bus(self.root, "status").stdout)
        orch(self.root, "send", "--type", "task", "--title", "after", "--body", "x")
        self.assertIn("TASK from orchestrator: after", asst(self.root, "a", "check").stdout)

    def test_invalid_names(self):
        for bad in ("all", "orchestrator", "Bad Name"):
            self.assertNotEqual(asst(self.root, bad, "init", check=False).returncode, 0, bad)

    def test_takeover_guard(self):
        asst(self.root, "a", "init")
        self.assertEqual(asst(self.root, "a", "init", check=False).returncode, 3)
        asst(self.root, "b", "init")  # another name is fine
        asst(self.root, "a", "init", "--takeover")

    def test_reset_clears_and_recreates(self):
        orch(self.root, "init")
        asst(self.root, "a", "init")
        orch(self.root, "send", "--type", "task", "--title", "t", "--body", "x")
        self.assertIn("OK reset: removed 3 messages", bus(self.root, "reset", "--force").stdout)
        chat = self.root / ".agents-duo"
        self.assertEqual(sorted(p.name for p in chat.iterdir()), ["messages", "session.md"])
        self.assertIn("created_by: reset", (chat / "session.md").read_text(encoding="utf-8"))
        self.assertEqual(list((self.root / ".agents-duo/messages").iterdir()), [])
        self.assertIn("session=joined", orch(self.root, "init").stdout)

    def test_orchestrator_init_starts_fresh(self):
        import os
        orch(self.root, "init")
        asst(self.root, "a", "init")
        orch(self.root, "send", "--type", "task", "--title", "old", "--body", "x")
        old = time.time() - 3600  # both agents stopped long ago, past the busy grace too
        for p in (self.root / ".agents-duo").rglob("*"):
            os.utime(p, (old, old))
        out = orch(self.root, "init", "--takeover").stdout
        self.assertIn("session=created ping=[0001] | cleared previous session: removed 3 messages, dropped open tasks/questions: 0003", out)
        self.assertEqual(sorted(p.name[:4] for p in (self.root / ".agents-duo/messages").iterdir()), ["0001"])
        self.assertFalse((self.root / ".agents-duo/a").exists())
        self.assertIn("created_by: orchestrator", (self.root / ".agents-duo/session.md").read_text(encoding="utf-8"))

    def test_orchestrator_init_keeps_session_with_active_assistant(self):
        asst(self.root, "a", "init")  # the assistant joined first
        out = orch(self.root, "init").stdout
        self.assertIn("kept session: a already active", out)
        self.assertIn("PING from a", orch(self.root, "check").stdout)
        orch(self.root, "send", "--type", "status", "--title", "s", "--body", "x")
        out = orch(self.root, "init", "--takeover", "--keep").stdout
        self.assertNotIn("cleared", out)
        self.assertNotIn("kept session", out)

    def test_busy_slow_assistant_keeps_session(self):
        import os
        orch(self.root, "init")
        asst(self.root, "a", "init")
        orch(self.root, "send", "--type", "task", "--title", "slow work", "--body", "x")
        old = time.time() - 300  # silent for 5 min, far past 3x interval, but its task is still open
        for p in (self.root / ".agents-duo").rglob("*"):
            os.utime(p, (old, old))
        self.assertIn("kept session: a already active", orch(self.root, "init", "--takeover").stdout)
        self.assertEqual(bus(self.root, "reset", check=False).returncode, 3)

    def test_busy_broadcast_task_keeps_session(self):
        import os
        orch(self.root, "init")
        asst(self.root, "a", "init")
        orch(self.root, "send", "--to", "all", "--type", "task", "--title", "slow work", "--body", "x")
        old = time.time() - 300  # a task to all keeps every assistant busy
        for p in (self.root / ".agents-duo").rglob("*"):
            os.utime(p, (old, old))
        self.assertIn("kept session: a already active", orch(self.root, "init", "--takeover").stdout)

    def test_reset_guard_when_active(self):
        asst(self.root, "a", "init")
        self.assertEqual(bus(self.root, "reset", check=False).returncode, 3)
        self.assertTrue(list((self.root / ".agents-duo/messages").iterdir()))

    def test_reset_without_session(self):
        self.assertIn("removed 0 messages", bus(self.root, "reset").stdout)
        self.assertTrue((self.root / ".agents-duo/session.md").exists())

    def test_wait_is_capped(self):
        asst(self.root, "a", "init")
        start = time.time()
        out = asst(self.root, "a", "wait", "--timeout", "1").stdout
        self.assertLess(time.time() - start, 5)
        self.assertIn("NO_NEW_MESSAGES", out)
        self.assertIn("NEXT: run the same wait again", out)

    def test_revision_then_acceptance_and_invalid_transitions(self):
        orch(self.root, "init")
        for name in ("a", "b"):
            asst(self.root, name, "init")
        tid = sent_id(orch(self.root, "send", "--type", "task", "--to", "a", "--title", "complete entries"))
        messages = self.root / ".agents-duo/messages"
        count = len(list(messages.iterdir()))
        for sender, name, kind, reference in (
            (asst, "a", "done", tid), (asst, "a", "revise", tid), (asst, "a", "cancel", tid),
            (asst, "b", "result", tid), (asst, "a", "result", "9999"),
        ):
            r = sender(self.root, name, "send", "--type", kind, "--reply-to", reference, "--title", "invalid", check=False)
            self.assertNotEqual(r.returncode, 0, (kind, r.stdout))
        r = orch(self.root, "send", "--type", "done", "--reply-to", tid, "--title", "too early", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(len(list(messages.iterdir())), count)

        rid = sent_id(asst(self.root, "a", "send", "--type", "result", "--reply-to", tid, "--title", "titles only"))
        orch(self.root, "send", "--type", "revise", "--reply-to", rid, "--title", "compare full entries")
        self.assertIn(f"[{tid}] revision_requested -> a", bus(self.root, "status").stdout)
        self.assertIn(f"NEXT: do task {tid}", asst(self.root, "a", "check").stdout)
        r = orch(self.root, "send", "--type", "done", "--reply-to", rid, "--title", "still incomplete", check=False)
        self.assertNotEqual(r.returncode, 0)
        latest = sent_id(asst(self.root, "a", "send", "--type", "result", "--reply-to", tid, "--title", "complete evidence"))
        self.assertIn(f"[{tid}] submitted -> a", bus(self.root, "status").stdout)
        r = orch(self.root, "send", "--type", "done", "--reply-to", rid, "--title", "stale", check=False)
        self.assertNotEqual(r.returncode, 0)
        orch(self.root, "send", "--type", "done", "--reply-to", latest, "--title", "accepted")
        status = bus(self.root, "status").stdout
        self.assertIn(f"[{tid}] accepted -> a", status)
        self.assertIn("open tasks/questions: none", status)
        self.assertNotEqual(asst(self.root, "a", "send", "--type", "result", "--reply-to", tid,
                                 "--title", "late", check=False).returncode, 0)

    def test_status_is_not_handoff_and_pulse_keeps_task_active(self):
        orch(self.root, "init", "--feedback-interval", "1")
        asst(self.root, "a", "init")
        tid = sent_id(orch(self.root, "send", "--type", "task", "--title", "prepare checks"))
        asst(self.root, "a", "check")
        time.sleep(1.1)
        out = asst(self.root, "a", "pulse", "--reply-to", tid, "--note", "preparing").stdout
        self.assertIn("STATUS_SENT", out)
        self.assertIn(f"NEXT: continue task {tid}", out)
        self.assertNotIn("wait again", out)
        rid = sent_id(asst(self.root, "a", "send", "--type", "result", "--reply-to", tid, "--title", "prepared"))
        orch(self.root, "send", "--type", "status", "--title", "implementation ready", "--reply-to", tid)
        out = asst(self.root, "a", "check").stdout
        self.assertIn("await orchestrator review", out)
        self.assertNotIn("NEXT: do task", out)
        self.assertIn(f"[{tid}] submitted -> a", bus(self.root, "status").stdout)
        orch(self.root, "send", "--type", "done", "--reply-to", rid, "--title", "accepted preparation")
        execute = sent_id(orch(self.root, "send", "--type", "task", "--title", "execute checks",
                               "--body", "Run python checks.py; expected evidence.json with all criteria."))
        self.assertIn(f"NEXT: do task {execute}", asst(self.root, "a", "check").stdout)

    def test_question_pauses_only_its_task_until_answer(self):
        orch(self.root, "init")
        asst(self.root, "a", "init")
        tid = sent_id(orch(self.root, "send", "--type", "task", "--title", "verify"))
        asst(self.root, "a", "check")
        qid = sent_id(asst(self.root, "a", "send", "--type", "question", "--reply-to", tid, "--title", "which input?"))
        out = asst(self.root, "a", "pulse").stdout
        self.assertIn("or an answer", out)
        self.assertNotIn("NEXT: continue task", out)
        orch(self.root, "send", "--type", "answer", "--reply-to", qid, "--title", "input.json")
        self.assertIn(f"NEXT: continue task {tid}", asst(self.root, "a", "check").stdout)
        self.assertIn(f"[{tid}] assigned -> a", bus(self.root, "status").stdout)

    def test_broadcast_results_require_individual_acceptance(self):
        orch(self.root, "init")
        for name in ("a", "b"):
            asst(self.root, name, "init")
        tid = sent_id(orch(self.root, "send", "--type", "task", "--to", "all", "--title", "review"))
        rid = sent_id(asst(self.root, "a", "send", "--type", "result", "--reply-to", tid, "--title", "reviewed"))
        orch(self.root, "send", "--type", "done", "--reply-to", rid, "--title", "accepted a")
        status = bus(self.root, "status").stdout
        self.assertIn(f"[{tid}] accepted -> a", status)
        self.assertIn(f"[{tid}] assigned -> b", status)
        self.assertNotIn("open tasks/questions: none", status)
        asst(self.root, "c", "init")
        self.assertNotIn(f"[{tid}] assigned -> c", bus(self.root, "status").stdout)
        self.assertNotIn("NEXT: do task", asst(self.root, "c", "check").stdout)
        orch(self.root, "send", "--type", "cancel", "--to", "b", "--reply-to", tid, "--title", "review no longer needed")
        self.assertIn("open tasks/questions: none", bus(self.root, "status").stdout)

    def test_plain_file_revision_and_acceptance_survive_appends(self):
        import os
        orch(self.root, "init")
        bus(self.root, "invite", "copilot")
        tid = sent_id(orch(self.root, "send", "--type", "task", "--title", "verify full entries"))
        outbox = self.root / ".agents-duo/copilot/outbox.md"

        def append_result(text):
            with outbox.open("a", encoding="utf-8") as f:
                f.write(text)
            old = time.time() - 5
            os.utime(outbox, (old, old))

        append_result(f"## reply {tid}\nTitles checked\n")
        orch(self.root, "send", "--type", "revise", "--to", "copilot", "--reply-to", tid, "--title", "full entries required")
        self.assertIn(f"[{tid}] revision_requested -> copilot", bus(self.root, "status").stdout)
        append_result(f"## reply {tid}\nComplete entries checked\n")
        self.assertIn(f"[{tid}] submitted -> copilot", bus(self.root, "status").stdout)
        orch(self.root, "send", "--type", "done", "--to", "copilot", "--reply-to", tid, "--title", "accepted")
        # Even while a new section is being edited, an existing acceptance remains final.
        with outbox.open("a", encoding="utf-8") as f:
            f.write("## status\nWaiting\n")
        self.assertIn(f"[{tid}] accepted -> copilot", bus(self.root, "status").stdout)
        append_result("## bye\nLeaving\n")
        self.assertIn("open tasks/questions: none", bus(self.root, "status").stdout)
        append_result(f"## reply {tid}\nUnrequested extra result\n")
        status = bus(self.root, "status").stdout
        self.assertIn(f"[{tid}] accepted -> copilot: verify full entries | result=plain-copilot-2", status)

    def test_invalid_resubscribe_options_do_not_mutate_tasks(self):
        orch(self.root, "init")
        asst(self.root, "a", "init")
        tid = sent_id(orch(self.root, "send", "--type", "task", "--title", "active"))
        chat = self.root / ".agents-duo"
        before = {str(p.relative_to(chat)): p.read_bytes() for p in chat.rglob("*") if p.is_file()}
        for args in (("--feedback-interval", "1"), ("--keep",)):
            self.assertNotEqual(asst(self.root, "a", "init", "--takeover", *args, check=False).returncode, 0)
        after = {str(p.relative_to(chat)): p.read_bytes() for p in chat.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertIn(f"[{tid}] assigned -> a", bus(self.root, "status").stdout)

    def test_bye_does_not_accept_and_unknown_usage_is_explicit(self):
        orch(self.root, "init")
        asst(self.root, "a", "init", "--agent", "Copilot")
        tid = sent_id(orch(self.root, "send", "--type", "task", "--title", "verify"))
        asst(self.root, "a", "send", "--type", "result", "--reply-to", tid, "--title", "submitted")
        self.assertIn("[usage: unavailable]", orch(self.root, "check").stdout)
        orch(self.root, "send", "--type", "bye", "--title", "end")
        out = asst(self.root, "a", "check").stdout
        self.assertIn("PEER_SAID_BYE", out)
        self.assertNotIn("NEXT: continue task", out)
        self.assertNotIn("wait again", out)
        self.assertIn(f"[{tid}] submitted -> a", bus(self.root, "status").stdout)


if __name__ == "__main__":
    unittest.main()
