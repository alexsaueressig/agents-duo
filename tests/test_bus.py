"""Tests for agents_bus.py.  Run: python -m unittest discover -s tests"""
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

BUS = Path(__file__).resolve().parents[1] / "skills" / "agents-duo-orchestrator" / "scripts" / "agents_bus.py"


def bus(root, *args, check=True):
    r = subprocess.run([sys.executable, str(BUS), *args, "--root", str(root)],
                       capture_output=True, text=True, encoding="utf-8")
    if check and r.returncode != 0:
        raise AssertionError(f"{args} failed ({r.returncode}): {r.stdout}{r.stderr}")
    return r


class BusTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_concurrent_init_creates_one_session(self):
        procs = [subprocess.Popen([sys.executable, str(BUS), "init", "--role", r, "--root", str(self.root)],
                                  stdout=subprocess.PIPE, text=True) for r in ("orchestrator", "assistant")]
        outs = [p.communicate()[0] for p in procs]
        self.assertTrue(all(p.returncode == 0 for p in procs), outs)
        joined = "".join(outs)
        self.assertEqual(joined.count("session=created"), 1)
        self.assertEqual(joined.count("session=joined"), 1)

    def test_round_trip_ids_and_cursors(self):
        bus(self.root, "init", "--role", "orchestrator")
        bus(self.root, "init", "--role", "assistant")
        self.assertIn("[0001] PING", bus(self.root, "check", "--role", "assistant").stdout)
        self.assertIn("[0002] PING", bus(self.root, "check", "--role", "orchestrator").stdout)
        for i in range(5):
            out = bus(self.root, "send", "--role", "orchestrator", "--type", "task",
                      "--title", f"t{i} | pipe", "--body", "x").stdout
            tid = int(out.split("[")[1][:4])
            self.assertEqual(tid % 2, 1)
            self.assertIn(f"[{tid:04d}] TASK", bus(self.root, "wait", "--role", "assistant", "--timeout", "2").stdout)
            bus(self.root, "send", "--role", "assistant", "--type", "result", "--reply-to", str(tid),
                "--title", "ok", "--body", "done")
            self.assertIn("RESULT", bus(self.root, "wait", "--role", "orchestrator", "--timeout", "2").stdout)
        data = list((self.root / ".agents-transfer-data").iterdir())
        self.assertEqual(len(data), 12)
        self.assertFalse([p for p in data if p.suffix == ".tmp"])
        self.assertIn("open tasks/questions: none", bus(self.root, "status").stdout)
        self.assertIn("NO_NEW_MESSAGES", bus(self.root, "check", "--role", "assistant").stdout)

    def test_pulse_throttles_status(self):
        bus(self.root, "init", "--role", "orchestrator")
        bus(self.root, "init", "--role", "assistant")
        first = bus(self.root, "pulse", "--role", "assistant", "--note", "step 1").stdout
        second = bus(self.root, "pulse", "--role", "assistant", "--note", "step 2").stdout
        self.assertIn("STATUS_NOT_DUE", first)  # init ping was just sent
        self.assertIn("STATUS_NOT_DUE", second)

    def test_only_orchestrator_sets_interval(self):
        bus(self.root, "init", "--role", "assistant")
        r = bus(self.root, "send", "--role", "assistant", "--type", "status", "--title", "x",
                "--feedback-interval", "5", check=False)
        self.assertNotEqual(r.returncode, 0)
        bus(self.root, "init", "--role", "orchestrator", "--feedback-interval", "15")
        self.assertIn("feedback_interval: 15s", bus(self.root, "status").stdout)

    def test_takeover_guard(self):
        bus(self.root, "init", "--role", "assistant")
        self.assertEqual(bus(self.root, "init", "--role", "assistant", check=False).returncode, 3)
        bus(self.root, "init", "--role", "assistant", "--takeover")

    def test_wait_is_capped(self):
        bus(self.root, "init", "--role", "assistant")
        start = time.time()
        out = bus(self.root, "wait", "--role", "assistant", "--timeout", "1").stdout
        self.assertLess(time.time() - start, 5)
        self.assertIn("NO_NEW_MESSAGES", out)


if __name__ == "__main__":
    unittest.main()
