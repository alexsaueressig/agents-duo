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
            asst(self.root, "codex", "send", "--type", "result", "--reply-to", str(tid), "--title", "ok", "--body", "d")
            self.assertIn("RESULT from codex", orch(self.root, "wait", "--timeout", "2").stdout)
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
        self.assertIn("open tasks/questions: none", status)
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
        self.assertIn("created_by: duo-start", (chat / "session.md").read_text(encoding="utf-8"))
        self.assertEqual(list((self.root / ".agents-duo/messages").iterdir()), [])
        self.assertIn("session=joined", orch(self.root, "init").stdout)

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


if __name__ == "__main__":
    unittest.main()
